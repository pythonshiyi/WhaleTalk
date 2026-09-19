"""P0 审查修复的回归测试。

覆盖（每项对应代码里修复的一个真实缺陷）：
1. fetch_blocked 黑名单模式不得绕过 SSRF 硬底线（内网/链路本地必须拦）。
2. 插件市场安装 fail-closed：既无 sha256 也无签名 → 拒绝；sha256 不符 → 拒绝。
3. 批量删除会话：全部失败时不再谎报成功。
4. 会话落盘走 atomic_json_write；写失败要显式报错，不静默。
5. 流式作业链路触发自动记忆提炼（此前只在非流式路径调用 → auto_memory 失效）。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import api_server  # noqa: E402
import fetch_blocked  # noqa: E402
import permissions  # noqa: E402

# ── 1. fetch_blocked SSRF 硬底线 ─────────────────────────────────────

@pytest.fixture
def blacklist_ctx(monkeypatch):
    """默认黑名单模式上下文：用户网络黑名单为空（出厂仅一条），硬底线应生效。"""
    old = permissions._data
    permissions._data = {
        "version": 2,
        "security_mode": "blacklist",
        "blocklist_enabled": True,
        "filesystem": {"blocked_dirs": []},
        "shell": {"blocklist": []},
        "network": {"blocklist": [], "block_private": True, "allow_loopback": True},
    }
    try:
        yield
    finally:
        permissions._data = old


def test_fetch_blocked_blocks_private_and_linklocal(blacklist_ctx):
    for host in ("10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254"):
        assert fetch_blocked._is_blocked_host(host) is True, \
            f"{host} 属内网/链路本地，必须被硬底线拦截"


def test_fetch_blocked_allows_loopback_and_public(blacklist_ctx):
    assert fetch_blocked._is_blocked_host("127.0.0.1") is False, "回环默认放行（本地开发）"
    assert fetch_blocked._is_blocked_host("8.8.8.8") is False, "公网地址应放行"


# ── 2. 插件市场安装 fail-closed ──────────────────────────────────────

def test_plugin_download_fail_closed_no_sha_no_sig(monkeypatch):
    monkeypatch.setattr(api_server, "_market_public_key", lambda: "")
    ok, err = api_server._verify_plugin_download(b"anything", {})
    assert ok is False and "拒绝" in err


def test_plugin_download_accepts_matching_sha256(monkeypatch):
    import hashlib
    monkeypatch.setattr(api_server, "_market_public_key", lambda: "")
    raw = b"plugin-bytes"
    entry = {"sha256": hashlib.sha256(raw).hexdigest()}
    ok, err = api_server._verify_plugin_download(raw, entry)
    assert ok is True and err == ""


def test_plugin_download_rejects_wrong_sha256(monkeypatch):
    monkeypatch.setattr(api_server, "_market_public_key", lambda: "")
    ok, err = api_server._verify_plugin_download(b"plugin-bytes", {"sha256": "0" * 64})
    assert ok is False and "SHA-256" in err


# ── 3. 批量删除会话：失败不再谎报成功 ────────────────────────────────

class _DeleteH:
    def __init__(self, ok):
        self.ok = ok

    def _delete_session(self, sid):
        return (True, None) if self.ok else (False, f"删除失败：{sid}")


def test_batch_delete_all_failed_reports_error():
    ok, removed, err = api_server._Handler._delete_sessions_batch(_DeleteH(False), ["a", "b"])
    assert ok is False and removed == 0 and err, "全部失败必须返回错误，不能显示成功"


def test_batch_delete_partial_reports_removed():
    class MixH:
        def _delete_session(self, sid):
            return (sid == "a", None if sid == "a" else "boom")

    ok, removed, err = api_server._Handler._delete_sessions_batch(MixH(), ["a", "b"])
    assert ok is True and removed == 1 and err is None


# ── 4. 会话落盘原子写：失败要显式报错 ────────────────────────────────

class _SessionH:
    _safe_sid = api_server._Handler._safe_sid


def test_save_session_reports_atomic_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(api_server, "_index_session_locked", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_save_session_index", lambda *a, **k: None)
    import persistence
    monkeypatch.setattr(persistence, "atomic_json_write", lambda *a, **k: False)
    sid, err = api_server._Handler._save_session(
        _SessionH(), {"id": "s-atom", "messages": [{"role": "user", "content": "x"}]})
    assert sid is None and err and "写入失败" in err


# ── 5. 流式作业触发自动记忆提炼 ──────────────────────────────────────

class _MemoClient:
    def chat(self, messages, **kwargs):
        cb = kwargs.get("on_content")
        if cb:
            cb("记住：我在用鲸语")
        out = kwargs.get("new_messages_out")
        if out is not None:
            out.append({"role": "assistant", "content": "记住：我在用鲸语"})
        return True


class _JobH(api_server._Handler):
    def __init__(self):
        pass


def _stub_pipeline(monkeypatch):
    monkeypatch.setattr(api_server._Handler, "_client_from_cfg",
                        lambda self, body: (_MemoClient(), {}))
    monkeypatch.setattr(api_server._Handler, "_budget_block", lambda self, cfg: None)
    monkeypatch.setattr(api_server._Handler, "_chat_kwargs", lambda self, body, cfg: {})
    monkeypatch.setattr(api_server._Handler, "_quiet_mode", lambda self, body, cfg: False)
    monkeypatch.setattr(api_server._Handler, "_inject_system_messages",
                        lambda self, m, cfg, pure, quiet: (m, ""))
    monkeypatch.setattr(api_server, "_compress_messages", lambda m, cfg, client: (m, None))
    monkeypatch.setattr(api_server, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_tool_bookkeeping", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_record_tasklog", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_clear_auto_checkpoint", lambda: None)
    monkeypatch.setattr(api_server, "_notify_completed", lambda ok=True: None)
    monkeypatch.setattr(api_server, "_set_current_task", lambda title: None)
    monkeypatch.setattr(api_server, "_LAST_TOOL_CHAIN", [])
    monkeypatch.setattr(api_server, "_save_job_turn", lambda *a, **k: None)


def test_stream_job_triggers_memory_harvest(monkeypatch):
    _stub_pipeline(monkeypatch)
    called = []
    monkeypatch.setattr(api_server, "_chat_harvest", lambda *a, **k: called.append(a))
    h = _JobH()
    job = api_server._ChatJob("st-mem", "sid-mem", "gw-mem")
    body = {"messages": [{"role": "user", "content": "hi"}]}
    api_server._Handler._run_chat_job_thread(
        h, job, body, [{"role": "user", "content": "hi"}])
    assert called, "流式作业正常结束后应触发自动记忆提炼（此前从未调用 → auto_memory 失效）"


# ── 6. _sanitize_messages：不得残留悬空 tool_calls（否则 API 400）─────

def _tc(i):
    return {"id": i, "type": "function", "function": {"name": "f", "arguments": "{}"}}


def test_sanitize_messages_drops_dangling_tool_calls():
    import deepseek_client as dc
    # assistant 声明 a → user → tool(a)：tool 错位，assistant 的 tool_calls 必须一并剔除
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [_tc("a")]},
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "a", "content": "r"},
    ]
    out = dc.DeepSeekClient._sanitize_messages(msgs)
    assert out == [{"role": "user", "content": "hi"}]
    assert not any(m.get("tool_calls") for m in out)


def test_sanitize_messages_keeps_valid_pair():
    import deepseek_client as dc
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [_tc("a")]},
        {"role": "tool", "tool_call_id": "a", "content": "r"},
    ]
    out = dc.DeepSeekClient._sanitize_messages(msgs)
    assert out[0].get("tool_calls"), "合法 assistant→tool 对应保留"
    assert out[-1]["role"] == "tool"


def test_sanitize_messages_partial_parallel_keeps_matched():
    import deepseek_client as dc
    # 并行两个调用，只回了一个 tool，另一个必须从 assistant 剔除
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [_tc("a"), _tc("b")]},
        {"role": "tool", "tool_call_id": "a", "content": "r"},
        {"role": "user", "content": "next"},
    ]
    out = dc.DeepSeekClient._sanitize_messages(msgs)
    a_msg = next(m for m in out if m.get("role") == "assistant")
    assert [t["id"] for t in a_msg["tool_calls"]] == ["a"]
