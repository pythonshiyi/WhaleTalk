# -*- coding: utf-8 -*-
"""文档数字自动校验（WhaleTalk 开发工具链）。

背景：仓库只有「版本号」是单一源，工具数/端点数等规模数字没有约束，
已真实发生漂移——README 写「120 项 Agent 工具」、TECH_NOTES 写「118 项」，
而源码实测是 135 个（v3.8.3 确认）。本脚本从源码 AST 实测，再核对文档声明。

用法：
    python tools/check_docs.py         # 校验：任何不一致返回 1（可入 CI）
    python tools/check_docs.py --fix   # 校验，并把文档中的数字就地修正
"""
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEEPSEEK = REPO_ROOT / "deepseek_client.py"
API = REPO_ROOT / "api_server.py"
CFG = REPO_ROOT / "config_defaults.py"
README = REPO_ROOT / "README.md"
TECH = REPO_ROOT / "TECH_NOTES.md"
MODULES = REPO_ROOT / "MODULES.md"


def _top_assign(tree, target):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == target:
                    return node.value
    return None


def count_tools():
    src = DEEPSEEK.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # P1-3 迁移后 TOOLS 由 build_tool_list(_TOOL_ORDER) 生成，字面量在 _TOOL_ORDER；
    # 顺序表与构建产物数量一致（构建期校验缺项/多余项）。
    order = _top_assign(tree, "_TOOL_ORDER")
    if isinstance(order, ast.List):
        return len(order.elts)
    # 兼容未迁移的旧结构
    tools = _top_assign(tree, "TOOLS")
    return len(tools.elts) if isinstance(tools, ast.List) else None


def count_endpoints():
    src = API.read_text(encoding="utf-8")
    tree = ast.parse(src)
    paths = set()

    def is_path_ref(n):
        if isinstance(n, ast.Name):
            return n.id == "path"
        if isinstance(n, ast.Attribute):
            return n.attr == "path"      # self.path
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if not isinstance(t, ast.Compare) or len(t.ops) != 1 or not is_path_ref(t.left):
            continue
        op = t.ops[0]
        if isinstance(op, ast.Eq):
            for c in t.comparators:
                if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                        and c.value.startswith("/v1/"):
                    paths.add(c.value)
        elif isinstance(op, ast.In):
            for c in t.comparators:
                if isinstance(c, (ast.Tuple, ast.List)):
                    for el in c.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str) \
                                and el.value.startswith("/v1/"):
                            paths.add(el.value)
    return len(paths)


def read_version():
    tree = ast.parse(CFG.read_text(encoding="utf-8"))
    v = _top_assign(tree, "VERSION")
    if isinstance(v, ast.Constant):
        return str(v.value)
    return None


# ── 文档声明清单：(文件, 正则[首捕获组=数字], 期望来源, 含义) ──
# 注意：正则需要让「数字」单独成捕获组，--fix 只替换该组，绝不触碰组外文本。
CLAIMS = [
    (README, r"(\d+)\s*项 Agent 工具", "tools",      "Agent 工具数量(中文)"),
    (README, r"(\d+)\s*Agent tools",   "tools",      "Agent 工具数量(英文)"),
    (README, r"（(\d+) 工具）",         "tools",      "能力总数(括号写法)"),
    (README, r"工具链（(\d+) 项）",     "tools",      "工具链栏目标题"),
    (TECH,   r"(\d+)\s*项 Agent 工具",  "tools",      "Agent 工具数量(中文)"),
    (TECH,   r"(\d+)\s*工具 \+ smart_tools", "tools", "能力引擎工具数"),
    (MODULES, r"(\d+)\s*个 Agent 工具",  "tools",      "Agent 工具数量(中文)"),
    (MODULES, r"(\d+)\s*工具 \+ smart_tools", "tools", "能力引擎工具数"),
    (TECH,   r"(\d+)\+\s*/v1 端点",     "endpoints",  "/v1 端点数量(带+)"),
    (MODULES, r"(\d+)\s*/v1 端点",      "endpoints",  "/v1 端点数量"),
]

# ── 文本断言：文档不应再包含的过期表述 ──
STALE_TEXT = [
    (TECH, "暂无 tests/ 目录",       "tests/ 已存在（28 个 pytest 用例）"),
    (TECH, "当前不跑 pytest",        "CI 已接入 pytest tests/"),
    (TECH, "118 项 Agent 工具",      "实际 135 个工具"),
    (README, "120 项 Agent 工具",    "实际 135 个工具"),
]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    fix = "--fix" in argv

    expected = {
        "tools": count_tools(),
        "endpoints": count_endpoints(),
        "version": read_version(),
    }
    print(f"实测: 工具 {expected['tools']} · /v1 路由 {expected['endpoints']} · "
          f"版本 {expected['version']}")

    problems = 0
    for path, pattern, key, label in CLAIMS:
        text = path.read_text(encoding="utf-8")
        exp = expected[key]
        hits = list(re.finditer(pattern, text))
        if not hits:
            problems += 1
            print(f"[缺失] {path.name} 找不到声明「{label}」({pattern})")
            continue
        diffs = [m for m in hits if int(m.group(1)) != exp]
        for m in diffs:
            s, e = m.start(1), m.end(1)
            problems += 1
            print(f"[不一致] {path.name}:{label} 声明 {m.group(1)}，实测 {exp}"
                  f" → {text[max(0, s - 40):e + 10].strip()}")
        if fix and diffs:
            def repl(m):
                if int(m.group(1)) != exp:
                    return m.group(0).replace(m.group(1), str(exp), 1)
                return m.group(0)
            new_text, _ = re.subn(pattern, repl, text)
            path.write_text(new_text, encoding="utf-8")
            print(f"       已修正为 {exp}（仅替换数字，不动组外文本）")

    for path, frag, why in STALE_TEXT:
        text = path.read_text(encoding="utf-8")
        if frag in text:
            problems += 1
            print(f"[过期表述] {path.name} 仍含「{frag}」：{why}")

    if problems:
        print(f"\n校验未通过：{problems} 处问题（--fix 可修正数字类问题）")
        return 1
    print("\n校验通过：文档声明与源码一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
