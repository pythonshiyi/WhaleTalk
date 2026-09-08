# -*- coding: utf-8 -*-
"""WhaleTalk MCP 出口（Model Context Protocol over stdio）——让外部 MCP 客户端接入。

外部工具/Agent（Claude Desktop、Cline、其他 MCP host）可把 WhaleTalk 当作一个
MCP server，调用其内部 AI 工具（147 个，如读文档/搜文件/生成 PPT 等）。

协议：MCP 基于 JSON-RPC 2.0，stdio 传输——每条消息一行 JSON，读到 EOF 退出。
支持方法：
  - initialize                      握手（返回协议版本 + server 信息）
  - notifications/initialized       忽略
  - tools/list                      列出全部可调用工具(schema)
  - tools/call                      执行工具 {name, arguments}
  - prompts/list / resources/list   返回空（本 server 主打 tools）
  - ping                             返回 {}

依赖：纯标准库（json/sys），无第三方 MCP SDK；协议为最小合规子集。

用法（作 MCP server 被外部拉起）：
  python mcp_server.py
外部 host 通过 stdio 与本进程通信。
也可手动测：
  echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python mcp_server.py
"""
import json
import os
import sys

# 工具名 → 白名单：默认开放全部注册工具（外部调用需真执行，谨慎）。可设环境变量
# WHALETALK_MCP_TOOLS="a,b,c" 限制；或 WHALETALK_MCP_ALLOW_READ=1 只暴露只读工具。
_PROTOCOL_VERSION = "2024-11-05"
_SERVER_INFO = {"name": "WhaleTalk", "version": "3.9.0"}


def _load_tools():
    """从 deepseek_client 加载工具 schema（name→function schema）。"""
    import deepseek_client as dc
    map = {}
    allow = [x.strip() for x in os.environ.get("WHALETALK_MCP_TOOLS", "").split(",") if x.strip()]
    read_only = os.environ.get("WHALETALK_MCP_ALLOW_READ") == "1"
    # 只读工具集合（依名称启发式；仅当 ALLOW_READ=1 时生效过滤）
    readish = ("read", "get", "list", "search", "peek", "query", "preview", "info", "status", "ls", "find")
    for t in dc.TOOLS:
        fn = t.get("function", {})
        name = fn.get("name", "")
        if not name or name not in dc.TOOL_CALL_MAP:
            continue
        if allow and name not in allow:
            continue
        if read_only and not any(k in name for k in readish):
            continue
        map[name] = fn
    return map, dc


def _handle_request(req, tools, dc):
    """处理单条 JSON-RPC 请求；返回响应 dict（或 None=通知无需响应）。"""
    method = req.get("method", "")
    rid = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}, "prompts": {}, "resources": {}},
            "serverInfo": _SERVER_INFO,
        }}
    if method in ("notifications/initialized",):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        items = []
        for name, fn in tools.items():
            items.append({
                "name": name,
                "description": fn.get("description", ""),
                "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": items}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        fn = tools.get(name)
        if not fn:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"工具不存在或未开放: {name}"}}
        try:
            # 复用 deepseek_client 的参数宽松解析 + 执行
            call = dc.TOOL_CALL_MAP.get(name)
            if not call:
                return {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32602, "message": f"工具 {name} 无法调用"}}
            result = call(**dict(args))
            txt = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": txt}], "isError": False}}
        except TypeError as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"参数错误: {e}"}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": f"执行失败: {e}"}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"prompts": []}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": []}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": []}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"未知方法: {method}"}}


def main():
    tools, dc = _load_tools()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            # 非 JSON 行（如日志混入）忽略
            continue
        try:
            resp = _handle_request(req, tools, dc)
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32700, "message": str(e)}}
        if resp is None:
            continue
        try:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass


if __name__ == "__main__":
    main()
