"""生命循环指标（brain/metrics.jsonl）。

只记录可量化的事实：tick 延迟、反思成功/失败、睡眠、注入耗时。
append-only，睡眠时裁剪；读取只聚合最近 N 天，不参与任何决策。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_LOCK = threading.RLock()


def _brain_dir() -> Path:
    try:
        import brainkit as bk
        return Path(bk.BRAIN_DIR)
    except Exception:
        return Path(__file__).resolve().parent.parent / "brain"


def path() -> Path:
    return _brain_dir() / "metrics.jsonl"


def _iso(ts=None) -> str:
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def record(name, value=1.0, **data) -> None:
    """追加一条指标；任何 IO 失败都静默（指标不能影响生命流程）。"""
    name = str(name or "event")[:40]
    try:
        val = float(value)
    except (TypeError, ValueError):
        val = 1.0
    ev = {"ts": _iso(), "name": name, "value": val}
    if data:
        ev["data"] = data
    p = path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        return


def _read(since=None) -> list:
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
                if not isinstance(ev, dict):
                    continue
                if since and str(ev.get("ts") or "") < str(since):
                    continue
                out.append(ev)
    except OSError:
        return []
    return out


def summary(days=1, limit=2000) -> dict:
    days = max(1, min(365, int(days or 1)))
    since = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    items = _read(since=since)[-max(1, int(limit or 2000)):]
    agg = {}
    for ev in items:
        name = str(ev.get("name") or "event")
        row = agg.setdefault(name, {"count": 0, "sum": 0.0, "max": 0.0, "last": ""})
        row["count"] += 1
        try:
            v = float(ev.get("value") or 0)
        except (TypeError, ValueError):
            v = 0.0
        row["sum"] += v
        row["max"] = max(row["max"], v)
        row["last"] = str(ev.get("ts") or row["last"])
    for row in agg.values():
        row["avg"] = round(row["sum"] / row["count"], 3) if row["count"] else 0.0
        row["sum"] = round(row["sum"], 3)
        row["max"] = round(row["max"], 3)
    return {"days": days, "events": len(items), "metrics": agg}


def prune(max_lines=5000) -> int:
    """只保留最后 max_lines 行；返回删除行数。"""
    p = path()
    if not p.exists():
        return 0
    max_lines = max(100, int(max_lines or 5000))
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    if len(lines) <= max_lines:
        return 0
    kept = lines[-max_lines:]
    try:
        with _LOCK:
            tmp = p.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
            tmp.replace(p)
    except OSError:
        return 0
    return len(lines) - len(kept)
