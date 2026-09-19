r"""审查修复的回归门禁（Critical/High 项）。

覆盖：
- 信任内核：只读工具/无改动调用 **不得** 推进基线（防止 read_file 洗白未声明改动）
- 会话 append：合法重复回合不得被内容去重整轮丢弃
- 配置保存：DPAPI 解密失败（明文空）后的一次无关保存不得抹掉磁盘密文
- 路径归一：UNC / \\?\ 前缀不得绕过黑名单
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import pytest

import api_server  # noqa: E402
import config_utils  # noqa: E402
import permissions  # noqa: E402
import trust_kernel as tk  # noqa: E402

# ── 信任内核：只读/无改动调用不得推进基线 ────────────────────────────────

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


def test_readonly_touch_does_not_launder_undeclared_change(trust_sandbox):
    """已有未声明改动时，对内核文件的「无改动」声明结算必须丢弃、不推进基线。"""
    tk_, root = trust_sandbox
    (root / "permissions.py").write_text("# 未声明改动\n", encoding="utf-8")
    assert tk_.verify()["ok"] is False
    # 模拟只读工具：pre 声明 → 工具未改文件 → post 结算
    handle = tk_.declare(str(root / "permissions.py"), "read_file(path)", actor="tool")
    assert tk_.resolve_after_write(handle, ok=True) is False
    # 基线未被洗白，改动仍被检出
    assert tk_.verify()["ok"] is False
    assert [c["name"] for c in tk_.verify()["changed"]] == ["permissions.py"]


def test_real_write_still_commits(trust_sandbox):
    """真正写入（内容改变）时仍应推进基线（修复不能误伤正常声明通道）。"""
    tk_, root = trust_sandbox
    handle = tk_.declare(str(root / "permissions.py"), "write_file(path)", actor="tool")
    (root / "permissions.py").write_text("# v2 真改了\n", encoding="utf-8")
    assert tk_.resolve_after_write(handle, ok=True) is True
    assert tk_.verify()["ok"] is True


# ── 会话 append：重复回合不得整轮丢失 ────────────────────────────────────

class _H:
    _safe_sid = api_server._Handler._safe_sid


def test_session_append_keeps_repeated_turns(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(api_server, "_index_session_locked", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_save_session_index", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_index_session_file", lambda *a, **k: None)
    h = _H()
    first = [{"role": "user", "content": "继续"}, {"role": "assistant", "content": "好的"}]
    second = [{"role": "user", "content": "继续"}, {"role": "assistant", "content": "已继续"}]
    api_server._Handler._save_session(h, {"id": "s9", "messages": first})
    # 第二轮首条 user 内容与旧会话重复，但整轮不同 → 必须追加（旧实现会整轮丢弃）
    api_server._Handler._save_session(h, {"id": "s9", "messages": second, "append": True})
    loaded = api_server._Handler._load_session_messages(h, "s9")
    contents = [m["content"] for m in loaded["messages"]]
    assert contents == ["继续", "好的", "继续", "已继续"]


def test_session_append_dedups_identical_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(api_server, "_index_session_locked", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_save_session_index", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_index_session_file", lambda *a, **k: None)
    h = _H()
    turn = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    api_server._Handler._save_session(h, {"id": "s10", "messages": turn})
    api_server._Handler._save_session(h, {"id": "s10", "messages": turn, "append": True})
    loaded = api_server._Handler._load_session_messages(h, "s10")
    assert len(loaded["messages"]) == 2  # 完全相同的尾部被识别为重复，不追加


# ── 配置保存：不抹掉解密失败的密文 ──────────────────────────────────────

def test_save_config_keeps_disk_cipher_when_plaintext_empty(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"api_key": "dpapi:SECRET", "model": "m"}), encoding="utf-8")
    # 模拟「解密失败 → 明文为 ''」后的一次无关保存
    config_utils.save_config({"api_key": "", "model": "m2"}, str(cfg_path))
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved.get("api_key") == "dpapi:SECRET"  # 密文被保留，未被抹成空串
    assert saved.get("model") == "m2"


# ── 路径归一：UNC / \\?\ ────────────────────────────────────────────────

def test_resolve_rejects_unc_and_strips_extended_prefix():
    assert permissions.resolve("\\\\localhost\\C$\\Windows") is None
    assert permissions.resolve("\\\\server\\share\\x") is None
    assert permissions.resolve("//server/share/x") is None      # 正斜杠 UNC 也不得绕过
    assert permissions.resolve("//localhost/C$/Windows") is None
    stripped = permissions.resolve("\\\\?\\C:\\Windows")
    assert stripped is not None and not stripped.startswith("\\\\")


# ── 快照 id 净化 / 配置加密失败 fail-closed ──────────────────────────────

def test_restore_snapshot_rejects_traversal_id(tmp_path, monkeypatch):
    import snapshot
    monkeypatch.setattr(snapshot, "UNDO_DIR", str(tmp_path))
    for bad in ("../evil", "a/b", "a\\b", "C:\\Windows", "..", ""):
        ok, _msg = snapshot.restore_snapshot(bad)
        assert ok is False, f"非法快照 id 应拒绝：{bad!r}"
    # 合法条目名通过 id 校验，只是不存在
    ok2, msg2 = snapshot.restore_snapshot("20240101-000000_write_ab12")
    assert ok2 is False and "不存在" in msg2


def test_save_config_aborts_when_encrypt_fails_without_disk_cipher(tmp_path, monkeypatch):
    """加密失败且无磁盘密文：不得写明文、不得静默删除密钥，直接中止保存。"""
    import crypto

    def _boom(_v):
        raise crypto.CryptError("boom")
    monkeypatch.setattr(crypto, "encrypt", _boom)
    cfg_path = tmp_path / "config.json"
    r = config_utils.save_config({"api_key": "PLAIN", "model": "m"}, str(cfg_path))
    assert r is False
    assert not cfg_path.exists(), "加密失败时不应写盘（避免明文/丢密钥）"
