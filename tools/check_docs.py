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
SECURITY = REPO_ROOT / "SECURITY.md"
CONTRIB = REPO_ROOT / "CONTRIBUTING.md"
README = REPO_ROOT / "README.md"
TECH = REPO_ROOT / "TECH_NOTES.md"
MODULES = REPO_ROOT / "MODULES.md"
AI_GUIDE = REPO_ROOT / "docs" / "AI_PROJECT_GUIDE.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


# ── 输出编码加固 ───────────────────────────────────────────────────
# Windows 控制台与 CI 的默认码页（cp1252 / cp936 等）无法编码中文提示，print 时
# 直接抛 UnicodeEncodeError 并退出 1 —— 会让这些门禁在 CI 上恒红，而本地终端
# 通常是 UTF-8 所以看不到（本项目 CI 的 gate-tools 正是因此长期失败）。
# 与 web_app._harden_stdio 同款处理：强制 UTF-8 输出，不可编码字符降级替换。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


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

    def add_str(v):
        if isinstance(v, ast.Constant) and isinstance(v.value, str) \
                and v.value.startswith("/v1/"):
            paths.add(v.value)

    # 1) 仍保留 if/elif 链（do_GET token 特例等）里的 path 比较
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if not isinstance(t, ast.Compare) or len(t.ops) != 1 or not is_path_ref(t.left):
            continue
        op = t.ops[0]
        if isinstance(op, ast.Eq):
            for c in t.comparators:
                add_str(c)
        elif isinstance(op, ast.In):
            for c in t.comparators:
                if isinstance(c, (ast.Tuple, ast.List)):
                    for el in c.elts:
                        add_str(el)

    # 2) 端点路由表单一来源：@_post_route / @_get_route 装饰器注册
    #    （P2-2 后 do_GET 主链已路由表化；qpath/pre 型元组内的 /v1 路径一并登记）
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) \
                        and d.func.id in ("_post_route", "_get_route") and d.args:
                    add_str(d.args[0])
                    if isinstance(d.args[0], ast.Tuple):
                        for el in d.args[0].elts:
                            add_str(el)
    return len(paths)


def count_tool_modules():
    """工具域模块数（agent_tools/tool_*.py）。

    只数 `tool_*.py`（不含 `__init__.py`）——与文档「N 个工具域模块」的口径一致：
    `__init__.py` 是聚合入口，不承载工具定义。
    """
    return len(sorted((REPO_ROOT / "agent_tools").glob("tool_*.py")))


def count_webui_suites():
    """前端 node 套件数（webui/tests/*.test.mjs）。"""
    return len(sorted((REPO_ROOT / "webui" / "tests").glob("*.test.mjs")))


def count_test_files():
    """后端自举回归测试文件数（tests/test_*.py）。"""
    return len(sorted((REPO_ROOT / "tests").glob("test_*.py")))


def count_test_cases():
    """pytest 实际收集的用例数（含参数化）。缺 pytest/依赖时返回 None（跳过该项校验）。"""
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180)
        m = re.search(r"(\d+)\s+tests?\s+collected",
                      (r.stdout or "") + "\n" + (r.stderr or ""))
        return int(m.group(1)) if m else None
    except Exception:
        return None


def read_version():
    tree = ast.parse(CFG.read_text(encoding="utf-8"))
    v = _top_assign(tree, "VERSION")
    if isinstance(v, ast.Constant):
        return str(v.value)
    return None


