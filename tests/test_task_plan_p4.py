"""P4：配方复用 / 证据即完成 / 开工前避坑清单。"""
import json

import task_plan


def _setup(tmp_path, monkeypatch, goal="先写报告，再校验内容", steps=None):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    plans = tmp_path / "plans"
    plans.mkdir(exist_ok=True)
    monkeypatch.setattr(task_plan, "_PLAN_DIR", str(plans))
    monkeypatch.setattr(task_plan, "_WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(task_plan, "_QUEUE", [])
    monkeypatch.setattr(task_plan, "_ACTIVE", {})
    return ws, plans


def _completed_plan(plan_id="p4done01", goal="先写报告，再校验内容"):
    plan = task_plan.TaskPlan(
        plan_id=plan_id,
        session_id="sess-p4",
        goal=goal,
        steps=[{"title": "写报告"}, {"title": "校验内容"}],
    )
    # 先落盘初始计划；complete_step 内部 load 新对象逐步验收，不能用旧对象覆盖
    task_plan.save(plan)
    task_plan.complete_step(plan_id, 1, "done", "报告已写入 report.md")
    task_plan.complete_step(plan_id, 2, "done", "内容校验通过")
    return task_plan.load(plan_id)


class _NoopClient:
    """配方命中时不应调用模型规划；若被调用则记录，供断言。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        cb = kwargs.get("on_content")
        if cb:
            cb(json.dumps({"steps": [{"title": "模型临时规划"}]}, ensure_ascii=False))
        return True


def test_save_recipe_and_reuse_skips_planning(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    done = _completed_plan()
    recipe, note = task_plan.save_recipe(done)
    assert recipe is not None and "配方" in note
    assert len(recipe["steps"]) == 2

    recipes = task_plan.list_recipes()
    assert any(r.get("recipe_id") == recipe["recipe_id"] for r in recipes)

    # 同类目标命中配方，直接套用已验证步骤，不调用模型规划
    client = _NoopClient()
    plan = task_plan.create(client, "先写报告，再校验内容（重试）", "sess-p4")
    assert client.calls == 0, "命中配方不应再调用模型规划"
    assert plan.recipe_id == recipe["recipe_id"]
    assert [s["title"] for s in plan.steps] == ["写报告", "校验内容"]


def test_create_without_recipe_still_calls_model(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client = _NoopClient()
    plan = task_plan.create(client, "完全不相关的另一件事：配置网络代理", "sess-p4")
    assert client.calls == 1
    assert plan.recipe_id == ""
    assert plan.steps[0]["title"] == "模型临时规划"


def test_evidence_is_done_no_recheck(tmp_path, monkeypatch):
    """最后一步验收通过即 completed，finalize 不再追加复核轮。"""
    _setup(tmp_path, monkeypatch)
    plan = task_plan.TaskPlan(
        plan_id="p4final1", session_id="s", goal="做一件事",
        steps=[{"title": "第一步"}, {"title": "第二步"}],
    )
    task_plan.save(plan)
    task_plan.complete_step("p4final1", 1, "done", "完成证据一")
    plan2, just = task_plan.finalize("p4final1")
    assert just is False and plan2.status == "running"

    task_plan.complete_step("p4final1", 2, "done", "完成证据二")
    # mark() 内部已按证据置 completed；finalize 幂等
    assert task_plan.load("p4final1").status == "completed"
    plan3, just2 = task_plan.finalize("p4final1")
    assert just2 is False and plan3.status == "completed"


def test_pitfall_recorded_and_injected(tmp_path, monkeypatch):
    ws, _ = _setup(tmp_path, monkeypatch)
    plan = task_plan.TaskPlan(
        plan_id="p4pitf01", session_id="s", goal="先写报告，再校验内容",
        steps=[{"title": "写报告"}, {"title": "校验内容"}],
    )
    task_plan.save(plan)
    # 第一步连续失败直到阻塞，会记录避坑经验
    for _ in range(task_plan.MAX_STEP_ATTEMPTS):
        task_plan.complete_step("p4pitf01", 1, "failed", "接口超时，报告未写入")
    pitfalls = task_plan.list_pitfalls()
    assert any("接口超时" in (p.get("reason") or "") for p in pitfalls)

    # 新同类任务开工：自动套用避坑经验（不调用模型，验证 create 会带上）
    client = _NoopClient()
    new_plan = task_plan.create(client, "先写报告，再校验内容", "s2")
    assert new_plan.pitfalls_applied, "同类任务应套用历史避坑条目"
    prompt = task_plan.render_prompt(new_plan)
    assert "避坑" in prompt and "接口超时" in prompt


def test_delete_recipe(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    done = _completed_plan("p4del001")
    recipe, _ = task_plan.save_recipe(done)
    assert task_plan.delete_recipe(recipe["recipe_id"]) is True
    assert task_plan.delete_recipe(recipe["recipe_id"]) is False
    assert all(r.get("recipe_id") != recipe["recipe_id"] for r in task_plan.list_recipes())
