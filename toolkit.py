# -*- coding: utf-8 -*-
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
"""

from __future__ import annotations

import ast
import copy
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_UNSET = object()


class ToolDef:
    __slots__ = ("schema", "fn", "groups", "phrases", "preactivate_keys", "executor")

    def __init__(self, schema, fn=None, groups=(), phrases=None, preactivate_keys=(), executor=_UNSET):
        self.schema = schema
        self.fn = fn
        self.groups = tuple(groups)
        self.phrases = phrases
        self.preactivate_keys = tuple(preactivate_keys)
        self.executor = fn if executor is _UNSET else executor


_REGISTRY: Dict[str, ToolDef] = {}


def _register(name, schema, fn, groups, phrases, preactivate_keys, executor):
    """注册一个工具；重复名直接抛错（防静默覆盖漂移）。"""
    if name in _REGISTRY:
        raise RuntimeError(f"[toolkit] 工具名重复注册: {name!r}（检查 @tool 与 register_tool）")
    _REGISTRY[name] = ToolDef(
        schema=schema, fn=fn, groups=groups,
        phrases=phrases, preactivate_keys=preactivate_keys, executor=executor,
    )


def tool(schema: dict, *, groups: Optional[Sequence[str]] = None,
         phrases: Optional[str] = None, preactivate: Optional[Sequence[Sequence[str]]] = None,
         executor: Any = _UNSET) -> Callable:
    """装饰器：把工具 schema 绑定到执行函数上，注册进单一来源注册表。

    参数：
      schema       OpenAI function calling 格式 dict（必须含 function.name）
      groups       展示组名列表（可多个；默认不属于任何组）
      phrases      动作短语（模型理解用；默认 None=不出现在 _TOOL_ACTION_PHRASES）
      preactivate  预激活提示参与声明，每个元素是一组口语关键词
                   （默认 None=不参与 _PREACTIVATE_HINTS）
      executor     显式执行函数（默认=被装饰函数；传 None 表示特殊回调处理）
    """
    fn = schema.get("function", {})
    name = fn.get("name") if isinstance(fn, dict) else None
    if not name:
        raise ValueError(f"[toolkit] schema 缺少 function.name: {schema!r}")

    def deco(f):
        _register(name, schema, f, groups or (), phrases, preactivate or (), executor)
        return f

    return deco


def register_tool(schema: dict, *, groups: Optional[Sequence[str]] = None,
                  phrases: Optional[str] = None, preactivate: Optional[Sequence[Sequence[str]]] = None,
                  executor: Any = _UNSET) -> None:
    """命令式注册（无函数定义处可用，如 ask_user 走回调、无独立执行函数）。"""
    fn = schema.get("function", {})
    name = fn.get("name") if isinstance(fn, dict) else None
    if not name:
        raise ValueError(f"[toolkit] schema 缺少 function.name: {schema!r}")
    _register(name, schema, None, groups or (), phrases, preactivate or (), executor)


# ---------------------------------------------------------------------------
# 构建函数：由注册表 + 显式顺序生成六层
# ---------------------------------------------------------------------------

def _order_check(order, registry, what):
    missing = [n for n in order if n not in registry]
    if missing:
        raise RuntimeError(f"[toolkit] {what} 顺序表含未注册工具: {missing}")
    extra = [n for n in registry if n not in order]
    if extra:
        raise RuntimeError(f"[toolkit] {what} 顺序表遗漏已注册工具: {extra}")


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
    """
    by_group: Dict[str, List[str]] = {g: [] for g in group_order}
    for name in tool_order:
        for g in _REGISTRY[name].groups:
            by_group[g].append(name)
    missing = [g for g in group_order if not by_group[g]]
    if missing:
        raise RuntimeError(f"[toolkit] TOOL_GROUPS 顺序表含无成员组: {missing}")
    return [(g, by_group[g]) for g in group_order]


def build_phrases() -> Dict[str, str]:
    """第 4 层 _TOOL_ACTION_PHRASES：工具名 → 动作短语（按注册顺序）。"""
    return {n: d.phrases for n, d in _REGISTRY.items() if d.phrases}


def build_preactivate(hint_order: Sequence[Tuple[str, ...]], tool_order: Sequence[str]) -> List[Tuple[Tuple[str, ...], List[str]]]:
    """第 6 层 _PREACTIVATE_HINTS：按 hint_order 的关键词元组顺序生成
    [(关键词元组, [工具名...]), ...]；同关键词的多个工具聚合为一条。

    成员顺序 = tool_order（与 TOOLS 列表一致），避免注册顺序造成漂移。
    每个工具可声明多组 preactivate 关键词（参与多条提示）；所有声明必须命中
    hint_order 中的某条，否则抛错（防「写了关键词但没出现在提示里」的静默遗漏）。
    """
    by_keys: Dict[Tuple[str, ...], List[str]] = {tuple(ks): [] for ks in hint_order}
    for name in tool_order:
        for ks in _REGISTRY[name].preactivate_keys:
            by_keys[tuple(ks)].append(name)
    hint_set = set(by_keys)
    order_set = set(hint_order)
    if hint_set != order_set:
        only_hint = order_set - hint_set
        only_tool = hint_set - order_set
        msg = []
        if only_hint:
            msg.append(f"hint 顺序表无对应工具: {sorted(only_hint)}")
        if only_tool:
            msg.append(f"工具声明了顺序表外的关键词: {sorted(only_tool)}")
        raise RuntimeError("[toolkit] _PREACTIVATE_HINTS 顺序表与工具声明不一致: " + "; ".join(msg))
    return [(ks, by_keys[ks]) for ks in hint_order]


def registry_names() -> List[str]:
    """已注册工具名（声明顺序）。"""
    return list(_REGISTRY.keys())


def clear_registry() -> None:
    """清空注册表（仅测试用）。"""
    _REGISTRY.clear()


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

    return {
        "TOOLS": build_tool_list(tool_order),
        "TOOL_CALL_MAP": build_call_map(),
        "TOOL_GROUPS": build_groups(group_order or [], tool_order),
        "_TOOL_ACTION_PHRASES": build_phrases(),
        "_PREACTIVATE_HINTS": build_preactivate(hint_order, tool_order),
    }
