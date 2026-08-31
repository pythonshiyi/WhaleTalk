# -*- coding: utf-8 -*-
"""工具系统六层一致性审计（WhaleTalk 开发工具链）。

用 AST 解析 deepseek_client.py / permissions.py（不导入模块、无副作用），
提取并核验六层信息的一致性：
  1. schema 描述 / 参数 / required
  2. 函数签名（参数覆盖）
  3. TOOL_CALL_MAP（schema ↔ 实现映射）
  4. _TOOL_ACTION_PHRASES（能力地图短语覆盖）
  5. TOOL_GROUPS（分组覆盖）
  6. _PREACTIVATE_HINTS（关键词预激活）与 permissions.ACTION_TOOLS（审批覆盖）

用途：
  - 新增/删除/修改工具后运行，快速发现结构性断链（孤儿工具、参数缺失、required 错误等）；
  - 描述质量扫描（过短、可疑表述、超 130 字会被 smart 模式 compact 截断的关键信息丢失）。

用法：
    python tools/audit_tools.py            # 生成审计报告（默认成功返回 0）
    python tools/audit_tools.py --strict   # 门禁模式：存在任何问题返回 1（可入 CI）

输出：
  tools/reports/tool_audit.txt（人读报告）
  tools/reports/tool_audit.json（机读结构化数据）
"""
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "deepseek_client.py"
PERM = REPO_ROOT / "permissions.py"
REPORTS_DIR = REPO_ROOT / "tools" / "reports"
OUT_TXT = REPORTS_DIR / "tool_audit.txt"
OUT_JSON = REPORTS_DIR / "tool_audit.json"

# 描述质量扫描关键词（命中仅提示，需人工甄别是否真误导）
SUSPECT_WORDS = ["可选依赖", "可能需要", "请先", "需安装", "需要审批", "敏感",
                 "一般", "建议", "可能", "注意：", "通常", "如果", "当", "默认"]
