# -*- coding: utf-8 -*-
"""从会话记录恢复被误删的进化提案（救援脚本）。

背景：`_evolution_ignore` 曾用 `shutil.rmtree` 硬删提案，叠加 `evolutions/` 在
.gitignore 中 → 一次「忽略」即永久丢失（曾吃掉 4 份提案，见 evolutions/_RESTORED.md）。

唯一副本来源：`data/history/sessions/*.json` 里 `create_evolution` 的**调用入参**
（`files: [{path, content}]` 含各文件完整内容）。

用法：
    python tools/_restore_proposals.py            # 恢复所有「会话里有、目录里没有」的提案
    python tools/_restore_proposals.py --dry-run  # 只列出可恢复项，不落盘

说明：恢复后入口页 `EVOLUTION.md` 用当前 `_evolve_stub`（G17 修复后的生成器）补写；
已存在的提案目录会被跳过，不会覆盖。
"""
import argparse
import glob
import io
import json
import os
import sys

SEP = chr(92)  # 反斜杠（避免转义地狱）


def _collect_create_evolution():
    """从会话记录收集全部 create_evolution 调用，返回 {name: files}。"""
    found = {}
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in sorted(glob.glob(os.path.join(base, "data", "history", "sessions", "*.json"))):
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            continue
        for m in d.get("messages") or []:
            if not isinstance(m, dict):
                continue
            for tc in (m.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function")
                if not isinstance(fn, dict) or fn.get("name") != "create_evolution":
                    continue
                raw = fn.get("arguments")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except Exception:
                    continue
                if isinstance(args, dict) and args.get("name") and args.get("files"):
                    found.setdefault(str(args["name"]), args.get("files") or [])
    return found


def _restore(name, files, dry_run=False):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    evo = os.path.join(base, "evolutions")
    # 找同名（可能带时间戳后缀）已存在的目录，有则跳过
    for d in os.listdir(evo) if os.path.isdir(evo) else []:
        if d.split("_2026")[0].split("_2025")[0] == name or d == name:
            return None  # 已存在，跳过
    if dry_run:
        return files
    # 恢复为一个带时间戳的新目录（与原目录名结构一致）
    import time
    slug = f"{name}_{time.strftime('%Y%m%d_%H%M%S')}"
    branch = os.path.join(evo, slug)
    contents = {}
    for f in files:
        if not isinstance(f, dict):
            continue
        rel = str(f.get("path") or "").replace(SEP, "/")
        full = os.path.join(branch, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        io.open(full, "w", encoding="utf-8", newline="").write(str(f.get("content") or ""))
        contents[rel] = str(f.get("content") or "")
    # 延迟导入：避免污染调用方（需要 deepseek_client 先加载）
    from agent_tools.tool_system import _evolve_stub
    io.open(os.path.join(branch, "EVOLUTION.md"), "w", encoding="utf-8", newline="").write(
        _evolve_stub(name, contents)
    )
    return slug


def main(argv=None):
    ap = argparse.ArgumentParser(description="从会话记录恢复被误删的进化提案")
    ap.add_argument("--dry-run", action="store_true", help="只列出可恢复项")
    args = ap.parse_args(argv)

    found = _collect_create_evolution()
    print(f"会话记录中发现 {len(found)} 条 create_evolution 记录")
    restored = 0
    for name, files in found.items():
        r = _restore(name, files, dry_run=args.dry_run)
        if r is None:
            print(f"  - 跳过（已存在）：{name}")
        else:
            restored += 1
            print(f"  {'可恢复' if args.dry_run else '已恢复'}：{name} → {r}（{len(files)} 文件）")
    print(f"\n{'可恢复' if args.dry_run else '已恢复'} {restored} 项")


if __name__ == "__main__":
    main()