def version_major_minor(version):
    """从完整版本号提取主.次（3.9.0 → 3.9），无法解析返回原串。"""
    m = re.match(r"(\d+)\.(\d+)", str(version))
    return f"{m.group(1)}.{m.group(2)}" if m else str(version)


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
    # 补充规则（原为盲区，靠人工发现）：README 的「全部 N 项工具」是当前状态表述，
    # 与历史版本段落（如「v3.9 大版本新增（147 项）」）不同，必须随源码更新。
    (README, r"全部\s*(\d+)\s*项工具",  "tools",      "可用工具总数(全部N项工具)"),
    # MODULES 端点清单的另一种写法（此前改数字靠手工）
    (MODULES, r"等\s*(\d+)\s*端点",     "endpoints",  "/v1 端点数量(等N端点写法)"),
    # 测试规模（此前为门禁盲区：README/MODULES 长期停留在 61 文件 / 671 用例）
    (README, r"(\d+)\s*个 pytest 文件", "pytest_files", "测试文件数"),
    (README, r"(\d+)\s*用例",           "pytest_cases", "测试用例数"),
    (MODULES, r"(\d+)\s*个 pytest 文件", "pytest_files", "测试文件数"),
    (MODULES, r"(\d+)\s*用例",           "pytest_cases", "测试用例数"),
    # ── 2026-10 补盲：上一轮实测发现的两类漂移（此前完全在门禁之外）──
    # ① 工具域模块数：README 写「13 个工具域模块」而 agent_tools/ 已有 14 个 def 模块
    #    （tool_community.py 加入后无人更新）；TECH_NOTES 目录树同理。
    (README,   r"agent_tools/（(\d+) 个工具域模块）", "tool_modules", "工具域模块数(README 架构图)"),
    (AI_GUIDE, r"(\d+) 个模块共 \d+ 工具",            "tool_modules", "工具域模块数(AI 指南)"),
    # ② AI 指南内的测试规模与路由数：与 README/MODULES 同类，但当时只有后两者入库
    (AI_GUIDE, r"(\d+)\s*个文件 / (\d+) 用例",        "pytest_files", "测试文件数(AI 指南)"),
    (AI_GUIDE, r"\*\*(\d+) 个 `/v1` 路由\*\*",        "endpoints",    "/v1 路由数(AI 指南加粗式)"),
    # ③ 前端 node 套件数：README 曾写「21 个」而实际 22 个（且其中 2 个从未被 runner 跑到）
    (README,   r"前端\s*(\d+)\s*个 node 套件",         "webui_suites", "前端 node 套件数(README)"),
    (AI_GUIDE, r"\+ (\d+) 个前端 node 套件",           "webui_suites", "前端 node 套件数(AI 指南)"),
]

# ── 文本断言：文档不应再包含的过期表述 ──
# 安全语义（P1-4 统一为「默认自由 + 黑名单 + 硬限额」；run_python 无沙箱）：
STALE_TEXT = [
    (TECH, "暂无 tests/ 目录",       "tests/ 已存在（28 个 pytest 用例）"),
    (TECH, "当前不跑 pytest",        "CI 已接入 pytest tests/"),
    (TECH, "118 项 Agent 工具",      "实际 135 个工具"),
    (README, "120 项 Agent 工具",    "实际 135 个工具"),
    # ── P1-4 文档统一门禁：以下片段一旦回潮即说明安全文档又漂移 ──
    (README, "sandbox Python 禁用危险模块", "run_python 已无沙箱/静态拦截（等同本机 python -c）"),
    (README, "文件权限白名单",         "白名单 UI 已移除（黑名单主导；旧 whitelist 仅回退路径）"),
    (README, "工具权限黑白名单",       "已无黑白名单双轨表述（黑名单为唯一限制来源）"),
    (README, "zip 炸弹防护",          "无对应代码，属白名单时代的幽灵声明"),
    (README, "沙箱 Python、",         "run_python 已直通本机解释器（无沙箱）"),
    (SECURITY, "3.5.x（main 分支）",  "当前支持版本为 3.9.x（见 SECURITY 支持版本表一致性检查）"),
    (SECURITY, "静态 AST 检查 + `-I -S` 隔离执行", "run_python 无沙箱（等同本机 python -c）"),
    (SECURITY, "要求新增行动工具接入审批流", "默认零审批（approval_actions 空）；确需加严才登记"),
    (CONTRIB, "沙箱补 ast 校验",      "run_python 无沙箱；示例提交信息已过时"),
    (TECH, "沙箱 Python：AST 静态检查", "run_python 无沙箱（等同本机 python -c）"),
    (TECH, "zip 炸弹防护",            "无对应代码（已随白名单时代移除）"),
    # ── 2026-10 补盲：本轮实测修掉的具体漂移，加护栏防回潮 ──
    (TECH, "共 13 模块 163 工具",      "agent_tools/ 现为 14 模块 167 工具（tool_community 加入后未更新）"),
    (README, "agent_tools/（13 个工具域模块）", "agent_tools/ 现为 14 个工具域模块"),
    (MODULES, "主文件 13,115 → 5,574 行", "deepseek_client.py 已增长，行数须以源码实测为准"),
]


