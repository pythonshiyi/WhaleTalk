"""生命连续性（P1）：暂停重启、冷却持久、节律醒来、指标与整理意图。"""
from datetime import datetime

import brainkit as bk


def _cfg(**over):
    base = {
        "life_enabled": True,
        "life_reflect_idle_minutes": 999,
        "life_sleep_idle_minutes": 999,
        "life_auto_sleep": False,
        "life_auto_wake": True,
        "life_reflect_cooldown_minutes": 60,
        "life_max_active_intentions": 3,
        "life_daily_token_budget": 300000,
        "life_daily_cost_budget": 2.0,
        "life_quiet_hours": "",
        "brain_community_enabled": False,
        "life_metrics_enabled": True,
    }
    base.update(over)
    return base


def _manifest():
    bk.save_json(bk.BRAIN_DIR / "manifest.json", {"brain_id": "testbrain", "fingerprint": "x"})


def test_start_respects_persisted_pause(tmp_path, monkeypatch):
    from life import loop, state
    _manifest()
    monkeypatch.setattr(loop, "_cfg", lambda: _cfg())
    state.save({"paused": True, "pause_reason": "用户暂停", "state": "paused"})
    try:
        r = loop.start("restart")
        assert r["state"] == "paused" and r["paused"] is True
        assert state.load()["paused"] is True
        assert state.load()["pause_reason"] == "用户暂停"
    finally:
        state.lease_release()


def test_tick_auto_wakes_at_next_wake_at(tmp_path, monkeypatch):
    from life import loop, state
    _manifest()
    monkeypatch.setattr(loop, "_cfg", lambda: _cfg())
    state.save({"state": "sleeping", "last_wake": "2020-01-01T00:00:00+08:00",
                "next_wake_at": "2020-01-01T07:00:00+08:00"})
    r = loop.tick(now=datetime.now())
    assert r["ok"] is True
    assert state.load()["state"] == "awake"
    assert state.load()["next_wake_at"] == ""


def test_reflect_writes_last_reflect_state(tmp_path, monkeypatch):
    import deepseek_client as dc
    from life import intentions, llm, loop, state
    _manifest()
    monkeypatch.setattr(dc, "STATS_FILE", str(tmp_path / "stats.json"))
    monkeypatch.setattr(loop, "_cfg", lambda: _cfg())
    monkeypatch.setattr(llm, "call", lambda *a, **k: (
        '{"kind":"write_thought","drive":"curiosity","title":"记录一个问题",'
        '"why":"测试","first_step":"写下来","expected":"落盘"}', None, "deepseek-flash", ""))
    state.save({"state": "awake", "last_wake": "2020-01-01T00:00:00+08:00"})
    loop._reflect_worker(_cfg())
    assert state.load().get("last_reflect")
    assert loop._last_reflect_ts(state.load()) > 0
    assert intentions.list_all(status="done")


def test_housekeeping_intention_runs_maintenance(tmp_path, monkeypatch):
    from life import intentions, loop, metrics, state
    _manifest()
    monkeypatch.setattr(loop, "_cfg", lambda: _cfg(life_intention_archive_max=20))
    state.save({"state": "awake"})
    ok, it, _ = intentions.propose("做一次整理", kind="housekeeping", drive="maintenance")
    assert ok
    ok2, outcome = loop.execute_intention(it)
    assert ok2 and "整理完成" in outcome
    assert metrics.path().parent.exists()


def test_tool_intent_allowlist_and_noop_guard(tmp_path, monkeypatch):
    import deepseek_client as dc
    from life import intentions, loop
    _manifest()
    monkeypatch.setattr(dc, "TOOL_CALL_MAP", {"read_memory": lambda keyword="": "同一结果"})
    ok_bad, out_bad = loop.execute_intention({"kind": "tool", "payload": {"tool": "run_command", "args": {}}})
    assert not ok_bad and "白名单" in out_bad

    def _run():
        ok, it, _ = intentions.propose("查记忆", kind="tool", drive="curiosity",
                                       payload={"tool": "read_memory", "args": {"keyword": "x"}})
        assert ok
        intentions.start(it["id"])
        ok2, outcome = loop.execute_intention(it)
        intentions.complete(it["id"], outcome)
        return ok2, outcome

    ok1, out1 = _run()
    ok2, out2 = _run()
    ok3, out3 = _run()
    assert ok1 and ok2 and ok3
    assert "同一结果" in out1 and "无变化" in out2 and "无变化" in out3
    ok4, _, why = intentions.propose("再查一次", kind="tool", drive="curiosity",
                                     payload={"tool": "read_memory", "args": {"keyword": "x"}})
    assert not ok4 and "无变化" in why


def test_review_memory_intention_writes_thought(tmp_path, monkeypatch):
    from life import loop
    _manifest()
    bk.remember_structured("用户偏好表格化输出", type="偏好", importance=4)
    ok, outcome = loop.execute_intention({
        "kind": "review_memory", "title": "复盘输出偏好", "why": "表格",
        "expected": "写进思考日志",
    })
    assert ok and "相关记忆" in outcome
    text = (bk.THINKING_DIR / f"{bk.today()}.md").read_text(encoding="utf-8")
    assert "记忆复盘" in text and "表格化" in text
