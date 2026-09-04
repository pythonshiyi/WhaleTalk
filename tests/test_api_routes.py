# -*- coding: utf-8 -*-
"""P2-8/P2-2 路由表行为契约：do_POST / do_GET 轻量路由表（装饰器注册）的回归测试。

覆盖：
- POST 路由表完整性（52 条，exact/pre/set 三种 matcher）
- GET 路由表完整性（47 条，exact/pre/qpath 三种 matcher；由原 do_GET 46 分支迁移 + 统一记忆端点）
- 查表函数 _match_post_route / _match_get_route 对各形态路径的分发正确性
- do_POST / do_GET 兜底（未匹配 → None → 404）与鉴权前置不变
"""
import ast
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import api_server  # noqa: E402  （模块加载时会执行装饰器注册，两张路由表被填充）


def test_route_table_size():
    """POST 端点数 = 52（50 精确 + 1 pre + 1 set），且与 do_POST 分支时代一致。"""
    routes = api_server._POST_ROUTES
    assert len(routes) == 52, f"路由表应有 52 条，实际 {len(routes)}"
    kinds = {}
    for matcher, _ in routes:
        k = matcher[0] if isinstance(matcher, tuple) else "exact"
        kinds[k] = kinds.get(k, 0) + 1
    assert kinds == {"exact": 50, "pre": 1, "set": 1}, f"matcher 类型分布异常: {kinds}"


def test_route_order_priority():
    """匹配优先级 = 注册顺序：pre 型 tools/invoke 在精确匹配之后注册（保持原 if/elif 顺序）。"""
    routes = api_server._POST_ROUTES
    names = [n for _, n in routes]
    # 关键顺序断言（与原 do_POST if/elif 链一致）
    assert names.index("_p_v1_tools_invoke") < names.index("_p_v1_fim")
    assert names.index("_p_v1_prompts") < names.index("_p_v1_prompts_save_actions")
    assert names.index("_p_v1_sessions") < names.index("_p_v1_sessions_delete_batch")


def test_exact_match():
    cases = {
        "/v1/chat": "_p_v1_chat",
        "/v1/chat/stream": "_p_v1_chat_stream",
        "/v1/brain/memory": "_p_v1_brain_memory",
        "/v1/config": "_p_v1_config",
        "/v1/config/reset": "_p_v1_config_reset",  # P1-5：带副作用 reset 仅 POST（GET 分支已移除）
        "/v1/upload": "_p_v1_upload",
        "/v1/mode": "_p_v1_mode",
        "/v1/respond": "_p_v1_respond",
    }
    for path, expect in cases.items():
        got = api_server._match_post_route(path)
        assert got == expect, f"{path}: 期望 {expect} 实际 {got}"


def test_pre_matcher():
    """pre 型：/v1/tools/<任意名>/invoke 命中 _p_v1_tools_invoke。"""
    for p in ("/v1/tools/search_web/invoke", "/v1/tools/list_files/invoke", "/v1/tools/a/invoke"):
        assert api_server._match_post_route(p) == "_p_v1_tools_invoke", p


def test_set_matcher():
    """set 型：prompts 六个动作路由到同一方法。"""
    for p in ("/v1/prompts/save", "/v1/prompts/delete", "/v1/prompts/reorder",
              "/v1/prompts/import", "/v1/prompts/use", "/v1/prompts/restore_builtin"):
        assert api_server._match_post_route(p) == "_p_v1_prompts_save_actions", p


def test_no_match_returns_none():
    """未匹配路径返回 None → do_POST 兜底 404。"""
    for p in ("/v1/nonexistent", "", "/v1", "/v1/tools/", "/v1/tools/x/invoke/",
              "/v1/chatx", "/v1/chat/", "/v1/tts/voices"):
        assert api_server._match_post_route(p) is None, p


