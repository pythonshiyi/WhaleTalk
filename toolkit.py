"""@tool() 装饰器 —— 工具声明单一来源（P1-3）。

背景：WhaleTalk 的工具信息此前散布在六处手工维护（TOOLS schema / 函数签名 /
TOOL_CALL_MAP / _TOOL_ACTION_PHRASES / TOOL_GROUPS / _PREACTIVATE_HINTS），
任何一处漏改都会静默漂移。本模块把六层收敛为「在函数定义处的一次声明」：

    @tool(
        {schema dict},                  # name/description/parameters（OpenAI 格式）
        groups=["🔧 系统与基础"],        # 可属多个展示组
        phrases="动作短语",              # 模型理解用的一句话描述
        preactivate=(("关键词1", "关键词2"), ("另一组",)),  # 可参与多条预激活提示
    )
    def my_tool(...): ...

别名/特殊工具（执行函数与工具名不同，或走回调不落函数）用 register_tool()：

    register_tool(schema, executor=None, groups=..., phrases=..., preactivate=...)

注册完成后调用构建函数生成六层；顺序由显式 _ORDER 常量保证（迁移自历史数据）。
重复注册/顺序表缺项/多余项均在模块加载时抛错——比任何 AST 门禁都早。

## 来源（source）：core 与 capability 共用一张表

v3.17 起，运行时装载的「扩展能力」（插件工具）与内置工具进**同一张注册表**，
唯一区别是 `ToolDef.source`：

    core        源码里 @tool() 声明的内置工具，顺序由 _TOOL_ORDER 固定
    capability  运行时装载（capability.py），可撤销、可热替换

这条边界必须在这里守住，原因是六层的构建方式：`_TOOL_ORDER` 是**核心工具的
显式顺序表**，而运行时工具没有顺序表（它们按装载顺序排）。因此：

- `_order_check` 只把 **core** 子集与顺序表比对（否则插件一装载，六层构建就报
  "顺序表遗漏已注册工具"）；
- `build_groups` / `build_preactivate` 把 capability 工具**追加**在核心成员之后，
  组名/关键词命中已有的就直接并进去（插件不必自造分组，能复用现成能力地图）；
- `unregister` 只允许撤销 capability 工具——核心工具由源码声明，撤销它等于让
  六层与源码不一致。
"""

from __future__ import annotations

import ast
import copy
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_UNSET = object()

# 工具来源：core = 源码声明；capability = 运行时装载（可撤销）。
SOURCE_CORE = "core"
SOURCE_CAPABILITY = "capability"


class ToolDef:
    __slots__ = ("schema", "fn", "groups", "phrases", "preactivate_keys", "executor",
                 "source", "plugin_id")

    def __init__(self, schema, fn=None, groups=(), phrases=None, preactivate_keys=(),
                 executor=_UNSET, source=SOURCE_CORE, plugin_id=None):
        self.schema = schema
        self.fn = fn
        self.groups = tuple(groups)
        self.phrases = phrases
        self.preactivate_keys = tuple(preactivate_keys)
        self.executor = fn if executor is _UNSET else executor
        self.source = str(source or SOURCE_CORE)
        self.plugin_id = plugin_id


_REGISTRY: Dict[str, ToolDef] = {}


def _register(name, schema, fn, groups, phrases, preactivate_keys, executor,
              source=SOURCE_CORE, plugin_id=None):
    """注册一个工具；重复名直接抛错（防静默覆盖漂移）。"""
    if name in _REGISTRY:
        raise RuntimeError(f"[toolkit] 工具名重复注册: {name!r}（检查 @tool 与 register_tool）")
    _REGISTRY[name] = ToolDef(
        schema=schema, fn=fn, groups=groups,
        phrases=phrases, preactivate_keys=preactivate_keys, executor=executor,
        source=source, plugin_id=plugin_id,
    )


