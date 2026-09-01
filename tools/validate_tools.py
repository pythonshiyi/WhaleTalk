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
  7. activate_tools 描述自包含（组名 + 反「能力错觉」约束）
  8. build_smart_hint 精简能力提示可生成且含能力总数

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

# P1-3 迁移后 TOOLS/TOOL_GROUPS/_TOOL_ACTION_PHRASES 由构建调用生成，
# 不能直接 exec；经 toolkit.rebuild_layers() AST 重建后预置进命名空间
# （CI 不装依赖，不能 import deepseek_client）。
sys.path.insert(0, str(REPO_ROOT))
import toolkit


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
    # 六层构建产物（TOOLS/TOOL_GROUPS/_TOOL_ACTION_PHRASES）预置自 rebuild_layers，
    # 不进入 exec block（其赋值是 build_* 调用，直接 exec 会 NameError）
    block += get_assign_nodes(src, tree, ["_TOOL_INDEX_CACHE", "_TOOL_INDEX_KEY", "ACTIVATE_TOOL",
                                          "_GROUP_NAMES_TEXT"])
    block += get_assign_nodes(src, tree, ["_TOOL_GROUP_NAME_MAP"])
    block += get_func_src(src, tree, ["build_tool_index", "compact_tool_schema",
                                      "compact_tools_list", "_patch_array_items",
                                      "_finalize_activate_tool", "build_smart_hint"])
    layers = toolkit.rebuild_layers(src)
    ns = {"re": re, "json": json, "__name__": "validate_block",
          "TOOLS": layers["TOOLS"],
          "TOOL_GROUPS": layers["TOOL_GROUPS"],
          "_TOOL_ACTION_PHRASES": layers["_TOOL_ACTION_PHRASES"]}
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

    # 7. activate_tools 点菜工具：描述必须自包含（能力地图降级后仍可点菜）
    act = ns.get("ACTIVATE_TOOL") or {}
    if act.get("function", {}).get("name") != "activate_tools":
        fails.append("ACTIVATE_TOOL 缺失或结构异常")
    try:
        ns["_finalize_activate_tool"]()
    except Exception as e:
        fails.append(f"_finalize_activate_tool 异常: {e}")
    act_desc = str((act.get("function") or {}).get("description") or "")
    grp_text = str(ns.get("_GROUP_NAMES_TEXT") or "")
    if grp_text and not any(g in act_desc for g in grp_text.split("、")[:3]):
        fails.append("activate_tools 描述未包含组名（能力地图移除后无法点菜）")
    if "不要因为" not in act_desc:
        fails.append("activate_tools 描述缺少反「能力错觉」约束")

    # 8. 精简能力提示（完整地图降级后的常驻替代）
    try:
        hint = ns["build_smart_hint"](["read_file", "write_file"], tools)
        if not isinstance(hint, str) or len(hint) < 50:
            fails.append("build_smart_hint 生成内容过短")
        if str(len(tools)) not in hint:
            fails.append("build_smart_hint 未包含能力总数")
        if "组名" not in hint:
            fails.append("build_smart_hint 未包含组名指引")
        if len(hint) > 1200:
            fails.append(f"build_smart_hint 过长（{len(hint)} 字），失去省 token 意义")
    except Exception as e:
        fails.append(f"build_smart_hint 异常: {e}")

    if fails:
        print(f"验证失败：{len(fails)} 个问题")
        for f in fails:
            print(f"  FAIL  {f}")
        return 1
    print(f"验证通过：{len(tools)} 个工具全链路正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
