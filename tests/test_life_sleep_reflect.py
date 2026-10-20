"""睡眠/反思/报告/API 回归（不联网）。"""

import brainkit as bk


def _cfg(**over):
    base = {
        "life_enabled": True,
        "life_reflect_idle_minutes": 0,
        "life_sleep_idle_minutes": 999,
        "life_auto_sleep": False,
        "life_reflect_cooldown_minutes": 0,
        "life_max_active_intentions": 3,
        "life_daily_token_budget": 300000,
        "life_daily_cost_budget": 2.0,
        "life_quiet_hours": "",
        "life_sleep_llm": False,
        "life_reflect_max_tokens": 500,
        "brain_community_enabled": False,
    }
    base.update(over)
    return base


def _manifest():
    bk.save_json(bk.BRAIN_DIR / "manifest.json", {"brain_id": "testbrain", "fingerprint": "x"})


def test_sleep_idempotent_and_narrative(tmp_path, monkeypatch):
    from life import sleep, state, timeline
    _manifest()
    monkeypatch.setattr(sleep, "_cfg", lambda: _cfg())
    r1 = sleep.run(reason="test", use_llm=False)
    assert r1["ok"] and not r1["skipped"]
    assert state.load()["last_sleep_day"]
    assert timeline.last("sleep_end")
    r2 = sleep.run(reason="test", use_llm=False)
    assert r2["skipped"] is True


def test_reflect_creates_intention_and_outcome(tmp_path, monkeypatch):
    import deepseek_client as dc
    from life import intentions, llm, loop, state
    _manifest()
    monkeypatch.setattr(dc, "STATS_FILE", str(tmp_path / "stats.json"))
    monkeypatch.setattr(loop, "_cfg", lambda: _cfg())
    monkeypatch.setattr(llm, "call", lambda *a, **k: (
        '{"kind":"write_thought","drive":"curiosity","title":"记录一个未解问题",'
        '"why":"模型返回","first_step":"写下来","expected":"thinking_log 多一条"}',
        None, "deepseek-flash", ""))
    state.save({"state": "awake", "last_wake": "2020-01-01T00:00:00+08:00"})
    loop._reflect_worker(_cfg())
    done = [x for x in intentions.list_all(status="done")]
    assert done, intentions.list_all()
    assert done[0]["kind"] == "write_thought"
    assert done[0]["outcome"]
    assert state.load()["state"] == "awake"


def test_refresh_self_model_uses_stream_callback(tmp_path, monkeypatch):
    import brain_api
    import deepseek_client as dc
    class FakeClient:
        model = "deepseek-flash"
        def chat(self, messages, **kw):
            cb = kw.get("on_content")
            if cb:
                cb('{"knows":["我知道测试"],"unknowns":["未知"],"limits":["局限"]}')
            return True
    monkeypatch.setattr(dc, "get_active_client", lambda: FakeClient())
    monkeypatch.setattr(dc, "TOOL_CALL_MAP", {}, raising=False)
    assert brain_api.refresh_self_model() is True
    sm = bk.load_json(bk.BRAIN_DIR / "self_model.json", {})
    assert sm["source"] == "llm" and sm["knows"][0] == "我知道测试"


def test_report_and_api_dispatch(tmp_path, monkeypatch):
    from life import api, loop, report, state, timeline
    _manifest()
    monkeypatch.setattr(loop, "_cfg", lambda: _cfg())
    state.save({"state": "awake", "tick_count": 3, "last_tick": "2020-01-01T00:00:00+08:00"})
    timeline.append("wake", "test")
    rep = report.report(7)
    assert rep["continuity"]["tick_count"] == 3
    assert 0 <= rep["health"] <= 100
    assert "生命循环报告" in report.render_markdown(rep)
    st = api.status()
    assert st["ok"] and st["life"]["state"]["state"] == "awake"
    r = api.dispatch("add-intention", {"title": "手动意图", "source": "user"})
    assert r["ok"] and r["intention"]["source"] == "user"
    assert api.dispatch("not-a-real-action")["ok"] is False
