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


# ── 接线：MCP 工具 → 运行时能力包 ──────────────────────────────────────

def test_manifest_for_server_is_valid_capability(fake_server):
    tools = mcp_client.list_tools(fake_server)
    manifest = mcp_client.manifest_for_server(fake_server, tools)
    import capability
    ok, errs = capability.validate_manifest(manifest)
    assert ok, errs
    caps = manifest["capabilities"]
    assert {c["name"] for c in caps} >= {"p_mcp_fake_echo", "p_mcp_fake_add"}
    for c in caps:
        assert c["exec"]["type"] == "mcp"
        assert c["exec"]["tool"] in ("echo", "add")
        assert c["parameters"]["type"] == "object"
        assert c["groups"]


def test_manifest_normalizes_messy_tool_names():
    man = mcp_client.manifest_for_server(
        {"name": "My Server", "command": "x"},
        [{"name": "Read File!", "description": "", "inputSchema": {"type": "object"}}],
    )
    name = man["capabilities"][0]["name"]
    assert name.startswith("p_mcp_my_server_") and name.islower()
    assert len(name) <= 63


def test_manifest_wraps_non_object_schema():
    man = mcp_client.manifest_for_server(
        {"name": "s", "command": "x"},
        [{"name": "weird", "inputSchema": {"type": "string"}}],
    )
    assert man["capabilities"][0]["parameters"]["type"] == "object"


def test_load_into_runtime_registers_and_unregisters(fake_server, monkeypatch):
    """端到端：配置 → 能力包 → 工具注册表（并清理，避免污染其它用例）。"""
    monkeypatch.setenv("WHALETALK_MCP_SERVERS", json.dumps([fake_server]))
    import capability
    import toolkit

    res = mcp_client.load_into_runtime()
    try:
        assert res["ok"], res
        assert len(res["tools"]) == 2
        for name in res["tools"]:
            assert toolkit.is_registered(name), name
            assert toolkit.executor_for(name) is not None
    finally:
        capability.unregister_plugin("mcp_fake", reason="test cleanup")
    for name in res["tools"]:
        assert not toolkit.is_registered(name)


def test_load_into_runtime_noop_without_config(monkeypatch):
    monkeypatch.delenv("WHALETALK_MCP_SERVERS", raising=False)
    monkeypatch.setattr(mcp_client, "list_servers", lambda: [])
    assert mcp_client.load_into_runtime()["tools"] == []