def tool(schema: dict, *, groups: Optional[Sequence[str]] = None,
         phrases: Optional[str] = None, preactivate: Optional[Sequence[Sequence[str]]] = None,
         executor: Any = _UNSET, hooks: Optional[Sequence[str]] = None) -> Callable:
    """装饰器：把工具 schema 绑定到执行函数上，注册进单一来源注册表。

    参数：
      schema       OpenAI function calling 格式 dict（必须含 function.name）
      groups       展示组名列表（可多个；默认不属于任何组）
      phrases      动作短语（模型理解用；默认 None=不出现在 _TOOL_ACTION_PHRASES）
      preactivate  预激活提示参与声明，每个元素是一组口语关键词
                   （默认 None=不参与 _PREACTIVATE_HINTS）
      executor     显式执行函数（默认=被装饰函数；传 None 表示特殊回调处理）
      hooks        本工具额外声明的横切钩子名（见 tool_hooks.py），如 ("snapshot",)。
                   trust_declare / trust_commit 是全局钩子，无需声明。

    横切关注的收口（P0-1）：注册时统一用 tool_hooks.wrap 包装执行体——
      - 返回包装体作为模块级名字，**任何调用路径**（分发/HTTP invoke/工作流/
        测试直调）都拿得到钩子，而不是只有分发点那一条路；
      - `functools.wraps` 保证 __name__/__doc__/inspect.signature 对调用方不变。
    为什么不放分发处：execute_tool 只是众多调用路径之一，包装在注册处才关得住。
    """
    fn = schema.get("function", {})
    name = fn.get("name") if isinstance(fn, dict) else None
    if not name:
        raise ValueError(f"[toolkit] schema 缺少 function.name: {schema!r}")

    def deco(f):
        import tool_hooks
        declared = tuple(hooks or ())
        if executor is not _UNSET:
            # 显式执行体（被装饰函数不是执行体的场景）：包装执行体，函数名保持原样
            wrapped_exec = tool_hooks.wrap(name, executor, declared)
            _register(name, schema, f, groups or (), phrases, preactivate or (), wrapped_exec)
            return f
        wrapped = tool_hooks.wrap(name, f, declared)
        _register(name, schema, wrapped, groups or (), phrases, preactivate or (), wrapped)
        return wrapped

    return deco


def register_tool(schema: dict, *, groups: Optional[Sequence[str]] = None,
                  phrases: Optional[str] = None, preactivate: Optional[Sequence[Sequence[str]]] = None,
                  executor: Any = _UNSET, hooks: Optional[Sequence[str]] = None,
                  source: str = SOURCE_CORE, plugin_id: Optional[str] = None) -> None:
    """命令式注册（无函数定义处可用，如 ask_user 走回调、无独立执行函数）。

    executor 为可调用对象时同样过 tool_hooks 包装；字符串（AST 重建模式）与
    None 原样保留——AST 门禁不执行函数，不需要钩子。

    source/plugin_id：运行时扩展能力用 SOURCE_CAPABILITY + 所属能力包 id。
    合法的 capability 名必须带前缀（由 capability.py 在装载时强制），这里不再判。
    """
    fn = schema.get("function", {})
    name = fn.get("name") if isinstance(fn, dict) else None
    if not name:
        raise ValueError(f"[toolkit] schema 缺少 function.name: {schema!r}")
    exec_ = None if executor is _UNSET else executor
    if callable(exec_):
        import tool_hooks
        exec_ = tool_hooks.wrap(name, exec_, hooks or ())
    _register(name, schema, None, groups or (), phrases, preactivate or (), exec_,
              source=source, plugin_id=plugin_id)


def unregister(name: str) -> bool:
    """撤销一个**运行时**工具（core 工具不可撤销：它由源码声明）。

    返回是否真的移除。调用方（capability.py）负责在之后刷新六层视图。
    """
    d = _REGISTRY.get(str(name))
    if d is None:
        return False
    if d.source == SOURCE_CORE:
        raise RuntimeError(f"[toolkit] 不允许撤销内置工具: {name!r}（它由 @tool() 声明）")
    _REGISTRY.pop(str(name), None)
    return True


def is_registered(name: str) -> bool:
    """工具名是否已被占用（内置或运行时）。装载前查冲突用。"""
    return str(name) in _REGISTRY


def executor_for(name: str):
    """取工具的执行体（None = 特殊回调或未注册）。供 builtin_ref 复用内置能力。"""
    d = _REGISTRY.get(str(name))
    return d.executor if d is not None else None


def capability_names() -> List[str]:
    """运行时装载的工具名（声明顺序）。"""
    return [n for n, d in _REGISTRY.items() if d.source != SOURCE_CORE]


def capability_defs() -> List[Tuple[str, "ToolDef"]]:
    """运行时装载的 (名字, ToolDef) 列表。"""
    return [(n, d) for n, d in _REGISTRY.items() if d.source != SOURCE_CORE]


# ---------------------------------------------------------------------------
# 构建函数：由注册表 + 显式顺序生成六层
# ---------------------------------------------------------------------------

def _order_check(order, registry, what):
    """顺序表与**核心**工具集合的一致性校验。

    只比 core 子集：运行时能力（capability）没有顺序表，它们按装载顺序追加。
    若把 capability 也算进"遗漏"，插件一装载六层构建就会炸——这条边界必须在这里
    而不是在调用方守住，否则每个 build_* 调用点都要各自过滤一遍。
    """
    missing = [n for n in order if n not in registry]
    if missing:
        raise RuntimeError(f"[toolkit] {what} 顺序表含未注册工具: {missing}")
    extra = [n for n, d in registry.items() if d.source == SOURCE_CORE and n not in order]
    if extra:
        raise RuntimeError(f"[toolkit] {what} 顺序表遗漏已注册工具: {extra}")


