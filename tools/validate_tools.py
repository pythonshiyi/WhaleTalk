# -*- coding: utf-8 -*-
"""工具系统功能级验证（WhaleTalk 开发工具链回归测试）。

用 AST 提取 deepseek_client.py 的真实源码块执行（不导入模块、无副作用），
验证 smart_tools 全链路可运行：
  1. build_tool_index 能力地图可生成
  2. compact_tool_schema 对全部工具可执行且结果合法（smart 激活注入）
  3. _patch_array_items 能兜底补齐缺失的 items
  4. TOOLS 整体 JSON 可序列化、无重名
  5. 全部描述 ≤130 字（smart 模式不截断关键信息）
  6. 全部数组参数带 items

用途：新增/修改工具定义后的回归门禁。可配合 audit_tools.py 使用。

用法：
    python tools/validate_tools.py      # 全量验证，失败返回非 0
"""
import ast
import copy
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "deepseek_client.py"


def get_assign_nodes(src, tree, target_names):
    """按文件顺序返回指定顶层赋值节点的源码片段。"""
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in target_names:
                    out.append(ast.get_source_segment(src, node))
                    break
    return out


def get_func_src(src, tree, func_names):
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in func_names:
            out.append(ast.get_source_segment(src, node))
    return out


def main():
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    block = []
    block += get_assign_nodes(src, tree, ["TOOLS", "_TOOL_ACTION_PHRASES", "TOOL_GROUPS",
                                          "_TOOL_INDEX_CACHE", "_TOOL_INDEX_KEY", "ACTIVATE_TOOL"])
    block += get_func_src(src, tree, ["build_tool_index", "compact_tool_schema",
                                      "compact_tools_list", "_patch_array_items"])
    ns = {"re": re, "json": json, "__name__": "validate_block"}
    exec(compile("\n".join(block), "tools_block", "exec"), ns)

    tools = ns["TOOLS"]
    fails = []

    # 1. 能力地图
    try:
        index = ns["build_tool_index"]()
        if not (isinstance(index, str) and len(index) > 200):
            fails.append("能力地图为空或过短")
    except Exception as e:
        fails.append(f"能力地图生成异常: {e}")

    # 2. compact 全量
    for t in tools:
        try:
            c = ns["compact_tool_schema"](t)
            json.dumps(c, ensure_ascii=False)
            if len(c["function"]["description"]) > 130:
                fails.append(f"{t['function']['name']}: compact 后描述仍超 130")
            for pn, pv in (c["function"].get("parameters", {}).get("properties") or {}).items():
                if pv.get("type") == "array" and "items" not in pv:
                    fails.append(f"{t['function']['name']}: {pn} 缺 items")
        except Exception as e:
            fails.append(f"{t['function']['name']}: compact 异常 {e}")

    # 3. _patch_array_items 兜底
    try:
        tools_copy = copy.deepcopy(tools)
        for t in tools_copy:
            if t["function"]["name"] == "subagent_run":
                t["function"]["parameters"]["properties"]["tasks"] = {
                    "type": "array", "description": "x"}
                break
        ns["_patch_array_items"](tools_copy)
        ok = all(
            pv.get("type") != "array" or "items" in pv
            for t in tools_copy
            for pv in t["function"]["parameters"].get("properties", {}).values()
        )
        if not ok:
            fails.append("_patch_array_items 未能补齐缺失 items")
    except Exception as e:
        fails.append(f"_patch_array_items 异常: {e}")

    # 4. 整体序列化 / 重名
    try:
        json.dumps(tools, ensure_ascii=False)
    except Exception as e:
        fails.append(f"TOOLS 序列化失败: {e}")
    names = [t["function"]["name"] for t in tools]
    if len(names) != len(set(names)):
        fails.append("存在重名工具")

    # 5. 描述长度
    for t in tools:
        d = t["function"]["description"]
        if len(d) > 130:
            fails.append(f"{t['function']['name']}: 描述 {len(d)} 字超 130")

    # 6. 数组参数 items
    for t in tools:
        for pn, pv in t["function"]["parameters"].get("properties", {}).items():
            if pv.get("type") == "array" and "items" not in pv:
                fails.append(f"{t['function']['name']}: {pn} 缺 items")

    # 7. activate_tools 点菜工具
    act = ns.get("ACTIVATE_TOOL") or {}
    if act.get("function", {}).get("name") != "activate_tools":
        fails.append("ACTIVATE_TOOL 缺失或结构异常")

    if fails:
        print(f"验证失败：{len(fails)} 个问题")
        for f in fails:
            print(f"  FAIL  {f}")
        return 1
    print(f"验证通过：{len(tools)} 个工具全链路正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
