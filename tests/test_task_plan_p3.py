import json

import task_plan


def _setup(tmp_path, monkeypatch, steps=None):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    plans = tmp_path / "plans"
    plans.mkdir(exist_ok=True)
    monkeypatch.setattr(task_plan, "_PLAN_DIR", str(plans))
    monkeypatch.setattr(task_plan, "_WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(task_plan, "_QUEUE", [])
    monkeypatch.setattr(task_plan, "_ACTIVE", {})
    plan = task_plan.TaskPlan(
        plan_id="p3abc123",
        session_id="sess-3",
        goal="先改文件，再校验内容",
        steps=steps or [{"title": "改文件"}],
    )
    task_plan.save(plan)
    return ws, plan


def test_baseline_artifact_diff_and_rollback(tmp_path, monkeypatch):
    ws, plan = _setup(tmp_path, monkeypatch)
    target = ws / "a.txt"
    target.write_text("old line\n", encoding="utf-8")
    created = ws / "b.txt"
    task_plan.capture_workspace_baseline(plan.plan_id)

    target.write_text("new line\n", encoding="utf-8")
    created.write_text("brand new\n", encoding="utf-8")

    art, msg = task_plan.register_artifact(plan.plan_id, 1, str(target), "已修改 a.txt")
    assert art["state"] == "modified"
    assert "-old line" in art["diff"] and "+new line" in art["diff"]
    assert "已登记产物" in msg

    art2, _ = task_plan.register_artifact(plan.plan_id, 1, str(created), "新建 b.txt")
    assert art2["state"] == "created"

    plan, results, _msg = task_plan.rollback(plan.plan_id)
    assert plan is not None
    assert target.read_text(encoding="utf-8") == "old line\n"
    assert not created.exists()
    assert all(r.get("ok") for r in results)
    assert plan.rollbacks[-1]["summary"] == "2/2 成功"


def test_queue_priority_and_release(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(task_plan, "max_concurrent", lambda: 1)
    p1 = task_plan.TaskPlan(plan_id="queueaaa", session_id="s1", goal="处理 out/a.txt",
                            steps=[{"title": "A"}], priority=2)
    p2 = task_plan.TaskPlan(plan_id="queuebbb", session_id="s2", goal="处理 out/b.txt",
                            steps=[{"title": "B"}], priority=0)
    task_plan.save(p1)
    task_plan.save(p2)

    assert task_plan.wait_for_slot(p1.plan_id, timeout=0.2) is True
    assert task_plan.queue_position(p1.plan_id) == 0
    assert task_plan.wait_for_slot(p2.plan_id, timeout=0.2) is False
    assert task_plan.queue_snapshot() == []

    task_plan.release_slot(p1.plan_id)
    assert task_plan.wait_for_slot(p2.plan_id, timeout=0.2) is True
    task_plan.release_slot(p2.plan_id)


def test_queue_conflict_same_path(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(task_plan, "max_concurrent", lambda: 4)
    task_plan._ACTIVE["otherplan"] = {
        "session_id": "s-other", "paths": ["out/shared.md"], "acquired_at": 0,
    }
    plan = task_plan.TaskPlan(plan_id="conflict1", session_id="s3", goal="处理 out/shared.md",
                              steps=[{"title": "写同一文件"}])
    task_plan.save(plan)
    assert task_plan.wait_for_slot(plan.plan_id, timeout=0.2) is False
    assert task_plan.queue_snapshot() == []
    task_plan._ACTIVE.clear()


def test_dag_dependency_waiting_and_failure_isolation(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, steps=[
        {"title": "A"},
        {"title": "B", "depends_on": [1]},
        {"title": "C"},
    ])
    plan = task_plan.load("p3abc123")
    assert plan.steps[1]["status"] == "waiting"
    assert [s["id"] for s in plan.ready_steps()] == [1, 3]

    task_plan.complete_step("p3abc123", 1, "done", "A 完成")
    plan = task_plan.load("p3abc123")
    assert plan.steps[1]["status"] == "pending"
    assert plan.all_done() is False

    for _ in range(task_plan.MAX_STEP_ATTEMPTS):
        task_plan.complete_step("p3abc123", 3, "failed", "C 失败")
    plan = task_plan.load("p3abc123")
    assert plan.steps[2]["status"] == "blocked"
    assert task_plan.current_hint(plan)
    # C 分支失败，但 B 无依赖可继续：计划不应整体阻塞
    assert plan.status == "running"
    assert [s["id"] for s in plan.ready_steps()] == [2]

    task_plan.complete_step("p3abc123", 2, "done", "B 完成")
    plan = task_plan.load("p3abc123")
    assert plan.done_count() == 2
    assert "依赖" in task_plan.render_prompt(plan)


def test_runtime_error_backoff_and_resume(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    task_plan.bind_stream("p3abc123", "stream-xyz")
    plan, wait, blocked = task_plan.note_runtime_error("p3abc123", "429", "请求过快")
    assert plan.runtime["stream_id"] == "stream-xyz"
    assert plan.runtime["state"] == "waiting"
    assert wait > 0 and blocked is False

    plan, _wait, blocked = task_plan.note_runtime_error("p3abc123", "429", "请求过快")
    assert plan.runtime["retries"] == 2
    plan, _wait, blocked = task_plan.note_runtime_error("p3abc123", "429", "请求过快")
    assert blocked is True
    assert plan.status == "blocked"

    plan, msg = task_plan.control("p3abc123", "resume")
    assert plan.status == "running"
    assert plan.runtime["state"] == "ok"
    assert plan.runtime["stream_id"] == "stream-xyz"
    assert "恢复" in msg


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, messages, **kwargs):
        cb = kwargs.get("on_content")
        if cb:
            cb(json.dumps(self.payload, ensure_ascii=False))
        return True


def test_replan_remaining_uses_fake_client(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, steps=[
        {"title": "旧方案第一步"},
        {"title": "旧方案第二步"},
    ])
    task_plan.complete_step("p3abc123", 1, "done", "第一步已完成")
    plan, _ = task_plan.complete_step("p3abc123", 2, "failed", "接口超时")
    client = _FakeClient({"steps": [{"title": "改用本地缓存重试", "depends_on": [1]}]})
    replanned, changed = task_plan.replan_remaining(client, plan)
    assert changed is True
    assert replanned.steps[0]["status"] == "done"
    assert replanned.steps[1]["title"] == "改用本地缓存重试"
    assert replanned.steps[1]["attempts"] == 0
    assert replanned.status == "running"