def test_all_routes_cover_all_old_branches():
    """路由表路径集合 == 源码中 @_post_route 装饰器路径集合（无漂移）。"""
    src = (REPO / "api_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    deco_paths = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) \
                        and d.func.id == "_post_route" and d.args:
                    arg = d.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        deco_paths.add(arg.value)
                    elif isinstance(arg, ast.Tuple):
                        for el in ast.walk(arg):
                            if isinstance(el, ast.Constant) and isinstance(el.value, str) \
                                    and el.value.startswith("/v1/"):
                                deco_paths.add(el.value)

    table_paths = set()
    for matcher, _ in api_server._POST_ROUTES:
        if isinstance(matcher, str):
            table_paths.add(matcher)
        elif matcher[0] == "set":
            table_paths.update(p for p in matcher[1] if isinstance(p, str) and p.startswith("/v1/"))
        elif matcher[0] == "pre":
            # pre 型前缀本身是 /v1/tools/（无独立端点），只登记前缀以便双向核对
            table_paths.add(matcher[1])

    assert table_paths == deco_paths, (
        f"路由表与装饰器声明不一致\n  仅表内: {sorted(table_paths - deco_paths)}\n"
        f"  仅装饰器: {sorted(deco_paths - table_paths)}"
    )


def test_do_post_sources_decorated_methods():
    """do_POST 必须通过 _match_post_route 查表（而非内联 if/elif）。"""
    src = inspect_source("api_server.py")
    # 主 Handler 的 do_POST（4 空格缩进，行首锚定避免误匹配 _InboundHandler 的 8 空格版本）
    m = re.search(r"(?m)^    def do_POST\(self\):(.*?)^    def _valid_messages", src, re.S)
    assert m, "未找到主 Handler 的 do_POST 方法体"
    body = m.group(1)
    assert "_match_post_route(self.path)" in body, "do_POST 未使用路由表"
    assert "getattr(self, handler)()" in body, "do_POST 未通过 getattr 分发"
    # 不应再有内联 if/elif 链（顶层 12 空格 if）
    inline = re.findall(r"^            (?:if|elif) self\.path", body, re.M)
    assert not inline, f"do_POST 仍有内联路由分支: {inline}"


# ── P2-2：GET 路由表（do_GET 46 分支 if/elif 迁移而来）────────────────────

def test_get_route_table_size():
    """GET 端点数 = 47（39 精确 + 5 pre + 3 qpath），与迁移前的 do_GET 分支数一致。"""
    routes = api_server._GET_ROUTES
    assert len(routes) == 47, f"GET 路由表应有 47 条，实际 {len(routes)}"
    kinds = {}
    for matcher, _ in routes:
        k = matcher[0] if isinstance(matcher, tuple) else "exact"
        kinds[k] = kinds.get(k, 0) + 1
    assert kinds == {"exact": 39, "pre": 5, "qpath": 3}, f"matcher 类型分布异常: {kinds}"


def test_get_exact_match():
    cases = {
        "/health": "_g_health",
        "/v1/sessions": "_g_v1_sessions",
        "/v1/models": "_g_v1_models",
        "/v1/config": "_g_v1_config",
        "/v1/failures": "_g_v1_failures",
        "/v1/evolutions": "_g_v1_evolutions",
        "/v1/plugins": "_g_v1_plugins",
        "/v1/plugin_market": "_g_v1_plugin_market",
        "/v1/context": "_g_v1_context",
        "/v1/tts/voices": "_g_v1_tts_voices",
    }
    for path, expect in cases.items():
        got = api_server._match_get_route(path)
        assert got == expect, f"{path}: 期望 {expect} 实际 {got}"


def test_get_pre_matcher():
    """pre 型（空后缀 = 纯前缀 / 带后缀）：/v1/tools/、/v1/evolutions/、/v1/plugins/、
    /v1/tts/audio/ 前缀命中各自端点；sessions 需同时满足前缀+后缀。"""
    cases = {
        "/v1/tools/search_web": "_g_v1_tools_item",
        "/v1/tools/": "_g_v1_tools_item",          # 空名：路由命中，端点内部 404
        "/v1/evolutions/abc": "_g_v1_evolutions_item",
        "/v1/plugins/xyz": "_g_v1_plugins_item",
        "/v1/tts/audio/1a2b3c4d.wav": "_g_v1_tts_audio",
        "/v1/sessions/abc123/messages": "_g_v1_sessions_messages",
    }
    for path, expect in cases.items():
        got = api_server._match_get_route(path)
        assert got == expect, f"{path}: 期望 {expect} 实际 {got}"


def test_get_qpath_matcher():
    """qpath 型：去查询串后精确匹配（带不带 ?query 都命中同一端点）。"""
    for p in ("/v1/files", "/v1/files?dir=/tmp",
              "/v1/brain/memories", "/v1/brain/memories?query=hi&limit=3",
              "/v1/brain/unified-memories", "/v1/brain/unified-memories?query=项目&sources=brain"):
        got = api_server._match_get_route(p)
        assert got in ("_g_v1_files", "_g_v1_brain_memories", "_g_v1_brain_unified_memories"), p


def test_get_no_match_returns_none():
    """未匹配路径返回 None → do_GET 兜底 404（含 /v1 前缀未知路径）。"""
    for p in ("/v1/nonexistent", "/v1", "", "/v1/filesx",
              "/v1/tts/voices/extra", "/v1/sessions/x/messagesx",
              "/v1/plugin_extra", "/v1/tools"):
        assert api_server._match_get_route(p) is None, p


def test_get_all_routes_cover_all_decorators():
    """GET 路由表路径集合 == 源码中 @_get_route 装饰器路径集合（无漂移）。"""
    src = (REPO / "api_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    deco_paths = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) \
                        and d.func.id == "_get_route" and d.args:
                    arg = d.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        deco_paths.add(arg.value)
                    elif isinstance(arg, ast.Tuple):
                        for el in ast.walk(arg):
                            if isinstance(el, ast.Constant) and isinstance(el.value, str) \
                                    and el.value.startswith("/v1/"):
                                deco_paths.add(el.value)

    table_paths = set()
    for matcher, _ in api_server._GET_ROUTES:
        if isinstance(matcher, str):
            table_paths.add(matcher)
        elif matcher[0] in ("pre", "qpath"):
            table_paths.add(matcher[1])

    assert table_paths == deco_paths, (
        f"GET 路由表与装饰器声明不一致\n  仅表内: {sorted(table_paths - deco_paths)}\n"
        f"  仅装饰器: {sorted(deco_paths - table_paths)}"
    )


def test_do_get_sources_decorated_methods():
    """do_GET 必须通过 _match_get_route 查表分发（而非内联 46 分支 if/elif）。"""
    src = inspect_source("api_server.py")
    # 主 Handler 的 do_GET：方法体止于第一个 @_get_route 装饰器
    m = re.search(r"(?m)^    def do_GET\(self\):(.*?)^    @_get_route", src, re.S)
    assert m, "未找到主 Handler 的 do_GET 方法体（或其后无 @_get_route 端点）"
    body = m.group(1)
    assert "_match_get_route(self.path)" in body, "do_GET 未使用 GET 路由表"
    assert "getattr(self, handler)()" in body, "do_GET 未通过 getattr 分发"
    # 不应再有内联 if/elif 链（顶层 12 空格 if；token 特例为 8 空格属预期）
    inline = re.findall(r"^            (?:if|elif) self\.path", body, re.M)
    assert not inline, f"do_GET 仍有内联路由分支: {inline}"


def test_get_handlers_all_exist():
    """路由表登记的方法名都真实存在于 _Handler（防拼写漂移 → AttributeError）。"""
    for _, name in api_server._GET_ROUTES:
        assert hasattr(api_server._Handler, name), f"GET 端点方法缺失: {name}"
    for _, name in api_server._POST_ROUTES:
        assert hasattr(api_server._Handler, name), f"POST 端点方法缺失: {name}"


def inspect_source(fname):
    return (REPO / fname).read_text(encoding="utf-8")
