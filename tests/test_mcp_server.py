# -*- coding: utf-8 -*-
"""MCP 出口回归：WhaleTalk 作为 MCP stdio server 可被外部 host 拉起调用工具。"""
import json
import subprocess
import sys

import pytest


def _run_mcp(msgs, timeout=40):
    """向 mcp_server.py 子进程喂 JSON-RPC 行，收集响应。"""
    payload = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n"
    proc = subprocess.run(
        [sys.executable, "mcp_server.py"],
        input=payload, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout, cwd=None)
    out = []
    for ln in (proc.stdout or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out, proc


def test_mcp_initialize_and_tools():
    out, proc = _run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
    ])
    byid = {o.get("id"): o for o in out if o.get("id") is not None}
    assert byid[1]["result"]["serverInfo"]["name"] == "WhaleTalk"
    tools = byid[2]["result"]["tools"]
    assert len(tools) >= 140, f"应列出全量工具，实际 {len(tools)}"
    names = {t["name"] for t in tools}
    # 抽查核心工具
    for core in ("environment_info", "pptx_create", "docx_read", "find_images"):
        assert core in names, f"MCP 应暴露 {core}"
    assert byid[3]["result"] == {}


def test_mcp_tools_call_runs_tool():
    out, proc = _run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "environment_info", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "no_such_tool", "arguments": {}}},
    ])
    byid = {o.get("id"): o for o in out if o.get("id") is not None}
    res = byid[1]["result"]
    assert res["content"][0]["type"] == "text"
    assert "平台" in res["content"][0]["text"] or "Python" in res["content"][0]["text"]
    assert byid[2].get("error"), "未知工具应返回错误"
