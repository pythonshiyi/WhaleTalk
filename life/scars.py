"""代价记录（brain/scars.jsonl）。

不是惩罚，也不是安全日志：它记录"我失去了什么、做错了什么、付出了什么"，
让睡眠叙事与自我模型有重量。只追加，不删除。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

_LOCK = threading.RLock()


def _brain_dir() -> Path:
    try:
        import brainkit as bk
        return Path(bk.BRAIN_DIR)
    except Exception:
        return Path(__file__).resolve().parent.parent / "brain"


def path() -> Path:
    return _brain_dir() / "scars.jsonl"


def _iso(ts=None) -> str:
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def add(kind, what, cost="", lesson="", ref="", data=None) -> dict:
    ev = {
        "id": "sc-" + uuid.uuid4().hex[:10],
        "ts": _iso(),
        "kind": str(kind or "loss")[:40],
        "what": str(what or "")[:300],
        "cost": str(cost or "")[:120],
        "lesson": str(lesson or "")[:300],
        "ref": str(ref or "")[:80],
        "data": data if isinstance(data, dict) else {},
    }
    p = path()
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    try:
        from . import timeline
        timeline.append("scar", summary=f"{ev['kind']}: {ev['what'][:120]}",
                        data={"id": ev["id"], "cost": ev["cost"], "lesson": ev["lesson"]},
                        actor="self", ref=ev["ref"])
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    return ev


def list_recent(limit=20) -> list:
    p = path()
    if not p.exists():
        return []
    out = []
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if isinstance(ev, dict):
                    out.append(ev)
    except OSError:
        return []
    return out[-max(1, int(limit or 20)):]


def prune(max_items=5000) -> int:
    """把最旧的代价记录移入 scars_archive.jsonl，避免单文件无限增长。"""
    p = path()
    if not p.exists():
        return 0
    max_items = max(100, int(max_items or 5000))
    try:
        lines = [x for x in p.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
    except OSError:
        return 0
    if len(lines) <= max_items:
        return 0
    moved = lines[:-max_items]
    kept = lines[-max_items:]
    try:
        with (p.parent / "scars_archive.jsonl").open("a", encoding="utf-8") as f:
            for line in moved:
                f.write(line + "\n")
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError:
        return 0
    return len(moved)


def summary() -> dict:
    items = list_recent(200)
    counts = {}
    for x in items:
        k = str(x.get("kind") or "loss")
        counts[k] = counts.get(k, 0) + 1
    return {"total": len(items), "counts": counts, "recent": items[-5:]}
