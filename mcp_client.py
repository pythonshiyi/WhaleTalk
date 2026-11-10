"""WhaleTalk MCP 客户端（⑤a）：把外部 MCP server（stdio JSON-RPC 2.0）的工具并入本机。

与 mcp_server.py（把本机工具**暴露**给外部 host）相反，本模块让鲸语**消费**外部
MCP server。协议是最小可用子集：initialize → notifications/initialized → tools/list
/ tools/call，逐行 JSON（每条消息一行），不依赖任何 MCP SDK。

配置（config.json）：
    "mcp_servers": [
        {"name": "filesystem", "command": "npx",
         "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/work"], "env": {}}
    ]

也可用环境变量 `WHALETALK_MCP_SERVERS`（JSON 字符串）覆盖，便于免配置测试。

Python API：
    list_servers()                  → 配置中的 server 列表
    list_tools(server, timeout=20)  → [{name, description, inputSchema}]
    call_tool(server, tool, args)   → tools/call 结果（文本化）
    load_into_runtime(timeout=20)   → 把上面这些工具装成运行时能力包（启动时调用）

接线位置：`api_server.start_server()` 在装载完内置/用户能力包后，用后台线程调用
`load_into_runtime()`；每个外部工具变成 `p_mcp_<server>_<tool>` 能力，走
capability 的校验 / 超时 / 熔断 / 卸载，模型在能力地图里能看到并调用它们。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

_PROTOCOL_VERSION = "2024-11-05"

# 能力名契约（capability.NAME_PREFIX + 小写字母数字下划线，总长 ≤ 63）
_MCP_NAME_RE = re.compile(r"^p_[a-z][a-z0-9_]{0,60}$")


def _slug(text: str) -> str:
    """把任意 server/tool 名压成能力名可用片段。"""
    s = re.sub(r"[^0-9a-z]+", "_", str(text or "").lower()).strip("_")
    return s or "x"


def _cap_name(server_name: str, tool_name: str, used: set) -> str:
    """生成不冲突的能力名：p_mcp_<server>_<tool>（超长截断后补序号）。"""
    base = f"p_mcp_{_slug(server_name)}_{_slug(tool_name)}"
    base = base[:63].rstrip("_")
    if not _MCP_NAME_RE.match(base):
        base = f"p_mcp_{_slug(tool_name)}"[:63].rstrip("_")
    cand, i = base, 2
    while cand in used:
        suffix = f"_{i}"
        cand = base[: 63 - len(suffix)].rstrip("_") + suffix
        i += 1
    used.add(cand)
    return cand


def _schema_of(tool: dict) -> dict:
    """把 MCP inputSchema 归一成 OpenAI function parameters（必须是 type=object）。"""
    schema = tool.get("inputSchema") if isinstance(tool, dict) else None
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out = dict(schema)
    out.setdefault("type", "object")
    if out.get("type") != "object":
        # 少数 server 返回非对象 schema：包一层，别把非法 schema 交给模型（会被 API 400）。
        out = {"type": "object", "properties": {"input": out}}
    out.setdefault("properties", {})
    return out


def manifest_for_server(server: dict, tools) -> dict:
    """把「一个 server + 它的 tools/list」翻译成能力包 manifest（纯函数，便于单测）。"""
    name = str(server.get("name") or server.get("command") or "server")
    used: set = set()
    caps = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        tool_name = str(t.get("name") or "").strip()
        if not tool_name:
            continue
        desc = str(t.get("description") or "").strip()
        caps.append({
            "name": _cap_name(name, tool_name, used),
            "description": (f"[MCP:{name}] {desc}" if desc
                            else f"[MCP:{name}] 调用外部工具 {tool_name}")[:300],
            "parameters": _schema_of(t),
            "exec": {"type": "mcp", "server": dict(server), "tool": tool_name},
            "groups": ["🧩 扩展能力"],
            "phrases": f"调用外部 MCP 工具 {tool_name}",
            "preactivate": [(name.lower(), tool_name.lower())],
            "budget": {"timeout": 60, "write": False},
            "io": {"max_chars": 20000},
        })
    return {"api": "whaletalk.tool/1", "meta": {"name": f"MCP: {name}"}, "capabilities": caps}


def load_into_runtime(timeout: float = 20.0) -> dict:
    """把配置里的外部 MCP 工具装成运行时能力包（幂等；单 server 失败不影响其余）。

    返回 {ok, servers, tools, errors}。未配置 `mcp_servers` 时是一次 no-op。
    """
    servers = list_servers()
    out = {"ok": True, "servers": len(servers), "tools": [], "errors": []}
    if not servers:
        return out
    import capability

    for srv in servers:
        label = str(srv.get("name") or srv.get("command") or "server")
        try:
            tools = list_tools(srv, timeout=timeout)
        except Exception as e:  # noqa: BLE001 - 外部进程失败只损失该 server
            out["ok"] = False
            out["errors"].append(f"{label}: {e}")
            continue
        if not tools:
            continue
        manifest = manifest_for_server(srv, tools)
        if not manifest["capabilities"]:
            continue
        pid = "mcp_" + _slug(label)
        try:
            res = capability.register_manifest(manifest, plugin_id=pid)
        except Exception as e:  # noqa: BLE001
            out["ok"] = False
            out["errors"].append(f"{label}: 装载异常 {e}")
            continue
        if res.get("ok"):
            out["tools"].extend(res.get("tools") or [])
        else:
            out["ok"] = False
            out["errors"].extend(f"{label}: {x}" for x in (res.get("errors") or []))
    return out


class MCPError(RuntimeError):
    pass


def list_servers():
    """读取配置的 MCP server 列表（环境变量优先，便于测试/临时覆盖）。"""
    raw = os.environ.get("WHALETALK_MCP_SERVERS", "")
    if raw:
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return []
    else:
        try:
            import config_utils
            data = config_utils.load_config().get("mcp_servers") or []
        except Exception:  # noqa: BLE001
            return []
    out = []
    if isinstance(data, dict):
        for name, cfg in data.items():
            if isinstance(cfg, dict):
                out.append({"name": str(name), **cfg})
    elif isinstance(data, list):
        for cfg in data:
            if isinstance(cfg, dict) and (cfg.get("command") or cfg.get("name")):
                out.append(cfg)
    return out


def _server_cmd(server):
    if isinstance(server, str):
        raise MCPError("请传入 server 配置 dict（含 command/args）")
    cmd = str(server.get("command") or "").strip()
    if not cmd:
        raise MCPError("server 缺少 command")
    args = [str(a) for a in (server.get("args") or [])]
    return [cmd] + args


def _spawn(server):
    env = dict(os.environ)
    for k, v in (server.get("env") or {}).items():
        env[str(k)] = str(v)
    try:
        return subprocess.Popen(
            _server_cmd(server), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            bufsize=1, env=env,
        )
    except Exception as e:  # noqa: BLE001
        raise MCPError(f"无法启动 MCP server：{e}") from e


def _rpc(proc, method, params, req_id, timeout):
    """发送一条 JSON-RPC 请求并读取 id 匹配的响应（跳过通知/其它消息）。"""
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    try:
        proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        proc.stdin.flush()
    except Exception as e:  # noqa: BLE001
        raise MCPError(f"写入失败：{e}") from e
    import time
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MCPError(f"等待 {method} 响应超时（{timeout}s）")
        line = proc.stdout.readline()
        if not line:
            raise MCPError(f"MCP server 提前退出（等待 {method}）")
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue  # 非 JSON 日志行：忽略
        if obj.get("id") == req_id:
            if "error" in obj:
                raise MCPError(str(obj["error"]))
            return obj.get("result") or {}
    # unreachable


def _handshake(proc, timeout):
    _rpc(proc, "initialize", {
        "protocolVersion": _PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "WhaleTalk", "version": "1"},
    }, 1, timeout)
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
    except Exception:  # noqa: BLE001
        pass


def list_tools(server, timeout=20):
    """连一次外部 server，列出其全部工具。返回 [{name, description, inputSchema}]。"""
    proc = _spawn(server)
    try:
        _handshake(proc, timeout)
        result = _rpc(proc, "tools/list", {}, 2, timeout)
        tools = result.get("tools") or []
        return [{"name": str(t.get("name") or ""), "description": str(t.get("description") or ""),
                 "inputSchema": t.get("inputSchema") or {}} for t in tools if t.get("name")]
    finally:
        _close(proc)


def call_tool(server, tool, arguments=None, timeout=30):
    """调用外部 server 的某个工具，返回文本化结果。"""
    proc = _spawn(server)
    try:
        _handshake(proc, timeout)
        result = _rpc(proc, "tools/call",
                      {"name": str(tool), "arguments": arguments or {}}, 2, timeout)
        return _textify(result)
    finally:
        _close(proc)


def _textify(result):
    """把 tools/call 结果归一为字符串（优先拼接 text 内容块）。"""
    if not isinstance(result, dict):
        return str(result)
    parts = []
    for c in result.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text") or ""))
    if parts:
        return "\n".join(parts)
    return json.dumps(result, ensure_ascii=False)


def _close(proc):
    import contextlib
    with contextlib.suppress(Exception):
        proc.stdin.close()
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            proc.kill()


def _cli():
    """命令行自检：python mcp_client.py list <cmd> [args...]"""
    argv = sys.argv[1:]
    if not argv:
        print("用法：python mcp_client.py list <command> [args...]")
        return 2
    action, rest = argv[0], argv[1:]
    server = {"name": "cli", "command": rest[0], "args": rest[1:]} if rest else None
    if action == "list" and server:
        for t in list_tools(server):
            print(f"- {t['name']}: {t['description'][:80]}")
        return 0
    print("未知命令")
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
