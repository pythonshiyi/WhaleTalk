"""check_silent_except.py —— 「静默吞异常」存量门禁：只拦新增，不许再变多。

## 为什么

`except …: pass` 全项目有 **400+ 处**。它们不是代码质量洁癖问题，而是**可观测性问题**：
某处失效了，没人知道，表现只是「AI 好像变笨了」。项目已有 `degrade.py` 作为唯一出口，
但从 `pass` 到 `degrade(...)` 不能一次性全改（很多是无害的探测/清理）。

所以本门禁采用的策略与 `self_evolve` 的 lint 闸门一致——**基线差分**：

1. 存量按**指纹**固化到 `tools/silent_except_baseline.txt`（不按行号，避免改一行就全体失效）；
2. 新增的静默吞异常 → 红灯（要么改成 `degrade()`，要么显式写 `# noqa: silent-except` 说明无害）；
3. 修掉的 → 只是提示，可 `--update-baseline` 收紧基线。

指纹 = `文件 | 所在函数限定名 | except 类型 | try 首条语句摘要` 的 md5 前 12 位。
函数改名或 try 首句大改会变成「新指纹」——这是有意的保守：那确实是需要人看一眼的改动。

## 用法

    python tools/check_silent_except.py              # 门禁：有新增即退出码 1
    python tools/check_silent_except.py --report     # 按模块分组 + 启发式分类（只读报告）
    python tools/check_silent_except.py --update-baseline   # 收紧基线（修完一批后跑）
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "silent_except_baseline.txt"
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", "assets",
             "experiments", "trust"}          # trust/ 是内核快照，不参与门禁
NOQA_MARK = "noqa: silent-except"

# 报告用的启发式分类（只影响可读性，不影响门禁判定）
_CLEANUP_HINTS = ("os.path.", "os.remove", "os.unlink", ".close(", "rollback", "shutdown",
                  "kill(", "terminate(", "settimeout", "cancel(")
_OPTIONAL_HINTS = ("import ", "__import__", "find_spec", "ImportError", "ModuleNotFound")


def _iter_py(root: Path):
    for p in sorted(root.rglob("*.py")):
        parts = set(p.parts)
        if parts & SKIP_DIRS:
            continue
        yield p


def _first_stmt_src(node):
    try:
        return ast.unparse(node)[:100]
    except Exception:  # noqa: BLE001 - 3.8 兜底（unparse 3.9+），退化为类名
        return type(node).__name__


def _handler_type(node: ast.ExceptHandler):
    return ast.unparse(node.type) if node.type is not None else "bare"


def _is_silent(handler: ast.ExceptHandler) -> bool:
    """只认「函数体恰好一条 pass」——这类才是真正无声的失败。"""
    body = [s for s in handler.body if not (isinstance(s, ast.Expr)
                                            and isinstance(s.value, ast.Constant)
                                            and isinstance(s.value.value, str))]
    return len(body) == 1 and isinstance(body[0], ast.Pass)


def _has_noqa(lines, lineno) -> bool:
    """同一行或上一行写了豁免注释的，视为已说明的无害跳过。"""
    for i in (lineno - 1, lineno - 2):
        if 0 <= i < len(lines) and NOQA_MARK in lines[i]:
            return True
    return False


def _parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _qualname(node, parents):
    """从节点向上回溯，得到「类.函数」限定名（不含行号，函数改名才算变）。"""
    names = []
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(cur.name)
        cur = parents.get(id(cur))
    return ".".join(reversed(names)) or "<module>"


def _try_nodes(tree):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            out.append(node)
        else:
            star = getattr(ast, "TryStar", None)
            if star is not None and isinstance(node, star):
                out.append(node)
    return out


def scan(root: Path = REPO):
    """返回 [{file, line, func, etype, fingerprint, kind}]。"""
    out = []
    for path in _iter_py(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except (SyntaxError, OSError):
            continue
        lines = text.splitlines()
        rel = str(path.relative_to(root)).replace("\\", "/")
        parents = _parent_map(tree)
        seen = {}          # 同一函数里「同类型 + 同 try 首句」的第几处（去重靠它）
        for try_node in _try_nodes(tree):
            silent = [h for h in try_node.handlers if _is_silent(h)]
            if not silent:
                continue
            try_src = _first_stmt_src(try_node.body[0]) if try_node.body else ""
            func = _qualname(try_node, parents)
            context = " ".join(lines[max(0, try_node.lineno - 8):try_node.lineno + 2])
            base_sig = "|".join([rel, func, _handler_type(silent[0]), try_src])
            ordinal = seen.get(base_sig, 0)
            seen[base_sig] = ordinal + 1
            for handler in silent:
                if _has_noqa(lines, handler.lineno):
                    continue
                # 序号进指纹：同一函数里两处「长得一样」的静默点必须能各自被计数，
                # 否则多写一处也不会进红灯（保守取向：插入顺序变化会触发一次人工确认）。
                sig = "|".join([rel, func, _handler_type(handler), try_src, str(ordinal)])
                fp = hashlib.md5(sig.encode("utf-8")).hexdigest()[:12]
                kind = "cleanup" if any(h in context for h in _CLEANUP_HINTS) else (
                    "optional" if any(h in context for h in _OPTIONAL_HINTS) else "review")
                out.append({"file": rel, "line": handler.lineno, "func": func,
                            "etype": _handler_type(handler), "fingerprint": fp, "kind": kind})
    return out


def load_baseline(path: Path = BASELINE):
    if not path.exists():
        return set()
    items = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.add(line.split()[0])
    return items


def save_baseline(entries, path: Path = BASELINE):
    fps = sorted({e["fingerprint"] for e in entries})
    header = [
        "# 静默吞异常基线（tools/check_silent_except.py 维护）",
        "# 每行 = 一个「except 体只有 pass」位置的指纹；新增位置必须走 degrade() 或加豁免注释。",
        f"# 生成时间见 git 提交历史；当前共 {len(fps)} 处。",
    ]
    path.write_text("\n".join(header + fps) + "\n", encoding="utf-8")
    return len(fps)


def diff(entries, baseline):
    current = {e["fingerprint"]: e for e in entries}
    new = [e for fp, e in current.items() if fp not in baseline]
    fixed = sorted(baseline - set(current))
    return new, fixed


def report(entries):
    by_file = {}
    for e in entries:
        by_file.setdefault(e["file"], []).append(e)
    kinds = {"cleanup": 0, "optional": 0, "review": 0}
    for e in entries:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"静默吞异常（except 体只有 pass）共 {len(entries)} 处")
    print(f"启发式分类：资源探测/清理 {kinds['cleanup']} · 可选依赖 {kinds['optional']} · "
          f"待人工评估 {kinds['review']}")
    print("\n待人工评估最多的模块（Top 12）：")
    ranked = sorted(by_file.items(), key=lambda kv: -len([e for e in kv[1] if e["kind"] == "review"]))
    for f, items in ranked[:12]:
        review = len([e for e in items if e["kind"] == "review"])
        print(f"  {review:>3} 待评 / {len(items):>3} 总数  {f}")
    print("\n提示：`review` 不等于必须改——先看它在失败时会不会让用户看到假象；"
          "会有假象的才补 degrade()。")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    entries = scan()
    if "--report" in argv or "--list" in argv:
        report(entries)
        return 0
    if "--update-baseline" in argv:
        n = save_baseline(entries)
        print(f"基线已更新：{BASELINE.name}（{n} 处）")
        return 0
    baseline = load_baseline()
    if not baseline:
        print(f"[缺失] {BASELINE.name} 不存在——先运行 --update-baseline 固化存量")
        return 1
    new, fixed = diff(entries, baseline)
    if fixed:
        print(f"[提示] 有 {len(fixed)} 处基线位置已不存在（修掉了或函数改名了）；"
              f"确认后可用 --update-baseline 收紧基线")
    if new:
        print(f"[新增] 发现 {len(new)} 处新的静默吞异常（except 体只有 pass）：")
        for e in sorted(new, key=lambda x: (x["file"], x["line"]))[:40]:
            print(f"  {e['file']}:{e['line']}  在 {e['func']}  except {e['etype']}")
        print("\n处理方式二选一：① 改为 degrade(component, e, \"影响说明\", critical=?)；"
              "② 确认无害则在 except 行上方加 `# noqa: silent-except: 原因`。")
        return 1
    print(f"静默吞异常门禁通过：{len(entries)} 处，全部在基线内（无新增）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
