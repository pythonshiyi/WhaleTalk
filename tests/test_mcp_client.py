"""⑤a MCP 客户端回归：用假 MCP server（stdio JSON-RPC）验证握手/列工具/调用。"""
import json
import sys
import textwrap

import pytest

import mcp_client

_FAKE_SERVER = textwrap.dedent('''
    import json, sys
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2024-11-05",
                  "capabilities":{},"serverInfo":{"name":"fake","version":"1"}}})
        elif method == "tools/list":
            send({"jsonrpc":"2.0","id":mid,"result":{"tools":[
                {"name":"echo","description":"回显","inputSchema":{"type":"object"}},
                {"name":"add","description":"相加","inputSchema":{"type":"object"}}]}})
        elif method == "tools/call":
            p = msg.get("params") or {}
            name = p.get("name"); args = p.get("arguments") or {}
            if name == "echo":
                text = str(args.get("text",""))
            elif name == "add":
                text = str(int(args.get("a",0)) + int(args.get("b",0)))
            else:
                text = "unknown"
            send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":text}]}})
        elif mid is not None:
            send({"jsonrpc":"2.0","id":mid,"result":{}})
''')


@pytest.fixture()
def fake_server(tmp_path):
    p = tmp_path / "fake_mcp.py"
    p.write_text(_FAKE_SERVER, encoding="utf-8")
    return {"name": "fake", "command": sys.executable, "args": [str(p)]}


def test_list_tools(fake_server):
    tools = mcp_client.list_tools(fake_server)
    names = {t["name"] for t in tools}
    assert {"echo", "add"} <= names


def test_call_tool_echo(fake_server):
    assert mcp_client.call_tool(fake_server, "echo", {"text": "hello"}) == "hello"


def test_call_tool_add(fake_server):
    assert mcp_client.call_tool(fake_server, "add", {"a": 2, "b": 40}) == "42"


def test_list_servers_from_env(monkeypatch):
    monkeypatch.setenv("WHALETALK_MCP_SERVERS", json.dumps([
        {"name": "fs", "command": "npx", "args": ["-y", "x"]}]))
    servers = mcp_client.list_servers()
    assert servers and servers[0]["name"] == "fs"


def test_missing_command_raises():
    with pytest.raises(mcp_client.MCPError):
        mcp_client.list_tools({"name": "bad"})
