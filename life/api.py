"""生命循环的 API 适配层。

只做参数校验与派发；状态/动作在 ``loop`` / ``intentions`` / ``report`` 中。
"""
from __future__ import annotations

from . import intentions, loop, report, scars, timeline


def status() -> dict:
    return {"ok": True, "life": loop.status()}


def brief() -> str:
    return loop.brief()


def get_timeline(limit=50, kind=None) -> dict:
    try:
        n = max(1, min(500, int(limit or 50)))
    except (TypeError, ValueError):
        n = 50
    items = timeline.read(limit=n, kind=kind or None)
    return {"ok": True, "items": items, "count": len(items)}


def get_intentions(limit=50, status=None) -> dict:
    try:
        n = max(1, min(500, int(limit or 50)))
    except (TypeError, ValueError):
        n = 50
    return {"ok": True, "items": intentions.list_all(limit=n, status=status or None),
            "counts": intentions.counts()}


def get_report(days=7) -> dict:
    try:
        d = max(1, min(365, int(days or 7)))
    except (TypeError, ValueError):
        d = 7
    rep = report.report(d)
    return {"ok": True, "report": rep, "markdown": report.render_markdown(rep)}


def get_scars(limit=20) -> dict:
    try:
        n = max(1, min(200, int(limit or 20)))
    except (TypeError, ValueError):
        n = 20
    return {"ok": True, "items": scars.list_recent(n), "summary": scars.summary()}


def dispatch(action, payload=None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    action = str(action or "").strip()
    try:
        if action in ("status", "get"):
            return status()
        if action in ("tick", "step"):
            return {"ok": True, "result": loop.tick(force=bool(payload.get("force")))}
        if action == "pause":
            return loop.pause(str(payload.get("reason") or "manual"))
        if action == "resume":
            return loop.resume(str(payload.get("reason") or "manual"))
        if action == "wake":
            return loop.wake_now(str(payload.get("reason") or "manual"))
        if action == "sleep":
            return loop.sleep_now(str(payload.get("reason") or "manual"))
        if action == "reflect":
            return loop.reflect_now(str(payload.get("reason") or "manual"))
        if action == "add-intention":
            ok, it, why = intentions.propose(
                title=str(payload.get("title") or ""),
                drive=str(payload.get("drive") or "curiosity"),
                why=str(payload.get("why") or ""),
                first_step=str(payload.get("first_step") or ""),
                expected=str(payload.get("expected") or ""),
                kind=str(payload.get("kind") or "reflect"),
                source=str(payload.get("source") or "user"),
            )
            return {"ok": ok, "intention": it, "message": why}
        if action == "start-intention":
            return {"ok": intentions.start(str(payload.get("id") or ""))}
        if action == "complete-intention":
            return {"ok": intentions.complete(str(payload.get("id") or ""), str(payload.get("outcome") or ""))}
        if action == "fail-intention":
            return {"ok": intentions.fail(str(payload.get("id") or ""), str(payload.get("outcome") or ""))}
        if action == "abandon-intention":
            return {"ok": intentions.abandon(str(payload.get("id") or ""), str(payload.get("reason") or ""))}
        if action == "delete-intention":
            return {"ok": intentions.delete(str(payload.get("id") or ""))}
        if action == "timeline":
            return get_timeline(payload.get("limit", 50), payload.get("kind"))
        if action == "report":
            return get_report(payload.get("days", 7))
        if action == "scars":
            return get_scars(payload.get("limit", 20))
        return {"ok": False, "message": f"未知生命循环动作: {action}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}
