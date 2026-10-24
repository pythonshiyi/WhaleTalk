"""QQ 官方 Bot：配置、命令、发送体与路由。"""
import qq_official


def test_config_normalize():
    c = qq_official._normalize({
        "app_id": " 123 ",
        "api_base": "api.bot.qq.com",
        "allowed_users": "openid1, openid2",
        "max_reply_chars": 10,
        "rate_limit_per_minute": 999,
        "quiet_hours": "23:00-08:00",
    })
    assert c["app_id"] == "123"
    assert c["api_base"] == "https://api.bot.qq.com"
    assert c["allowed_users"] == ["openid1", "openid2"]
    assert c["max_reply_chars"] == 100
    assert c["rate_limit_per_minute"] == 120
    assert c["quiet_hours"] == "23:00-08:00"
    assert qq_official._normalize({"quiet_hours": "bad"})["quiet_hours"] == ""


def test_persona_command_and_state(tmp_path):
    import personas

    personas.init(str(tmp_path / "personas.json"), reset=True)
    qq_official.init(str(tmp_path / "qqo.json"), str(tmp_path / "state.json"), reset=True)
    reply, handled = qq_official._command_reply("openid-1", "/角色 小鲸", qq_official.load_config())
    assert handled and "小鲸" in reply
    entry = qq_official._load_state()["users"]["openid-1"]
    assert entry["persona_id"] == "p_xiaojing"
    assert entry["session_id"].startswith("qqo_openid-1_")


def test_send_c2c_builds_passive_reply(monkeypatch):
    cfg = qq_official._normalize({"app_id": "a", "client_secret": "s"})
    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "msg-out"}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update({"url": url, "headers": headers, "json": json})
        return _Resp()

    monkeypatch.setattr(qq_official, "_get_token", lambda cfg, force=False: "tok")
    monkeypatch.setattr(qq_official.httpx, "post", fake_post)
    ok, err = qq_official._send_c2c_sync(cfg, "openid-1", "你好", msg_id="m1", seq=2)
    assert ok and err == ""
    assert captured["url"].endswith("/v2/users/openid-1/messages")
    assert captured["headers"]["Authorization"] == "QQBot tok"
    assert captured["json"]["msg_id"] == "m1"
    assert captured["json"]["msg_seq"] == 2
    assert captured["json"]["msg_type"] == 0


def test_routes_and_api_save(tmp_path):
    import api_server
    import personas

    personas.init(str(tmp_path / "personas.json"), reset=True)
    qq_official.init(str(tmp_path / "qqo.json"), str(tmp_path / "state.json"), reset=True)
    assert any(str(r[0]) == "/v1/qq-official" for r in api_server._GET_ROUTES)
    assert any(str(r[0]) == "/v1/qq-official" for r in api_server._POST_ROUTES)

    result, err = api_server._qq_official_action({
        "action": "save",
        "config": {
            "enabled": False,
            "app_id": "1900000000",
            "api_base": "https://api.bot.qq.com",
            "default_persona_id": "p_xiaojing",
            "allowed_users": ["openid-1"],
        },
    })
    assert err is None
    assert result["status"]["app_id"] == "1900000000"
    assert result["status"]["allowed_users"] == ["openid-1"]
    assert result["status"]["has_secret"] is False