def _build_order(tool_order: Sequence[str]) -> List[str]:
    """核心顺序 + 运行时能力（追加在后，已被核心覆盖的不重复）。"""
    order = list(tool_order)
    seen = set(order)
    for n in capability_names():
        if n not in seen:
            order.append(n)
            seen.add(n)
    return order


def build_tool_list(order: Sequence[str]) -> List[dict]:
    """第 1 层 TOOLS：按 order 顺序返回 schema 深拷贝（调用方可能增删）。"""
    _order_check(order, _REGISTRY, "TOOLS")
    return [copy.deepcopy(_REGISTRY[n].schema) for n in order]


def build_call_map() -> Dict[str, Any]:
    """第 3 层 TOOL_CALL_MAP：工具名 → 执行函数（None=特殊回调处理）。"""
    return {n: d.executor for n, d in _REGISTRY.items()}


def build_groups(group_order: Sequence[str], tool_order: Sequence[str]) -> List[Tuple[str, List[str]]]:
    """第 5 层 TOOL_GROUPS：按 group_order 顺序生成 (组名, [成员...])。

    成员顺序 = tool_order（即 _TOOL_ORDER/TOOLS 列表顺序），与历史数据
    （手工 TOOL_GROUPS 字面量）保持一致；若按注册顺序则等于函数定义顺序，
    可能与 TOOLS 顺序不同而静默漂移。

    运行时能力：声明了**已有组名**的并入该组（插件不必自造分类）；声明了**新组名**的
    按装载顺序追加在末尾——否则插件工具在能力地图里不可见，等于没有装载（这正是
    v3.16 之前插件工具的真实处境：有实现，模型找不到）。
    """
    by_group: Dict[str, List[str]] = {g: [] for g in group_order}
    extra_groups: List[str] = []
    for name in _build_order(tool_order):
        for g in _REGISTRY[name].groups:
            if g not in by_group:
                by_group[g] = []
                extra_groups.append(g)
            by_group[g].append(name)
    missing = [g for g in group_order if not by_group[g]]
    if missing:
        raise RuntimeError(f"[toolkit] TOOL_GROUPS 顺序表含无成员组: {missing}")
    return [(g, by_group[g]) for g in list(group_order) + extra_groups]


def build_phrases() -> Dict[str, str]:
    """第 4 层 _TOOL_ACTION_PHRASES：工具名 → 动作短语（按注册顺序）。"""
    return {n: d.phrases for n, d in _REGISTRY.items() if d.phrases}


def build_preactivate(hint_order: Sequence[Tuple[str, ...]], tool_order: Sequence[str]) -> List[Tuple[Tuple[str, ...], List[str]]]:
    """第 6 层 _PREACTIVATE_HINTS：按 hint_order 的关键词元组顺序生成
    [(关键词元组, [工具名...]), ...]；同关键词的多个工具聚合为一条。

    成员顺序 = tool_order（与 TOOLS 列表一致），避免注册顺序造成漂移。
    每个工具可声明多组 preactivate 关键词（参与多条提示）；所有声明必须命中
    hint_order 中的某条，否则抛错（防「写了关键词但没出现在提示里」的静默遗漏）。

    运行时能力允许带来**新的关键词元组**（追加在核心 hint 之后）——但若它声明的
    关键词与核心某条相同，直接并入那一条，不重复建条目。
    """
    by_keys: Dict[Tuple[str, ...], List[str]] = {tuple(ks): [] for ks in hint_order}
    extra_keys: List[Tuple[str, ...]] = []
    for name in _build_order(tool_order):
        for ks in _REGISTRY[name].preactivate_keys:
            k = tuple(ks)
            if k not in by_keys:
                by_keys[k] = []
                extra_keys.append(k)
            by_keys[k].append(name)
    hint_set = set(by_keys)
    order_set = set(hint_order)
    only_hint = order_set - hint_set
    only_tool = hint_set - order_set - set(extra_keys)
    if only_hint or only_tool:
        msg = []
        if only_hint:
            msg.append(f"hint 顺序表无对应工具: {sorted(only_hint)}")
        if only_tool:
            msg.append(f"工具声明了顺序表外的关键词: {sorted(only_tool)}")
        raise RuntimeError("[toolkit] _PREACTIVATE_HINTS 顺序表与工具声明不一致: " + "; ".join(msg))
    return [(ks, by_keys[ks]) for ks in list(hint_order) + extra_keys]


def build_capability_list() -> List[dict]:
    """运行时能力的 schema 列表（按装载顺序）。"""
    return [copy.deepcopy(d.schema) for _n, d in capability_defs()]


def registry_names() -> List[str]:
    """已注册工具名（声明顺序）。"""
    return list(_REGISTRY.keys())