def check_doc_orphans():
    """孤儿文档检查：docs/*.md 必须至少被一份其它文档引用。

    背景：`插件开发指南.md`（296 行，描述 .wtplugin v2 完整协议）曾长期**零引用**——
    内容是对的，但没人能找到它，等于不存在。坏链检查只覆盖「链到不存在的文件」，
    反向的「文件没人链」是盲区。本检查把「新增文档必须挂进索引」这条纪律变成门禁。
    返回问题条数。
    """
    docs_dir = REPO_ROOT / "docs"
    candidates = sorted(docs_dir.glob("*.md"))
    # 索引来源：仓库内全部 markdown（README / AI 指南 / 其它 docs / CHANGELOG 等）
    sources = [p for p in REPO_ROOT.glob("*.md")] + candidates
    problems = 0
    for doc in candidates:
        name = doc.name
        referenced = False
        for src in sources:
            if src.name == name:
                continue
            try:
                if name in src.read_text(encoding="utf-8"):
                    referenced = True
                    break
            except Exception:  # noqa: BLE001 - 读不到就跳过，不因单文件失败中断门禁
                continue
        if not referenced:
            problems += 1
            print(f"[孤儿文档] docs/{name} 未被任何文档引用"
                  f"——请挂进 README 文档表或 docs/AI_PROJECT_GUIDE.md 的文档地图")
    return problems


def check_frontend_orphan_components():
    """前端组件「写了就要接上」：webui/src 下的 .jsx 必须被其它文件引用。

    背景（2026-10）：一次性发现三处「完整实现 + 有后端接口，却零调用点」——
    `OfficePreview.jsx`（247 行，xlsx/Word 就地编辑）、`PixelDocViewer.jsx`、
    以及 `ToolCard.jsx` 里的 `team_run` 流水线解析。这类死代码**不会让构建或测试失败**
    （没人 import 就不进 bundle），只能靠引用扫描发现。
    注意：本检查只覆盖 `.jsx` **组件**——纯 `.js` 工具模块允许仅被测试引用。
    返回问题条数。
    """
    src = REPO_ROOT / "webui" / "src"
    if not src.is_dir():
        return 0
    candidates = [p for p in src.rglob("*.jsx") if p.name != "main.jsx"]
    # 收集全部可能含 import 说明符的文本
    blobs = {}
    for p in list(src.rglob("*")) + list((REPO_ROOT / "webui" / "tests").glob("*.mjs")):
        if p.is_file() and p.suffix in (".js", ".jsx", ".mjs"):
            blobs[p] = p.read_text(encoding="utf-8", errors="replace")
    problems = 0
    for f in sorted(candidates):
        stem = f.stem
        pat = re.compile(r"""['"][^'"]*(?:/|^)""" + re.escape(stem) + r"""(?:\.jsx?)?['"]""")
        hit = any(pat.search(t) for p, t in blobs.items() if p != f)
        if not hit:
            problems += 1
            print(f"[孤儿组件] webui/src/{f.relative_to(src).as_posix()} 无任何 import"
                  f"——要么补调用点，要么删除（死组件不会让构建/测试失败）")
    return problems


def check_webui_runner_discovers_all():
    """前端套件「存在即被跑」不变式：runTests.mjs 必须自动发现全部 *.test.mjs。

    背景：2026-10 实测发现 `extractProducts.test.mjs` / `segmentNotes.test.mjs`
    躺在 `webui/tests/` 却不在当时手写的 `npm test` 链里——`extractProducts` 因此
    带着一个**真实的失败断言**（URL 被误当盘符路径）长期躲过 CI。runner 已改为
    读目录自动发现；本检查确保这个语义不被改回手写清单。返回问题条数。
    """
    runner = REPO_ROOT / "webui" / "runTests.mjs"
    if not runner.exists():
        print("[缺失] webui/runTests.mjs 不存在——前端套件跑测器缺失，"
              "tests/*.test.mjs 将无人执行")
        return 1
    src = runner.read_text(encoding="utf-8")
    if "readdirSync" not in src or "test.mjs" not in src:
        print("[退化] webui/runTests.mjs 不再是「自动发现」式 runner"
              "（应 readdirSync 扫描 tests/*.test.mjs）——手写清单会漏跑套件")
        return 1
    # package.json 必须把 test 指给 runner，而不是内联手写链
    pkg = REPO_ROOT / "webui" / "package.json"
    try:
        import json
        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 无法解析 webui/package.json：{exc}")
        return 1
    problems = 0
    for key in ("test", "test:fast"):
        cmd = str(scripts.get(key, ""))
        if "runTests.mjs" not in cmd:
            problems += 1
            print(f"[不一致] webui/package.json 的 `{key}` 未指向 runTests.mjs：{cmd[:60]}")
    return problems


