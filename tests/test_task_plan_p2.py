import os

import task_plan


def _setup(tmp_path, monkeypatch):
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    monkeypatch.setattr(task_plan, "_PLAN_DIR", str(plan_dir))
    plan = task_plan.TaskPlan(
        plan_id="p2abc123",
        session_id="sess-1",
        goal="先写文件，再校验内容",
        steps=[{"title": "写文件"}, {"title": "校验内容"}],
    )
    task_plan.save(plan)
    return plan


def test_structured_verification_pass_and_fail(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = tmp_path / "out.txt"
    out.write_text("hello whaletalk", encoding="utf-8")

    plan, msg = task_plan.complete_step(
        "p2abc123", 1, "done", "已写出 out.txt",
        {"expect_contains": ["whaletalk"], "min_size": 4},
        str(out),
    )
    assert plan is not None
    assert plan.steps[0]["status"] == "done"
    assert plan.steps[0]["verification"]["ok"] is True
    assert "验收" in msg

    plan, msg = task_plan.complete_step(
        "p2abc123", 2, "done", "看起来对了",
        {"expect_contains": ["不存在的内容"]},
        str(out),
    )
    assert plan.steps[1]["status"] == "failed"
    assert plan.steps[1]["attempts"] == 1
    assert "验收失败" in msg


def test_repeated_failure_blocks_step(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for _ in range(task_plan.MAX_STEP_ATTEMPTS):
        plan, _msg = task_plan.complete_step("p2abc123", 1, "failed", "网络错误")
    assert plan.steps[0]["status"] == "blocked"
    assert plan.status == "blocked"
    assert "换一种方法" in task_plan.current_hint(plan)


def test_control_pause_resume_retry_skip(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    plan, _ = task_plan.control("p2abc123", "pause")
    assert plan.status == "paused"

    plan, _ = task_plan.control("p2abc123", "resume")
    assert plan.status == "running"

    task_plan.complete_step("p2abc123", 1, "failed", "工具不可用")
    plan, _ = task_plan.control("p2abc123", "retry", 1)
    assert plan.steps[0]["status"] == "pending"
    assert plan.steps[0]["attempts"] == 0

    task_plan.control("p2abc123", "skip", 1)
    task_plan.control("p2abc123", "skip", 2)
    plan, _ = task_plan.control("p2abc123", "skip", 2)
    assert plan.status == "completed"
    assert plan.all_done() is True


def test_list_and_archive(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    task_plan.complete_step("p2abc123", 1, "done", "已写入 a.txt")
    items = task_plan.list_plans(status="active", session_id="sess-1")
    assert len(items) == 1
    assert items[0]["done"] == 1

    plan = task_plan.load("p2abc123")
    text = task_plan.archive_text(plan)
    assert "任务归档" in text
    assert "已写入 a.txt" in text
