"""唯一时间线（brain/timeline.jsonl）。

一个大脑只有一条 canonical 时间线；分支/合并/恢复通过 ``epoch`` 区分。
事件是 append-only 的 JSONL，坏行跳过、不阻塞读取。

事件结构::

    {
      "id": "ev-xxxxxxxx",
      "ts": "2026-01-01T12:00:00+08:00",
      "epoch": 1,
      "kind": "wake|sleep_start|sleep_end|reflect|intention|action|outcome|goal|merge|scar|pulse|...",
      "actor": "self|user|system",
      "ref": "关联 id",
      "summary": "一句话可读摘要",
      "data": {...}
    }
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

_LOCK = threading.RLock()
# 单文件软上限：超过后仅在睡眠时由调用方 prune；这里不做隐式删除。
MAX_EVENTS = 20000


def _brain_dir() -> Path:
    try:
        import brainkit as bk
        return Path(bk.BRAIN_DIR)
    except Exception:
        return Path(__file__).resolve().parent.parent / "brain"


def path() -> Path:
    return _brain_dir() / "timeline.jsonl"


def _iso(ts=None) -> str:
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _epoch() -> int:
    """读取当前 epoch；没初始化时按 1 处理。"""
    try:
        p = _brain_dir() / "life.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        return max(1, int(data.get("epoch") or 1))
    except Exception:
        return 1


def append(kind, summary="", data=None, actor="self", ref="", ts=None, epoch=None) -> dict:
    """追加一个事件并返回它。写失败向上抛，由调用方决定是否降级。"""
    kind = str(kind or "event")[:40]
    ev = {
        "id": "ev-" + uuid.uuid4().hex[:12],
        "ts": _iso(ts),
        "epoch": int(epoch if epoch is not None else _epoch()),
        "kind": kind,
        "actor": str(actor or "self")[:20],
        "ref": str(ref or "")[:80],
        "summary": str(summary or "")[:300],
        "data": data if isinstance(data, dict) else {},
    }
    p = path()
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def _parse_line(line):
    line = (line or "").strip()
    if not line:
        return None
    try:
        ev = json.loads(line)
    except Exception:
        return None
    if not isinstance(ev, dict) or not ev.get("kind"):
        return None
    return ev


def _read_tail(limit: int) -> list:
    """从文件末尾反向分块读取最后 N 条合法事件（避免每次全文件扫描）。"""
    p = path()
    if limit <= 0 or not p.exists():
        return []
    try:
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = b""
            while pos > 0 and buf.count(b"\n") <= limit:
                step = min(65536, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
            lines = buf.splitlines()
    except OSError:
        return []
    out = []
    for raw in reversed(lines):
        ev = _parse_line(raw.decode("utf-8", "replace"))
        if ev is not None:
            out.append(ev)
            if len(out) >= limit:
                break
    out.reverse()
    return out


def read(limit=None, kind=None, since=None, reverse=False) -> list:
    """读取事件。

    limit   只保留最后 N 条（reverse=True 时保留最前 N 条后再反转）。
    kind    单个 kind 或 kind 集合。
    since   ISO 时间字符串；按字符串比较（同一时区格式可直接比较）。
    """
    p = path()
    if not p.exists():
        return []
    kinds = None
    if kind:
        kinds = {kind} if isinstance(kind, str) else set(kind)
    n = max(0, int(limit)) if limit else 0
    # 快路径：只取最后 N 条且无 kind/since 过滤时，从文件尾部读。
    if n and not reverse and not kinds and not since:
        return _read_tail(n)
    out = deque(maxlen=n) if (n and not reverse) else []
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                ev = _parse_line(line)
                if ev is None:
                    continue
                if kinds and ev.get("kind") not in kinds:
                    continue
                if since and str(ev.get("ts") or "") < str(since):
                    continue
                if reverse:
                    if n and len(out) >= n:
                        break
                    out.append(ev)
                else:
                    out.append(ev)
    except OSError:
        return []
    items = list(out)
    if reverse:
        items.reverse()
    return items


def tail(limit=30) -> list:
    return read(limit=limit)


def last(kind=None) -> dict:
    items = read(limit=1, kind=kind)
    return items[-1] if items else {}


def since(days=7) -> str:
    return (datetime.now().astimezone() - timedelta(days=max(1, int(days)))).isoformat(timespec="seconds")


def summary(days=7) -> dict:
    """按 kind 汇总最近 N 天；用于 API/报告的轻量指标。"""
    items = read(since=since(days))
    counts = {}
    for ev in items:
        k = str(ev.get("kind") or "event")
        counts[k] = counts.get(k, 0) + 1
    return {
        "days": int(days),
        "events": len(items),
        "counts": counts,
        "last": items[-1] if items else None,
    }


def prune(max_events=MAX_EVENTS) -> int:
    """只保留最后 ``max_events`` 行；返回删除行数。睡眠时调用。"""
    p = path()
    if not p.exists():
        return 0
    max_events = max(1000, int(max_events or MAX_EVENTS))
    with _LOCK:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return 0
        if len(lines) <= max_events:
            return 0
        kept = lines[-max_events:]
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        return len(lines) - len(kept)
