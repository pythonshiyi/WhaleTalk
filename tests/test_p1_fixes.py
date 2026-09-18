# -*- coding: utf-8 -*-
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
        os.path.join(PROJECT_ROOT, "webui", "src", "components", "ChatPage.jsx"),
        "r", encoding="utf-8").read())
    reset = 'batchRef.current = { think: "", text: "", gen: "" };'
    # flushBatch 内 1 处 + 卸载清理 1 处（修复点）= 至少 2 处
    assert src.count(reset) >= 2, "停止/卸载清理必须复位 rAF 累积缓冲"
