"""P5：确定性技能 / 跨会话任务召回。"""
import json

import task_plan


def _setup(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    plans = tmp_path / "plans"
    plans.mkdir(exist_ok=True)
    monkeypatch.setattr(task_plan, "_PLAN_DIR", str(plans))
    monkeypatch.setattr(task_plan, "_WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(task_plan, "_QUEUE", [])
    monkeypatch.setattr(task_plan, "_ACTIVE", {})
    return ws, plans


def _skill_client():
    """命中技能时不应调用模型规划；若调用则计数。"""
    class C:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            cb = kwargs.get("on_content")
            if cb:
                cb(json.dumps({"steps": [{"title": "临时规划"}]}, ensure_ascii=False))
            return True
    return C()


def _seed_recipe(tmp_path, monkeypatch, goal, successes=2):
    """造一份带 traces 的已验证配方（模拟真实成功后沉淀）。"""
    _setup(tmp_path, monkeypatch)
    from pathlib import Path
    recipes = Path(task_plan._recipes_path())
    recipes.parent.mkdir(parents=True, exist_ok=True)
    rows = [{
        "recipe_id": "skill00001",
        "name": goal[:40],
        "tokens": sorted(task_plan._goal_tokens(goal)),
        "strategy": "general",
        "steps": [{"title": "写 alpha 文件"}, {"title": "校验文件"}],
        "traces": {
            "写 alpha 文件": [{"name": "write_file", "args": {
                "path": str(tmp_path / "ws" / "alpha.txt"), "content": "skill-ok"}}],
            "校验文件": [{"name": "verify_files", "args": {
                "paths": [str(tmp_path / "ws" / "alpha.txt")]}}],
        },
        "source_plan_id": "seedplan01",
        "uses": 0,
        "successes": successes,
        "created_at": "2026-11-27 00:00:00",
        "updated_at": "2026-11-27 00:00:00",
    }]
    recipes.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return goal


def test_skill_requires_two_successes_and_traces(tmp_path, monkeypatch):
    goal = _seed_recipe(tmp_path, monkeypatch, "写 alpha 文件再校验文件", successes=1)
    assert task_plan.match_skill(goal) is None, "成功不足 2 次不应成为技能"
    task_plan._setup_rows = None  # noqa: 仅占位说明
    # 提升到 2 次
    from pathlib import Path
    p = Path(task_plan._recipes_path())
    rows = json.loads(p.read_text(encoding="utf-8"))
    rows[0]["successes"] = 2
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    hit = task_plan.match_skill(goal)
    assert hit is not None and hit["traces"], "精确命中且达标的配方应返回技能"


def test_create_marks_deterministic_on_skill_hit(tmp_path, monkeypatch):
    goal = _seed_recipe(tmp_path, monkeypatch, "写 alpha 文件再校验文件")
    client = _skill_client()
    plan = task_plan.create(client, goal, "sess-p5")
    assert client.calls == 0, "命中技能不应调用模型规划"
    assert plan.execution_mode == "deterministic"
    assert [s["title"] for s in plan.steps] == ["写 alpha 文件", "校验文件"]


def test_execute_skill_replays_zero_model_calls(tmp_path, monkeypatch):
    goal = _seed_recipe(tmp_path, monkeypatch, "写 alpha 文件再校验文件")
    client = _skill_client()
    plan = task_plan.create(client, goal, "sess-p5")
    task_plan.save(plan)
    skill = task_plan.match_skill(goal)
    events = []
    ok, msg = task_plan.execute_skill(plan, skill, emit=lambda ev, data: events.append((ev, data)))
    assert ok is True, msg
    assert client.calls == 0, "确定性执行全程不调用模型"
    assert (tmp_path / "ws" / "alpha.txt").read_text(encoding="utf-8") == "skill-ok"
    assert any(ev == "tool_start" for ev, _ in events)
    fresh = task_plan.load(plan.plan_id)
    assert fresh.all_done() and fresh.status == "completed"


def test_execute_skill_rejects_unsafe_trace(tmp_path, monkeypatch):
    """含不可重放工具的轨迹应整段作废，降级模型执行。"""
    goal = _seed_recipe(tmp_path, monkeypatch, "写 alpha 文件再校验文件")
    from pathlib import Path
    p = Path(task_plan._recipes_path())
    rows = json.loads(p.read_text(encoding="utf-8"))
    rows[0]["traces"]["校验文件"] = [{"name": "run_shell", "args": {"cmd": "echo hi"}}]
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    client = _skill_client()
    plan = task_plan.create(client, goal, "sess-p5")
    task_plan.save(plan)
    skill = task_plan.match_skill(goal)
    ok, msg = task_plan.execute_skill(plan, skill)
    assert ok is False and "降级" in msg
    assert client.calls == 0


def test_buffer_and_flush_trace(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    plan = task_plan.TaskPlan(
        plan_id="ptrace01", session_id="s", goal="写报告并校验",
        steps=[{"title": "写报告"}, {"title": "校验"}],
    )
    task_plan.save(plan)
    task_plan.buffer_tool_call("ptrace01", "write_file", {"path": "a.txt", "content": "x"})
    task_plan.flush_tool_calls("ptrace01", 1)
    fresh = task_plan.load("ptrace01")
    assert fresh.step_traces.get("1") and fresh.step_traces["1"][0]["name"] == "write_file"
    # 交互工具不入轨迹
    task_plan.buffer_tool_call("ptrace01", "ask_user", {"prompt": "?"})
    task_plan.flush_tool_calls("ptrace01", 2)
    assert "2" not in task_plan.load("ptrace01").step_traces


def _completed_with_artifact(tmp_path, monkeypatch, plan_id, goal, relpath, evidence):
    _setup(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    f = ws / relpath
    f.write_text(evidence, encoding="utf-8")
    plan = task_plan.TaskPlan(
        plan_id=plan_id, session_id="s", goal=goal,
        steps=[{"title": "写文件"}, {"title": "校验"}],
    )
    task_plan.save(plan)
    task_plan.complete_step(plan_id, 1, "done", f"已写 {relpath}")
    task_plan.register_artifact(plan_id, 1, str(f), f"已写 {relpath}")
    task_plan.complete_step(plan_id, 2, "done", evidence)
    return task_plan.load(plan_id)


def test_recall_tasks_by_goal_and_artifact(tmp_path, monkeypatch):
    _completed_with_artifact(
        tmp_path, monkeypatch, "recall001", "生成季度销售报告并校验数据",
        "sales_report.md", "报告已完成，核心结论：营收增长 12%")
    # 按目标词召回
    rows = task_plan.recall_tasks("上次的销售报告结论是什么")
    assert rows and rows[0]["plan_id"] == "recall001"
    assert any("sales_report.md" in (a or "") for a in rows[0]["artifacts"])
    assert any("12%" in (s.get("evidence") or "") for s in rows[0]["steps"])
    # 按产物文件名召回
    rows2 = task_plan.recall_tasks("sales_report.md 在哪")
    assert rows2 and rows2[0]["plan_id"] == "recall001"
    # 渲染成可注入文本
    text = task_plan.recall_text("销售报告")
    assert "历史任务召回" in text and "sales_report.md" in text


def test_recall_ignores_incomplete_and_unrelated(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    running = task_plan.TaskPlan(plan_id="recallrun", session_id="s", goal="销售报告草稿",
                                 steps=[{"title": "写"}])
    task_plan.save(running)  # 未完成，不应召回
    assert task_plan.recall_tasks("销售报告") == []
