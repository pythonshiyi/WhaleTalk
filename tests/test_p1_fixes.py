"""P1 审查修复的回归测试。

覆盖：
1. 工具失败前缀统一：异常/参数包装串必须被判定为失败。
2. 会话索引落盘失败要留退化日志（不再静默）。
3. stop_server 收敛在跑的后台作业（set stop_event）并调用浏览器/进程收尾。
4. 前端停止后清空 rAF 累积缓冲（防串味）。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import api_server  # noqa: E402

# ── 1. 失败前缀统一 ────────────────────────────────────────────────

def test_fail_prefixes_cover_wrapped_tool_errors():
    import shared
    cases = [
        "工具执行失败: boom",
        "工具参数错误: x",
        "工具参数解析失败: y",
        "错误：x",
        "权限拒绝：x",
        "超时",
        "（用户停止）",
    ]
    for s in cases:
        assert s.startswith(shared.TOOL_RESULT_FAIL_PREFIXES), f"未识别为失败：{s}"


# ── 2. 会话索引落盘失败留痕 ────────────────────────────────────────

def test_session_index_save_failure_records_degrade(tmp_path, monkeypatch):
    import degrade
    degrade.reset()
    monkeypatch.setattr(api_server, "SESSION_INDEX_PATH", str(tmp_path / "idx.json"))

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(api_server.os, "replace", _boom)
    api_server._save_session_index()
    keys = [e["key"] for e in degrade.snapshot(limit=50)]
    assert any("session_index.save" in k for k in keys), "索引落盘失败必须留退化日志"


# ── 3. stop_server 收敛后台作业 ────────────────────────────────────

def test_stop_server_stops_chat_jobs(monkeypatch):
    import brain_api
    import deepseek_client as dc

    monkeypatch.setattr(brain_api, "brain_status", lambda **k: None)
    closed = []
    monkeypatch.setattr(dc, "close_browser", lambda: closed.append("browser"))
    monkeypatch.setattr(dc, "cleanup_all_processes", lambda: closed.append("procs"))

    job = api_server._ChatJob("st-stop-srv", "sid", "gw")
    with api_server._CHAT_JOBS_LOCK:
        api_server._CHAT_JOBS["st-stop-srv"] = job

    class _FakeSrv:
        def shutdown(self):
            closed.append("server")

        def server_close(self):
            closed.append("close")

    monkeypatch.setattr(api_server, "_SERVER", _FakeSrv())
    monkeypatch.setattr(api_server, "_THREAD", None)
    try:
        assert api_server.stop_server() is True
        assert job.stop_event.is_set(), "stop_server 必须收敛在跑作业"
        assert "browser" in closed, "stop_server 必须关闭共享浏览器（防孤儿 Chromium）"
        assert "procs" in closed, "stop_server 必须清理后台子进程"
    finally:
        with api_server._CHAT_JOBS_LOCK:
            api_server._CHAT_JOBS.pop("st-stop-srv", None)


# ── 4. 前端 rAF 缓冲复位（静态检查次数）────────────────────────────

def test_chatpage_resets_batch_buffer():
    src = (PROJECT_ROOT and open(
        os.path.join(PROJECT_ROOT, "webui", "src", "components", "ChatPage.jsx"), encoding="utf-8").read())
    reset = 'batchRef.current = { think: "", text: "", gen: "" };'
    # flushBatch 内 1 处 + 卸载清理 1 处（修复点）= 至少 2 处
    assert src.count(reset) >= 2, "停止/卸载清理必须复位 rAF 累积缓冲"


# ── 5. 请求级 full_auto 覆盖（去全局竞态）──────────────────────────

def test_request_approval_per_request_override():
    import permissions
    old_data = permissions._data
    old_auto = permissions.FULL_AUTO
    old_cb = permissions._approval_callback
    try:
        permissions._data = {
            "security_mode": "blacklist",
            "approval_actions": ["run_command"],
            "approval_mode": "confirm",
            "approval_timeout": 60,
        }
        permissions.FULL_AUTO = False
        permissions._approval_callback = None
        # 请求级 True：即便清单含该动作也放行（不再看全局）
        assert permissions.request_approval("run_command", {}, full_auto=True) == (True, "")
        # 请求级 False：清单内、无回调 → 拒绝
        ok, reason = permissions.request_approval("run_command", {}, full_auto=False)
        assert ok is False and "权限" in reason
    finally:
        permissions._data = old_data
        permissions.FULL_AUTO = old_auto
        permissions._approval_callback = old_cb


def test_approval_cb_uses_request_full_auto():
    """full_auto=True 请求快照时回调直接放行，不依赖全局 FULL_AUTO / 审批通道。"""
    import permissions
    old_auto = permissions.FULL_AUTO
    try:
        permissions.FULL_AUTO = False
        cb = api_server._make_approval_cb(lambda *a: True, None, True)
        assert cb("run_command", {}) == (True, "")
    finally:
        permissions.FULL_AUTO = old_auto


# ── 6. SSRF 信任白名单接线 ─────────────────────────────────────────

def test_ssrf_trusted_whitelist_exempts_hard_floor():
    import permissions
    import security
    old_data = permissions._data
    old_trusted = list(security.SSRF_TRUSTED)
    try:
        permissions._data = {
            "security_mode": "blacklist",
            "blocklist_enabled": True,
            "network": {"blocklist": [], "block_private": True, "allow_loopback": True},
        }
        security.set_ssrf_trusted(["10.0.0.5"])
        assert security._hard_floor_reason("10.0.0.5") == "", "显式信任的内网应豁免硬底线"
        assert security._hard_floor_reason("10.0.0.6") != "", "未信任的内网仍须拦截"
    finally:
        security.set_ssrf_trusted(old_trusted)
        permissions._data = old_data