def clear_registry() -> None:
    """清空注册表（仅测试用）。"""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# 工具返回契约的辅助函数
# ---------------------------------------------------------------------------

# 与 shared.TOOL_RESULT_FAIL_PREFIXES 保持一致（此处不 import shared：toolkit 在
# 导入链最上游，shared 会反向依赖工具域模块的阈值常量，提前引入会有循环风险）。
# 用 fail() 产出的文本天然以「错误」开头，故必须命中原口径。
_FAIL_PREFIX = "错误"


def fail(message: str) -> str:
    """构造一句符合全仓工具返回契约的失败文案。

    契约（见 CONTRIBUTING.md / AI_PROJECT_GUIDE.md §6.3）：失败**返回字符串而非
    抛异常**，且必须以「错误」开头（全角冒号分隔）——`shared.TOOL_RESULT_FAIL_PREFIXES`
    据此判定成功/失败，失败记忆、自动消解、审计都依赖这一前缀。手写时极易漏掉
    全角冒号或误用半角，`fail()` 把这件事收敛到一处。

    用法：
        return fail("sql 必填")
        return fail(f"查询失败: {e}")

    **仅用于新代码与正在改动的代码**：存量工具手写的 `return f"错误：…"` 行为完全
    一致、无需批量替换（批量改写只会制造风险，不带来收益）。
    """
    msg = str(message or "").strip()
    # 容忍调用方把前缀也写进来的情况，避免出现「错误：错误：…」
    for p in (_FAIL_PREFIX + "：", _FAIL_PREFIX + ":"):
        if msg.startswith(p):
            msg = msg[len(p):].lstrip()
            break
    # 兜底文案不含「错误」二字：避免同一句里出现两次错误前缀标记，
    # 让「以错误开头」成为唯一判定信号。
    return f"{_FAIL_PREFIX}：{msg or '未提供失败原因'}"


# ---------------------------------------------------------------------------
# AST 重建：不执行模块即可拿到六层（供无依赖环境的独立审计/门禁工具）
# ---------------------------------------------------------------------------

def rebuild_layers(source_text: str, *extra_sources: str) -> Dict[str, Any]:
    """从 deepseek_client.py 及工具域模块源码 AST 重建六层，不 import 模块。

    P0-1 拆分后 @tool() 装饰器分布在多个文件（主文件 + agent_tools/ 域模块）：
      - source_text 与全部 extra_sources 均参与 @tool()/register_tool() 收集
        （同一工具只在一个文件中声明，跨文件不会重复）；
      - 顺序常量 _TOOL_ORDER/_GROUP_ORDER/_HINT_ORDER 只从主文件顶层读取。
    其余语义同单文件版本：executor 用名字字符串标记（无真实函数对象）；
    调用会清空并重建全局注册表。
    """
    # 保存并恢复全局注册表：本函数是**只读审计接口**，却被就地清空重建过——
    # 同进程内调用它（门禁脚本、测试）会让运行时能力（capability）从注册表里消失，
    # 而 dc.TOOLS 仍留着它们的 schema，两层就此分叉。审计不该改变被审计对象的状态。
    saved = dict(_REGISTRY)
    clear_registry()

    def arg_value(call_node, key, default=None):
        for kw in call_node.keywords:
            if kw.arg == key:
                return ast.literal_eval(kw.value)
        return default

    for src in (source_text,) + tuple(extra_sources):
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.decorator_list:
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "tool":
                        register_tool(
                            ast.literal_eval(dec.args[0]),
                            groups=arg_value(dec, "groups"),
                            phrases=arg_value(dec, "phrases"),
                            preactivate=arg_value(dec, "preactivate"),
                            executor=node.name,
                        )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "register_tool" and node.args:
                register_tool(
                    ast.literal_eval(node.args[0]),
                    groups=arg_value(node, "groups"),
                    phrases=arg_value(node, "phrases"),
                    preactivate=arg_value(node, "preactivate"),
                    executor=arg_value(node, "executor"),
                )

    tree = ast.parse(source_text)

    def find_assign(name):
        for n in tree.body:
            if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in n.targets
            ):
                return ast.literal_eval(n.value)
        return None

    tool_order = find_assign("_TOOL_ORDER")
    group_order = find_assign("_GROUP_ORDER")
    hint_order = [tuple(h) for h in find_assign("_HINT_ORDER") or []]
    if not tool_order:
        raise ValueError("[toolkit] rebuild_layers 找不到 _TOOL_ORDER（文件未迁移？）")

    try:
        return {
            "TOOLS": build_tool_list(tool_order),
            "TOOL_CALL_MAP": build_call_map(),
            "TOOL_GROUPS": build_groups(group_order or [], tool_order),
            "_TOOL_ACTION_PHRASES": build_phrases(),
            "_PREACTIVATE_HINTS": build_preactivate(hint_order, tool_order),
        }
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)
