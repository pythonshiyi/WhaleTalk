"""POST /v1/config 的记忆/大脑分层键持久化回归（修复「设置页开关不落盘」）。

背景：memory_enabled / auto_memory / quiet_mode 此前**不在** `_p_v1_config` 的白名单里，
设置页点开关只改了前端乐观状态，后端静默丢弃 → 刷新即回弹。本测试锁定：
- 新键（memory_harvest_mode / brain_persona_enabled / brain_continuity_enabled /
  memory_inject_limit / memory_min_score）能写入并落盘；
- 枚举/数值做校验（memory_harvest_mode 只收 pending/auto/off；min_score 夹到 [0,1]）。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402
import config_utils  # noqa: E402
import security  # noqa: E402


class _H(api_server._Handler):
    """绕过 __init__ 的轻量 handler：只喂 body、抓响应。"""

    def __init__(self, body):
        self._body = body
        self._resp = None

    def _read_body(self, *a, **k):
        return self._body

    def _json(self, code, payload):
        self._resp = (code, payload)


def _run(monkeypatch, body):
    captured = {}
    monkeypatch.setattr(config_utils, "mutable_config", lambda *a, **k: {})
    monkeypatch.setattr(config_utils, "save_config", lambda cfg, *a, **k: captured.update(cfg) or True)
    monkeypatch.setattr(api_server, "_sync_gateway_key", lambda *a, **k: False)
    monkeypatch.setattr(api_server, "_init_dc_paths", lambda *a, **k: None)
    monkeypatch.setattr(security, "set_ssrf_trusted", lambda *a, **k: None)
    h = _H(body)
    h._p_v1_config()
    assert h._resp and h._resp[0] == 200, h._resp
    return captured


def test_memory_brain_keys_persist(monkeypatch):
    saved = _run(monkeypatch, {
        "memory_enabled": False,
        "auto_memory": False,
        "quiet_mode": True,
        "brain_persona_enabled": True,
        "brain_continuity_enabled": False,
        "memory_inject_limit": 3,
    })
    assert saved["memory_enabled"] is False
    assert saved["auto_memory"] is False
    assert saved["quiet_mode"] is True
    assert saved["brain_persona_enabled"] is True
    assert saved["brain_continuity_enabled"] is False
    assert saved["memory_inject_limit"] == 3


def test_harvest_mode_only_accepts_enum(monkeypatch):
    assert _run(monkeypatch, {"memory_harvest_mode": "off"})["memory_harvest_mode"] == "off"
    assert _run(monkeypatch, {"memory_harvest_mode": "AUTO"})["memory_harvest_mode"] == "auto"
    # 非法值被忽略（不写入）
    assert "memory_harvest_mode" not in _run(monkeypatch, {"memory_harvest_mode": "bogus"})


def test_min_score_clamped(monkeypatch):
    assert _run(monkeypatch, {"memory_min_score": 5})["memory_min_score"] == 1.0
    assert _run(monkeypatch, {"memory_min_score": -3})["memory_min_score"] == 0.0
