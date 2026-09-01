# -*- coding: utf-8 -*-
"""P1-5 安全默认值回归测试。

验证：
- config_defaults 默认 full_auto=False（初始即对话模式 + 高危审批）
- permissions 默认 approval_actions 预填高危动作（run_command/删除/发信等）
- request_approval：blacklist 模式下清单内动作需审批、清单外放行
- FULL_AUTO=True（任务模式）时直接放行
- _config_reset 恢复默认后回到安全默认（不再强制 full_auto=True）
- 首次启动前端初始 mode=dialog（App.jsx 静态检查）
"""
import os
import sys
import types
from contextlib import contextmanager

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config_defaults
import permissions


# ── 默认值断言 ─────────────────────────────────────────────

def test_full_auto_default_is_false():
    """config_defaults：full_auto 默认 False（安全默认）。"""
    assert config_defaults.DEFAULT_CONFIG.get("full_auto") is False


def test_approval_actions_default_has_high_risk():
    """permissions：默认 approval_actions 预填高危动作。"""
    acts = permissions.DEFAULT_PERMISSIONS.get("approval_actions") or []
    for high_risk in ("run_command", "delete_file", "send_email", "run_python", "pip_install"):
        assert high_risk in acts, f"高危动作 {high_risk} 应默认在审批清单中"


def test_approval_actions_not_all_tools():
    """审批清单是精选高危子集，不应包含全部工具（否则太打扰）。"""
    acts = permissions.DEFAULT_PERMISSIONS.get("approval_actions") or []
    # 创作/只读类动作不应默认要求审批
    for low_risk in ("get_date", "get_weather", "search_local"):
        assert low_risk not in acts, f"{low_risk} 不应默认要求审批"


# ── request_approval 判定逻辑 ──────────────────────────────

@contextmanager
def _approval_ctx(tmp_path, approval_actions, full_auto=False, security_mode="blacklist"):
    """构造最小权限上下文（直接写模块全局，测后恢复）。"""
    old_data = permissions._data
    old_auto = permissions.FULL_AUTO
    data = {
        "version": 2,
        "security_mode": security_mode,
        "approval_actions": approval_actions,
        "approval_mode": "confirm",
        "approval_timeout": 60,
        "plan_confirm": False,
    }
    permissions._data = data
    permissions.FULL_AUTO = full_auto
    yield data
    permissions._data = old_data
    permissions.FULL_AUTO = old_auto


def test_blacklist_high_risk_requires_approval(tmp_path):
    """blacklist + 非完全智能：清单内动作应走审批回调。"""
    with _approval_ctx(tmp_path, ["run_command", "delete_file"]):
        calls = []
        permissions._approval_callback = lambda name, args: (calls.append(name), (True, ""))[1]
        try:
            ok, _ = permissions.request_approval("run_command", {"cmd": "rm -rf /"})
            assert ok is True
            assert calls == ["run_command"], "清单内动作必须调用审批回调"
        finally:
            permissions._approval_callback = None


def test_blacklist_low_risk_auto_pass(tmp_path):
    """blacklist + 非完全智能：清单外动作直接放行（不打扰）。"""
    with _approval_ctx(tmp_path, ["run_command"]):
        calls = []
        permissions._approval_callback = lambda name, args: (calls.append(name), (True, ""))[1]
        try:
            ok, _ = permissions.request_approval("get_date", {})
            assert ok is True
            assert calls == [], "清单外动作不应触发审批"
        finally:
            permissions._approval_callback = None


def test_full_auto_skips_approval(tmp_path):
    """任务模式（FULL_AUTO）：即使清单内动作也直接放行。"""
    with _approval_ctx(tmp_path, ["run_command"], full_auto=True):
        calls = []
        permissions._approval_callback = lambda name, args: (calls.append(name), (True, ""))[1]
        try:
            ok, _ = permissions.request_approval("run_command", {})
            assert ok is True
            assert calls == [], "FULL_AUTO 模式不应触发审批"
        finally:
            permissions._approval_callback = None


def test_approval_rejected_when_callback_denies(tmp_path):
    """审批回调拒绝 → 动作被拦下。"""
    with _approval_ctx(tmp_path, ["run_command"]):
        permissions._approval_callback = lambda name, args: (False, "用户拒绝")
        try:
            ok, reason = permissions.request_approval("run_command", {})
            assert ok is False
            assert "拒绝" in reason
        finally:
            permissions._approval_callback = None


# ── _config_reset 安全默认 ─────────────────────────────────

def test_config_reset_backs_to_safe_default(monkeypatch, tmp_path):
    """_config_reset 后 full_auto 回到安全默认（False），不再强制 True。"""
    import api_server
    import config_utils  # api_server 内为函数内 import，patch 模块对象即全局生效

    saved = {}

    def fake_load():
        return {"api_key": "", "inbound_token": "", "image_api_key": "",
                "active_dir": "", "full_auto": True}

    def fake_save(cfg):
        saved["cfg"] = cfg

    def fake_set_full_auto(v):
        saved["full_auto_synced"] = v

    monkeypatch.setattr(config_utils, "load_config", fake_load)
    monkeypatch.setattr(config_utils, "save_config", fake_save)
    monkeypatch.setattr(permissions, "set_full_auto", fake_set_full_auto)

    res = api_server._config_reset()
    assert res == {"ok": True}
    assert saved["cfg"]["full_auto"] is False, "恢复默认应回到安全默认 full_auto=False"
    assert saved["full_auto_synced"] is False, "权限模块 FULL_AUTO 应同步为 False"


# ── 前端初始模式静态检查 ───────────────────────────────────

def test_frontend_initial_mode_is_dialog():
    """App.jsx 初始 mode 应为 dialog（对齐后端安全默认）。"""
    app_path = os.path.join(PROJECT_ROOT, "webui", "src", "App.jsx")
    with open(app_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert 'useState("dialog")' in src, "App.jsx 初始 mode 应为 dialog"
    assert 'useState("task")' not in src, "App.jsx 不应再以 task 为初始 mode"


def test_first_run_shows_safety_notice():
    """首次启动向导应包含安全默认说明（P1-5 确认引导）。"""
    frp = os.path.join(PROJECT_ROOT, "webui", "src", "components", "FirstRunPage.jsx")
    with open(frp, "r", encoding="utf-8") as f:
        src = f.read()
    assert "安全默认已启用" in src, "首次启动向导应有安全默认说明"
    assert "高危操作" in src, "说明应提及高危操作需确认"
