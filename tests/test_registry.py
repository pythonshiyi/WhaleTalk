"""工具注册表六层一致性核心断言（自举回归的锚）。

自我进化（self_evolve）改动 deepseek_client.py 后，pytest 会回退到 tests/ 全量跑本文件：
任何一层注册表被进化破坏（新增工具漏注册/改名未同步），测试即失败 → 进化自动回滚。
这正是「防止瞎进化」的工程闸。
"""
import deepseek_client as dsc
import permissions
from config_defaults import BUILTIN_TOOL_NAMES


def _tool_names(tools):
    return {t["function"]["name"] for t in tools if isinstance(t, dict) and t.get("type") == "function"}


def test_tools_call_map_bidirectional():
    """TOOLS 与 TOOL_CALL_MAP 必须双向无缺口、无重复。"""
    tools = _tool_names(dsc.TOOLS)
    cmap = set(dsc.TOOL_CALL_MAP)
    assert tools == cmap, (
        f"TOOLS({len(tools)}) 与 TOOL_CALL_MAP({len(cmap)}) 不一致："
        f"TOOLS 有而 CALL_MAP 缺 {sorted(tools - cmap)}；CALL_MAP 多 {sorted(cmap - tools)}"
    )


def test_tool_groups_cover_all_tools():
    """每个工具必须归属至少一个 TOOL_GROUPS 分组。"""
    tools = _tool_names(dsc.TOOLS)
    grouped = set()
    for _group, names in dsc.TOOL_GROUPS:
        grouped.update(names)
    missing = tools - grouped
    assert not missing, f"TOOL_GROUPS 未覆盖 {len(missing)} 个工具：{sorted(missing)}"


def test_builtin_tool_names_cover():
    """BUILTIN_TOOL_NAMES（config_defaults 默认启用集）必须是注册表的子集：
    默认启用的每个工具都必须在 TOOLS 中有完整 schema（可被 enable 后立即调用）。"""
    tools = _tool_names(dsc.TOOLS)
    missing = set(BUILTIN_TOOL_NAMES) - tools
    assert not missing, f"BUILTIN_TOOL_NAMES 含未注册工具：{sorted(missing)}"


def test_action_tools_cover():
    """ACTION_TOOLS（需审批动作集）必须全部存在于注册工具中（正向包含）。"""
    tools = _tool_names(dsc.TOOLS)
    missing = set(permissions.ACTION_TOOLS) - tools
    assert not missing, f"ACTION_TOOLS 存在未注册动作：{sorted(missing)}"


def test_every_tool_schema_complete():
    """每个工具必须有 name/description/parameters（缺 parameters 会导致 API 400）。"""
    for t in dsc.TOOLS:
        fn = t["function"]
        assert fn.get("name"), f"存在缺 name 的工具项：{t}"
        assert fn.get("description"), f"工具缺 description：{fn['name']}"
        assert isinstance(fn.get("parameters"), dict), f"工具缺 parameters：{fn['name']}"


def test_tool_call_map_all_callable():
    """TOOL_CALL_MAP 的每个条目必须绑定可调用实现（防注册了 schema 没实现）。
    唯一例外：交互回调型工具（ask_user/request_permission）设计上绑定 None，
    由 chat() 通过 on_ask/审批回调询问用户。"""
    none_ok = {"ask_user", "request_permission"}
    for name, impl in dsc.TOOL_CALL_MAP.items():
        if name in none_ok and impl is None:
            continue
        assert callable(impl), f"TOOL_CALL_MAP[{name}] 不可调用：{impl!r}"


def test_self_evolve_guard_exists():
    """四层验证链的闸函数必须存在（防进化把验证链本身删掉）。"""
    for guard in ("_evolve_compile", "_evolve_lint", "_evolve_smoke", "_evolve_tests"):
        assert callable(getattr(dsc, guard, None)), f"验证闸函数 {guard} 不存在或不可调用"
