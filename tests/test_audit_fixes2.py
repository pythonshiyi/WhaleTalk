"""审查修复（第二批）回归门禁：shell 黑名单绕过、审计脱敏、信任内核撤销、会话索引防抖。"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import pytest

import api_server  # noqa: E402
import permissions  # noqa: E402
import trust_kernel as tk  # noqa: E402

# ── shell 黑名单：无空格分隔 / 引号 / 包装器 均不可绕过 ─────────────────

def _perm_data(blocklist):
    return {
        "security_mode": "blacklist",
        "blocklist_enabled": True,
        "filesystem": {"blocked_dirs": [], "allowed_dirs": [], "allow_write": True, "max_write_size": 0},
        "shell": {"blocklist": blocklist, "whitelist": [], "allow_run_command": True, "timeout": 120},
        "network": {"blocklist": []},
        "approval_actions": [],
    }


@pytest.mark.parametrize("cmd", [
    "powershell -c calc",
    'echo hi&powershell',          # 无空格分隔符（旧实现漏检）
    'echo hi&&powershell',
    'echo hi|powershell',
    '"powershell" -c calc',        # 引号包裹（旧实现漏检）
    "whoami | powershell",
    "cmd /c del x",                # 包装器内层命令
    'cmd /c "del x"',
    "cmd.exe /k del x",
    "start /B powershell",         # 启动器内层命令（旧实现漏检）
    "start del x",
    "call powershell -c calc",
    "cmd /c start del x",
])
def test_shell_blocklist_bypass_fixed(monkeypatch, cmd):
    monkeypatch.setattr(permissions, "_data", _perm_data(["powershell", "del"]))
    ok, _reason, _argv = permissions.check_shell(cmd)
    assert ok is False, f"应拦截：{cmd}"


def test_shell_blocklist_allows_normal(monkeypatch):
    monkeypatch.setattr(permissions, "_data", _perm_data(["powershell"]))
    ok, _reason, argv = permissions.check_shell("echo hello")
    assert ok is True and argv


# ── 审计：敏感工具结果不得明文落盘 ──────────────────────────────────────

def test_audit_masks_sensitive_tool_result(tmp_path, monkeypatch):
    monkeypatch.setattr(permissions, "AUDIT_ENABLED", True)
    monkeypatch.setattr(permissions, "AUDIT_LOG_DIR", str(tmp_path))
    permissions.tool_trace("secret_store", {"action": "get", "name": "k"}, "SUPERSECRET123", 0.1)
    permissions.tool_trace("read_file", {"path": "x"}, "ok-content", 0.1)
    permissions.tool_trace_flush(3.0)
    log = tmp_path / "tools.log"
    assert log.is_file()
    txt = log.read_text(encoding="utf-8")
    assert "SUPERSECRET123" not in txt          # 敏感结果已脱敏
    assert "ok-content" in txt                  # 普通工具结果照常记录


# ── 信任内核：可撤销一次已声明的改动 ────────────────────────────────────

@pytest.fixture
def trust_sandbox(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    for name in tk.PROTECTED:
        (root / name).write_text(f"# v1 {name}\n", encoding="utf-8")
    monkeypatch.setattr(tk, "PROJECT_DIR", str(root))
    monkeypatch.setattr(tk, "TRUST_DIR", str(root / "trust"))
    import tool_hooks
    tool_hooks.clear_kernel_cache()
    tk.init()
    yield tk, root


def test_undo_restores_declared_change(trust_sandbox):
    tk_, root = trust_sandbox
    original = (root / "permissions.py").read_text(encoding="utf-8")
    handle = tk_.declare(str(root / "permissions.py"), "write_file(path)", actor="tool")
    (root / "permissions.py").write_text("# v2 已声明改动\n", encoding="utf-8")
    assert tk_.resolve_after_write(handle, ok=True) is True
    assert tk_.verify()["ok"] is True
    ok, msg = tk_.undo("permissions.py")
    assert ok is True, msg
    assert (root / "permissions.py").read_text(encoding="utf-8") == original  # 回到改动前
    assert tk_.verify()["ok"] is True


def test_undo_without_record_fails_softly(trust_sandbox):
    tk_, _ = trust_sandbox
    ok, msg = tk_.undo("permissions.py")
    assert ok is False and "没有可撤销" in msg


# ── 会话索引：坏文件不再导致每次列表全量重建 ────────────────────────────

def test_corrupt_session_no_rebuild_loop(tmp_path, monkeypatch):
    sess = tmp_path / "sess"
    sess.mkdir()
    # 索引放在 SESSIONS_DIR 之外（与生产一致：DATA_DIR），否则写索引会改目录 mtime
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(sess))
    monkeypatch.setattr(api_server, "SESSION_INDEX_PATH", str(tmp_path / "idx.json"))
    monkeypatch.setattr(api_server, "_SESSIONS_INDEX", {})
    monkeypatch.setattr(api_server, "_SESSION_BAD_FILES", set())
    monkeypatch.setattr(api_server, "_session_dir_mtime", 0)
    (sess / "good.json").write_text(
        json.dumps({"id": "good", "name": "g", "messages": []}), encoding="utf-8")
    (sess / "bad.json").write_text("{ not json", encoding="utf-8")

    api_server._Handler._list_sessions(None)  # 首次：建立索引（坏文件计入 bad）
    assert {"bad.json"} == api_server._SESSION_BAD_FILES

    calls = {"n": 0}
    orig = api_server._rebuild_session_index

    def _counting():
        calls["n"] += 1
        return orig()

    monkeypatch.setattr(api_server, "_rebuild_session_index", _counting)
    api_server._Handler._list_sessions(None)  # 再次：不应触发重建
    assert calls["n"] == 0
