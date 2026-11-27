"""直播角色 v2：房间状态 / 注意力 / 导演决策 / 模拟器 / 舞台。

这些用例不联网：演员层被打桩，验证的是「什么时候说、对谁说、什么时候不说」。
"""
import time


import live_engine
from live import attention, director as director_mod, safety, simulator, stage
from live.events import EV_DANMAKU, EV_LIKE, LiveEvent
from live.room import Room


def _room(energy: float = 70.0) -> Room:
    r = Room(None).load()
    r.data()["energy"] = float(energy)
    return r


def _director(monkeypatch, energy: float = 70.0):
    r = _room(energy)
    d = director_mod.Director(room=r)
    calls = []

    def fake_reply(ev, room, persona_id=""):
        calls.append(("reply", ev.text))
        room.add_line("回：" + (ev.text or "礼物"), "reply")
        return {"text": "回：" + (ev.text or "礼物"), "spoken": True}

    def fake_self(room, persona_id=""):
        calls.append(("self", ""))
        room.add_line("自言自语", "self_talk")
        return {"text": "自言自语", "spoken": True}

    monkeypatch.setattr(director_mod.actor, "reply_to", fake_reply)
    monkeypatch.setattr(director_mod.actor, "self_talk", fake_self)
    monkeypatch.setattr(director_mod, "_cfg", lambda: {
        "live_max_lines_per_minute": 6,
        "live_idle_speak_seconds": 25,
    })
    return d, calls, r


# ── 房间 ───────────────────────────────────────────────────

def test_room_tracks_visitors_and_relation():
    r = _room()
    r.touch_viewer("u1", "老王", now=1000)
    r.touch_viewer("u1", "老王", now=1010)
    ev = LiveEvent(EV_DANMAKU, "u1", "老王", "你好", ts=1020)
    assert "来过 2 次" in r.relation(ev)
    assert r.snapshot()["viewers"] == 1


def test_room_energy_decays_but_bounded():
    r = _room(70)
    r.data()["last_activity"] = time.time() - 50
    r.tick()
    assert 68.0 <= r.data()["energy"] <= 70.0
    assert r.mood() in ("还算有精神", "有点安静", "兴致很高", "有些倦了")


def test_room_load_keeps_relationships_but_clears_session(tmp_path):
    """观众关系跨场保留；事件/台词/限速时间戳是"现场"，不应跨场继承。"""
    import json
    p = tmp_path / "room.json"
    p.write_text(json.dumps({
        "version": 1, "day": time.strftime("%Y-%m-%d"), "energy": 80,
        "last_line_ts": time.time(), "last_activity": time.time(), "topic": "旧话题",
        "agenda": ["a"], "agenda_i": 1,
        "viewers": {"u1": {"name": "老王", "visits": 3, "first_seen": 0,
                           "last_seen": 0, "affinity": 2}},
        "lines": [{"text": "旧台词"}], "events": [{"kind": "danmaku"}],
        "seen": [{"uid": "u1", "sig": "x", "ts": 0}],
        "stats": {"danmaku": 2},
    }, ensure_ascii=False), encoding="utf-8")
    r = Room(str(p)).load()
    assert r.data()["lines"] == [] and r.data()["events"] == [] and r.data()["seen"] == []
    assert r.data()["last_line_ts"] == 0.0
    assert r.data()["viewers"]["u1"]["visits"] == 3      # 关系保留
    assert r.data()["topic"] == "旧话题"                  # 话题保留


def test_room_next_topic_rotates():
    r = _room()
    first = r.next_topic()
    second = r.next_topic()
    assert first and second and first != second
    assert r.snapshot()["topic"] == second


# ── 注意力 ─────────────────────────────────────────────────

def test_question_beats_plain_chatter():
    r = _room()
    now = time.time()
    q = LiveEvent(EV_DANMAKU, "u1", "小鱼", "主播今天吃什么呀？", ts=now)
    p = LiveEvent(EV_DANMAKU, "u2", "路人", "哈哈", ts=now)
    assert attention.score(q, r, now) > attention.score(p, r, now)


def test_like_and_enter_are_low_priority():
    r = _room()
    now = time.time()
    like = LiveEvent(EV_LIKE, "u1", "潜水员", ts=now)
    assert attention.score(like, r, now) < attention.MIN_REPLY_SCORE


def test_spam_is_silently_dropped():
    r = _room()
    now = 1000.0
    for _ in range(4):
        r.note_seen("u1", "刷屏", now=now)
    ev = LiveEvent(EV_DANMAKU, "u1", "键盘侠", "刷屏", ts=now)
    assert attention.score(ev, r, now) < 0


def test_first_occurrence_is_not_treated_as_repeat():
    """回归：submit 会把当前事件记进 seen，打分时不能把它自己算成"复读"。"""
    r = _room()
    ev = LiveEvent(EV_DANMAKU, "u1", "小鱼", "新人报道，主播在干嘛呀", ts=time.time())
    r.note_seen("u1", ev.text)          # 模拟 Director.submit 已经记过
    assert not r.repeated("u1", ev.text, before=ev.ts)
    assert attention.score(ev, r, ev.ts + 1) >= attention.MIN_REPLY_SCORE


