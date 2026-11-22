"""备用网关：只在显式开启且配置完整时构建；主客户端不会递归回退。"""
import config_utils
import deepseek_client as dc


def _cfg(**kw):
    base = {
        "fallback_enabled": True,
        "fallback_base_url": "https://backup.example.com/v1",
        "fallback_api_key": "sk-backup",
        "fallback_model": "backup-model",
    }
    base.update(kw)
    return base


def test_fallback_client_builds_from_config(monkeypatch):
    monkeypatch.setattr(config_utils, "load_config", lambda: _cfg())
    c = dc.DeepSeekClient("sk-main", base_url="https://api.deepseek.com", model="main")
    fb = c._fallback_client()
    assert fb is not None
    assert fb.model == "backup-model"
    assert fb.base_url.endswith("/v1")
    assert getattr(fb, "_is_fallback", False) is True
    assert c._fallback_client() is fb, "同一配置应复用备用客户端"


def test_fallback_disabled_or_incomplete(monkeypatch):
    c = dc.DeepSeekClient("sk-main", base_url="https://api.deepseek.com", model="main")
    monkeypatch.setattr(config_utils, "load_config", lambda: _cfg(fallback_enabled=False))
    assert c._fallback_client() is None
    c._fallback_cache = None
    monkeypatch.setattr(config_utils, "load_config", lambda: _cfg(fallback_api_key=""))
    assert c._fallback_client() is None


def test_fallback_client_does_not_recurse(monkeypatch):
    monkeypatch.setattr(config_utils, "load_config", lambda: _cfg())
    c = dc.DeepSeekClient("sk-main", base_url="https://api.deepseek.com", model="main")
    fb = c._fallback_client()
    assert fb._fallback_client() is None