def check_broken_links():
    """文档内相对链接的存活性校验。

    背景：`experiments/鲸群实验场/` 实验本体被剥离出主程序且**不在本仓库中**，
    但 README 仍指向它 —— 读者点开即 404。这类「引用了不存在的路径」在
    纯数字门禁之外，此前没有任何检查。返回问题条数。
    """
    docs = [README, MODULES, TECH, CONTRIB, SECURITY, CHANGELOG, AI_GUIDE]
    docs += sorted((REPO_ROOT / "docs").glob("*.md"))
    problems = 0
    seen = set()
    for d in docs:
        if not d.exists() or d in seen:
            continue
        seen.add(d)
        text = d.read_text(encoding="utf-8")
        for m in re.finditer(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)", text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (d.parent / target).exists():
                problems += 1
                print(f"[坏链] {d.relative_to(REPO_ROOT).as_posix()} → {target}（目标不存在）")
    return problems


def check_prompt_consistency():
    """提示词一致性门禁：上下文规模、内置指令库条数、能力总数硬编码、引用的工具名。

    这些数字/名称散落在 prompt 文本里，此前完全在门禁之外，随时可漂移。
    返回问题条数。
    """
    problems = 0
    cfg_src = CFG.read_text(encoding="utf-8")
    dsc_src = DEEPSEEK.read_text(encoding="utf-8")

    # 1) DEFAULT_SYSTEM_PROMPT 声明的上下文规模 vs MODELS.max_context_tokens
    m_ctx = re.search(r"长达\s*(\d+)\s*万\s*Token", cfg_src)
    m_tok = re.search(r"\"max_context_tokens\"\s*:\s*([0-9_]+)", dsc_src)
    if m_ctx and m_tok:
        declared = int(m_ctx.group(1))
        actual = int(m_tok.group(1).replace("_", "")) // 10000
        if declared != actual:
            problems += 1
            print(f"[不一致] DEFAULT_SYSTEM_PROMPT 声明 {declared} 万 Token，"
                  f"MODELS.max_context_tokens 实测 {actual} 万")

    # 2) 能力总数不得在提示词里写死（应动态取自注册表）
    if re.search(r"拥有\s*\d+\s*\+?\s*项专业能力", dsc_src):
        problems += 1
        print("[过期表述] deepseek_client 提示词仍写死能力总数（应动态取自 all_tools）")

    # 3) 内置指令库条数 vs 文档声明「内置指令库（N 条模板）」
    try:
        tree = ast.parse(cfg_src)
        bp = _top_assign(tree, "BUILTIN_PROMPTS")
        n_bp = len(bp.elts) if isinstance(bp, ast.List) else None
    except Exception:
        n_bp = None
    if n_bp is not None:
        for path in (README, MODULES):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"内置指令库（(\d+)\s*条模板）", text):
                if int(m.group(1)) != n_bp:
                    problems += 1
                    print(f"[不一致] {path.name} 声明内置指令库 {m.group(1)} 条，实测 {n_bp} 条")

    # 4) TASK_QUALITY_GUIDE 引用的 snake_case 工具名必须真实存在
    order = _top_assign(ast.parse(dsc_src), "_TOOL_ORDER")
    tool_names = {e.value for e in order.elts} if isinstance(order, ast.List) else set()
    m_guide = re.search(r"TASK_QUALITY_GUIDE\s*=\s*\((.*?)\n\)", cfg_src, re.S)
    if m_guide and tool_names:
        refs = set(re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", m_guide.group(1)))
        # 排除路径/文件名类标识（非工具名），以及「工具参数名」——指南里会
        # 举例说明参数命名规范（如 read_file 的 start_line/max_lines），这些不是工具名。
        refs -= {
            "api_server", "sample_plugins", "data_dir",
            "start_line", "max_lines", "max_items", "old_string", "new_string",
            "read_file", "live_tag",
        }
        unknown = sorted(r for r in refs if r not in tool_names)
        if unknown:
            problems += 1
            print(f"[不一致] TASK_QUALITY_GUIDE 引用了未注册工具/标识：{unknown}")
    return problems


def _write_preserving_eol(path, text):
    """写回文本，并**保留原文件的行尾风格**。

    Windows 上 `Path.write_text` 默认做换行转换（\\n → \\r\\n）：原文件是 LF 时
    一次 --fix 就会把整文件重写成 CRLF，制造上千行的伪 diff（本项目真实踩过——
    README.md / TECH_NOTES.md 被 --fix 从 LF 改成了 CRLF）。这里先看原文件实际
    行尾，再决定写回风格，让 --fix 只改数字、不改字节风格。
    """
    try:
        raw = path.read_bytes()
    except Exception:
        raw = b""
    n_lf = raw.count(b"\n")
    if n_lf and raw.count(b"\r\n") == n_lf:
        data = text.replace("\r\n", "\n").replace("\n", "\r\n")  # 原为 CRLF
    else:
        data = text.replace("\r\n", "\n")                        # 原为 LF
    path.write_bytes(data.encode("utf-8"))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    fix = "--fix" in argv

    expected = {
        "tools": count_tools(),
        "endpoints": count_endpoints(),
        "version": read_version(),
        "pytest_files": count_test_files(),
        "pytest_cases": count_test_cases(),
        "tool_modules": count_tool_modules(),
        "webui_suites": count_webui_suites(),
    }
    print(f"实测: 工具 {expected['tools']} · /v1 路由 {expected['endpoints']} · "
          f"工具域模块 {expected['tool_modules']} · "
          f"版本 {expected['version']} · 测试 {expected['pytest_files']} 文件 / "
          f"{expected['pytest_cases'] if expected['pytest_cases'] is not None else '?'} 用例 · "
          f"前端 {expected['webui_suites']} 套件")

    problems = 0
    for path, pattern, key, label in CLAIMS:
        text = path.read_text(encoding="utf-8")
        exp = expected[key]
        if exp is None:
            print(f"[跳过] {path.name}:{label} — 无法实测（缺 pytest/依赖）")
            continue
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
            _write_preserving_eol(path, new_text)
            print(f"       已修正为 {exp}（仅替换数字，不动组外文本）")

    for path, frag, why in STALE_TEXT:
        text = path.read_text(encoding="utf-8")
        if frag in text:
            problems += 1
            print(f"[过期表述] {path.name} 仍含「{frag}」：{why}")

    # ── 提示词一致性（上下文规模 / 指令库条数 / 写死的能力数 / 引用工具名）──
    problems += check_prompt_consistency()

    # ── 文档相对链接存活性（引用了不存在/已剥离的路径）──
    problems += check_broken_links()

    # ── 前端套件「存在即被跑」不变式 ──
    problems += check_webui_runner_discovers_all()

    # ── 孤儿文档（docs/*.md 没人引用 = 找不到 = 等于不存在）──
    problems += check_doc_orphans()

    # ── 前端孤儿组件（写了但没接上 = 功能不存在，且构建/测试不会报错）──
    problems += check_frontend_orphan_components()

    # ── SECURITY 支持版本表与单一版本源对齐 ──
    # 表行形如 "| 3.9.x（main 分支） | ✅ 积极维护 |"：主版本号从源码 VERSION 推导。
    mm = version_major_minor(expected["version"])
    sec_text = SECURITY.read_text(encoding="utf-8")
    sec_ver = re.search(r"\|\s*(\d+\.\d+)\.x", sec_text)
    if sec_ver:
        if sec_ver.group(1) != mm:
            problems += 1
            print(f"[不一致] SECURITY.md 支持版本表声明 {sec_ver.group(1)}.x，"
                  f"实测 {mm}.x（--fix 自动修正）")
            if fix:
                new_sec = sec_text.replace(
                    sec_ver.group(1) + ".x", mm + ".x", 1)
                _write_preserving_eol(SECURITY, new_sec)
                print(f"       已修正支持版本表为 {mm}.x")
    else:
        problems += 1
        print(f"[缺失] SECURITY.md 找不到「支持的版本」表行（期望 {mm}.x）")

    if problems:
        print(f"\n校验未通过：{problems} 处问题（--fix 可修正数字类问题）")
        return 1
    print("\n校验通过：文档声明与源码一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
