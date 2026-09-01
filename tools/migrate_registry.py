#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性迁移脚本：deepseek_client.py 六层手工注册表 → @tool() 单一来源（P1-3）。

做什么：
  1. AST 解析旧六层（TOOLS / TOOL_CALL_MAP / TOOL_GROUPS / _TOOL_ACTION_PHRASES /
     _PREACTIVATE_HINTS）与全部工具函数定义位置；
  2. 在每个工具函数定义前插入 @tool(...) 装饰器（schema 文本原样迁移，零改动）；
  3. 特殊工具（ask_user/request_permission 无执行函数）改为 register_tool；
  4. 删除原六层字面量，在原 TOOL_CALL_MAP 位置生成构建区
     （_TOOL_ORDER/_GROUP_ORDER/_HINT_ORDER + 五个构建调用）；
  5. 顶部注入 toolkit import；
  6. 静态等价性校验：模拟注册后 build 出的六层与原数据 deep-equal。
     （例外：TOOL_GROUPS / _PREACTIVATE_HINTS 的**成员顺序**有意统一为 TOOLS
     列表顺序——历史字面量有独立的第三套手工顺序，纯展示性质无功能依赖，
     重构时消除这套漂移源；校验按「顺序表一致 + 成员集合一致」进行。）

用法：python tools/migrate_registry.py
安全：改写前自动备份 deepseek_client.py.bak_registry（同目录）。
"""
import ast
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "deepseek_client.py"
BAK = SRC.with_name("deepseek_client.py.bak_registry")


def dict_get(d, key):
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    return None


def find_assign(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node
    return None


def main():
    old_text = SRC.read_text(encoding="utf-8")
    old_lines = old_text.splitlines(keepends=True)
    tree = ast.parse(old_text)

    def ntext(node):
        return "".join(old_lines[node.lineno - 1 : node.end_lineno])

    # ── 1. TOOLS：name → (schema 原始文本) ──
    tools_node = find_assign(tree, "TOOLS")
    tools_raw = {}
    tools_order = []
    for item in tools_node.value.elts:
        fn = dict_get(item, "function")
        nm = dict_get(fn, "name")
        name = nm.value
        tools_raw[name] = ntext(item)
        tools_order.append(name)
    print(f"[1] TOOLS: {len(tools_order)} 个工具")

    # ── 2. TOOL_GROUPS：name → [组名]，组顺序 ──
    groups_node = find_assign(tree, "TOOL_GROUPS")
    name_groups = {}
    group_order = []
    for e in groups_node.value.elts:
        cat = e.elts[0].value
        members = [e.elts[1].elts[i].value for i in range(len(e.elts[1].elts))]
        group_order.append(cat)
        for m in members:
            name_groups.setdefault(m, []).append(cat)
    print(f"[2] TOOL_GROUPS: {len(group_order)} 组")

    # ── 3. _TOOL_ACTION_PHRASES ──
    phrases_node = find_assign(tree, "_TOOL_ACTION_PHRASES")
    phrases = {
        k.value: v.value
        for k, v in zip(phrases_node.value.keys, phrases_node.value.values)
        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)
    }
    print(f"[3] _TOOL_ACTION_PHRASES: {len(phrases)} 条")

    # ── 4. _PREACTIVATE_HINTS：name → [关键词元组]，提示顺序 ──
    hints_node = find_assign(tree, "_PREACTIVATE_HINTS")
    name_hints = {}
    hint_order = []
    for e in hints_node.value.elts:
        kws = tuple(e.elts[0].elts[i].value for i in range(len(e.elts[0].elts)))
        members = [e.elts[1].elts[i].value for i in range(len(e.elts[1].elts))]
        hint_order.append(kws)
        for t in members:
            name_hints.setdefault(t, []).append(kws)
    print(f"[4] _PREACTIVATE_HINTS: {len(hint_order)} 条提示")

    # ── 5. TOOL_CALL_MAP：name → executor（None=特殊处理）──
    map_node = find_assign(tree, "TOOL_CALL_MAP")
    exec_src = {}
    for k, v in zip(map_node.value.keys, map_node.value.values):
        name = k.value
        if isinstance(v, ast.Constant) and v.value is None:
            exec_src[name] = None
        elif isinstance(v, ast.Name):
            exec_src[name] = v.id
        else:
            raise SystemExit(f"无法解析 TOOL_CALL_MAP 条目: {name} -> {ntext(v).strip()}")
    print(f"[5] TOOL_CALL_MAP: {len(exec_src)} 条；特殊(executor=None): {[n for n,e in exec_src.items() if e is None]}")

    # ── 6. 模块级函数定义行号 ──
    fn_lines = {
        n.name: n.lineno
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    # ── 7. 生成装饰器文本（普通工具 + 别名工具挂到执行函数）──
    decorators = {}  # def 行号(1-based) -> 装饰器文本
    registered = set()  # 已通过 @tool 注册的工具名
    alias_hit = {"fetch_blocked": "_run_fetch_blocked", "git": "git_tool"}

    def _deco_text(name, raw_schema):
        args = []
        gs = name_groups.get(name)
        if gs:
            args.append("    groups=" + repr(gs))
        ph = phrases.get(name)
        if ph is not None:
            args.append("    phrases=" + repr(ph))
        hs = name_hints.get(name)
        if hs:
            args.append("    preactivate=" + repr(tuple(tuple(h) for h in hs)))
        # raw_schema 以列表项结尾（`},`）：去掉尾逗号后统一补一个参数分隔逗号
        raw = raw_schema.rstrip().rstrip(",")
        schema_indent = "    " + raw.replace("\n", "\n    ")
        extra = (",\n" + ",\n".join(args)) if args else ""
        return "@tool(\n" + schema_indent + extra + ",\n)\n"

    for name in tools_order:
        ex = exec_src[name]
        if ex is None:
            continue  # ask_user / request_permission → register_tool（构建区）
        target = alias_hit.get(name, name)
        if target not in fn_lines:
            raise SystemExit(f"找不到 {name} 的执行函数 {target!r} 的定义行")
        if target in decorators:
            raise SystemExit(f"{target} 已生成装饰器（重复？）")
        decorators[fn_lines[target]] = _deco_text(name, tools_raw[name])
        registered.add(name)
    print(f"[7] 生成装饰器 {len(decorators)} 个，注册 {len(registered)} 个工具")

    register_text = ""
    for name in tools_order:
        if exec_src[name] is not None:
            continue
        register_text += (
            "register_tool(\n"
            + ("    " + tools_raw[name].rstrip().rstrip(",").replace("\n", "\n    "))
            + ",\n"
            + "".join(f"    {k}={v!r},\n" for k, v in [
                ("executor", None),
                ("groups", name_groups.get(name)),
                ("phrases", phrases.get(name)),
                ("preactivate", tuple(tuple(h) for h in name_hints.get(name, ()))),
            ] if v not in (None, [], ()))
            + ")\n"
        )
    if register_text:
        print(f"[7b] register_tool 块: {len(register_text)} 字符")

    # ── 8. 构建区文本 ──
    def fmt_names(names, per=8):
        return "[\n" + "".join(
            "    " + " ".join(f"{n!r}," for n in names[i : i + per]) + "\n"
            for i in range(0, len(names), per)
        ) + "]"

    def fmt_hints(hints, per=3):
        return "[\n" + "".join(
            "    " + " ".join(f"{h!r}," for h in hints[i : i + per]) + "\n"
            for i in range(0, len(hints), per)
        ) + "]"

    build_block = (
        "# ── 六层工具注册表（P1-3 单一来源）────────────────────────────────\n"
        "# TOOLS / TOOL_CALL_MAP / TOOL_GROUPS / _TOOL_ACTION_PHRASES /\n"
        "# _PREACTIVATE_HINTS 全部由 @tool() / register_tool() 声明生成（机制见 toolkit.py）；\n"
        "# 顺序常量（_TOOL_ORDER/_GROUP_ORDER/_HINT_ORDER）迁移自历史数据。\n"
        "# 新增/修改工具：只改函数定义处的装饰器，其余层自动同步；\n"
        "# 构建期一致性校验（重复名/顺序缺项/多余项）失败会直接抛错，早于任何 AST 门禁。\n\n"
        + register_text
        + "\n_TOOL_ORDER = " + fmt_names(tools_order) + "\n\n"
        + "_GROUP_ORDER = " + fmt_names(group_order, per=4) + "\n\n"
        + "_HINT_ORDER = " + fmt_hints(hint_order) + "\n\n"
        + "TOOLS = build_tool_list(_TOOL_ORDER)\n"
        + "TOOL_CALL_MAP = build_call_map()\n"
        + "TOOL_GROUPS = build_groups(_GROUP_ORDER, _TOOL_ORDER)\n"
        + "_TOOL_ACTION_PHRASES = build_phrases()\n"
        + "_PREACTIVATE_HINTS = build_preactivate(_HINT_ORDER, _TOOL_ORDER)\n"
    )

    # ── 9. 组装新文件（编辑点按行号降序处理）──
    new_lines = list(old_lines)

    def splice(start, end, text=None):
        """删除 [start,end)（1-based 含 start，不含 end），可替换为 text。"""
        si, ei = start - 1, end - 1
        if text is None:
            del new_lines[si:ei]
        else:
            new_lines[si:ei] = [text]

    # 9a. 删 _TOOL_ACTION_PHRASES
    splice(phrases_node.lineno, phrases_node.end_lineno + 1)
    # 9b. 删 _PREACTIVATE_HINTS
    splice(hints_node.lineno, hints_node.end_lineno + 1)
    # 9c. 删 TOOL_GROUPS
    splice(groups_node.lineno, groups_node.end_lineno + 1)
    # 9d. 替换 TOOL_CALL_MAP → 构建区
    splice(map_node.lineno, map_node.end_lineno + 1, build_block)
    # 9e. 插装饰器（从后往前）
    for lineno in sorted(decorators, reverse=True):
        insert_at = lineno - 1
        new_lines[insert_at:insert_at] = [decorators[lineno]]
    # 9f. 删 TOOLS 字面量
    splice(tools_node.lineno, tools_node.end_lineno + 1)
    # 9g. 顶部 import toolkit
    import_stmt = (
        "# P1-3 工具单一来源：@tool() 装饰器 + 六层注册表生成（toolkit.py）\n"
        "from toolkit import tool, register_tool, build_tool_list, build_call_map, build_groups, build_phrases, build_preactivate\n\n"
    )
    # 找到第一个顶层 FunctionDef / 最后 import 后插入（第 73 行 Import 后）
    anchor = None
    for i, node in enumerate(tree.body):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            anchor = node.lineno
            break
    insert_idx = anchor - 1 if anchor else 0
    new_lines[insert_idx:insert_idx] = [import_stmt]

    new_text = "".join(new_lines)

    # ── 10. 语法校验 ──
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        Path(SRC.parent / "gen_check.py").write_text(new_text, encoding="utf-8")
        raise SystemExit(f"生成文件语法错误 @{e.lineno}: {e.msg}（已保存 gen_check.py 供排查）")

    # ── 11. 静态等价性校验：模拟注册 → 构建 → 与原六层 deep-equal ──
    sys.path.insert(0, str(SRC.parent))
    import toolkit

    toolkit.clear_registry()
    new_tree = ast.parse(new_text)

    def arg_value(call_node, key, default=None):
        for kw in call_node.keywords:
            if kw.arg == key:
                return ast.literal_eval(kw.value)
        return default

    n_reg = 0
    for node in ast.walk(new_tree):
        # @tool(...) 装饰器
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "tool":
                    schema = ast.literal_eval(dec.args[0])
                    toolkit.register_tool(
                        schema,
                        groups=arg_value(dec, "groups"),
                        phrases=arg_value(dec, "phrases"),
                        preactivate=arg_value(dec, "preactivate"),
                        # @tool 的 executor 语义 = 被装饰函数本身；AST 模拟阶段
                        # 无真实函数对象，用函数名字符串标记，校验时按名字比对。
                        executor=node.name,
                    )
                    n_reg += 1
        # register_tool(...) 调用
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "register_tool" and node.args:
            schema = ast.literal_eval(node.args[0])
            toolkit.register_tool(
                schema,
                groups=arg_value(node, "groups"),
                phrases=arg_value(node, "phrases"),
                preactivate=arg_value(node, "preactivate"),
                executor=arg_value(node, "executor"),
            )
            n_reg += 1
    print(f"[11] 模拟注册 {n_reg} 个工具")

    order_node = find_assign(new_tree, "_TOOL_ORDER")
    group_order_node = find_assign(new_tree, "_GROUP_ORDER")
    hint_order_node = find_assign(new_tree, "_HINT_ORDER")
    t_order = ast.literal_eval(order_node.value)
    g_order = ast.literal_eval(group_order_node.value)
    h_order = [tuple(h) for h in ast.literal_eval(hint_order_node.value)]

    new_tools = toolkit.build_tool_list(t_order)
    new_map = toolkit.build_call_map()
    new_groups = toolkit.build_groups(g_order, t_order)
    new_phrases = toolkit.build_phrases()
    new_hints = toolkit.build_preactivate(h_order, t_order)

    # 与原数据对比
    # 注意：不能对 ntext(元素) 文本做 literal_eval——元素文本带列表尾逗号（`},\n`），
    # eval 模式会把它解析成单元素 tuple，导致与 dict 恒不等。直接对 AST 节点求值。
    old_tools = [ast.literal_eval(item) for item in tools_node.value.elts]
    try:
        assert new_tools == old_tools, "TOOLS 不一致！"
        # 键集合必须相等（build_call_map 的键顺序=注册顺序，与旧字面量顺序无关）
        assert set(new_map.keys()) == set(exec_src.keys()), "TOOL_CALL_MAP 键不一致！"
        for n in t_order:
            ex = exec_src[n]
            got = new_map[n]
            if ex is None:
                assert got is None, f"{n} executor 应为 None，实际 {got}"
            else:
                got_name = got if isinstance(got, str) else got.__name__
                assert got_name == ex, f"{n} executor 应为 {ex}，实际 {got_name}"
        old_groups = [(e.elts[0].value, [e.elts[1].elts[i].value for i in range(len(e.elts[1].elts))]) for e in groups_node.value.elts]
        # 组顺序与成员集合必须一致；组内成员顺序有意统一为 TOOLS 列表顺序
        # （历史字面量有独立手工顺序，纯展示性质无功能依赖，重构时消除这套漂移源）
        assert [g for g, _ in new_groups] == [g for g, _ in old_groups], "TOOL_GROUPS 组顺序不一致！"
        assert [frozenset(m) for _, m in new_groups] == [frozenset(m) for _, m in old_groups], "TOOL_GROUPS 成员不一致！"
        assert new_phrases == phrases, "_TOOL_ACTION_PHRASES 不一致！"
        old_hints = [(tuple(e.elts[0].elts[i].value for i in range(len(e.elts[0].elts))),
                      [e.elts[1].elts[i].value for i in range(len(e.elts[1].elts))]) for e in hints_node.value.elts]
        assert [ks for ks, _ in new_hints] == [ks for ks, _ in old_hints], "_PREACTIVATE_HINTS 关键词顺序不一致！"
        assert [frozenset(ts) for _, ts in new_hints] == [frozenset(ts) for _, ts in old_hints], "_PREACTIVATE_HINTS 成员不一致！"
    except AssertionError as e:
        Path(SRC.parent / "gen_check.py").write_text(new_text, encoding="utf-8")
        raise SystemExit(f"{e}（已保存 gen_check.py 供排查）")
    print("[11] ✅ 六层等价性校验全部通过")

    # ── 12. 备份并写回（write_bytes 强制 LF 行尾，避免 git 全文件 diff）──
    shutil.copy2(SRC, BAK)
    SRC.write_bytes(new_text.encode("utf-8"))
    print(f"[12] 已备份 {BAK.name} 并写回 {SRC.name}（{len(new_lines)} 行，LF）")
    print("完成 ✅")


if __name__ == "__main__":
    main()
