# -*- coding: utf-8 -*-
"""tool_system —— P0-1 批量拆分（工具域模块）：🔧 系统与项目.

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
引用 deepseek_client 的常量与辅助依赖加载顺序契约——主文件在共享基建全部定义后
才执行 `from agent_tools import *`，此处 from-import 可安全解析。
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime

import permissions
import plugins as plugins_mod

from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import deepseek_client as _dc  # 可变注入配置动态访问（dc.X 注入后立即生效）
from deepseek_client import (

    EVO_WRITE_EXTS,
    PROJECT_DIR,
    PROJECT_READ_EXTS,
    _NOTIFY_PS,
    _SEARCH_SKIP_DIRS,
    _atomic_write,
    _current_version,
    _evolve_compile,
    _evolve_lint,
    _evolve_restore_file,
    _evolve_smoke,
    _evolve_tests,
    _load_watch_state,
    _proc_capture,
    _py_stats,
    _save_watch_state,
    _to_tool_schema,
    _which_any,
    _win_installed_apps,
)



@tool(
        {
            "type": "function",
            "function": {
                "name": "watch_files",
                "description": "持续感知：监听目录文件变化（新增/修改/删除），首次建立基线、之后返回与上次的差异；适合定期查看产出目录/项目目录有没有新东西",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要监听的目录绝对路径"},
                        "pattern": {"type": "string", "description": "可选：文件通配符过滤，如 *.md"},
                        "max_items": {"type": "integer", "description": "可选：每类变化最多列出条数（默认 50）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='文件变化监听',
    preactivate=(('文件变化', '监听', '有没有新文件', '新东西', '持续感知', '看看变化'),),
)
def watch_files(path, pattern="", max_items=50):
    """持续感知：监听目录文件变化（新增/修改/删除），跨调用对比状态。
    首次调用建立基线快照；之后返回与上次的差异。适合定期查看产出目录有没有新东西。"""
    ok, reason = permissions.check_filesystem(path, write=False)
    if not ok:
        return reason
    p = permissions.resolve(path)
    if not os.path.isdir(p):
        return f"错误：目录不存在：{p}"
    import fnmatch
    snap = {}
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in _SEARCH_SKIP_DIRS]
        for fn in files:
            if pattern and not fnmatch.fnmatch(fn, pattern):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, p)
            try:
                st = os.stat(full)
                snap[rel] = [int(st.st_mtime), st.st_size]  # list：与 JSON 读回类型一致（tuple 会永远 !=）
            except OSError:
                continue
    state = _load_watch_state()
    prev = (state.get("files") or {}).get(p, {})
    if not prev:
        state.setdefault("files", {})[p] = snap
        _save_watch_state(state)
        return f"已建立监听基线：{p}（{len(snap)} 个文件）"
    added = sorted(k for k in snap if k not in prev)
    removed = sorted(k for k in prev if k not in snap)
    modified = sorted(k for k in snap if k in prev and snap[k] != prev[k])
    if pattern:  # pattern 统一作用于三类变化（含删除，否则删除列表泄漏范围外文件）
        added = [k for k in added if fnmatch.fnmatch(k, pattern)]
        removed = [k for k in removed if fnmatch.fnmatch(k, pattern)]
        modified = [k for k in modified if fnmatch.fnmatch(k, pattern)]
    state.setdefault("files", {})[p] = snap
    _save_watch_state(state)
    if not (added or removed or modified):
        return f"无变化（{len(snap)} 个文件，自上次检查后无新增/修改/删除）"
    try:
        limit = max(1, min(100, int(max_items or 50)))
    except (TypeError, ValueError):
        limit = 50
    lines = [f"文件变化（{p}）："]
    if added:
        lines.append(f"  + 新增 {len(added)} 个")
        lines += [f"    {k}" for k in added[:limit]]
    if modified:
        lines.append(f"  ~ 修改 {len(modified)} 个")
        lines += [f"    {k}" for k in modified[:limit]]
    if removed:
        lines.append(f"  - 删除 {len(removed)} 个")
        lines += [f"    {k}" for k in removed[:limit]]
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "recall_session",
                "description": "情景记忆：回顾历史会话时间线（按日期或关键词过滤），返回名称/时间/消息数/首问/末答；跨会话延续上下文",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "可选：关键词（匹配会话名或开头内容）"},
                        "date": {"type": "string", "description": "可选：日期 YYYY-MM-DD 过滤"},
                        "limit": {"type": "integer", "description": "可选：最多返回几个会话（默认 5，最多 20）"},
                    },
                    "required": [],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='历史会话回顾',
    preactivate=(('之前聊过', '上次说', '回顾会话', '历史会话', '前几天', '之前的对话', '记得我们'),),
)
def recall_session(query="", date="", limit=5):
    """情景记忆：回顾历史会话时间线。按日期（YYYY-MM-DD）或关键词过滤，
    返回最近会话的名称/时间/消息数/首问/末答，延续跨会话上下文。"""
    if not _dc.SESSIONS_DIR or not os.path.isdir(_dc.SESSIONS_DIR):
        return "（会话库不可用，无法回顾）"
    import glob as _glob
    try:
        limit = max(1, min(20, int(limit or 5)))
    except (TypeError, ValueError):
        limit = 5
    q = str(query or "").strip().lower()
    dt = str(date or "").strip()
    items = []
    for fn in _glob.glob(os.path.join(_dc.SESSIONS_DIR, "*.json")):
        if fn.endswith(".bak"):
            continue
        try:
            d = json.load(open(fn, encoding="utf-8"))
            msgs = d.get("messages") or []
            name = str(d.get("name") or "未命名会话")
            saved = str(d.get("saved_at") or "")
            if dt and not saved.startswith(dt):
                continue
            if q:
                hay = (name + " " + " ".join(str(m.get("content", ""))[:80] for m in msgs[:3])).lower()
                if q not in hay:
                    continue
            first_user = next((str(m.get("content", ""))[:60] for m in msgs if m.get("role") == "user"), "")
            last_asm = next((str(m.get("content", ""))[:60] for m in reversed(msgs) if m.get("role") == "assistant"), "")
            items.append({"name": name, "saved": saved, "msgs": len(msgs), "first": first_user, "last": last_asm})
        except Exception:
            continue
    items.sort(key=lambda x: x["saved"] or "", reverse=True)
    items = items[:limit]
    if not items:
        return f"未找到相关会话（日期={dt or '任意'}，关键词={q or '任意'}）"
    lines = [f"历史会话时间线（{len(items)} 个）："]
    for it in items:
        lines.append(f"· {it['saved'][:16]} {it['name']}（{it['msgs']} 条）")
        if it["first"]:
            lines.append(f"    首问：{it['first']}")
        if it["last"]:
            lines.append(f"    末答：{it['last']}")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "project_info",
                "description": "感知鲸语自身代码库：版本、项目文件清单与规模（只读，自我进化分析用）",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    groups=['🔧 系统与基础'],
    phrases='项目信息/文件树',
    preactivate=(('自身代码', '读源码', '项目文件', '鲸语代码', '看代码库'),),
)
def project_info():
    """感知鲸语自身代码库（只读）。"""
    lines = [f"鲸语版本: {_current_version()}", "项目文件："]
    for fn in sorted(os.listdir(PROJECT_DIR)):
        full = os.path.join(PROJECT_DIR, fn)
        if os.path.isfile(full) and fn.endswith(PROJECT_READ_EXTS):
            try:
                size = os.path.getsize(full)
                size_txt = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
                extra = f" · {_py_stats(full)}" if fn.endswith(".py") else ""
                lines.append(f"- {fn}（{size_txt}{extra}）")
            except Exception:
                lines.append(f"- {fn}（读取失败）")
    lines.append("说明：自我改进请用 create_evolution 写入 evolutions/ 分支，勿修改原文件。")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "read_project_file",
                "description": "读取鲸语自身源码文件（仅限项目目录内 .py/.md/.json/.txt/.bat/.html，只读；支持 offset/limit 分页读取大文件）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "项目内文件路径，如 web_app.py 或 deepseek_client.py"},
                        "offset": {"type": "integer", "description": "可选：起始字符偏移（分页读取）"},
                        "limit": {"type": "integer", "description": "可选：本次读取字符数上限"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='读取项目文件',
    preactivate=(('自身代码', '读源码', '项目文件', '鲸语代码', '看代码库'),),
)
def read_project_file(path, offset=0, limit=0):
    """读取鲸语自身源码（仅项目目录内白名单扩展名，只读）。

    offset/limit：按字符分页（大型文件如 main.py 320KB 需分页读取），
    limit=0 表示读取到 offset+80000 或文件尾。
    """
    p = os.path.abspath(os.path.expanduser(str(path or "")))
    base = os.path.abspath(PROJECT_DIR)
    # Windows 路径大小写不敏感：normcase 后比较，防合法路径被误拒
    if os.path.normcase(p) != os.path.normcase(base) and not os.path.normcase(p).startswith(
        os.path.normcase(base).rstrip("\\/") + os.sep
    ):
        return "权限拒绝：只能读取项目目录内的文件"
    if not os.path.isfile(p):
        return f"错误：文件不存在：{p}"
    p_lower = p.lower()
    if not any(p_lower.endswith(ext) for ext in PROJECT_READ_EXTS):
        return f"错误：不支持的文件类型（仅 {'/'.join(PROJECT_READ_EXTS)}）"
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        try:
            off = max(0, int(offset or 0))
            lim = max(0, int(limit or 0))
        except (TypeError, ValueError):
            off, lim = 0, 0
        total = len(content)
        if off >= total:
            return f"[已到达文件末尾] {p}（共 {total} 字符）"
        if lim > 0:
            chunk = content[off : off + lim]
        else:
            chunk = content[off : off + 80000]
        head = f"[{p} 第 {off}-{off + len(chunk)} 字符 / 共 {total} 字符]\n"
        return head + chunk + ("\n[已截断，可继续用 offset 读取后续]" if off + len(chunk) < total else "")
    except Exception as e:
        return f"错误：读取失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "create_evolution",
                "description": "自我进化提案：发现项目改进点（尤其方案性、需人决策、或不确定是否该直接改的）时，把改进后的代码写入 evolutions/ 分支（不改原文件），供人审阅采纳/忽略。确定要改且能验证的改动用 self_evolve 分支实施",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "提案名称，如 fix_typo / optimize_render"},
                        "files": {
                            "type": "array",
                            "description": "修改后的文件列表",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string", "description": "相对项目根目录的路径，如 web_app.py"},
                                    "content": {"type": "string", "description": "修改后的完整文件内容"},
                                },
                                "required": ["path", "content"],
                            },
                        },
                    },
                    "required": ["name", "files"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='提进化提案（方案审阅）',
    preactivate=(('进化提案', '改进提案', '提个方案', '改进建议'),),
)
def create_evolution(name, files):
    """自我进化提案：写入 evolutions/<name>_<ts>/ 分支，绝不修改原文件。

    返回分支路径；EVOLUTION.md 缺失时自动生成基础说明（AI 应尽量在
    files 中包含完整的 EVOLUTION.md：改动内容/原因/风险/验证方式）。
    """
    name = re.sub(r'[\\/:*?"<>|]', "_", str(name or "evolution").strip())[:40] or "evolution"
    if not isinstance(files, list) or not files:
        return '错误：files 必须是非空数组 [{"path": "main.py", "content": "..."}]'
    if len(files) > 20:
        return "错误：文件数超过 20 上限"
    total_bytes = sum(len(str(f.get("content") or "")) for f in files if isinstance(f, dict))
    if total_bytes > 50 * 1024 * 1024:
        return "错误：提案内容超过 50MB 总上限"
    # 校验前置：非法路径/类型在创建任何目录前拒绝（避免空分支残留）
    for f in files[:20]:
        if not isinstance(f, dict):
            return "错误：files 元素必须是对象"
        rel = str(f.get("path") or "").strip().replace("\\", "/")
        if not rel or rel in (".", "..") or ".." in rel.split("/"):
            return f"错误：非法相对路径：{rel}"
        if not rel.endswith(EVO_WRITE_EXTS):
            return f"错误：不支持的文件类型：{rel}"
        branch_preview = os.path.join(_dc.EVOLUTIONS_DIR, "_preview")
        full = os.path.normpath(os.path.join(branch_preview, rel))
        if full != branch_preview and not full.startswith(branch_preview.rstrip("\\/") + os.sep):
            return f"错误：路径越界：{rel}"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    branch = os.path.join(_dc.EVOLUTIONS_DIR, f"{name}_{ts}")
    try:
        os.makedirs(branch, exist_ok=True)
    except Exception as e:
        return f"错误：创建分支失败: {e}"
    written = []
    has_md = False
    for f in files[:20]:
        rel = str(f.get("path") or "").strip().replace("\\", "/")
        content = f.get("content") or ""
        full = os.path.normpath(os.path.join(branch, rel))
        try:
            os.makedirs(os.path.dirname(full) or branch, exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(rel)
            if rel == "EVOLUTION.md":
                has_md = True
        except Exception as e:
            return f"错误：写入 {rel} 失败: {e}"
    if not has_md:
        try:
            with open(os.path.join(branch, "EVOLUTION.md"), "w", encoding="utf-8") as fh:
                fh.write(
                    f"# 进化提案：{name}\n\n"
                    f"- 时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
                    f"- 修改文件：{', '.join(written)}\n\n"
                    "## 说明\n（鲸语补充：改动内容、原因、风险与验证方式）\n"
                )
        except Exception:
            pass
    permissions.audit("create_evolution", name, ", ".join(written))
    return (
        f"自我进化提案已创建：{branch}\n"
        f"文件：{', '.join(written)}\n"
        "请在「工具 → 自我进化」中查看差异、采纳或忽略。"
    )


@tool(
        {
            "type": "function",
            "function": {
                "name": "self_evolve",
                "description": "闭环自我进化：在 git 分支上实施自我改进补丁，四层验证（语法编译→ruff lint→导入冒烟→测试）全过才提交分支供合入，任何一级失败自动回滚，不碰生产代码。适合自主改进自身代码能力",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "feature_name": {"type": "string", "description": "改进点名称"},
                        "files": {"type": "array", "items": {"type": "object"}, "description": "补丁文件列表 [{path, content}]"},
                        "project_dir": {"type": "string", "description": "可选：项目目录（默认鲸语自身代码库）"},
                    },
                    "required": ["feature_name", "files"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='闭环自我进化',
    preactivate=(('自我进化', '改进自己', '升级自己', '自我改进', '修复自己', '自省'),),
)
def self_evolve(feature_name, files, project_dir=None):
    """闭环自我进化：在 git 分支上实施自我改进补丁，四层验证后报告合入。

    流程：观察自身缺陷 → 生成方案（files 补丁）→ 新建 evolve/ 分支应用补丁 →
          py_compile 语法编译 → ruff lint → import 冒烟 → pytest 验证 →
          通过则提交分支并报告（合入权在用户）/ 失败则自动回滚删除分支，生产代码零改动。
    """
    import subprocess
    from datetime import datetime

    name = (feature_name or "improvement").strip()[:40]
    if not files or not isinstance(files, list):
        return "错误：files 必须是非空数组 [{path, content}]"
    if len(files) > 20:
        return "错误：文件数超过 20 上限"

    base = project_dir or PROJECT_DIR
    if not base or not os.path.isdir(base):
        return "错误：项目目录不存在"
    ok, reason = permissions.check_filesystem(base, write=True)
    if not ok:
        return reason

    def _git(args):
        try:
            r = subprocess.run(
                ["git"] + args, cwd=base, capture_output=True, text=True,
                timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return ((r.stdout or "") + (r.stderr or "")).strip(), r.returncode
        except FileNotFoundError:
            return "本机未安装 git", 1
        except Exception as e:
            return str(e), 1

    # 确认项目目录是独立 git 仓库（自身含 .git；避免误判上级仓库，如用户主目录碰巧是仓库）
    if not os.path.isdir(os.path.join(base, ".git")) and not os.path.isfile(os.path.join(base, ".git")):
        return "错误：项目目录不是独立 git 仓库（自我进化需要 git 分支隔离）"
    cur, cur_code = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    if cur_code != 0:
        # 无提交基线（unborn HEAD）：分支切换不可靠，回滚完全依赖内存备份
        cur = "main"

    # 应用补丁前：内存备份每个目标文件原内容。回滚时写回备份，
    # 100% 恢复生产文件，不依赖 git 提交基线（无提交仓库也安全）。
    orig = {}
    for f in files[:20]:
        rel = str(f.get("path") or "").strip().replace("\\", "/")
        if not rel or ".." in rel.split("/"):
            continue
        full = os.path.normpath(os.path.join(base, rel))
        if full != base and not full.startswith(base.rstrip("\\/") + os.sep):
            continue
        if os.path.exists(full):
            try:
                with open(full, "rb") as fh:
                    orig[rel] = fh.read()
            except Exception:
                orig[rel] = None
        else:
            orig[rel] = None

    branch = f"evolve/{name}_{int(time.time())}"
    out, code = _git(["checkout", "-b", branch])
    if code != 0:
        return f"错误：创建分支失败：{out}"

    applied, failed = [], []
    for f in files[:20]:
        if not isinstance(f, dict):
            failed.append(("?", "元素必须是对象"))
            continue
        rel = str(f.get("path") or "").strip().replace("\\", "/")
        content = f.get("content") or ""
        if not rel or ".." in rel.split("/"):
            failed.append((rel or "?", "非法相对路径"))
            continue
        full = os.path.normpath(os.path.join(base, rel))
        if full != base and not full.startswith(base.rstrip("\\/") + os.sep):
            failed.append((rel, "路径越界"))
            continue
        try:
            os.makedirs(os.path.dirname(full) or base, exist_ok=True)
            _atomic_write(full, content)
            applied.append(rel)
        except Exception as e:
            failed.append((rel, str(e)))

    if not applied:
        for rel, oc in orig.items():  # 恢复任何被部分应用的备份
            _evolve_restore_file(base, rel, oc)
        try:
            _git(["checkout", cur])
        except Exception:
            pass
        return "错误：补丁全部失败：" + "；".join(f"{r}({why})" for r, why in failed)

    # 验证链（四层串行闸）：语法编译 → lint → 导入冒烟 → 测试。
    # 任何一级失败立即回滚，杜绝「改完就以为成功」的瞎进化。
    compile_r = _evolve_compile(base, applied)
    lint_r = _evolve_lint(base, applied)
    smoke_r = _evolve_smoke(base, applied)
    tests_r = _evolve_tests(base, applied)
    compile_ok = compile_r.startswith(("编译通过", "（"))
    lint_ok = lint_r.startswith(("无问题", "（"))
    smoke_ok = smoke_r.startswith(("导入通过", "（"))
    tests_ok = tests_r.startswith("（") or ("passed" in tests_r or "退出码 0" in tests_r)

    if compile_ok and lint_ok and smoke_ok and tests_ok:
        _git(["add", "."])
        _git(["commit", "-m", f"self-evolve: {name}"])
        _git(["checkout", cur])
        return (
            f"进化完成：{name}\n"
            f"分支：{branch}（已提交，可审查后合入 main）\n"
            f"补丁：{len(applied)} 个文件（{'、'.join(applied[:5])}）\n"
            f"编译：{compile_r.splitlines()[0] if not compile_ok else '通过'}\n"
            f"lint：通过\n"
            f"导入：{smoke_r.splitlines()[0] if not smoke_ok else '通过'}\n"
            f"测试：{tests_r.splitlines()[0] if not tests_r.startswith('（') else tests_r}\n"
            f"已切回 {cur} 分支。合入权在你：git merge {branch}"
        )

    # 回滚（安全版）：内存备份写回原内容 + 清理 _atomic_write 的 .bak + 新建文件删除，
    # 绝不因回滚丢失任何生产文件（修复：此前 os.remove 会删掉被覆盖的已有文件）
    for rel in applied:
        _evolve_restore_file(base, rel, orig.get(rel))
    try:
        _git(["checkout", cur])
    except Exception:
        pass
    _git(["branch", "-D", branch])
    return (
        f"进化验证未通过，已回滚到 {cur} 分支（生产代码已恢复原状）：\n"
        f"编译：{compile_r.splitlines()[0] if not compile_ok else '通过'}\n"
        f"lint：{lint_r.splitlines()[0] if not lint_ok else '通过'}\n"
        f"导入：{smoke_r.splitlines()[0] if not smoke_ok else '通过'}\n"
        f"测试：{tests_r.splitlines()[0] if not tests_r.startswith('（') else tests_r}\n"
        "请参考上述输出调整方案后重试。"
    )


@tool(
        {
            "type": "function",
            "function": {
                "name": "verify_files",
                "description": "批量核验文件是否存在及其大小（写文件/建工程后核验产物真实存在；相对路径基于工作目录）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "description": "要核验的文件路径列表（绝对路径或相对工作目录的路径）",
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["paths"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='核验项目文件完整性',
    preactivate=(('核验文件', '检查产物', '产物存在', '验证文件'),),
)
def verify_files(paths):
    """批量核验文件存在性与大小（写文件后自检，防幻觉）。

    相对路径基于工作目录解析；只读操作。
    """
    if not isinstance(paths, list) or not paths:
        return '错误：paths 必须是非空数组，如 ["src/main.py", "C:/x/app.py"]'
    if len(paths) > 30:
        return "错误：文件数超过 30 上限"
    lines = []
    exist = 0
    missing = 0
    for raw in paths[:30]:
        p = str(raw or "").strip()
        if not p:
            continue
        if not os.path.isabs(p) and permissions.WORKSPACE_DIR:
            # 相对路径限定在工作区内，防 ../ 越界探测工作区外文件
            ws = permissions.WORKSPACE_DIR.rstrip("\\/")
            full = os.path.normpath(os.path.join(permissions.WORKSPACE_DIR, p))
            if full != ws and not full.startswith(ws + os.sep):
                lines.append(f"❌ 越界路径被拒绝 {p}")
                missing += 1
                continue
            if os.path.exists(full):
                p = full
        elif os.path.isabs(p):
            # 绝对路径同样走权限判定：防探测磁盘任意文件的存在性与大小
            ok_abs, _ = permissions.check_filesystem(p, write=False)
            if not ok_abs:
                lines.append(f"❌ 越界路径被拒绝 {p}")
                missing += 1
                continue
        if os.path.isfile(p):
            try:
                size = os.path.getsize(p)
                lines.append(f"✅ 存在 {p}（{size} 字节）")
            except OSError:
                lines.append(f"✅ 存在 {p}（大小未知）")
            exist += 1
        else:
            lines.append(f"❌ 缺失 {p}")
            missing += 1
    lines.append(f"核验结果：{exist} 个存在 / {missing} 个缺失")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "git",
                "description": "本地 Git 版本管理：init/status/add/commit/diff/log/checkout/branch。开发项目时 init 建仓、改一段 commit 一段、改坏用 checkout 回滚",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "操作：init/status/add/commit/diff/log/checkout/branch"},
                        "path": {"type": "string", "description": "可选：目标目录（不填用工作目录）"},
                        "message": {"type": "string", "description": "commit 时的提交说明"},
                        "target": {"type": "string", "description": "checkout 的文件路径或分支名；branch 的分支名"},
                        "files": {"type": "string", "description": "add 时要暂存的文件（默认 .）"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='本地 Git 版本管理',
    preactivate=(('git', 'commit', '提交', '回滚', '版本控制', '版本管理', '仓库'),),
)
def git_tool(action, path=None, message=None, target=None, files=None):
    """本地 Git 版本管理（项目开发的安全网）。

    action:
      init      初始化仓库
      status    查看未提交改动（git status --short）
      add       暂存文件（files 或 target 指定，默认 .）
      commit    提交（需 message）
      diff      查看差异（git diff）
      log       提交历史（最近 20 条）
      checkout  回滚文件（target=文件路径，git checkout -- file）或切换分支（target=分支名）
      branch    列出分支 / 创建分支（target=分支名）

    操作限定在允许目录内；不提供 reset --hard 等破坏性命令，回退一律走 checkout。
    """
    import subprocess

    base = path or _dc.WORKING_DIR or permissions.WORKSPACE_DIR or os.getcwd()
    write_op = action in ("init", "add", "commit", "checkout", "branch")
    ok, reason = permissions.check_filesystem(base, write=write_op)
    if not ok:
        return reason
    base = permissions.resolve(base) or base
    if os.path.isfile(base):
        base = os.path.dirname(base)

    def _run(args, cwd):
        try:
            r = subprocess.run(
                ["git"] + args, cwd=cwd, capture_output=True, text=True,
                timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            return out, r.returncode
        except FileNotFoundError:
            return "错误：本机未安装 git（或不在 PATH 中）", 1
        except Exception as e:
            return f"git 执行失败：{e}", 1

    a = (action or "").strip().lower()
    if a == "init":
        out, _ = _run(["init"], base)
        return out or "仓库已初始化"
    if a == "status":
        out, _ = _run(["status", "--short"], base)
        return out or "工作区干净（无未提交改动）"
    if a == "add":
        tgt = files or target or "."
        out, _ = _run(["add", "--", tgt], base)
        return out or "已暂存"
    if a == "commit":
        if not (message or "").strip():
            return "错误：commit 需要 message（提交说明）"
        out, _ = _run(["commit", "-m", message.strip()], base)
        return out or "已提交"
    if a == "diff":
        out, _ = _run(["diff"], base)
        return out or "无未提交改动"
    if a == "log":
        out, _ = _run(["log", "--oneline", "-20"], base)
        return out or "暂无提交"
    if a == "checkout":
        if not target:
            return "错误：checkout 需要 target（文件路径或分支名）"
        if os.path.isfile(os.path.join(base, target)):
            out, _ = _run(["checkout", "--", target], base)
        else:
            out, _ = _run(["checkout", target], base)
        return out or "已回滚/切换"
    if a == "branch":
        if target:
            out, _ = _run(["branch", target], base)
        else:
            out, _ = _run(["branch"], base)
        return out or "（无分支）"
    return "错误：未知 action，可用 init/status/add/commit/diff/log/checkout/branch"


@tool(
        {
            "type": "function",
            "function": {
                "name": "notify_desktop",
                "description": "发送 Windows 桌面 Toast 通知（离线可用）：任务完成、定时任务触发、长任务结束时提醒",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "可选：通知标题（默认 鲸语提醒）"},
                        "text": {"type": "string", "description": "通知正文"},
                        "fallback_sound": {"type": "boolean", "description": "可选：系统通知音被禁用时是否播放备用提示音（默认 true）"},
                    },
                    "required": ["text"],
                },
            },
        },
    groups=['🖱 桌面自动化'],
    phrases='桌面通知',
    preactivate=(('桌面通知', 'toast', '弹通知', '提醒通知'),),
)
def notify_desktop(title="鲸语提醒", text="", fallback_sound=True):
    """Windows 桌面 Toast 通知（离线可用，任务完成/定时任务触发时使用）。
    fallback_sound=False：toast 失败时不播兜底提示音（用户已关闭完成提示音的场景）。"""
    if not str(text or "").strip():
        return "错误：text 必填"
    title = str(title or "鲸语提醒")[:60]
    body = str(text).strip()[:300]
    try:
        import tempfile

        fd, ps_path = tempfile.mkstemp(suffix=".ps1")
        os.close(fd)
        try:
            title_quoted = "'" + str(title).replace("'", "''") + "'"
            body_quoted = "'" + body.replace("'", "''") + "'"
            # 先替换标题为哨兵，再替换正文，最后回填标题：防止标题/正文互相包含对方占位符
            title_sentinel = "__WHALETALK_TITLE__"
            script = _NOTIFY_PS.replace("@TITLE@", title_sentinel)
            script = script.replace("@BODY@", body_quoted)
            script = script.replace(title_sentinel, title_quoted)
            with open(ps_path, "w", encoding="utf-8-sig") as f:
                f.write(script)
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_path],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0:
                # Toast 不可用（老系统/受限环境）时兜底为提示音（可关闭）
                if fallback_sound:
                    try:
                        import winsound

                        winsound.Beep(880, 250)
                        winsound.Beep(660, 250)
                    except Exception:
                        pass
                return f"通知显示失败{'（已静音）' if not fallback_sound else '（已播放提示音）'}：{(proc.stderr or '')[:150]}"
            return f"已发送桌面通知：{title}"
        finally:
            try:
                os.remove(ps_path)
            except OSError:
                pass
    except Exception as e:
        return f"错误：通知失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "app_manage",
                "description": "Windows 应用安装与卸载管理（winget/choco 自动选择）：列出已装软件、搜索、静默安装、卸载、检查可升级。安装/卸载前应先向用户确认",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["managers", "list", "search", "install", "uninstall", "upgrade"], "description": "操作类型（默认 list 列出已安装应用）"},
                        "query": {"type": "string", "description": "list 时为过滤关键字；search/install/uninstall 时为软件名或包 ID（必填）"},
                        "source": {"type": "string", "enum": ["auto", "winget", "choco"], "description": "可选：指定包管理器（默认 auto 自动选）"},
                    },
                },
            },
        },
    groups=['📦 应用与环境'],
    phrases='应用安装/卸载管理（winget/choco）',
    preactivate=(('装软件', '卸载软件', '应用管理', '安装程序', '软件列表'),),
)
def app_manage(action="list", query="", source="auto"):
    """Windows 应用安装与卸载管理。

    action：
      managers — 检测本机可用的包管理器（winget/choco）
      list     — 列出本机已安装应用（注册表枚举，query 可过滤）
      search   — 在包管理器中搜索软件（query 必填）
      install  — 安装软件（query 为名称或 --id 的 ID；敏感操作，请先向用户确认）
      uninstall— 卸载软件（query 为包管理器中的 ID/名称；敏感操作，请先向用户确认）
      upgrade  — 列出有可用更新的软件
    """
    act = str(action or "list").strip().lower()
    q = str(query or "").strip()
    winget = _which_any("winget")
    choco = _which_any("choco")
    base_timeout = permissions.shell_timeout()

    def _pkg_argv(op, args_extra):
        use_winget = (source == "winget" or source == "auto") and winget
        if use_winget:
            return [winget] + op + args_extra
        if (source == "choco" or source == "auto") and choco:
            return ["choco"] + op + args_extra
        return None

    if act == "managers":
        lines = [
            f"- winget：{'✅ ' + winget if winget else '❌ 未安装'}",
            f"- choco ：{'✅ ' + choco if choco else '❌ 未安装'}",
        ]
        hint = "" if (winget or choco) else "\n提示：两者均未安装，可先用 app_manage 安装（winget 需 Microsoft Store「应用安装程序」；choco 安装命令见 chocolatey.org）"
        return "\n".join(lines) + hint

    if act == "list":
        apps = _win_installed_apps()
        if not apps:
            return "未枚举到已安装应用（非 Windows 或注册表不可读）"
        ql = q.lower()
        rows = [
            (name, meta.get("version", ""), meta.get("publisher", ""))
            for name, meta in sorted(apps.items())
            if not ql or ql in name.lower()
        ]
        total = len(rows)
        rows = rows[:80]
        lines = [f"{n}｜版本 {v or '?'}｜{pub}"[:150] for n, v, pub in rows]
        tail = f"\n—— 共 {total} 项{'（截取前 80 条）' if total > 80 else ''} ——"
        permissions.audit("app_manage", "list", str(q)[:60])
        return "\n".join(lines) + tail

    if act == "upgrade":
        argv = _pkg_argv(["upgrade"], [])
        if not argv:
            return "错误：未找到可用包管理器（winget/choco）。可先运行 app_manage(action='managers') 查看"
        rc, out = _proc_capture(argv, max(base_timeout, 300))
        permissions.audit("app_manage", "upgrade", out[:60])
        return f"退出码 {rc}\n{out.strip() or '（无输出）'}"

    if act in ("search", "install", "uninstall"):
        if not q:
            return f"错误：action={act} 时 query 必填"
        if act == "search":
            argv = _pkg_argv(["search"], [q])
            timeout = max(base_timeout, 120)
        elif act == "install":
            if winget and (source != "choco"):
                argv = [winget, "install", "--id", q, "--silent", "--accept-package-agreements", "--accept-source-agreements"]
            else:
                argv = ["choco", "install", "-y", q]
            timeout = max(base_timeout, 900)
        else:
            if winget and (source != "choco"):
                argv = [winget, "uninstall", "--id", q]
            else:
                argv = ["choco", "uninstall", "-y", q]
            timeout = max(base_timeout, 600)
        if not argv:
            return "错误：未找到可用包管理器（winget/choco），无法执行该操作。可让用户手动安装包管理器后再试"
        rc, out = _proc_capture(argv, timeout)
        permissions.audit("app_manage", act, f"{os.path.basename(argv[0])} {q}"[:80])
        verdict = "成功" if rc == 0 else "失败（可尝试改用 --id 包 ID，或先 search 确认准确标识）"
        return f"{act} {q}：{verdict}（退出码 {rc}）\n{out.strip() or '（无输出）'}"

    return f"错误：未知 action={act}（支持 managers/list/search/install/uninstall/upgrade）"


@tool(
        {
            "type": "function",
            "function": {
                "name": "usage_report",
                "description": "生成用量洞察报告（近 N 天 token/费用/缓存命中/逐日明细），可配合定时任务每周自动生成",
                "parameters": {
                    "type": "object",
                    "properties": {"days": {"type": "integer", "description": "可选：统计最近 N 天（默认 7，最大 90）"}},
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='用量/费用统计',
    preactivate=(('用量', '费用统计', 'token统计', '花费多少', '花了多少'),),
)
def usage_report(days=7):
    """生成用量洞察报告（按天/模型汇总 token 与估算费用）。"""
    if not _dc.STATS_FILE or not os.path.exists(_dc.STATS_FILE):
        return "暂无用量统计数据"
    try:
        days = max(1, min(90, int(days or 7)))
    except (TypeError, ValueError):
        days = 7
    try:
        import stats as stats_mod
        from datetime import date as _date, timedelta

        data = stats_mod.load_stats(_dc.STATS_FILE)
        totals = stats_mod.empty_day()
        model_usage = {}
        per_day = []
        for i in range(days - 1, -1, -1):
            d = (_date.today() - timedelta(days=i)).isoformat()
            day_data = data.get(d)
            if not day_data:
                continue
            day_total = stats_mod.day_total(data, d)
            if not any(day_total.values()):
                continue
            per_day.append(
                f"{d}: 输入 {day_total['prompt']:,} / 输出 {day_total['completion']:,}"
                f" / 缓存命中 {day_total['cache_hit']:,}"
            )
            for k in totals:
                totals[k] += day_total[k]
            for model, usage in day_data.items():
                acc = model_usage.setdefault(model, stats_mod.empty_day())
                for k in acc:
                    acc[k] += usage.get(k, 0)
        if not any(totals.values()):
            return f"近 {days} 天没有使用记录"
        hit_ratio = totals["cache_hit"] / max(1, totals["prompt"])
        lines = [
            f"近 {days} 天用量报告：",
            f"输入 {totals['prompt']:,} / 输出 {totals['completion']:,} token，"
            f"缓存命中 {totals['cache_hit']:,}（{hit_ratio:.0%}）",
        ]
        for model, u in model_usage.items():
            lines.append(f"模型 {model}: 输入 {u['prompt']:,} / 输出 {u['completion']:,} / 费用约 ¥{stats_mod.estimate_cost(u, model):.2f}")
        if per_day:
            lines.append("逐日明细：\n" + "\n".join(per_day))
        return "\n".join(lines)
    except Exception as e:
        return f"错误：生成报告失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "create_plugin",
                "description": "根据用户需求生成并安装鲸语插件：组合自定义工具/技能模板/自动化流程/场景配置，生成后立即生效。适合『添加一个XX工具』『创建一个XX流程』『帮我加个小红书文案技能』等需求",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "插件名称（简短，如 小红书文案助手）"},
                        "description": {"type": "string", "description": "可选：插件说明"},
                        "tools": {
                            "type": "array",
                            "description": "可选：自定义 HTTP 工具列表，每项 {name, endpoint, description, method, params}（params 为逗号分隔的参数名）",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "工具名（英文标识）"},
                                    "endpoint": {"type": "string", "description": "HTTP 地址（http/https）"},
                                    "description": {"type": "string", "description": "工具描述（AI 何时调用）"},
                                    "method": {"type": "string", "description": "可选：POST/GET（默认 POST）"},
                                    "params": {"type": "string", "description": "可选：参数名，逗号分隔，如 topic, style"},
                                },
                            },
                        },
                        "skills": {
                            "type": "array",
                            "description": "可选：技能/提示词模板，每项 {name, text}（text 中 {{TEXT}} 会被输入框内容替换）",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "技能名"},
                                    "text": {"type": "string", "description": "提示词模板内容"},
                                },
                            },
                        },
                        "workflows": {
                            "type": "object",
                            "description": "可选：自动化流程 {流程名: {steps: [{text: 指令}]}}",
                            "additionalProperties": {"type": "object"},
                        },
                        "scenario": {
                            "type": "object",
                            "description": "可选：一键场景配置 {name, thinking, system_prompt, enabled_tools}",
                        },
                        "requires": {
                            "type": "array",
                            "description": "可选：依赖的 pip 包名列表",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["name"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='创建用户插件',
    preactivate=(('创建插件', '加个插件', '写个技能', '做一个插件'),),
)
def create_plugin(name, description="", tools=None, skills=None, workflows=None,
                  scenario=None, requires=None):
    """AI 生成并安装插件：根据需求组合工具/技能/流程/场景，生成后立即生效。

    内部走审批闸门（ACTION_TOOLS 注册）；安装后可在「工具 → 插件管理」停用/卸载。
    """
    name = str(name or "").strip()
    if not name:
        return "错误：name 必填（插件名称）"
    contents = {}
    if tools:
        converted = [_to_tool_schema(t) for t in tools] if isinstance(tools, list) else [_to_tool_schema(tools)]
        converted = [t for t in converted if t]
        if converted:
            contents["tools"] = converted
    if skills:
        contents["skills"] = skills if isinstance(skills, list) else [skills]
    if workflows:
        contents["workflows"] = workflows
    if scenario:
        contents["scenario"] = scenario
    if not contents:
        return "错误：插件需要至少一项能力（tools / skills / workflows / scenario）"
    plugin = {
        "format": plugins_mod.PLUGIN_FORMAT,
        "version": 1,
        "meta": {
            "name": name[:40],
            "description": str(description or "")[:200],
            "author": "鲸语 AI",
            "version": "1.0.0",
        },
        "requires": [str(r).strip() for r in (requires or []) if str(r).strip()],
        "contents": contents,
    }
    ok, err = plugins_mod.validate_plugin(plugin)
    if not ok:
        return f"错误：插件校验失败：{err}"
    if not _dc.PLUGIN_PATHS:
        return "错误：插件模块未初始化"
    res = plugins_mod.apply_plugin(plugin, _dc.PLUGIN_PATHS)
    if not res.get("ok"):
        return f"错误：插件安装失败：{res.get('error')}"
    added = res.get("added") or {}
    parts = []
    if added.get("tools"):
        parts.append(f"工具 {'、'.join(added['tools'])}")
    if added.get("skills"):
        parts.append(f"技能 {'、'.join(added['skills'])}")
    if added.get("workflows"):
        parts.append(f"流程 {'、'.join(added['workflows'])}")
    if scenario:
        parts.append("场景配置（可在插件管理中应用）")
    miss = plugins_mod.missing_requires(plugin)
    note = f"\n⚠ 缺失依赖：{'、'.join(miss)}（pip install …，可在「依赖状态」查看）" if miss else ""
    return (
        f"✅ 插件「{name}」已生成并安装：{'；'.join(parts) or '空'}。\n"
        f"安装目录：{res.get('path')}{note}\n"
        "可在「工具 → 插件管理」查看、停用或卸载；插件可导出 .wtplugin 分享给他人。"
    )


__all__ = ['create_plugin', 'watch_files', 'recall_session', 'project_info', 'read_project_file', 'create_evolution', 'self_evolve', 'verify_files', 'git_tool', 'notify_desktop', 'app_manage', 'usage_report']
