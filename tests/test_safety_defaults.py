# -*- coding: utf-8 -*-
"""默认自由 + 黑名单主导回归测试。

设计理念（用户约定）：
- 默认任务模式 = 无限权限（full_auto=True）：全工具、零审批，法无禁止皆可为
- 黑名单为唯一限制来源（默认空 = 0 限制），带 blocklist_enabled 一键总开关
- approval_actions 默认空（零审批）；用户需要时自行添加（额外限制可选）

验证：
- config_defaults 默认 full_auto=True（默认任务模式 / 无限权限）
- permissions 默认 approval_actions 为空（零审批，黑名单主导）
- permissions 默认 blocklist_enabled=True（黑名单机制生效；黑名单空 = 0 限制）
- request_approval：blacklist 模式下用户配置的清单内动作需审批、清单外放行
- FULL_AUTO=True（任务模式）时直接放行
- blocklist_enabled=False（一键全放行）时 check_shell/check_filesystem/check_network_host 跳过黑名单
- _config_reset 恢复默认后回到默认任务模式（full_auto=True）
- 首次启动前端初始 mode=task，向导含「法无禁止皆可为」说明
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config_defaults
import permissions


# ── 默认值断言（默认自由 / 黑名单主导）──────────────────

def test_full_auto_default_is_true():
    """config_defaults：full_auto 默认 True（默认任务模式 / 无限权限）。"""
    assert config_defaults.DEFAULT_CONFIG.get("full_auto") is True


def test_approval_actions_default_empty():
    """permissions：默认 approval_actions 为空（零审批，黑名单主导）。"""
    assert (permissions.DEFAULT_PERMISSIONS.get("approval_actions") or []) == []


def test_blocklist_default_enabled():
    """permissions：默认 blocklist_enabled=True（黑名单机制生效；黑名单空 = 0 限制）。"""
    assert permissions.DEFAULT_PERMISSIONS.get("blocklist_enabled") is True


# ── request_approval 判定逻辑（机制保留：清单由用户配置）──

@pytest.fixture
def perm_ctx(monkeypatch):
    """构造最小权限上下文（直接写模块全局，测后恢复）。"""
    old_data = permissions._data
    old_auto = permissions.FULL_AUTO
    old_cb = permissions._approval_callback

    def make(approval_actions, full_auto=False, blocklist_enabled=True):
        data = {
            "version": 2,
            "security_mode": "blacklist",
            "blocklist_enabled": blocklist_enabled,
            "filesystem": {"blocked_dirs": [], "allowed_dirs": [], "allow_write": True},
            "shell": {"blocklist": [], "whitelist": [], "allow_run_command": True},
            "network": {"blocklist": []},
            "approval_actions": approval_actions,
            "approval_mode": "confirm",
            "approval_timeout": 60,
            "plan_confirm": False,
        }
        permissions._data = data
        permissions.FULL_AUTO = full_auto
        return data

    yield make
    permissions._data = old_data
    permissions.FULL_AUTO = old_auto
    permissions._approval_callback = old_cb


def test_blacklist_configured_action_requires_approval(perm_ctx):
    """blacklist + 非完全智能：用户配置的清单内动作应走审批回调。"""
    perm_ctx(["run_command", "delete_file"])
    calls = []
    permissions._approval_callback = lambda name, args: (calls.append(name), (True, ""))[1]
    ok, _ = permissions.request_approval("run_command", {"cmd": "rm -rf /"})
    assert ok is True
    assert calls == ["run_command"], "清单内动作必须调用审批回调"


def test_blacklist_low_risk_auto_pass(perm_ctx):
    """blacklist + 非完全智能：清单外动作直接放行（不打扰）。"""
    perm_ctx(["run_command"])
    calls = []
    permissions._approval_callback = lambda name, args: (calls.append(name), (True, ""))[1]
    ok, _ = permissions.request_approval("get_date", {})
    assert ok is True
    assert calls == [], "清单外动作不应触发审批"


def test_full_auto_skips_approval(perm_ctx):
    """任务模式（FULL_AUTO）：即使清单内动作也直接放行。"""
    perm_ctx(["run_command"], full_auto=True)
    calls = []
    permissions._approval_callback = lambda name, args: (calls.append(name), (True, ""))[1]
    ok, _ = permissions.request_approval("run_command", {})
    assert ok is True
    assert calls == [], "FULL_AUTO 模式不应触发审批"


def test_approval_rejected_when_callback_denies(perm_ctx):
    """审批回调拒绝 → 动作被拦下。"""
    perm_ctx(["run_command"])
    permissions._approval_callback = lambda name, args: (False, "用户拒绝")
    ok, reason = permissions.request_approval("run_command", {})
    assert ok is False
    assert "拒绝" in reason


# ── 黑名单一键开关（blocklist_enabled=False = 全放行）────

def test_blocklist_switch_gates_shell(perm_ctx):
    """blocklist_enabled=False：命令黑名单不生效（一键全放行）。"""
    data = perm_ctx([], blocklist_enabled=False)
    data["shell"]["blocklist"] = ["format", "shutdown"]
    ok, reason, _ = permissions.check_shell("format c:")
    assert ok is True, f"开关关闭时应放行被黑名单命令：{reason}"
    # 开关打开时同一命令被拦
    data["blocklist_enabled"] = True
    ok, reason, _ = permissions.check_shell("format c:")
    assert ok is False and "黑名单" in reason


def test_blocklist_switch_gates_filesystem(perm_ctx):
    """blocklist_enabled=False：路径黑名单不生效（一键全放行）。"""
    data = perm_ctx([], blocklist_enabled=False)
    data["filesystem"]["blocked_dirs"] = ["C:/Windows"]
    ok, reason = permissions.check_filesystem("C:/Windows/system32/drivers/etc/hosts")
    assert ok is True, f"开关关闭时应放行黑名单路径：{reason}"
    data["blocklist_enabled"] = True
    ok, reason = permissions.check_filesystem("C:/Windows/system32/drivers/etc/hosts")
    assert ok is False and "黑名单" in reason


def test_blocklist_switch_gates_network(perm_ctx):
    """blocklist_enabled=False：网络黑名单不生效（一键全放行）。"""
    data = perm_ctx([], blocklist_enabled=False)
    data["network"]["blocklist"] = ["evil.example.com"]
    ok, reason = permissions.check_network_host("evil.example.com")
    assert ok is True, f"开关关闭时应放行黑名单主机：{reason}"
    data["blocklist_enabled"] = True
    ok, reason = permissions.check_network_host("evil.example.com")
    assert ok is False and "黑名单" in reason


# ── _config_reset 默认任务模式 ──────────────────────────

def test_config_reset_backs_to_default_full_auto(monkeypatch, tmp_path):
    """_config_reset 后 full_auto 回到默认任务模式（True）。"""
    import api_server
    import config_utils  # api_server 内为函数内 import，patch 模块对象即全局生效

    saved = {}

    def fake_load():
        return {"api_key": "", "inbound_token": "", "image_api_key": "",
                "active_dir": "", "full_auto": False}

    def fake_save(cfg):
        saved["cfg"] = cfg

    def fake_set_full_auto(v):
        saved["full_auto_synced"] = v

    monkeypatch.setattr(config_utils, "load_config", fake_load)
    monkeypatch.setattr(config_utils, "save_config", fake_save)
    monkeypatch.setattr(permissions, "set_full_auto", fake_set_full_auto)

    res = api_server._config_reset()
    assert res == {"ok": True}
    assert saved["cfg"]["full_auto"] is True, "恢复默认应回到默认任务模式 full_auto=True"
    assert saved["full_auto_synced"] is True, "权限模块 FULL_AUTO 应同步为 True"


# ── 前端初始模式 / 向导说明静态检查 ─────────────────────

def test_frontend_initial_mode_is_task():
    """App.jsx 初始 mode 应为 task（默认任务模式 / 无限权限）。"""
    app_path = os.path.join(PROJECT_ROOT, "webui", "src", "App.jsx")
    with open(app_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert 'useState("task")' in src, "App.jsx 初始 mode 应为 task"


def test_first_run_shows_freedom_notice():
    """首次启动向导应包含「法无禁止皆可为」默认自由说明。"""
    frp = os.path.join(PROJECT_ROOT, "webui", "src", "components", "FirstRunPage.jsx")
    with open(frp, "r", encoding="utf-8") as f:
        src = f.read()
    assert "法无禁止皆可为" in src, "首次启动向导应有默认自由说明"
    assert "黑名单" in src, "说明应提及黑名单为限制来源"
