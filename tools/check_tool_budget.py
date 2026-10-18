"""核心工具预算门禁（core tool budget）—— 「可以加，但要留痕」。

## 为什么需要这道闸

能力主干（capability.py）打通后，往仓里加功能有了两条路：

    加一个内置工具（改源码，_TOOL_ORDER +1）
    加一个能力包（运行时装载，源码不动）

两条路都合法，但**成本完全不同**：前者永久增加每次对话的 schema 预算、能力地图
条目与门禁面；后者可停用、可卸载、可单独排障。没有闸门时人一定会选前者——它更直接。

所以本门禁不做"禁止"，只做**留痕 + 一次辩护机会**：

- 新增内置工具 → 红灯，直到用 `--waive "<为什么不能做成能力包>"` 记录理由；
- 删除内置工具 → 同样红灯（能力面缩水也是需要被看见的变更），`--sync` 即可。

## 用法

    python tools/check_tool_budget.py                 # 校验（CI 用）
    python tools/check_tool_budget.py --list          # 打印当前预算清单
    python tools/check_tool_budget.py --waive "理由"   # 接受变更：更新清单 + 追加留痕
    python tools/check_tool_budget.py --sync          # 只同步删除（等价 waive 但要求理由可省）
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUDGET = Path(__file__).resolve().parent / "core_tool_budget.txt"
LOG = Path(__file__).resolve().parent / "core_tool_budget_log.txt"
DEEPSEEK = REPO / "deepseek_client.py"

# Windows 控制台码页编不出中文会让门禁"以崩代红"（本项目真实踩过：CI gate-tools
# 连续 4 个提交因为一句中文 print 崩掉）。所有 tools/*.py 一律加固。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: silent-except: 极老解释器无 reconfigure，退化即可
        pass


def _top_assign(tree, target):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == target:
                    return node.value
    return None


def current_core_tools() -> list:
    """从源码 AST 读核心工具清单（不 import：CI 无需依赖）。"""
    tree = ast.parse(DEEPSEEK.read_text(encoding="utf-8"))
    order = _top_assign(tree, "_TOOL_ORDER")
    if not isinstance(order, ast.List):
        raise SystemExit("[错误] deepseek_client.py 找不到 _TOOL_ORDER 列表")
    return [e.value for e in order.elts if isinstance(e, ast.Constant)]


def load_budget() -> list:
    if not BUDGET.exists():
        return []
    out = []
    for line in BUDGET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def save_budget(tools) -> None:
    lines = [
        "# 核心工具预算清单（tools/check_tool_budget.py 维护）",
        "# 每行 = 一个内置工具（源码 @tool() 声明、进 _TOOL_ORDER）。",
        f"# 当前共 {len(tools)} 项。新增/删除都必须走 waive 并留痕，理由见 core_tool_budget_log.txt。",
    ]
    BUDGET.write_bytes(("\n".join(lines + sorted(tools)) + "\n").encode("utf-8"))


def _git_user() -> str:
    try:
        r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def append_log(added, removed, reason) -> None:
    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = [f"{stamp} | {_git_user()} | +{len(added)} -{len(removed)} | {reason or '（未填写理由）'}"]
    if added:
        entry.append("  added: " + ", ".join(sorted(added)))
    if removed:
        entry.append("  removed: " + ", ".join(sorted(removed)))
    lines = []
    if LOG.exists():
        lines = LOG.read_text(encoding="utf-8").splitlines()
        if not lines:
            lines = ["# 核心工具预算变更留痕（每行一次变更：时间 | 作者 | 增减 | 理由）"]
    else:
        lines = ["# 核心工具预算变更留痕（每行一次变更：时间 | 作者 | 增减 | 理由）"]
    lines.extend(entry)
    LOG.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cur = current_core_tools()
    budget = load_budget()

    if "--list" in argv:
        for n in sorted(cur):
            print(n)
        print(f"\n共 {len(cur)} 项（预算清单 {len(budget)} 项）")
        return 0

    # 首次初始化时允许直接 --waive 建立基线（否则永远无法引导）
    if not budget and not ("--waive" in argv or "--sync" in argv):
        print(f"[缺失] {BUDGET.name} 不存在——先运行 --waive \"初始化预算清单\" 固化存量")
        return 1

    added = [n for n in cur if n not in set(budget)]
    removed = [n for n in budget if n not in set(cur)]

    waive_reason = ""
    for i, a in enumerate(argv):
        if a == "--waive" and i + 1 < len(argv):
            waive_reason = argv[i + 1]
            break

    if "--waive" in argv or "--sync" in argv:
        if "--waive" in argv and not waive_reason.strip():
            print("[错误] --waive 必须附理由（为什么这个能力不能做成能力包）")
            return 1
        if not waive_reason and "--sync" in argv:
            waive_reason = "同步（仅删除）"
        save_budget(cur)
        append_log(added, removed, waive_reason)
        print(f"已接受变更：+{len(added)} -{len(removed)}，清单更新为 {len(cur)} 项")
        if added:
            print("  新增: " + ", ".join(sorted(added)))
        if removed:
            print("  移除: " + ", ".join(sorted(removed)))
        print(f"  留痕: {LOG.name}")
        return 0

    if added or removed:
        if added:
            print(f"[新增内置工具] {len(added)} 项：" + ", ".join(sorted(added)))
            print("  内置工具会被每次对话的能力地图/预激活与门禁面永久承载。")
            print("  若该能力可以做成能力包（capability.py），请改为能力包；")
            print("  确需内置则显式留痕：python tools/check_tool_budget.py --waive \"理由\"")
        if removed:
            print(f"[移除内置工具] {len(removed)} 项：" + ", ".join(sorted(removed)))
            print("  删除同样需要留痕（能力面缩水要被看见）：--sync 或 --waive \"理由\"")
        return 1

    print(f"核心工具预算未变更：{len(cur)} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
