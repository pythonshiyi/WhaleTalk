"""直播台词层：合规过滤与降级路径。"""
import live_engine
import live_state


def _fresh_state(**cfg):
    live_state.STATE_FILE = None
    live_engine.init(state_path=None, reset=True)
    st = live_state.LiveState(cfg)
    st.STATE_FILE = None
    live_engine._state = st
    st.load()
    live_engine._latest.update({"seq": 0, "text": "", "audio": "", "at": 0.0})
    return st


def test_english_lines_are_not_false_positives():
    """回归：裸 AI + IGNORECASE 会把 said/again/main 判成自我暴露。"""
    assert live_engine._is_safe_line("I said thank you!")
    assert live_engine._is_safe_line("See you again tomorrow")
    assert live_engine._is_safe_line("That's my main point")


def test_explicit_self_exposure_is_blocked():
    assert not live_engine._is_safe_line("我是一个 AI 模型")
    assert not live_engine._is_safe_line("这是程序生成的台词")
    assert not live_engine._is_safe_line("我是AI，正在写代码")
    assert not live_engine._is_safe_line("作为 LLM 我不能这么说")


def test_begging_for_gifts_is_blocked():
    assert not live_engine._is_safe_line("再送一个火箭我就唱歌")
    assert not live_engine._is_safe_line("帮我冲榜好不好")


def test_clean_line_strips_quotes_and_prefix():
    assert live_engine._clean_line('"谢谢你呀"') == "谢谢你呀"
    assert live_engine._clean_line("台词：晚上好") == "晚上好"
    assert live_engine._clean_line("第一行\n第二行") == "第一行"


def test_deliver_publishes_line_without_waiting_for_tts(monkeypatch):
    """语音必须异步：接口返回时不应等 TTS 合成。"""
    st = _fresh_state(daily_quota=20.0)
    called = []

    def _slow_speak(text):
        called.append(text)
        return {"audio": "/v1/tts/audio/x.mp3", "engine": "fake"}

    monkeypatch.setattr(live_engine, "_speak", _slow_speak)
    out = live_engine._deliver("谢谢你", st.snapshot().mood, say=True)
    assert out["spoken"] is True
    assert out["audio_pending"] is True
    assert out["audio"] == ""
    # 发布出去的台词立即可被 HUD 读到（音频稍后回填）
    line = live_engine.latest_line()
    assert line["text"] == "谢谢你"
    assert line["seq"] >= 1


def test_audio_backfill_marks_audio_seq(monkeypatch):
    """异步语音回填要用 audio_seq 标记归属，HUD 靠它判重播。"""
    _fresh_state(daily_quota=20.0)
    monkeypatch.setattr(live_engine, "_speak",
                        lambda text: {"audio": "/v1/tts/audio/a.mp3", "engine": "fake"})
    seq = live_engine._publish_line("你好", "")
    live_engine._speak_into_published("你好", seq)
    line = live_engine.latest_line()
    assert line["audio"] == "/v1/tts/audio/a.mp3"
    assert line["audio_seq"] == seq


def test_audio_backfill_skips_stale_line(monkeypatch):
    """旧台词的语音晚到，不能覆盖正在显示的新台词。"""
    _fresh_state(daily_quota=20.0)
    monkeypatch.setattr(live_engine, "_speak",
                        lambda text: {"audio": "/v1/tts/audio/a.mp3", "engine": "fake"})
    old_seq = live_engine._publish_line("旧台词", "")
    live_engine._publish_line("新台词", "")
    live_engine._speak_into_published("旧台词", old_seq)
    line = live_engine.latest_line()
    assert line["text"] == "新台词"
    assert line["audio"] == ""


def test_filtered_line_reports_reason():
    st = _fresh_state(daily_quota=20.0)
    out = live_engine._deliver("我是一个 AI 模型", st.snapshot().mood, say=False)
    assert out["spoken"] is False
    assert out["reason"] == "filtered"
