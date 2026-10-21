"""自发意图（brain/intentions.json）。

意图不是待办清单，而是"我为什么现在要做这件事"的最小记录：

    proposed → active → done / failed / abandoned

每条意图必须带 ``why`` 与 ``expected``，执行后写 ``outcome``；
这样自主行动才能被复盘，而不是只留下行为日志。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from ._atomic import write_json

_LOCK = threading.RLock()
STATUSES = ("proposed", "active", "done", "failed", "abandoned")
KINDS = ("reflect", "consolidate", "review_failure", "write_thought",
         "update_self_model", "propose_evolution", "goal", "rest",
         "review_memory", "housekeeping", "recalibrate_self", "tool")


def _brain_dir() -> Path:
    try:
        import brainkit as bk
        return Path(bk.BRAIN_DIR)
    except Exception:
        return Path(__file__).resolve().parent.parent / "brain"


def path() -> Path:
    return _brain_dir() / "intentions.json"


def archive_path() -> Path:
    return _brain_dir() / "intentions_archive.jsonl"


def _iso(ts=None) -> str:
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _max_active() -> int:
    try:
        import config_utils
        cfg = config_utils.load_config()
        return max(1, min(20, int(cfg.get("life_max_active_intentions", 3) or 3)))
    except Exception:
        return 3


def load() -> dict:
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("intentions"), list):
            return raw
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    return {"intentions": []}


def save(data) -> None:
    with _LOCK:
        write_json(path(), data)


def list_all(limit=None, status=None) -> list:
    items = load().get("intentions", [])
    if status:
        wanted = {status} if isinstance(status, str) else set(status)
        items = [x for x in items if x.get("status") in wanted]
    items = sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return items[:max(0, int(limit))] if limit else items


def active() -> list:
    return list_all(status=("proposed", "active"))


def counts() -> dict:
    out = {s: 0 for s in STATUSES}
    for x in load().get("intentions", []):
        s = str(x.get("status") or "proposed")
        out[s] = out.get(s, 0) + 1
    out["active"] = out.get("proposed", 0) + out.get("active", 0)
    return out


def _same_title(a, b) -> bool:
    return " ".join(str(a or "").split()).lower() == " ".join(str(b or "").split()).lower()


def _recent_noop(kind, streak=2, tool="") -> bool:
    """同一类动作最近连续无效果时，不再重复。

    典型场景：记忆为空，模型连续提出 consolidate，结果永远是
    「归档 0、合并 0、保留 0」——这不是自主，是空转。
    tool 非空时只统计同一只读工具的调用。
    """
    recent = [x for x in load().get("intentions", [])
              if x.get("kind") == kind and x.get("status") in ("done", "failed")
              and (not tool or str((x.get("payload") or {}).get("tool") or "") == str(tool))]
    recent = recent[-max(1, int(streak)):]
    if len(recent) < max(1, int(streak)):
        return False
    for x in recent:
        outcome = str(x.get("outcome") or "")
        if "无变化" in outcome:
            continue
        if "归档 0" in outcome and "合并 0" in outcome:
            continue
        return False
    return True


def propose(title, drive="", why="", first_step="", expected="", kind="reflect",
            payload=None, source="agent") -> tuple:
    """提出一条意图，返回 ``(ok, intention|None, reason)``。

    上限是内稳态：同时最多 ``life_max_active_intentions`` 条，避免目标漂移。
    """
    title = str(title or "").strip()[:120]
    if not title:
        return False, None, "标题为空"
    kind = str(kind or "reflect")
    if kind not in KINDS:
        kind = "reflect"
    with _LOCK:
        data = load()
        items = data.setdefault("intentions", [])
        for x in items:
            if x.get("status") in ("proposed", "active") and _same_title(x.get("title"), title):
                return False, x, "已有同标题的活跃意图"
        if source == "agent" and kind == "consolidate" and _recent_noop(kind, 2):
            return False, None, "同类整理连续两次无变化，先换一种观察或进入睡眠"
        if source == "agent" and kind == "tool":
            _tool = str((payload or {}).get("tool") or "")
            if _tool and _recent_noop("tool", 2, _tool):
                return False, None, f"工具 {_tool} 连续两次无变化，停止重复调用"
        act = [x for x in items if x.get("status") in ("proposed", "active")]
        if len(act) >= _max_active():
            return False, None, f"活跃意图已达上限 {_max_active()}"
        it = {
            "id": "it-" + uuid.uuid4().hex[:10],
            "title": title,
            "drive": str(drive or "")[:40],
            "why": str(why or "").strip()[:300],
            "first_step": str(first_step or "").strip()[:200],
            "expected": str(expected or "").strip()[:300],
            "kind": kind,
            "payload": payload if isinstance(payload, dict) else {},
            "source": str(source or "agent")[:20],
            "status": "proposed",
            "created_at": _iso(),
            "updated_at": _iso(),
            "started_at": None,
            "finished_at": None,
            "outcome": "",
        }
        items.append(it)
        data["updated_at"] = _iso()
        save(data)
    try:
        from . import timeline
        timeline.append("intention", summary=f"[{it['drive'] or kind}] {title}",
                        data={"id": it["id"], "kind": kind, "expected": it["expected"]},
                        actor="self", ref=it["id"])
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    return True, it, ""


def get(iid) -> dict:
    iid = str(iid or "")
    for x in load().get("intentions", []):
        if x.get("id") == iid:
            return x
    return {}


def update(iid, **fields) -> bool:
    iid = str(iid or "")
    allowed = {"title", "drive", "why", "first_step", "expected", "kind", "payload",
               "status", "outcome", "started_at", "finished_at"}
    with _LOCK:
        data = load()
        hit = None
        for x in data.get("intentions", []):
            if x.get("id") == iid:
                hit = x
                break
        if hit is None:
            return False
        for k, v in fields.items():
            if k in allowed:
                hit[k] = v
        hit["updated_at"] = _iso()
        data["updated_at"] = _iso()
        save(data)
    return True


def start(iid) -> bool:
    return update(iid, status="active", started_at=_iso())


def complete(iid, outcome="") -> bool:
    ok = update(iid, status="done", outcome=str(outcome or "")[:500], finished_at=_iso())
    if ok:
        _after_finish(iid, "done", outcome)
    return ok


def fail(iid, outcome="", scar=True) -> bool:
    ok = update(iid, status="failed", outcome=str(outcome or "")[:500], finished_at=_iso())
    if ok:
        _after_finish(iid, "failed", outcome)
        if scar:
            try:
                from . import scars
                it = get(iid)
                scars.add("intention_failed", f"{it.get('title') or iid}: {outcome}", lesson=it.get("why") or "")
            # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
            except Exception:
                pass
    return ok


def abandon(iid, reason="") -> bool:
    return update(iid, status="abandoned", outcome=str(reason or "")[:500], finished_at=_iso())


def delete(iid) -> bool:
    iid = str(iid or "")
    with _LOCK:
        data = load()
        items = data.get("intentions", [])
        kept = [x for x in items if x.get("id") != iid]
        if len(kept) == len(items):
            return False
        data["intentions"] = kept
        data["updated_at"] = _iso()
        save(data)
    return True


def _after_finish(iid, status, outcome=""):
    try:
        from . import timeline
        it = get(iid)
        timeline.append("outcome", summary=f"{status}: {it.get('title') or iid} — {str(outcome or '')[:120]}",
                        data={"id": iid, "status": status, "outcome": str(outcome or "")[:500]},
                        actor="self", ref=iid)
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass


def archived_count() -> int:
    """已归档意图条数（只读统计，供状态页展示）。"""
    try:
        return sum(1 for x in archive_path().read_text(encoding="utf-8", errors="ignore").splitlines() if x.strip())
    except OSError:
        return 0


def _archive_cap() -> int:
    try:
        import config_utils
        cfg = config_utils.load_config()
        return max(20, int(cfg.get("life_intention_archive_max", 200) or 200))
    except Exception:
        return 200


def prune(max_items=None) -> int:
    """把最旧的已完成意图移入 intentions_archive.jsonl，保留活跃意图。

    单文件全量重写 + 无上限会让 intentions.json 随运行时间无限增长；
    归档只挪位置不删除（仍可检索），返回归档条数。
    """
    cap = max(20, int(max_items or _archive_cap()))
    with _LOCK:
        data = load()
        items = data.get("intentions") or []
        if len(items) <= cap:
            return 0
        active = [x for x in items if x.get("status") in ("proposed", "active")]
        finished = [x for x in items if x.get("status") not in ("proposed", "active")]
        finished.sort(key=lambda x: str(x.get("finished_at") or x.get("created_at") or ""))
        drop_n = min(len(finished), max(0, len(items) - cap))
        if drop_n <= 0:
            return 0
        drop = finished[:drop_n]
        # 先写归档再裁剪：归档失败时宁可下次重复归档，也不能丢意图。
        try:
            with archive_path().open("a", encoding="utf-8") as f:
                for x in drop:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
        # noqa: silent-except: 归档写入失败则本次不裁剪，保留原文件
        except OSError:
            return 0
        data["intentions"] = active + finished[drop_n:]
        data["updated_at"] = _iso()
        save(data)
        return drop_n


def report(limit=10) -> dict:
    return {
        "counts": counts(),
        "active": active()[:limit],
        "recent": list_all(limit=limit),
    }
