"""search_web 引擎分发的跨模块可解析性回归测试。

背景（P0 回归，2026-09-09 由真实用户自测暴露）：
P0-1 巨石拆分把 search_web 迁入 agent_tools/tool_web.py，而其聚合的三引擎函数
_search_bing/_search_so360/_search_duckduckgo 留在 deepseek_client.py。search_web 用
`globals().get("_search_" + name)` 动态查表，查的是 tool_web 命名空间——但拆分后
tool_web 从未 import 这三个函数 → globals().get() 恒为 None → 每引擎静默空结果 →
连续 3 次被判"不可用"并暂停 10 分钟 → 界面/模型收到"可用搜索源均不可用"。
此静态断言语义错误无法被 import 时发现（None 非异常），故加本回归锚。

导入顺序须与真实运行一致：先 deepseek_client（其顶层第 ~3282 行 `from agent_tools import *`
完成全部工具注册），再访问 tool_web——若先 import tool_web，会因 deepseek_client 尚未
执行 agent_tools 聚合而触发 toolkit "未注册工具" 报错。
"""
import deepseek_client as dsc  # noqa: F401  # 必须先导入以完成 agent_tools 全量注册
import shared

# 显式导入 tool_web（在 deepseek_client 已完整注册之后）
from agent_tools import tool_web as tw

# 聚合注册的三个引擎名（与 shared._SEARCH_ENGINES 对齐）
_ENGINE_NAMES = [name for name, _w in shared._SEARCH_ENGINES]


def test_search_engines_resolvable_in_tool_web():
    """search_web 聚合的每个引擎函数必须能经 tool_web 命名空间 globals() 解析到。

    search_web 用 `globals().get("_search_" + name)` 动态查表；若函数未 import 进
    tool_web，解析为 None → 静默空结果。此处直接断言其可解析（防拆分再犯）。"""
    for name in _ENGINE_NAMES:
        fn = tw.__dict__.get("_search_" + name)
        assert fn is not None, (
            f"search_web 聚合引擎 _search_{name} 未解析到（tool_web 未 import？）。"
            f"这会让 search_web 对 {name} 恒返回空，最终报'可用搜索源均不可用'"
        )
        assert callable(fn), f"_search_{name} 不可调用：{fn!r}"


def test_search_engines_match_registry():
    """tool_web 解析到的引擎名 == shared._SEARCH_ENGINES 注册名（无漂移）。"""
    for name in _ENGINE_NAMES:
        assert getattr(dsc, "_search_" + name, None) is not None, (
            f"deepseek_client 缺失 _search_{name}（注册表 {name} 无实现）"
        )


def test_search_web_no_silent_engine_missing():
    """search_web 内部对每个健康引擎的 globals().get 必须命中（不因 None 静默空跑）。

    直接调用 search_web 需要网络，故此处校验其源码分发逻辑依赖的解析目标都就绪；
    避免引入真实网络的用例。"""
    # 复现 search_web 的动态查表：globals().get("_search_"+name)
    for name in _ENGINE_NAMES:
        assert tw.search_web.__globals__.get("_search_" + name) is not None, (
            f"search_web 的 __globals__ 查不到 _search_{name}（真实调用会空结果）"
        )


def test_missing_engine_raises_not_silent():
    """引擎函数缺失必须抛错（显式 RuntimeError），而非静默降级成"源不可用"。

    这是对 search_web 分发的加固验证：一旦未来某次拆分再把引擎函数甩在 tool_web
    之外，调用应立刻以清晰异常暴露，而不是走健康电路冷却后误报"可用搜索源均不可用"。
    通过把某引擎函数从 tool_web 命名空间摘除来触发 fn=None 分支（不碰网络）。"""
    import pytest

    saved = {}
    for name in _ENGINE_NAMES:
        attr = "_search_" + name
        saved[attr] = tw.__dict__.get(attr)
        tw.__dict__[attr] = None  # 模拟拆分回归：引擎函数全部从 tool_web 命名空间消失
    try:
        # 引擎全缺 → 每个 _run 命中 fn=None → ThreadPoolExecutor.map 抛 RuntimeError，
        # 且不发起任何网络请求（纯离线）。若加固失效会走"源均不可用"（错误降级）。
        with pytest.raises(RuntimeError, match="无实现"):
            tw.search_web("Python")
    finally:
        for attr, val in saved.items():
            tw.__dict__[attr] = val


