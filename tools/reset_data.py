"""数据重置：把鲸语恢复成「全新安装」的样子。

## 为什么做成工具而不是一次性 rm

「想要一个全新的程序，看看新程序长什么样」是会被反复提出的需求（换机、演示、
排障、验证首启流程）。手删目录既容易漏（一堆散落的 json），也容易多删（config 里
有 API Key）。把它做成带分级的命令，才能**可重复、可解释、可回滚**。

## 三个级别

    cache    只清可再生缓存：语音合成、浏览器配置、kv 缓存、日志、撤销栈、诊断
    business 缓存 + 业务数据（会话/记忆/统计/产物/上传/旧插件），**保留配置与密钥**
    all      business + 配置（等同全新安装：首次启动要重新填 API Key）

## 安全设计

- **默认只报告**（dry-run），什么都不动；真删要显式 `--yes`。
- 缓存直接删（可再生）；业务数据默认**移动**到 `<数据目录>/_archive/reset-<时间>/`，
  确认无误后可自行删除，或加 `--purge` 直接删。
- 只处理**已知名字**的条目：不遍历删除未知文件，避免把用户自己放进去的东西带走。
- config.json / permissions.json / secrets.json / *.token / profiles.json 属于"保留"，
  即便 `--level all` 也只移动不删除（重新安装本来就会重建它们）。

## 用法

    python tools/reset_data.py                          # 报告 Documents 目录与仓库 data/
    python tools/reset_data.py --level cache --yes      # 清缓存
    python tools/reset_data.py --level business --yes   # 清业务数据（保留配置）
    python tools/reset_data.py --level all --yes --purge  # 全新安装（不可回滚）
    python tools/reset_data.py --dir "C:/tmp/wt" --level business --yes
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: silent-except: 老解释器无 reconfigure，退化即可
        pass

# ── 分级清单（只列名字，不递归猜测）────────────────────────────────────
CACHE = [
    "voice", "browser_profile", "kv_cache", "logs", "undo", "diagnostics",
    "summaries", "temp", "tmp", "cache", "preactivate_hits.json",
]
LEGACY = [
    "plugins", "user_tools.json",
]
BUSINESS = [
    "sessions_index.json", "history", "sessions", "memory.json", "self_profile.json",
    "patterns.json", "failures.json", "failures_archive.json", "tasklog.json",
    "stats.json", "approvals.json", "checkpoints", "workflows.json", "prompts.json",
    "recent_outputs.json", "uploads", "workspace", "exports", "draft.json",
    "watch_state.json", "flybot.db", "egress.jsonl", "degradations.json",
    "first_run_done", "capabilities", "custom_themes.json",
]
KEEP = [
    "config.json", "permissions.json", "secrets.json", ".api_token", "profiles.json",
    "db_config.json", "email_config.json", "im_config.json", "gateway_keys.json",
    "user_roles.json", "webhooks.json", "gateway_config.json", "_archive",
]

LEVELS = {"cache": CACHE + LEGACY, "business": CACHE + LEGACY + BUSINESS,
          "all": CACHE + LEGACY + BUSINESS}
LEVEL_DESC = {
    "cache": "只清可再生缓存与旧插件残留（语音/浏览器/日志/撤销栈）",
    "business": "缓存 + 业务数据（会话/记忆/统计/产物/上传），保留配置与 API Key",
    "all": "全部（配置也移走）——等同全新安装，首次启动需重填 API Key",
}


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dp, _dn, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                continue
    return total


def _mb(n):
    return f"{n / 1024 / 1024:.1f} MB"


def candidate_dirs(explicit=None):
    """默认检查 Documents 数据目录 + 仓库 data/（开发实例）。"""
    if explicit:
        return [explicit]
    out = []
    home = os.path.expanduser("~")
    for p in (os.path.join(home, "Documents", "WhaleTalk"), os.path.join(REPO, "data")):
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def plan(directory, level):
    """返回 [(动作, 名称, 路径, 字节数)]；动作 in keep/move/delete。"""
    rows = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if name in KEEP:
            rows.append(("keep", name, path, _size(path)))
        elif name in LEVELS[level]:
            rows.append(("delete" if name in (CACHE + LEGACY) else "move", name, path, _size(path)))
        else:
            rows.append(("keep", name, path, _size(path)))
    rows.sort(key=lambda r: (r[0] != "keep", -r[3]))
    return rows


def _server_running(port=8745):
    """端口被占说明鲸语可能正在跑，此时动数据目录不安全。"""
    import socket
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def run(directory, level, *, yes=False, purge=False):
    rows = plan(directory, level)
    acts = [r for r in rows if r[0] != "keep"]
    freeable = sum(r[3] for r in acts)
    print(f"\n目录：{directory}")
    print(f"级别：{level} —— {LEVEL_DESC[level]}")
    print(f"将处理 {len(acts)} 项，合计 {_mb(freeable)}；其余 {len(rows) - len(acts)} 项保留\n")
    for act, name, _p, size in rows:
        flag = {"delete": "删除", "move": "归档", "keep": "保留"}[act]
        print(f"  [{flag}] {name:28} {_mb(size)}")
    if not acts:
        return 0
    if not yes:
        print("\n（未改动任何文件。确认后加 --yes 执行；缓存直接删，业务数据移到 _archive/reset-<时间>/）")
        return 0
    archive = os.path.join(directory, "_archive", "reset-" + time.strftime("%Y%m%d-%H%M%S"))
    failed = 0
    for act, name, path, _size_b in acts:
        try:
            if act == "delete":
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=False)
                else:
                    os.remove(path)
            elif purge:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=False)
                else:
                    os.remove(path)
            else:
                os.makedirs(archive, exist_ok=True)
                shutil.move(path, os.path.join(archive, name))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [失败] {name}: {type(e).__name__}: {e}")
    tail = "" if purge else f"\n业务数据已归档到：{archive}（确认无误后可自行删除）"
    print(f"\n完成：{len(acts) - failed}/{len(acts)} 项，释放约 {_mb(freeable)}。{tail}")
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="鲸语数据重置（默认只报告，不改动）")
    ap.add_argument("--level", choices=list(LEVELS), default="cache", help="重置级别（默认 cache）")
    ap.add_argument("--dir", default="", help="指定数据目录（默认检查 Documents\\WhaleTalk 与仓库 data/）")
    ap.add_argument("--yes", action="store_true", help="真正执行（缺省只报告）")
    ap.add_argument("--purge", action="store_true", help="业务数据也直接删除，不归档（不可回滚）")
    args = ap.parse_args(argv)

    if args.yes and _server_running():
        print("[中止] 检测到 127.0.0.1:8745 正在监听——请先关闭鲸语（托盘退出），"
              "运行中的进程会继续写这些文件。")
        return 2
    dirs = candidate_dirs(args.dir or None)
    if not dirs:
        print("未找到数据目录。可用 --dir 指定。")
        return 1
    for d in dirs:
        run(d, args.level, yes=args.yes, purge=args.purge)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
