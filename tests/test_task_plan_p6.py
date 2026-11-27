"""P6：参数化技能 / 产物直达 / 技能管理台 / 开工前影响预览。"""
import json
from pathlib import Path

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


class _NoModelClient:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return True


GOAL_A = (
    "请依次完成：第一步在工作区创建文件 alpha.txt，内容写入 hello-A；"
    "第二步创建文件 beta.txt，内容写入 world-B；第三步确认这两个文件都存在且内容正确。"
)
GOAL_B = (
    "请依次完成：第一步在工作区创建文件 gamma.txt，内容写入 hello-B；"
    "第二步创建文件 delta.txt，内容写入 world-C；第三步确认这两个文件都存在且内容正确。"
)


def _calls_for(ws):
    return [
        {"name": "write_file", "args": {"path": str(ws / "alpha.txt"), "content": "hello-A"}},
        {"name": "write_file", "args": {"path": str(ws / "beta.txt"), "content": "world-B"}},
        {"name": "verify_files", "args": {"paths": [str(ws / "alpha.txt"), str(ws / "beta.txt")]}},
    ]


def _seed_skill(tmp_path, monkeypatch, goal=GOAL_A, successes=2):
    ws, _ = _setup(tmp_path, monkeypatch)
    calls = _calls_for(ws)
    traces = {"1": calls[:2], "3": [calls[2]]}
    tpl = task_plan.build_skill_template(goal, calls)
    assert tpl is not None, "真实形态的轨迹应能生成参数化模板"
    row = {
        "recipe_id": "skillp6001",
        "name": goal[:60],
        "tokens": sorted(task_plan._goal_tokens(goal)),
        "strategy": "general",
        "steps": [{"title": "写第一个文件"}, {"title": "写第二个文件"}, {"title": "确认文件"}],
        "traces": traces,
        "template": tpl,
        "source_goal": goal,
        "source_plan_id": "seedp6001",
        "touched_paths": ["alpha.txt", "beta.txt"],
        "uses": 0,
        "successes": successes,
        "skill_hits": 0,
        "skill_failures": 0,
        "replay_calls": 0,
        "disabled": False,
        "created_at": "2026-11-27 00:00:00",
        "updated_at": "2026-11-27 00:00:00",
    }
    p = Path(task_plan._recipes_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")
    return ws, goal


def test_template_handles_absolute_paths_and_list_args(tmp_path, monkeypatch):
    ws, _ = _setup(tmp_path, monkeypatch)
    calls = _calls_for(ws)
    tpl = task_plan.build_skill_template(GOAL_A, calls)
    assert tpl["goal_pattern"].count("{s") == 4, "四个字面量片段都应成为槽位"
    assert tpl["slots"]
    # 绝对路径只替换文件名片段，目录前缀保留
    write_args = tpl["calls"][0]["args"]
    assert "{s" in write_args["path"] and str(ws) in write_args["path"]
    assert "{s" in tpl["calls"][2]["args"]["paths"][0], "列表参数也要参数化"


def test_changed_parameters_hit_template_not_exact(tmp_path, monkeypatch):
    ws, _ = _seed_skill(tmp_path, monkeypatch)
    hit = task_plan.match_skill(GOAL_B)
    assert hit is not None, "换了文件名与内容也应命中参数化技能"
    assert hit["_match_mode"] == "template", "不能误走旧字面量的精确重放"
    calls = hit["_skill_calls"]
    assert len(calls) == 3
    assert "gamma.txt" in calls[0]["args"]["path"]
    assert calls[0]["args"]["content"] == "hello-B"
    assert "delta.txt" in calls[1]["args"]["path"]
    assert calls[1]["args"]["content"] == "world-C"
    assert "gamma.txt" in calls[2]["args"]["paths"][0]
    assert "delta.txt" in calls[2]["args"]["paths"][1]


def test_create_skips_planner_on_parameterized_hit(tmp_path, monkeypatch):
    ws, _ = _seed_skill(tmp_path, monkeypatch)
    client = _NoModelClient()
    plan = task_plan.create(client, GOAL_B, "sess-p6")
    assert client.calls == 0, "参数化命中也必须跳过模型规划"
    assert plan.execution_mode == "deterministic"
    assert plan.skill_preview.get("match_mode") == "template"
    assert any("gamma.txt" in p for p in plan.skill_preview.get("paths", [])), "开工前应预知将改动的文件"
    assert plan.skill_preview.get("call_count") == 3


def test_parameterized_replay_writes_new_files(tmp_path, monkeypatch):
    ws, _ = _seed_skill(tmp_path, monkeypatch)
    client = _NoModelClient()
    plan = task_plan.create(client, GOAL_B, "sess-p6")
    task_plan.save(plan)
    skill = task_plan.match_skill(GOAL_B)
    ok, msg = task_plan.execute_skill(plan, skill)
    assert ok is True, msg
    assert client.calls == 0
    assert (ws / "gamma.txt").read_text(encoding="utf-8") == "hello-B"
    assert (ws / "delta.txt").read_text(encoding="utf-8") == "world-C"
    assert not (ws / "alpha.txt").exists(), "旧文件不应被重放出来"


def test_unsafe_slot_value_falls_back(tmp_path, monkeypatch):
    ws, goal = _seed_skill(tmp_path, monkeypatch)
    tpl = task_plan.build_skill_template(goal, _calls_for(ws))
    # 新目标把文件名换成路径穿越，必须拒绝抽取
    bad = GOAL_B.replace("gamma.txt", "../../escape.txt")
    assert task_plan.extract_slots(tpl["goal_pattern"], bad, tpl["slots"]) is None
    assert task_plan.match_skill(bad) is None


def test_disabled_skill_does_not_hit(tmp_path, monkeypatch):
    _seed_skill(tmp_path, monkeypatch)
    ok, data, msg = task_plan.control_skill("skillp6001", "disable")
    assert ok and data["disabled"] is True
    assert task_plan.match_skill(GOAL_A) is None
    ok, data, _ = task_plan.control_skill("skillp6001", "enable")
    assert ok and data["disabled"] is False
    assert task_plan.match_skill(GOAL_A) is not None


def test_skill_stats_and_auto_disable(tmp_path, monkeypatch):
    _seed_skill(tmp_path, monkeypatch)
    ok, _ = task_plan.record_skill_outcome("skillp6001", True, 3)
    assert ok
    totals = task_plan.skill_totals()
    assert totals["saved_model_rounds"] == 1 and totals["replay_calls"] == 3
    task_plan.record_skill_outcome("skillp6001", False)
    _, msg = task_plan.record_skill_outcome("skillp6001", False)
    assert "停用" in msg
    assert task_plan.skill_totals()["active"] == 0
    assert task_plan.match_skill(GOAL_A) is None


def test_preview_and_delete_skill(tmp_path, monkeypatch):
    _seed_skill(tmp_path, monkeypatch)
    ok, data, msg = task_plan.control_skill("skillp6001", "preview")
    assert ok and data["call_count"] == 3 and "不实际写文件" in data["note"]
    ok, _, _ = task_plan.control_skill("skillp6001", "delete")
    assert ok and task_plan.list_skills() == []


def test_recall_direct_zero_model(tmp_path, monkeypatch):
    ws, _ = _setup(tmp_path, monkeypatch)
    plan = task_plan.TaskPlan(
        plan_id="recallp601", session_id="s",
        goal="生成季度销售报告并校验数据",
        steps=[
            {"title": "写文件", "status": "done", "evidence": "已写 sales_report.md"},
            {"title": "校验", "status": "done", "evidence": "核心结论：营收增长 12%"},
        ],
        status="completed",
    )
    task_plan.save(plan)
    (ws / "sales_report.md").write_text("营收增长 12%", encoding="utf-8")
    task_plan.register_artifact("recallp601", 1, str(ws / "sales_report.md"), "已写销售报告")
    got = task_plan.recall_direct("上次的销售报告结论是什么？")
    assert got is not None, "强命中的历史追问应直达"
    text, card = got
    assert card["zero_model"] is True and card["artifacts"]
    # U3：结构化详情由卡片承载，正文只给引导 + 产物名，不再复述整段原始任务
    assert "销售报告" in card["goal"]
    assert "任务：生成季度销售报告" not in text, "正文不应复述原始任务"
    assert "sales_report.md" in text, "纯文本客户端仍要能看到产物名"
    # 执行指令不能直达，交回模型路径
    assert task_plan.recall_direct("请生成一份销售报告") is None
    # 无关问题不直达
    assert task_plan.recall_direct("今天天气怎么样？") is None