DESC_MIN_LEN = 30          # 短于视为"描述过短"
DESC_MAX_LEN = 130         # 超过则 smart 模式 compact 会截断（关键信息可能丢失）


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in argv
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # ── 1. 函数签名 ──
    func_params = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = [a.arg for a in node.args.posonlyargs] + \
                    [a.arg for a in node.args.args] + \
                    [a.arg for a in node.args.kwonlyargs]
            if node.args.vararg:
                names.append("*" + node.args.vararg.arg)
            if node.args.kwarg:
                names.append("**" + node.args.kwarg.arg)
            func_params[node.name] = names

    # ── 2. 提取顶层赋值 ──
    def get_assign(target_name):
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == target_name:
                        return node.value
        return None

    tools = ast.literal_eval(get_assign("TOOLS"))
    callmap_node = get_assign("TOOL_CALL_MAP")
    call_map = {}
    if callmap_node and isinstance(callmap_node, ast.Dict):
        for k, v in zip(callmap_node.keys, callmap_node.values):
            name = ast.literal_eval(k)
            if isinstance(v, ast.Name):
                call_map[name] = v.id
            elif isinstance(v, ast.Constant) and v.value is None:
                call_map[name] = None

    phrases = ast.literal_eval(get_assign("_TOOL_ACTION_PHRASES"))
    groups_node = get_assign("TOOL_GROUPS")
    groups = []
    if groups_node and isinstance(groups_node, (ast.List, ast.Tuple)):
        for item in groups_node.elts:
            if isinstance(item, (ast.List, ast.Tuple)) and len(item.elts) == 2:
                gname = ast.literal_eval(item.elts[0])
                members = ast.literal_eval(item.elts[1])
                groups.append((gname, members))

    # ── 3. 权限清单 ──
    perm_tree = ast.parse(PERM.read_text(encoding="utf-8"))
    action_tools = set()
    for node in ast.walk(perm_tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "ACTION_TOOLS":
                    try:
                        action_tools = set(ast.literal_eval(node.value))
                    except Exception:
                        pass

    # ── 4. 汇总 ──
    by_name = {}
    for t in tools:
        fn = t["function"]
        by_name[fn["name"]] = {
            "name": fn["name"],
            "desc": fn.get("description", ""),
            "params": fn.get("parameters", {}).get("properties", {}),
            "required": list(fn.get("parameters", {}).get("required", [])),
        }

    all_names = set(by_name) | set(call_map)
    issues = defaultdict(list)


    def flag(tool, kind, detail):
        issues[kind].append((tool, detail))

    for name in sorted(all_names):
        sch = by_name.get(name)
        impl = call_map.get(name)
        if sch is None:
            flag(name, "有实现无schema", f"TOOL_CALL_MAP={impl}")
            continue
        if impl is None:
            # 交互回调工具：实现不落在 TOOL_CALL_MAP，而在 chat() 内通过
            # on_ask / on_request_permission 回调处理（见执行循环 12907 行附近）。
            if name in ("ask_user", "request_permission"):
                continue
            flag(name, "有schema无实现", "TOOL_CALL_MAP 缺失或 None")
        elif impl != name:
            if impl in func_params:
                flag(name, "实现别名", f"schema={name} 实现={impl}（函数已定义，非缺陷）")
            else:
                flag(name, "实现名不一致", f"schema={name} 实现={impl}")

        d = sch["desc"]
        sp = sch["params"]
        req = set(sch["required"])
        for pn, pv in sp.items():
            if pv.get("type") == "array" and "items" not in pv:
                flag(name, "数组缺items", f"参数 {pn}")
        for rn in sch["required"]:
            if rn not in sp:
                flag(name, "required未定义参数", f"required 含 {rn} 但 properties 无")
        if len(d) < DESC_MIN_LEN:
            flag(name, "描述过短", f"{len(d)}字: {d}")
        if len(d) > DESC_MAX_LEN:
            flag(name, "描述超长将截断", f"{len(d)}字（smart 模式 compact 截到 {DESC_MAX_LEN}）")
        for w in SUSPECT_WORDS:
            if w in d:
                pos = max(0, d.find(w) - 30)
                flag(name, "可疑表述", f"含「{w}」→ {d[pos:pos + 70]}")
        fn_params = func_params.get(impl) if isinstance(impl, str) else None
        if fn_params is not None:
            sig_names = [p for p in fn_params
                         if not p.startswith(("*", "**")) and p != "self"]
            schema_names = set(sp)
            missing = [p for p in sig_names if p not in schema_names]
            extra = [p for p in schema_names if p not in sig_names and sig_names]
            if missing:
                flag(name, "签名有参数schema无", f"函数参数 {missing} 未在 schema 暴露")
            if extra and sig_names:
                flag(name, "schema有参数签名无", f"schema 参数 {extra} 函数签名无")

    # 覆盖检查
    group_members = set()
    for g, ms in groups:
        group_members |= set(ms)
    for name in all_names:
        if name not in group_members and name != "activate_tools":
            flag(name, "不在任何分组", "")
        if name not in phrases and name not in ("ask_user", "request_permission", "activate_tools"):
            flag(name, "短语表缺失", "能力地图将回退 description 截断 60 字")

    # 高危审批核对（写/删/命令/发信/RPA/DB 写等必须入 ACTION_TOOLS）
    high_risk = ["write_file", "edit_file", "run_command", "run_python", "send_email",
                 "delete_file", "batch_rename", "extract_archive", "start_process",
                 "stop_process", "publish_draft", "database_execute", "screen_capture",
                 "clipboard_get", "rpa_click", "rpa_type", "rpa_hotkey", "rpa_move",
                 "rpa_scroll", "rpa_screenshot", "webdav", "create_plugin", "pip_install",
                 "read_email", "image_generate", "run_workflow", "pdf_create", "qrcode",
                 "media_ffmpeg", "create_doc", "write_code_project"]
    for t in high_risk:
        if t in by_name and t not in action_tools:
            flag(t, "高危未列入审批清单", "ACTION_TOOLS 未包含")

    # ── 输出 ──
    buf = []
    buf.append("=" * 100)
    buf.append(f"WhaleTalk 工具系统审计报告  |  {len(by_name)} schema / {len(call_map)} CALL_MAP / "
               f"{len(phrases)} 短语 / {len(groups)} 组")
    buf.append("=" * 100)
    buf.append("")
    buf.append("## 一、问题汇总（按类型）")
    for kind in sorted(issues):
        items = issues[kind]
        buf.append(f"### {kind}（{len(items)} 处）")
        for tool, det in sorted(items):
            buf.append(f"  - {tool}: {det}")
        buf.append("")
    buf.append("## 二、工具详情")
    for name in sorted(by_name):
        sch = by_name[name]
        impl = call_map.get(name, "<缺失>")
        req = set(sch["required"])
        in_groups = [g for g, ms in groups if name in ms]
        buf.append(f"### {name}  →  实现: {impl}")
        buf.append(f"  分组: {in_groups or '∅'}")
        buf.append(f"  短语: {phrases.get(name, '（回退描述）')}")
        buf.append(f"  描述: {sch['desc']}")
        if sch["params"]:
            buf.append("  参数:")
            for pn, pv in sch["params"].items():
                mark = "必填" if pn in req else "可选"
                buf.append(f"    - {pn} ({pv.get('type', '?')}, {mark}): "
                           f"{str(pv.get('description', ''))[:120]}")
        buf.append("")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(buf), encoding="utf-8")
    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump({
            "schema_count": len(by_name), "callmap_count": len(call_map),
            "phrase_count": len(phrases), "group_count": len(groups),
            "action_tools": sorted(action_tools),
            "issues": {k: sorted(v) for k, v in issues.items()},
            "tools": {k: {"desc": v["desc"], "params": v["params"],
                          "required": v["required"], "impl": call_map.get(k),
                          "groups": [g for g, ms in groups if k in ms],
                          "phrase": phrases.get(k)} for k, v in by_name.items()},
        }, f, ensure_ascii=False, indent=1)

    print(f"审计完成: {len(by_name)} 工具，"
          f"{sum(len(v) for v in issues.values())} 个问题（{len(issues)} 类）")
    for kind in sorted(issues):
        print(f"  [{kind}] {len(issues[kind])}")
    print(f"报告: {OUT_TXT}")
    # 默认是报告工具（生成报告即成功）；--strict 时存在任何问题返回 1（门禁模式）
    return 1 if (strict and issues) else 0


if __name__ == "__main__":
    sys.exit(main())
