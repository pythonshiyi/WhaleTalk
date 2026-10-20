"""生命循环核心回归：时间线 / 租约 / 内驱 / 意图 / 代谢 / tick。

这些用例不联网、不调用真实模型；LLM 入口只在 reflect/sleep 中，
测试通过高空闲阈值与假配置绕开。
"""
import json


import brainkit as bk


def _cfg(**over):
    base = {
        "life_enabled": True,
        "life_reflect_idle_minutes": 999,
        "life_sleep_idle_minutes": 999,
        "life_auto_sleep": False,
        "life_reflect_cooldown_minutes": 60,
        "life_max_active_intentions": 3,
        "life_daily_token_budget": 300000,
        "life_daily_cost_budget": 2.0,
        "life_quiet_hours": "",
        "brain_community_enabled": False,
    }
    base.update(over)
    return base


def _manifest():
    bk.save_json(bk.BRAIN_DIR / "manifest.json", {"brain_id": "testbrain", "fingerprint": "x"})


def test_timeline_append_and_read(tmp_path):
    from life import timeline
    ev1 = timeline.append("wake", "醒来", data={"a": 1})
    ev2 = timeline.append("thought", "想了一下")
    assert ev1["epoch"] == 1 and ev1["kind"] == "wake"
    assert [x["kind"] for x in timeline.tail(2)] == ["wake", "thought"]
    assert timeline.last("thought")["id"] == ev2["id"]
    assert timeline.summary(7)["counts"]["wake"] == 1
    # 坏行不阻塞读取
    with timeline.path().open("a", encoding="utf-8") as f:
        f.write("{bad json}\n")
    assert len(timeline.tail(10)) == 2


def test_state_lease_and_epoch(tmp_path, monkeypatch):
    from life import state, timeline
    state.save({"state": "awake", "epoch": 2})
    assert state.load()["state"] == "awake"
    ok, info = state.lease_acquire("test")
    assert ok and info["pid"] > 0
    assert state.is_owner()
    # 别的活进程持有时拒绝
    state.lease_release()
    state.lease_path().write_text(json.dumps({
        "pid": 999999, "host": "x", "beat_at": 9e18, "token": "t",
    }), encoding="utf-8")
    ok2, _ = state.lease_acquire("other", stale_seconds=90)
    assert ok2 is False
    # 过期租约可接管
    state.lease_path().write_text(json.dumps({
        "pid": 999999, "host": "x", "beat_at": 0, "token": "t",
    }), encoding="utf-8")
    ok3, info3 = state.lease_acquire("takeover", stale_seconds=90)
    assert ok3 and info3["pid"] > 0
    assert state.bump_epoch("test") == 3
    assert state.load()["epoch"] == 3
    assert timeline.last("epoch")["data"]["to"] == 3
    state.lease_release()


def test_drives_scores(tmp_path, monkeypatch):
    from life import drives
    _manifest()
    bk.add_goal("完成生命循环", source="user")
    bk.record_decision("继续观察", reason="测试")
    bk.save_json(bk.BRAIN_DIR / "self_model.json", {"source": "template", "unknowns": ["a", "b"]})
    bk.save_json(bk.MERGE_CONFLICT_FILE, {"conflicts": [{"id": "c1"}]})
    snap = drives.compute(force=True)
    assert set(snap["drives"]) == {"curiosity", "maintenance", "connection", "mastery", "coherence"}
    assert all(0 <= v <= 100 for v in snap["drives"].values())
    assert snap["top"] in snap["drives"]
    assert snap["drives"]["coherence"] > 0


def test_intentions_cap_and_outcome(tmp_path, monkeypatch):
    from life import intentions
    monkeypatch.setattr(intentions, "_max_active", lambda: 2)
    ok, it, _ = intentions.propose("第一件事", drive="curiosity", why="测试", expected="完成")
    assert ok and it["status"] == "proposed"
    assert not intentions.propose("第一件事", drive="curiosity")[0]  # 去重
    assert intentions.propose("第二件事", drive="maintenance")[0]
    ok3, _, why = intentions.propose("第三件事", drive="mastery")
    assert not ok3 and "上限" in why
    assert intentions.start(it["id"])
    assert intentions.complete(it["id"], "做完了")
    assert intentions.get(it["id"])["status"] == "done"
    assert intentions.counts()["done"] == 1


def test_metabolism_budget_and_fatigue(tmp_path, monkeypatch):
    import deepseek_client as dc
    from life import metabolism
    monkeypatch.setattr(dc, "STATS_FILE", str(tmp_path / "stats.json"))
    monkeypatch.setattr(metabolism, "_cfg", lambda: _cfg(life_daily_token_budget=100, life_daily_cost_budget=1.0))
    before = metabolism.snapshot()
    assert before["can_think"] is True
    metabolism.charge({"prompt": 80, "completion": 40, "cache_hit": 0, "cache_miss": 80},
                      "deepseek-flash", reflection=True)
    after = metabolism.snapshot()
    assert after["used_tokens"] >= 120
    assert after["actions"] == 1 and after["reflections"] == 1
    assert after["fatigue"] > 0 and after["energy"] < 100
    assert after["can_think"] is False
    metabolism.recover(100)
    assert metabolism.snapshot()["energy"] == 100


def test_metabolism_scope_self_isolated_from_chat(tmp_path, monkeypatch):
    import deepseek_client as dc
    import stats
    from life import metabolism
    data_path = tmp_path / "stats.json"
    monkeypatch.setattr(dc, "STATS_FILE", str(data_path))
    stats.record_usage(str(data_path), "deepseek-flash",
                       {"prompt": 2_000_000, "completion": 100_000,
                        "cache_hit": 1_900_000, "cache_miss": 100_000})
    base = {"life_daily_token_budget": 1000, "life_daily_cost_budget": 1.0}
    self_snap = metabolism.snapshot({**base, "life_budget_scope": "self"})
    total_snap = metabolism.snapshot({**base, "life_budget_scope": "total"})
    assert self_snap["used_tokens"] == 0 and self_snap["can_think"] is True
    assert total_snap["used_tokens"] >= 200_000 and total_snap["can_think"] is False
    assert total_snap["self_tokens"] == 0


def test_intentions_noop_guard(tmp_path, monkeypatch):
    from life import intentions
    for i in range(2):
        ok, it, _ = intentions.propose(f"空转 {i}", kind="consolidate", drive="maintenance")
        assert ok
        intentions.start(it["id"])
        intentions.complete(it["id"], "整理完成：归档 0，合并 0，保留 0（无变化）")
    ok, _, why = intentions.propose("第三次整理", kind="consolidate", drive="maintenance")
    assert not ok and "无变化" in why
    ok_user, _, _ = intentions.propose("用户要求整理", kind="consolidate", source="user")
    assert ok_user


def test_loop_start_tick_activity_stop(tmp_path, monkeypatch):
    from life import loop, state
    _manifest()
    monkeypatch.setattr(loop, "_cfg", lambda: _cfg())
    r = loop.start("test")
    assert r["ok"] and state.is_owner()
    n0 = state.load()["tick_count"]
    loop.tick(force=True)
    assert state.load()["tick_count"] == n0 + 1
    state.save({"state": "sleeping"})
    loop.note_activity("chat")
    assert state.load()["state"] == "awake"
    loop.pause("test")
    assert state.load()["paused"] is True
    loop.resume("test")
    assert state.load()["paused"] is False
    loop.stop("test")
    assert not state.is_owner()
