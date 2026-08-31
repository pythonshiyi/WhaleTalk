# ── web_search 注入逻辑单元测试 ─────────────────
# 验证 pure_chat + web_search 时：只注入联网工具、注入提示、纯对话不注入、任务模式不受影响。
# 运行：python tests/test_web_search.py（仓库根目录）
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import deepseek_client as dc

def fake_stream(content="联网测试结果"):
    c1 = types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=content, reasoning_content=None, tool_calls=None), finish_reason=None)],
        usage=None,
    )
    c2 = types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=None, reasoning_content=None, tool_calls=None), finish_reason="stop")],
        usage=None,
    )
    yield c1
    yield c2

client = dc.DeepSeekClient(api_key="sk-test", base_url="https://example.invalid")

failed = 0
def check(cond, name):
    global failed
    if cond:
        print("PASS:", name)
    else:
        print("FAIL:", name)
        failed += 1

# ── 用例 1：pure_chat + web_search=True → 只注入联网工具 + 提示 ──
captured = {}
def fake_create1(**kwargs):
    captured.update(kwargs)
    return fake_stream()
client._create_with_retry = types.MethodType(lambda self, kw, attempts=3, stop_event=None: fake_create1(**kw), client)

content = []
client.chat([{"role": "user", "content": "今天北京的天气怎么样？"}], pure_chat=True, web_search=True, on_content=content.append, max_tokens=64, thinking="none")

tools = captured.get("tools", [])
names = [t["function"]["name"] for t in tools]
check(set(names) == set(dc.WEB_SEARCH_TOOLS), f"只注入联网工具 {sorted(dc.WEB_SEARCH_TOOLS)}，实际 {sorted(names)}")
check("search_web" in names, "包含 search_web")
msgs = captured.get("messages", [])
hint_found = any(m.get("role") == "system" and dc.WEB_SEARCH_HINT in m.get("content", "") for m in msgs)
check(hint_found, "注入联网使用提示")
check("".join(content).strip() == "联网测试结果", "对话流正常返回")
print("   kwargs.tools 数 =", len(tools))

# ── 用例 2：pure_chat + web_search=False → 不注入任何工具 ──
captured.clear()
def fake_create2(**kwargs):
    captured.update(kwargs)
    return fake_stream()
client._create_with_retry = types.MethodType(lambda self, kw, attempts=3, stop_event=None: fake_create2(**kw), client)
client.chat([{"role": "user", "content": "你好"}], pure_chat=True, web_search=False, max_tokens=64, thinking="none")
check("tools" not in captured, "纯对话(不开联网)不传 tools schema")

# ── 用例 3：pure_chat + web_search=True 但工具不可用(空 all_tools) → 不崩溃 ──
captured.clear()
def fake_create3(**kwargs):
    captured.update(kwargs)
    return fake_stream()
client._create_with_retry = types.MethodType(lambda self, kw, attempts=3, stop_event=None: fake_create3(**kw), client)
orig = dc._cached_all_tools
dc._cached_all_tools = lambda custom=(): []
try:
    client.chat([{"role": "user", "content": "你好"}], pure_chat=True, web_search=True, max_tokens=64, thinking="none")
    check("tools" not in captured, "联网工具不可用时优雅降级(不注入)")
finally:
    dc._cached_all_tools = orig

# ── 用例 4：任务模式(tools_enabled)不受 web_search 影响 ──
captured.clear()
def fake_create4(**kwargs):
    captured.update(kwargs)
    return fake_stream()
client._create_with_retry = types.MethodType(lambda self, kw, attempts=3, stop_event=None: fake_create4(**kw), client)
client.chat([{"role": "user", "content": "你好"}], pure_chat=False, tools_enabled=True, enabled_tools=["search_web", "search_github"], web_search=True, max_tokens=64, thinking="none")
names4 = [t["function"]["name"] for t in captured.get("tools", [])]
expect4 = sorted(set(["search_web", "search_github"]) | set(dc.SELF_EVOLUTION_TOOLS))
check(sorted(names4) == expect4, f"任务模式子集=enabled+自我进化工具(web_search 不额外注入)，实际 {sorted(names4)}")
check("search_realtime" not in names4, "任务模式不因 web_search 注入 search_realtime(纯对话专用)")

if failed:
    print(f"\n❌ {failed} 组失败")
    sys.exit(1)
print("\n✅ web_search 注入逻辑全部通过")
