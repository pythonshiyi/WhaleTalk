"""清理成功模式库（data/patterns.json）里的历史噪声。

为什么要有这个工具
------------------
`_record_success_pattern` 的准入是 v3.26 才加的。此前**任何"没报错"的调用都会入库**，
包括诊断脚本、探路命令、单元测试残留（曾出现过 8 条 tool0–tool7）。这些旧条目没有
`args_sig` 字段，绕过了新的准入逻辑，至今仍占着窗口。

注入端靠「通用工具限 1 席」把它们压住了，所以表面看不出来；但库里真实分布是
`run_python 17 / run_command 7 / 其余 6`——一旦限额调大或做细分，噪声会立刻浮现。

清洗规则（不是"按有没有 args_sig 删"）
--------------------------------------
按字段格式删会误伤：`write_file`、`list_dir`、`get_weather` 这些**参数化调用**同样没有
args_sig（写入时还没这套逻辑），却是有复用价值的记录。真正的判据是**这条记录是不是
可复用的调用模式**：

1. 无参数 / 结果仅 "ok" 等无信息量回执 → 删（探路）
2. 工具名形如 toolN → 删（测试残留）
3. **通用逃生舱**（run_python / run_command / run_shell / run_powershell）→ 删。
   它们的 args 是一次性脚本代码（`import os, json\\n...`），不是可复用的调用模式；
   而"我会用 Python"这件事模型本来就知道。
4. 其余旧条目**补上 args_sig** 迁移到新格式，而不是删除——有价值的数据不丢。

用法
----
    python tools/clean_patterns.py                 # 只报告，不动手（默认）
    python tools/clean_patterns.py --yes           # 执行（自动备份 .bak_<时间戳>）
    python tools/clean_patterns.py --path <文件>   # 指定文件（测试用）

退出码：0 = 干净或无变更；1 = 执行失败。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

GENERIC_TOOLS = frozenset({
    "run_python", "run_command", "run_shell", "run_powershell",
})
NOISE_RESULTS = frozenset({"", "ok", "success", "true", "false", "none", "done", "null"})


def _sig(args):
    return hashlib.md5(str(args or "").encode("utf-8")).hexdigest()[:12]


def classify(entry):
    """判定一条记录的处置。返回 ("keep"|"migrate"|"drop", 原因)。"""
    if not isinstance(entry, dict):
        return "drop", "非对象"
    tool = str(entry.get("tool") or "").strip()
    args = str(entry.get("args") or "").strip()
    result = str(entry.get("result") or "").strip()

    if not tool:
        return "drop", "无工具名"
    if not args:
        return "drop", "无参数（探路/测试残留，无法判定为什么成功）"
    if result.lower() in NOISE_RESULTS:
        return "drop", "结果无信息量"
    if tool.lower().startswith("tool") and tool[4:].isdigit():
        return "drop", "测试残留工具名"
    if tool in GENERIC_TOOLS:
        return "drop", "通用逃生舱（args 是一次性脚本，非可复用调用模式）"
    if not entry.get("args_sig"):
        return "migrate", "旧格式，补 args_sig"
    return "keep", ""


def plan(entries):
    """返回 (保留后的列表, 明细)。纯函数，便于测试。"""
    out, detail = [], []
    for e in entries:
        action, why = classify(e)
        # 非字典条目（None/"x"/42）也要能出明细——`(e or {}).get` 对字符串会炸
        tool = str(e.get("tool") or "") if isinstance(e, dict) else ""
        if action == "drop":
            detail.append(("drop", tool, why))
            continue
        if action == "migrate":
            e = dict(e)
            e["args_sig"] = _sig(e.get("args"))
            detail.append(("migrate", tool, why))
        else:
            detail.append(("keep", tool, why))
        out.append(e)
    return out, detail


def main(argv=None):
    ap = argparse.ArgumentParser(description="清理成功模式库的历史噪声")
    ap.add_argument("--path", default="", help="patterns.json 路径（默认 data/patterns.json）")
    ap.add_argument("--yes", action="store_true", help="真正执行（默认只报告）")
    args = ap.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = args.path or os.path.join(root, "data", "patterns.json")
    if not os.path.isfile(path):
        print(f"[跳过] {path} 不存在——还没有成功模式记录")
        return 0

    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[错误] 读取失败：{e}")
        return 1
    if not isinstance(entries, list):
        print("[错误] 顶层不是数组，拒绝处理")
        return 1

    kept, detail = plan(entries)
    n_drop = sum(1 for d in detail if d[0] == "drop")
    n_mig = sum(1 for d in detail if d[0] == "migrate")

    print(f"当前 {len(entries)} 条 → 清理后 {len(kept)} 条（删除 {n_drop}，迁移 {n_mig}）")
    if n_drop:
        by_tool = {}
        for a, tool, _why in detail:
            if a == "drop":
                by_tool[tool] = by_tool.get(tool, 0) + 1
        print("  将删除：", "、".join(f"{k}×{v}" for k, v in sorted(by_tool.items())))
    if n_mig:
        print("  将迁移：", "、".join(d[1] for d in detail if d[0] == "migrate"))

    if not args.yes:
        print("\n（只报告，未动手。加 --yes 执行，会自动备份 .bak_<时间戳>）")
        return 0
    if n_drop == 0 and n_mig == 0:
        print("无需变更")
        return 0

    bak = path + ".bak_" + time.strftime("%Y%m%d%H%M%S")
    try:
        shutil.copy2(path, bak)
    except OSError as e:
        print(f"[错误] 备份失败，已中止：{e}")
        return 1
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(kept, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        print(f"[错误] 写入失败（备份仍在 {bak}）：{e}")
        return 1
    print(f"\n已备份：{bak}")
    print(f"已写入：{path}（{len(kept)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
