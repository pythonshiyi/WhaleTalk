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

严重度分级（v3.8.3 起）：
  - **error（拦截）**：结构性断链——工具不可达、schema 与实现不匹配、参数/分组/审批清单缺口。
    这类问题会让工具系统真的坏掉，`--strict` 必须拦截。schema 与实现名不一致且未登记
    KNOWN_ALIASES 的，按 error「实现名不一致」处理。
  - **warn（仅提示）**：描述质量类（过长/过短/含依赖门槛语）。
    这类需人工甄别是否为真问题，**不拦截 CI**——否则中文常用字
    （「默认」「当」「建议」「可能」）会触发大量误报，把门禁变成噪声。

用法：
    python tools/audit_tools.py            # 生成审计报告（默认成功返回 0）
    python tools/audit_tools.py --strict   # 门禁模式：存在 error 级问题返回 1（可入 CI）
    python tools/audit_tools.py --warnings-as-errors
                                           # 连 warn 级也拦截（人工清理描述时用）

输出：
  tools/reports/tool_audit.txt（人读报告）
  tools/reports/tool_audit.json（机读结构化数据）
"""
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "deepseek_client.py"
TOOL_DIR = REPO_ROOT / "agent_tools"
PERM = REPO_ROOT / "permissions.py"
REPORTS_DIR = REPO_ROOT / "tools" / "reports"
OUT_TXT = REPORTS_DIR / "tool_audit.txt"
OUT_JSON = REPORTS_DIR / "tool_audit.json"

# P1-3 迁移后六层由 @tool() 声明生成，AST 重建（不 import 模块，CI 无依赖）
sys.path.insert(0, str(REPO_ROOT))
import toolkit


def audit_source_files():
    """P0-1 拆分：主文件 + agent_tools/ 域模块（不含 __init__ 聚合文件）。"""
    files = [SRC]
    if TOOL_DIR.is_dir():
        files += [p for p in sorted(TOOL_DIR.glob("*.py")) if p.name != "__init__.py"]
    return files

# ── 描述质量扫描（warn 级，命中仅提示，需人工甄别是否真误导）──
# 用"短语 + 正则"而非单字/泛词匹配：早期版本用 ["一般","建议","可能","通常",
# "如果","当","默认"] 这类中文常用字，135 个工具里命中 30 处几乎全是误报
# （如「获取当前日期」的"当"、「默认移入回收站」的"默认"），门禁形同虚设。
# 现只保留真正影响"这个工具现在能不能用"的提示语：依赖缺失、需审批、前置条件。
SUSPECT_PATTERNS = [
    r"可选依赖",
    r"需(?:先|要)?安装",
    r"未安装",
    r"需要审批",
    r"请先",
    r"可能需",
    r"可能会失败",
    r"视情况",
    r"尽量",
]
_SUSPECT_RE = [re.compile(p) for p in SUSPECT_PATTERNS]
DESC_MIN_LEN = 30          # 短于视为"描述过短"
DESC_MAX_LEN = 130         # 超过则 smart 模式 compact 会截断（关键信息可能丢失）

# ── 严重度分类 ──
# error：结构性断链，会让工具真的不可用/不可达，--strict 必须拦截
ERROR_KINDS = {
    "有实现无schema", "有schema无实现", "实现名不一致",
    "数组缺items", "required未定义参数",
    "签名有参数schema无", "schema有参数签名无",
    "不在任何分组", "短语表缺失", "高危未列入审批清单",
    "预激活未覆盖",
}
# warn：描述质量提示（描述过短/过长/含依赖门槛语），需人工甄别，不拦截 CI。
# 实现别名不在此列：登记进 KNOWN_ALIASES 即豁免（有意为之，静默接受），
# 未登记的 schema↔实现名差异一律按 error「实现名不一致」拦截（--strict 可挡）。
WARN_KINDS = {"描述过短", "描述超长将截断", "可疑表述"}

# 已知且无害的实现别名：schema 名与实现函数名不同，但函数确实存在（非缺陷）。
# 登记后审计静默接受（不再产生 warn）；新增别名时在此登记，避免每次审计都产生噪声。
# 注意：未登记的差异会按 error「实现名不一致」拦截，故登记即代表"已人工确认有意"。
KNOWN_ALIASES = {
    "fetch_blocked": "_run_fetch_blocked",   # fetch_blocked 是保留字冲突，实现另起名
    "git": "git_tool",                       # git 与内部变量名冲突
}


def _hint_membership(name, hints):
    """工具名是否出现在第 6 层任一组预激活关键词的成员中。

    hints 结构（toolkit.build_preactivate）：[(关键词元组, [工具名...]), ...]，
    同一关键词的多个工具聚合为一条。返回 bool。
    """
    return any(name in ms for _, ms in hints)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in argv
    src_files = audit_source_files()
    src = src_files[0].read_text(encoding="utf-8")

    # ── 1. 函数签名（主文件 + agent_tools 域模块）──
    func_params = {}
    for pf in src_files:
        tree = ast.parse(pf.read_text(encoding="utf-8"))
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

    # ── 2. 六层数据：P1-3 迁移后由 @tool() 声明生成，AST 重建 ──
    layers = toolkit.rebuild_layers(
        src, *[p.read_text(encoding="utf-8") for p in src_files[1:]])
    tools = layers["TOOLS"]
    call_map = layers["TOOL_CALL_MAP"]          # 值 = 实现函数名（字符串）或 None
    phrases = layers["_TOOL_ACTION_PHRASES"]
    groups = layers["TOOL_GROUPS"]              # [(组名, [成员...])]
    hints = layers["_PREACTIVATE_HINTS"]        # [(关键词元组, [工具名...])] 第 6 层（audit_preactivate_hints 提案）

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
                if KNOWN_ALIASES.get(name) == impl:
                    pass    # 已登记别名：有意为之，静默接受（登记即豁免，见 KNOWN_ALIASES 注释）
                else:
                    flag(name, "实现名不一致",
                         f"schema={name} 实现={impl}（函数已定义但未登记 KNOWN_ALIASES，"
                         f"请确认是否有意，并登记到 KNOWN_ALIASES）")
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
        for rx in _SUSPECT_RE:
            m = rx.search(d)
            if m:
                pos = max(0, m.start() - 30)
                flag(name, "可疑表述", f"命中 /{rx.pattern}/ → {d[pos:pos + 70]}")
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
        # 第 6 层 preactivate 覆盖（audit_preactivate_hints 提案）：交互回调工具豁免；
        # 建议进入预激活的其余工具必须有声明，防"工具加进系统但忘了挂预激活关键词"的静默漂移
        if name not in ("ask_user", "request_permission", "activate_tools"):
            if not _hint_membership(name, hints):
                flag(name, "预激活未覆盖",
                     "_PREACTIVATE_HINTS 未收录，将无法通过口语关键词被预激活")

    # 高危审批核对（写/删/命令/发信/RPA/DB 写等必须入 ACTION_TOOLS）
    # L8: rpa_screenshot 属只读"看屏幕"（可保存到工作区但语义只读），已从审批清单降级，此处不同步要求
    high_risk = ["write_file", "edit_file", "run_command", "run_python", "send_email",
                 "delete_file", "batch_rename", "extract_archive", "start_process",
                 "stop_process", "publish_draft", "database_execute", "screen_capture",
                 "clipboard_get", "rpa_click", "rpa_type", "rpa_hotkey", "rpa_move",
                 "rpa_scroll", "webdav", "create_plugin", "pip_install",
                 "read_email", "image_generate", "run_workflow", "pdf_create", "qrcode",
                 "media_ffmpeg", "create_doc", "write_code_project"]
    for t in high_risk:
        if t in by_name and t not in action_tools:
            flag(t, "高危未列入审批清单", "ACTION_TOOLS 未包含")

    # ── 输出 ──
    def severity(kind):
        if kind in ERROR_KINDS:
            return "error"
        if kind in WARN_KINDS:
            return "warn"
        return "error"      # 未知类型一律从严，防止新规则漏分级

    err_issues = {k: v for k, v in issues.items() if severity(k) == "error"}
    warn_issues = {k: v for k, v in issues.items() if severity(k) == "warn"}
    n_err = sum(len(v) for v in err_issues.values())
    n_warn = sum(len(v) for v in warn_issues.values())

    buf = []
    buf.append("=" * 100)
    buf.append(f"WhaleTalk 工具系统审计报告  |  {len(by_name)} schema / {len(call_map)} CALL_MAP / "
               f"{len(phrases)} 短语 / {len(groups)} 组 / {len(hints)} 预激活组")
    buf.append(f"error {n_err} 处（拦截 CI）　|　warn {n_warn} 处（仅提示，需人工甄别）")
    buf.append("=" * 100)
    buf.append("")
    buf.append("## 一、error：结构性断链（--strict 拦截）")
    if err_issues:
        for kind in sorted(err_issues):
            buf.append(f"### {kind}（{len(err_issues[kind])} 处）")
            for tool, det in sorted(err_issues[kind]):
                buf.append(f"  - {tool}: {det}")
    else:
        buf.append("（无）")
    buf.append("")
    buf.append("## 二、warn：质量提示（需人工甄别，不拦截 CI）")
    if warn_issues:
        for kind in sorted(warn_issues):
            buf.append(f"### {kind}（{len(warn_issues[kind])} 处）")
            for tool, det in sorted(warn_issues[kind]):
                buf.append(f"  - {tool}: {det}")
    else:
        buf.append("（无）")
    buf.append("")
    buf.append("## 三、工具详情")
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

    issues_with_sev = {k: {"severity": severity(k), "items": sorted(v)}
                       for k, v in issues.items()}
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(buf), encoding="utf-8")
    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump({
            "schema_count": len(by_name), "callmap_count": len(call_map),
            "phrase_count": len(phrases), "group_count": len(groups),
            "hint_count": len(hints),
            "action_tools": sorted(action_tools),
            "error_count": n_err, "warn_count": n_warn,
            "issues": issues_with_sev,
            "tools": {k: {"desc": v["desc"], "params": v["params"],
                          "required": v["required"], "impl": call_map.get(k),
                          "groups": [g for g, ms in groups if k in ms],
                          "phrase": phrases.get(k)} for k, v in by_name.items()},
        }, f, ensure_ascii=False, indent=1)

    print(f"审计完成: {len(by_name)} 工具，"
          f"error {n_err} 处（{len(err_issues)} 类）· warn {n_warn} 处（{len(warn_issues)} 类）")
    if warn_issues:
        for kind in sorted(warn_issues):
            print(f"  [warn] {kind}: {len(warn_issues[kind])}")
    if err_issues:
        for kind in sorted(err_issues):
            print(f"  [error] {kind}: {len(err_issues[kind])}")
    print(f"报告: {OUT_TXT}")
    # 默认是报告工具（生成报告即成功）；--strict 时存在 error 级问题返回 1（门禁模式）；
    # --warnings-as-errors 时连 warn 级也拦截（清理描述质量的强模式）
    if not strict:
        return 0
    if "--warnings-as-errors" in argv:
        return 1 if issues else 0
    return 1 if err_issues else 0


if __name__ == "__main__":
    sys.exit(main())
