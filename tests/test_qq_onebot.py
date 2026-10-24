"""QQ OneBot 接入：事件解析、回复切分、命令与路由。"""
import qq_onebot


def test_extract_text_segments_and_cq():
    ev = {"message": [
        {"type": "text", "data": {"text": "你好"}},
        {"type": "face", "data": {"id": "1"}},
        {"type": "text", "data": {"text": "呀"}},
    ]}
    assert qq_onebot.extract_text(ev) == "你好[表情]呀"
    assert qq_onebot.extract_text({"message": "hi[CQ:face,id=1] there"}) == "hi there"
    assert qq_onebot.extract_text({"raw_message": "raw[CQ:at,qq=1]"}) == "raw"


def test_extract_images_and_voice():
    ev = {"message": [
        {"type": "image", "data": {"url": "https://example.com/a.jpg", "file": "a.jpg"}},
        {"type": "record", "data": {"url": "https://example.com/a.amr"}},
    ]}
    assert qq_onebot.extract_images(ev) == ["https://example.com/a.jpg"]
    assert qq_onebot.extract_voice(ev) == ["https://example.com/a.amr"]
    assert qq_onebot.extract_images({"message": "plain"}) == []


def test_split_reply_respects_cap():
    text = "第一段\n\n" + "很长" * 200
    chunks = qq_onebot.split_reply(text, 100)
    assert chunks
    assert all(len(c) <= 100 for c in chunks)
    assert "第一段" in chunks[0]


def test_config_normalize():
    c = qq_onebot._normalize({
        "api_base": "127.0.0.1:3000",
        "listen_port": "99999",
        "allowed_users": "123, 456 abc",
        "max_reply_chars": 10,
        "rate_limit_per_minute": 999,
        "quiet_hours": "23:00-08:00",
    })
    assert c["api_base"] == "http://127.0.0.1:3000"
    assert c["listen_port"] == 65535
    assert c["allowed_users"] == ["123", "456"]
    assert c["max_reply_chars"] == 100
    assert c["rate_limit_per_minute"] == 120
    assert c["quiet_hours"] == "23:00-08:00"
    assert qq_onebot._normalize({"quiet_hours": "bad"})["quiet_hours"] == ""


def test_persona_command_and_state(tmp_path):
    import personas

    personas.init(str(tmp_path / "personas.json"), reset=True)
    qq_onebot.init(str(tmp_path / "qq.json"), str(tmp_path / "state.json"), reset=True)
    reply, handled = qq_onebot._command_reply("10001", "/角色 小鲸", qq_onebot.load_config())
    assert handled and "小鲸" in reply
    state = qq_onebot._load_state()
    entry = state["users"]["10001"]
    assert entry["persona_id"] == "p_xiaojing"
    assert entry["session_id"].startswith("qq_10001_")

    old_sid = entry["session_id"]
    reply, handled = qq_onebot._command_reply("10001", "/重置", qq_onebot.load_config())
    assert handled and "重新开始" in reply
    assert qq_onebot._load_state()["users"]["10001"]["session_id"] != old_sid


def test_qq_routes_registered():
    import api_server

    assert any(str(r[0]) == "/v1/qq" for r in api_server._GET_ROUTES)
    assert any(str(r[0]) == "/v1/qq" for r in api_server._POST_ROUTES)


def test_qq_api_save_and_status(tmp_path):
    import api_server
    import personas

    personas.init(str(tmp_path / "personas.json"), reset=True)
    qq_onebot.init(str(tmp_path / "qq.json"), str(tmp_path / "state.json"), reset=True)
    result, err = api_server._qq_action({
        "action": "save",
        "config": {
            "enabled": False,
            "listen_port": 18756,
            "default_persona_id": "p_xiaojing",
            "allowed_users": ["123456"],
        },
    })
    assert err is None
    assert result["status"]["listen_port"] == 18756
    assert result["status"]["allowed_users"] == ["123456"]
    assert result["status"]["has_token"] is False
    assert api_server._qq_status()["listen_port"] == 18756


def test_companion_reply_pipeline(tmp_path, monkeypatch):
    import api_server
    import config_utils
    import deepseek_client as dc
    import personas

    personas.init(str(tmp_path / "personas.json"), reset=True)
    monkeypatch.setattr(config_utils, "load_config", lambda: {
        "api_key": "k", "base_url": "http://x", "model": "m",
        "thinking": "low", "max_tokens": 100, "memory_enabled": True,
    })
    monkeypatch.setattr(api_server._Handler, "_load_session_messages",
                        lambda self, sid: None)
    monkeypatch.setattr(api_server._Handler, "_inject_system_messages",
                        lambda self, messages, cfg, pure, quiet: (messages, "MEM"))
    monkeypatch.setattr(api_server, "_compress_messages",
                        lambda messages, cfg, client, session_id=None, pure_chat=False: (messages, None))
    saved, harvested = [], []
    monkeypatch.setattr(api_server, "_save_session_detached",
                        lambda body: (saved.append(body), (body["id"], None))[1])
    monkeypatch.setattr(api_server, "_chat_harvest",
                        lambda *a, **k: harvested.append(k))
    captured = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def chat(self, messages, **kwargs):
            captured.update(kwargs)
            captured["messages"] = messages
            kwargs["on_content"]("你好呀")
            return True

    monkeypatch.setattr(dc, "DeepSeekClient", _FakeClient)
    monkeypatch.setattr(dc, "set_active_client", lambda c: None)

    reply = api_server._companion_reply("在吗", "qq_10001_1", "p_xiaojing",
                                        images=["C:/tmp/a.jpg"])
    assert reply == "你好呀"
    assert captured["pure_chat"] is True
    assert captured["tools_enabled"] is False
    assert captured["memory_text"] == "MEM"
    assert captured["messages"][-1]["images"] == ["C:/tmp/a.jpg"]
    assert saved and saved[0]["persona_id"] == "p_xiaojing"
    assert saved[0]["messages"][0]["content"] == "在吗"
    assert saved[0]["messages"][0]["images"] == ["C:/tmp/a.jpg"]
    assert harvested and harvested[0]["persona_id"] == "p_xiaojing"


def test_handle_event_routes_private_message(tmp_path, monkeypatch):
    import api_server
    import personas

    personas.init(str(tmp_path / "personas.json"), reset=True)
    qq_onebot.init(str(tmp_path / "qq.json"), str(tmp_path / "state.json"), reset=True)
    monkeypatch.setattr(qq_onebot, "_active_cfg",
                        qq_onebot._normalize({"enabled": True, "default_persona_id": "p_xiaojing"}))
    sent = []
    monkeypatch.setattr(qq_onebot, "send_private",
                        lambda uid, text, cfg=None: (sent.append((uid, text)), (True, ""))[1])
    monkeypatch.setattr(api_server, "_companion_reply",
                        lambda text, sid, pid, images=None: f"re:{text}:{pid}")
    qq_onebot._handle_event({
        "post_type": "message",
        "message_type": "private",
        "user_id": 10002,
        "message_id": 9,
        "message": [{"type": "text", "data": {"text": "在吗"}}],
    })
    assert sent == [("10002", "re:在吗:p_xiaojing")]
    entry = qq_onebot._load_state()["users"]["10002"]
    assert entry["persona_id"] == "p_xiaojing"
    assert entry["session_id"].startswith("qq_10002_")



