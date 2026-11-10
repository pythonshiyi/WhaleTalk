"""直播生存状态引擎：数值衰减、打赏、跨日重置、台词提示词。"""
import time

import live_state


def _fresh(**cfg):
    st = live_state.LiveState(cfg)
    st.STATE_FILE = None
    live_state.STATE_FILE = None
    st.load()
    return st


def test_initial_state_is_starving():
    st = _fresh()
    snap = st.snapshot()
    assert snap.mood == "starving"
    assert snap.filled == 0.0
    assert snap.today_yuan == 0.0
    assert snap.remaining_yuan == 20.0


def test_feed_increases_filled_and_yuan():
    st = _fresh(daily_quota=20.0)
    r = st.feed("张三", doucoin=50, gift_name="小心心", count=1)   # 50抖币 = 5元
    assert r["after"]["today_yuan"] == 5.0
    assert r["after"]["today_doucoin"] == 50.0
    assert r["after"]["filled"] > 0.2
    assert r["delta"]["yuan"] == 5.0
    assert r["record"]["who"] == "张三"
    assert r["record"]["gift"] == "小心心"


def test_feed_multiple_count():
    st = _fresh(daily_quota=20.0)
    r = st.feed("李四", doucoin=10, gift_name="玫瑰", count=5)   # 10*5=50抖币=5元
    assert r["after"]["today_yuan"] == 5.0
    assert r["after"]["today_doucoin"] == 50.0
    assert r["record"]["count"] == 5


def test_reaching_full_is_detected():
    st = _fresh(daily_quota=10.0)
    r = st.feed("土豪", doucoin=1000, gift_name="火箭", count=1)  # 1000抖币=100元 > 额度
    assert r["after"]["filled"] == 1.0
    assert r["after"]["mood"] == "full"
    assert r["just_full"] is True
    assert r["after"]["remaining_yuan"] == 0.0


def test_mood_transitions():
    st = _fresh(daily_quota=100.0)
    assert st.snapshot().mood == "starving"
    st.feed("a", doucoin=300)     # 30元 -> 30%
    assert st.snapshot().mood == "hungry"
    st.feed("b", doucoin=400)     # 累计 70%
    assert st.snapshot().mood == "normal"
    st.feed("c", doucoin=300)     # 累计 100%
    assert st.snapshot().mood == "full"


def test_hunger_decay_over_time():
    """时间流逝应该让角色变饿（饱腹度从打赏到达时刻起算）。"""
    st = _fresh(daily_quota=20.0, hunger_hours=1.0)
    st.feed("x", doucoin=200)     # 20元 = 满
    assert st.snapshot().filled == 1.0
    # 手动把「最后一次结算」往前拨 30 分钟 -> 应衰减一半
    st._data["satiety_at"] = time.time() - 1800
    filled = st.snapshot().filled
    assert 0.4 < filled < 0.6, filled


def test_late_day_gift_can_still_fill():
    """开播 8 小时后的打赏仍应能喂饱。

    回归：旧实现把衰减基准挂在「当日重置时刻」上，开播超过 hunger_hours 后
    衰减量 ≥ 1 个额度，观众打赏满额度也只会显示 starving（核心玩法失效）。
    """
    st = _fresh(daily_quota=20.0, hunger_hours=6.0)
    st._data["started_at"] = time.time() - 8 * 3600
    r = st.feed("观众", doucoin=200)      # 200 抖币 = 20 元 = 100% 额度
    assert r["after"]["filled"] == 1.0
    assert r["after"]["mood"] == "full"
    assert r["just_full"] is True


def test_remaining_yuan_follows_decay():
    """「还差多少吃饱」必须与 filled 同源（回归：旧口径按今日累计收入）。"""
    st = _fresh(daily_quota=20.0, hunger_hours=1.0)
    st.feed("x", doucoin=200)                    # 满额度
    st._data["satiety_at"] = time.time() - 3600  # 一整小时 -> 饱腹度归零
    snap = st.snapshot()
    assert snap.filled == 0.0
    assert snap.remaining_yuan == 20.0


def test_reset_hour_from_instance_config_is_honored():
    """reset_hour 必须取实例配置，而不是模块级 DEFAULTS（回归：曾完全被忽略）。"""
    import datetime as _dt
    now = _dt.datetime.now().replace(hour=4, minute=0, second=0, microsecond=0)
    # 实例把重置改成 03:00，04:00 应仍属「今天」
    assert live_state._today_key(now, reset_hour=3) == now.strftime("%Y-%m-%d")
    # 不传实例配置时回落到出厂默认（05:00），04:00 属前一天
    assert live_state._today_key(now) == (now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    st = _fresh(reset_hour=3)
    assert int(st.cfg["reset_hour"]) == 3


def test_top_gifter_tracked():
    st = _fresh()
    st.feed("小a", doucoin=10)
    st.feed("大b", doucoin=500)
    st.feed("小a", doucoin=10)
    snap = st.snapshot()
    assert snap.top_gifter == "大b"
    assert snap.top_gifter_yuan == 50.0
    assert snap.unique_givers == 2
    assert snap.gift_count == 3


def test_net_income_is_discounted():
    """主播实收应显著低于观众实付（平台+公会抽成）。"""
    st = _fresh()
    snap_r = st.feed("x", doucoin=1000)["after"]   # 100元
    assert snap_r["today_yuan"] == 100.0
    assert snap_r["today_net_yuan"] < 100.0
    assert snap_r["today_net_yuan"] > 0


def test_recent_events_capped():
    st = _fresh()
    for i in range(50):
        st.feed(f"u{i}", doucoin=1)
    assert len(st.snapshot().recent) <= 40


def test_day_rollover_resets():
    st = _fresh(daily_quota=20.0)
    st.feed("x", doucoin=100)
    assert st.snapshot().today_yuan > 0
    st._data["day"] = "1999-01-01"     # 伪造旧日期
    snap = st.snapshot()
    assert snap.today_yuan == 0.0
    assert snap.day != "1999-01-01"


def test_speak_rate_limit():
    st = _fresh(max_lines_per_minute=2)
    assert st.can_speak() is True
    st.mark_spoken()
    assert st.can_speak() is True
    st.mark_spoken()
    assert st.can_speak() is False


def test_user_cooldown():
    st = _fresh(max_chat_reply_seconds=20)
    assert st.user_can_talk("u1") is True
    st.mark_user_talked("u1")
    assert st.user_can_talk("u1") is False
    assert st.user_can_talk("u2") is True


def test_prompts_contain_key_constraints():
    st = _fresh()
    r = st.feed("小明", doucoin=50, gift_name="玫瑰")
    snap = live_state.LiveSnapshot(**r["after"])
    feed_p = live_state.build_feed_prompt(snap, r["record"])
    assert "小明" in feed_p
    assert "不要索要打赏" in feed_p          # 平台红线
    assert "不要提到 AI" in feed_p
    assert "虚拟角色" in feed_p

    chat_p = live_state.build_chat_prompt(snap, "小红", "你好胖")
    assert "小红" in chat_p
    assert "你好胖" in chat_p
    assert "不要索要礼物" in chat_p


def test_mood_hint_all_moods():
    for m in ("starving", "hungry", "normal", "full"):
        assert live_state.mood_hint(m)
    assert live_state.mood_hint("unknown") == live_state.mood_hint("normal")


def test_snapshot_serializable():
    st = _fresh()
    d = st.snapshot().as_dict()
    import json
    json.dumps(d)   # 必须可 JSON 序列化（HUD 要发）
    assert "mood" in d and "filled" in d and "today_yuan" in d
