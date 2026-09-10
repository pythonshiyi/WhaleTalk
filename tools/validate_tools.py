# -*- coding: utf-8 -*-
"""工具系统功能级验证（WhaleTalk 开发工具链回归测试）。

用 AST 提取 deepseek_client.py 的真实源码块执行（不导入模块、无副作用），
验证 smart_tools 全链路可运行：
  1. build_tool_index 能力地图可生成
  2. normalize_tool_schema 对全部工具可执行、结果合法，且**无损**
     （只允许空白归一，不得删除/截断任何描述——见 deepseek_client 中的说明）
  3. _patch_array_items 能兜底补齐缺失的 items
  4. TOOLS 整体 JSON 可序列化、无重名
  5. 描述保真：工具描述足以指导调用、参数描述 100% 覆盖
     （**不校验长度上限**——描述是工具能力的一部分，不以省 token 为由删减）
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
TOOL_DIR = REPO_ROOT / "agent_tools"

# 描述低于此长度视为"不足以指导模型调用"（硬拦截）。
# 注意：这里**没有**描述长度上限——历史上曾有"≤130 字"的硬门禁，
# 理由是"smart 模式 compact 会截断"，但该截断本身已被移除（有损且收益≈0），
# 上限随之取消。详见 deepseek_client.normalize_tool_schema 的说明。
DESC_MIN_LEN = 20

# P1-3 迁移后 TOOLS/TOOL_GROUPS/_TOOL_ACTION_PHRASES 由构建调用生成，
# 不能直接 exec；经 toolkit.rebuild_layers() AST 重建后预置进命名空间
# （CI 不装依赖，不能 import deepseek_client）。
sys.path.insert(0, str(REPO_ROOT))
import toolkit


def tool_sources():
    """P0-1 拆分：收集 agent_tools/ 域模块源码（不含 __init__ 聚合文件）。"""
    if not TOOL_DIR.is_dir():
        return []
    return [p.read_text(encoding="utf-8")
            for p in sorted(TOOL_DIR.glob("*.py")) if p.name != "__init__.py"]


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
    block += get_func_src(src, tree, ["build_tool_index", "_normalize_desc",
                                      "normalize_tool_schema",
                                      "normalize_tools_list", "_patch_array_items",
                                      "_finalize_activate_tool", "build_smart_hint"])
    layers = toolkit.rebuild_layers(src, *tool_sources())
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

    # 2. 规范化（原 compact）全量：必须**无损**——只允许空白归一，不得删除或截断任何内容。
    #    这条取代了旧的「compact 后 ≤130 字」检查（旧检查既允许有损截断，
    #    又因 [[:130] + "…"] 的 off-by-one 恒产出 131 字而自相矛盾）。
    def _sq(s):
        """去空白指纹：判定 compact 是否改动了实义内容。"""
        return re.sub(r"\s+", "", str(s or ""))

    for t in tools:
        try:
            c = ns["normalize_tool_schema"](t)
            json.dumps(c, ensure_ascii=False)
            name = t["function"]["name"]
            if _sq(t["function"].get("description")) != _sq(c["function"].get("description")):
                fails.append(f"{name}: compact 改动了工具描述内容（应为无损）")
            src_props = t["function"].get("parameters", {}).get("properties") or {}
            dst_props = c["function"].get("parameters", {}).get("properties") or {}
            for pn, pv in src_props.items():
                if _sq(pv.get("description")) != _sq((dst_props.get(pn) or {}).get("description")):
                    fails.append(f"{name}.{pn}: compact 改动了参数描述内容（应为无损）")
            for pn, pv in dst_props.items():
                if pv.get("type") == "array" and "items" not in pv:
                    fails.append(f"{name}: {pn} 缺 items")
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

    # 5. 描述保真与覆盖：**不设长度上限**（描述是工具能力的一部分，不以省 token
    #    为由删减）——只要求「工具描述足以指导调用」+「参数描述 100% 覆盖」，
    #    后者是模型能否正确填参的前提，此前完全无门禁。
    for t in tools:
        name = t["function"]["name"]
        d = str(t["function"].get("description") or "").strip()
        if len(d) < DESC_MIN_LEN:
            fails.append(f"{name}: 描述过短（{len(d)} 字 < {DESC_MIN_LEN}），不足以指导模型调用")
        for pn, pv in (t["function"].get("parameters", {}).get("properties") or {}).items():
            if not str(pv.get("description") or "").strip():
                fails.append(f"{name}.{pn}: 参数缺描述（模型无法知道如何填该参数）")

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
