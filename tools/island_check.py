# -*- coding: utf-8 -*-
"""工具系统全链路「孤岛」对账（WhaleTalk 开发工具链）。

对 9 层信息做差集对账，确保每个工具在每一层都可被发现/可被调用，
不存在「有实现但模型看不到 / 有 schema 但调不动 / UI 归不了类」的孤岛：

  1. TOOLS schema ↔ TOOL_CALL_MAP 实现映射
  2. 实现函数是否真的已定义（映射指向不存在的函数 = 死链）
  3. TOOL_GROUPS 能力地图分组（smart_tools 点菜依据）
  4. _TOOL_ACTION_PHRASES 动作短语（能力地图每项的描述）
  5. _PREACTIVATE_HINTS 关键词预激活（免点菜入口）
  6. api_server._TOOL_DOMAIN 能力中心 UI 域
  7. config.json enabled_tools 幽灵启用
  8. SELF_EVOLUTION_TOOLS 存在性
  9. config_defaults.BUILTIN_TOOL_NAMES 存在性

用法：
    python tools/island_check.py            # 对账并输出报告（默认返回 0）
    python tools/island_check.py --strict   # 门禁模式：存在任何缺口返回 1

说明：ask_user / request_permission 为交互回调工具（chat() 内走 on_ask /
on_request_permission），不在 TOOL_CALL_MAP，不计为缺口。
"""
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DC = REPO_ROOT / "deepseek_client.py"
AS = REPO_ROOT / "api_server.py"
CFG = REPO_ROOT / "config.json"
CD = REPO_ROOT / "config_defaults.py"

# P1-3 迁移后 deepseek_client 六层由 @tool() 声明生成，AST 重建（不 import 模块）
sys.path.insert(0, str(REPO_ROOT))
import toolkit

# 交互回调工具：实现不落在 TOOL_CALL_MAP（chat() 内 on_ask/on_request_permission 通道）
CALLBACK_TOOLS = {"ask_user", "request_permission"}


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _get_assign(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return node.value
    return None


def _collect_dict(dict_node):
    """AST Dict → python dict（键/值为字面量或 Name）。"""
    out = {}
    if not isinstance(dict_node, ast.Dict):
        return out
    for k, v in zip(dict_node.keys, dict_node.values):
        try:
            key = ast.literal_eval(k)
        except Exception:
            continue
        if isinstance(v, ast.Name):
            out[key] = v.id
        elif isinstance(v, ast.Constant):
            out[key] = v.value
        else:
            try:
                out[key] = ast.literal_eval(v)
            except Exception:
                out[key] = None
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in argv

    dc_tree = _parse(DC)
    layers = toolkit.rebuild_layers(DC.read_text(encoding="utf-8"))
    tool_names = {t["function"]["name"] for t in layers["TOOLS"]}

    call_map = layers["TOOL_CALL_MAP"]
    defined = {n.name for n in ast.walk(dc_tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    group_members = {m for _, ms in layers["TOOL_GROUPS"] for m in ms}
    phrases = set(layers["_TOOL_ACTION_PHRASES"])
    pre_covered = {t for _, ts in layers["_PREACTIVATE_HINTS"] for t in ts}
    self_evo = set(ast.literal_eval(_get_assign(dc_tree, "SELF_EVOLUTION_TOOLS")))

    as_tree = _parse(AS)
    domain = set(_collect_dict(_get_assign(as_tree, "_TOOL_DOMAIN")))

    enabled = set(json.loads(CFG.read_text(encoding="utf-8")).get("enabled_tools") or [])
    builtin = set(ast.literal_eval(_get_assign(_parse(CD), "BUILTIN_TOOL_NAMES")))

    gaps = {}

    def add(kind, items):
        if items:
            gaps[kind] = sorted(items)

    add("有schema无实现映射", [t for t in tool_names if not call_map.get(t) and t not in CALLBACK_TOOLS])
    add("实现指向未定义函数", [impl for impl in call_map.values() if impl and impl not in defined])
    add("不在能力地图分组", [t for t in tool_names if t not in group_members and t not in CALLBACK_TOOLS])
    add("能力地图含幽灵工具", sorted(group_members - tool_names))
    add("缺动作短语", [t for t in tool_names if t not in phrases and t not in CALLBACK_TOOLS])
    add("短语含幽灵工具", sorted(phrases - tool_names - {"activate_tools"}))
    add("无预激活关键词", [t for t in tool_names - pre_covered - CALLBACK_TOOLS])
    add("预激活含幽灵工具", sorted(pre_covered - tool_names))
    add("UI 能力域缺失", [t for t in tool_names - domain])
    add("UI 能力域幽灵键", sorted(domain - tool_names))
    add("enabled_tools 幽灵启用", sorted(enabled - tool_names))
    add("SELF_EVOLUTION 幽灵", sorted(self_evo - tool_names))
    add("BUILTIN 幽灵", sorted(builtin - tool_names))

    print("=" * 64)
    print(f"孤岛对账: {len(tool_names)} 工具 × 9 层")
    print("=" * 64)
    if not gaps:
        print("✅ 全部联通：无孤岛")
    for kind, items in gaps.items():
        print(f"\n[{kind}] {len(items)} 个")
        for it in items:
            print(f"  - {it}")
    print(f"\n报告: {'发现 ' + str(sum(len(v) for v in gaps.values())) + ' 处缺口' if gaps else '无缺口'}")
    return 1 if (strict and gaps) else 0


if __name__ == "__main__":
    sys.exit(main())