def test_repeated_message_is_penalized():
    r = _room()
    r.note_seen("u1", "今天吃什么", now=1000)
    repeat = LiveEvent(EV_DANMAKU, "u1", "老王", "今天吃什么", ts=1010)
    other = LiveEvent(EV_DANMAKU, "u2", "小鱼", "今天吃什么", ts=1010)
    assert attention.score(repeat, r, 1010) < attention.score(other, r, 1010)


def test_stale_event_is_not_picked():
    r = _room()
    now = time.time()
    old = LiveEvent(EV_DANMAKU, "u1", "老王", "在吗？", ts=now - 60)
    assert attention.pick([old], r, now) is None


# ── 导演 ───────────────────────────────────────────────────

def test_director_replies_to_question(monkeypatch):
    d, calls, _ = _director(monkeypatch)
    d.submit(LiveEvent(EV_DANMAKU, "u1", "小鱼", "主播在干嘛呀？"))
    out = d.tick_once()
    assert out["action"] == "reply"
    assert calls and calls[0][0] == "reply"


def test_director_ignores_spam(monkeypatch):
    d, calls, _ = _director(monkeypatch)
    for _ in range(4):
        d.submit(LiveEvent(EV_DANMAKU, "u1", "键盘侠", "刷屏刷屏"))
    out = d.tick_once()
    assert out["action"] != "reply"
    assert not calls


def test_director_rate_limits_then_answers_held_question(monkeypatch):
    """限速不是丢问题：等窗口过去后，刚才那条值得回的应该补上。"""
    d, calls, _ = _director(monkeypatch)
    t0 = time.time()
    d.submit(LiveEvent(EV_DANMAKU, "u1", "小鱼", "第一问？", ts=t0))
    assert d.tick_once(t0)["action"] == "reply"
    d.submit(LiveEvent(EV_DANMAKU, "u2", "老王", "第二问？", ts=t0 + 1))
    out = d.tick_once(t0 + 1)
    assert out["action"] == "idle" and out.get("held") == 1
    later = d.tick_once(t0 + 12)
    assert later["action"] == "reply"
    assert len(calls) == 2


def test_director_self_talks_when_quiet(monkeypatch):
    d, calls, r = _director(monkeypatch)
    now = time.time()
    r.data()["last_activity"] = now - 100
    r.data()["last_line_ts"] = now - 100
    out = d.tick_once(now=now)
    assert out["action"] == "self_talk"
    assert calls and calls[0][0] == "self"


def test_director_stays_quiet_when_tired(monkeypatch):
    d, calls, r = _director(monkeypatch, energy=10)
    now = time.time()
    r.data()["last_activity"] = now - 100
    r.data()["last_line_ts"] = now - 100
    assert d.tick_once(now=now)["action"] == "idle"
    assert not calls


def test_director_status_shape(monkeypatch):
    d, _, _ = _director(monkeypatch)
    st = d.status()
    assert {"running", "persona_name", "energy", "mood", "viewers"} <= set(st)


# ── 模拟器 ─────────────────────────────────────────────────

def test_simulator_script_flow(monkeypatch):
    d, _, _ = _director(monkeypatch)
    sim = simulator.Simulator(d, speed=100, loop=False)
    ev1 = sim.step()
    assert ev1 is not None and ev1.kind == "enter"
    ev2 = sim.step()
    assert ev2 is not None and ev2.kind == "danmaku" and "新人报道" in ev2.text


def test_simulator_wait_item_is_skipped(monkeypatch):
    d, _, _ = _director(monkeypatch)
    sim = simulator.Simulator(d, speed=100, loop=False)
    kinds = []
    for _ in range(9):          # 第 9 项是 wait
        ev = sim.step()
        kinds.append(ev.kind if ev else None)
    assert kinds[-1] is None


# ── 舞台 / 安全 ────────────────────────────────────────────

def test_stage_audio_only_attaches_to_current_line():
    stage.reset()
    seq = stage.publish("你好")
    assert stage.attach_audio(seq, "/v1/tts/audio/a.mp3")
    assert stage.latest()["audio"].endswith(".mp3")
    stage.publish("新的一句")
    assert not stage.attach_audio(seq, "/v1/tts/audio/b.mp3")


def test_clean_line_strips_stage_directions():
    assert safety.clean_line("（微笑）今天好呀") == "今天好呀"
    assert safety.clean_line("*挥手* 晚上好") == "晚上好"


def test_facade_sim_step_wiring(monkeypatch):
    """门面接线：sim_step 应该走到导演决策（演员打桩）。"""
    monkeypatch.setattr(director_mod.actor, "reply_to",
                        lambda ev, room, persona_id="": {"text": "回", "spoken": True})
    monkeypatch.setattr(director_mod.actor, "self_talk",
                        lambda room, persona_id="": {"text": "自", "spoken": True})
    live_engine.init(state_path=None, reset=True)
    try:
        first = live_engine.sim_step()
        assert first["event"]["kind"] == "enter"
        second = live_engine.sim_step()
        assert second["event"]["kind"] == "danmaku"
        assert second["tick"]["action"] in ("reply", "idle")
    finally:
        live_engine.sim_stop()
