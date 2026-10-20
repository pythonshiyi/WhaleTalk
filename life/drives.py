"""五维内驱：curiosity / maintenance / connection / mastery / coherence。

全部是本地信号，不调用模型；每 90 秒最多重算一次，避免 tick 频繁读记忆库。

- curiosity   未解决的决定、没有进展的目标、久未复盘的问题
- maintenance 距上次睡眠/整理的时间、记忆体量、归档比例
- connection  社区/外部连接是否开启、上次互动距今多久
- mastery     未解决的失败/未知、未完成目标
- coherence   合并冲突、自我模型是否仍是模板、未决决定
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from ._atomic import write_json

_LOCK = threading.RLock()
_CACHE = {"sig": None, "at": 0.0, "data": None}
CACHE_SECONDS = 90

DRIVE_LABELS = {
    "curiosity": "好奇",
    "maintenance": "维护",
    "connection": "连接",
    "mastery": "掌握",
    "coherence": "一致",
}


def _brain_dir() -> Path:
    try:
        import brainkit as bk
        return Path(bk.BRAIN_DIR)
    except Exception:
        return Path(__file__).resolve().parent.parent / "brain"


def path() -> Path:
    return _brain_dir() / "drives.json"


def _cfg() -> dict:
    try:
        import config_utils
        return config_utils.load_config()
    except Exception:
        try:
            from config_defaults import DEFAULT_CONFIG
            return dict(DEFAULT_CONFIG)
        except Exception:
            return {}


def _now_iso(ts=None) -> str:
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_dt(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _days_since(value, default=7.0):
    dt = _parse_dt(value)
    if dt is None:
        return float(default)
    try:
        return max(0.0, (datetime.now().astimezone() - dt.astimezone()).total_seconds() / 86400.0)
    except Exception:
        return float(default)


def _signals():
    import brainkit as bk
    out = {
        "goals_active": 0, "goals_stale": 0, "goals_no_progress": 0,
        "decisions_open": 0, "decisions_reversed": 0,
        "memories": 0, "memories_archived": 0,
        "unknowns": 0, "self_model_source": "template",
        "merge_conflicts": 0, "last_sleep": None, "last_wake": None,
        "community_enabled": False, "community_age_min": None,
    }
    try:
        goals = bk.load_goals()
        out["goals_active"] = sum(1 for g in goals if g.get("status") == "active")
        out["goals_no_progress"] = sum(
            1 for g in goals if g.get("status") == "active" and not str(g.get("progress") or "").strip())
        out["goals_stale"] = sum(
            1 for g in goals if g.get("status") == "active" and _days_since(g.get("updated_at"), 0) >= 7)
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    try:
        decisions = bk.list_decisions(limit=500)
        out["decisions_open"] = sum(1 for d in decisions if d.get("status") == "open")
        out["decisions_reversed"] = sum(1 for d in decisions if d.get("status") == "reversed")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    try:
        items = bk.load_memories(include_archived=True)
        out["memories"] = len(items)
        out["memories_archived"] = sum(1 for x in items if x.get("archived"))
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    try:
        sm = bk.load_json(bk.BRAIN_DIR / "self_model.json", {}) or {}
        out["unknowns"] = len(sm.get("unknowns") or [])
        out["self_model_source"] = str(sm.get("source") or "template")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    try:
        conflicts = bk.load_json(bk.MERGE_CONFLICT_FILE, {}) or {}
        out["merge_conflicts"] = len(conflicts.get("conflicts") or [])
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    try:
        life = json.loads((_brain_dir() / "life.json").read_text(encoding="utf-8"))
        out["last_sleep"] = life.get("last_sleep")
        out["last_wake"] = life.get("last_wake")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    cfg = _cfg()
    out["community_enabled"] = bool(cfg.get("brain_community_enabled", False))
    if out["community_enabled"]:
        try:
            from . import timeline
            ev = timeline.last("community")
            out["community_age_min"] = _days_since(ev.get("ts"), 30) * 1440 if ev else None
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
    return out


def _score(value, cap):
    if cap <= 0:
        return 0
    return int(max(0, min(100, round(float(value) / float(cap) * 100))))


def compute(now=None, force=False) -> dict:
    sig = str(_brain_dir())
    cached = _CACHE.get("data")
    if (not force and cached and _CACHE.get("sig") == sig
            and (time.time() - float(_CACHE.get("at") or 0)) < CACHE_SECONDS):
        return dict(cached)
    s = _signals()
    cfg = _cfg()
    # curiosity：未决 + 无进展 + 久未复盘目标
    curiosity = min(100, s["decisions_open"] * 12 + s["goals_no_progress"] * 10 + s["goals_stale"] * 12)
    # maintenance：越久没睡、记忆越多、归档比例越高，越需要整理。
    # 从未睡过时从“醒来”算，而不是默认 3 天——否则新大脑一出生就误判为欠睡眠。
    days_sleep = _days_since(s.get("last_sleep") or s.get("last_wake"), 0)
    ratio_arch = (s["memories_archived"] / s["memories"]) if s["memories"] else 0.0
    maintenance = min(100, days_sleep * 12 + max(0, s["memories"] - 300) / 20 + ratio_arch * 40)
    # connection：社区关闭时为 0；开启则按间隔衰减
    connection = 0
    if s["community_enabled"]:
        interval = max(1.0, float(cfg.get("brain_community_interval_min", 30) or 30))
        age = s.get("community_age_min")
        connection = 65 if age is None else _score(age, interval * 4)
    # mastery：未解决失败/未知、活跃目标
    mastery = min(100, s["unknowns"] * 14 + s["decisions_open"] * 8 + s["goals_active"] * 5)
    # coherence：冲突/模板自我模型/未决
    coherence = min(100, s["merge_conflicts"] * 35 + (18 if s["self_model_source"] == "template" else 0)
                    + s["decisions_open"] * 4)

    drives = {
        "curiosity": int(curiosity),
        "maintenance": int(maintenance),
        "connection": int(connection),
        "mastery": int(mastery),
        "coherence": int(coherence),
    }
    top = max(drives, key=lambda k: drives[k]) if drives else "maintenance"
    reasons = {
        "curiosity": f"未决 {s['decisions_open']} · 无进展目标 {s['goals_no_progress']} · 停滞目标 {s['goals_stale']}",
        "maintenance": f"距上次睡眠/醒来 {days_sleep:.1f} 天 · 记忆 {s['memories']} 条 · 归档比 {ratio_arch:.0%}",
        "connection": ("社区未开启" if not s["community_enabled"]
                       else f"上次连接 {s.get('community_age_min') or '未记录'} 分钟前"),
        "mastery": f"未知 {s['unknowns']} · 未决 {s['decisions_open']} · 活跃目标 {s['goals_active']}",
        "coherence": f"合并冲突 {s['merge_conflicts']} · 自我模型 {s['self_model_source']} · 未决 {s['decisions_open']}",
    }
    out = {
        "updated_at": _now_iso(now.timestamp() if isinstance(now, datetime) else None),
        "drives": drives,
        "top": top,
        "top_score": drives[top],
        "reasons": reasons,
        "signals": s,
    }
    _CACHE.update({"sig": sig, "at": time.time(), "data": out})
    return out


def save(snapshot=None) -> dict:
    data = snapshot if isinstance(snapshot, dict) else compute(force=True)
    with _LOCK:
        write_json(path(), data)
    return data


def latest() -> dict:
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("drives"):
            return raw
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    return compute()
