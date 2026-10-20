"""生命循环报告：把"像不像活着"拆成可量化指标。

连续性、自主性、完成率、成本、记忆健康、睡眠——没有一项依赖主观叙述。
"""
from __future__ import annotations

from datetime import datetime

from . import drives, intentions, metabolism, scars, timeline


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _state():
    try:
        from . import state
        return state.load()
    except Exception:
        return {}


def _brain():
    try:
        import brainkit as bk
        return bk
    except Exception:
        return None


def report(days=7) -> dict:
    days = max(1, min(365, int(days or 7)))
    st = _state()
    tl = timeline.summary(days=days)
    dr = drives.latest()
    meta = metabolism.snapshot()
    it = intentions.report(limit=20)
    sc = scars.summary()
    bk = _brain()
    mem_total = mem_archived = goals_active = 0
    try:
        items = bk.load_memories(include_archived=True) if bk else []
        mem_total = len(items)
        mem_archived = sum(1 for x in items if x.get("archived"))
        goals_active = sum(1 for g in bk.load_goals() if g.get("status") == "active") if bk else 0
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass

    counts = it.get("counts") or {}
    done = int(counts.get("done") or 0)
    failed = int(counts.get("failed") or 0)
    competence = (done / (done + failed) * 100.0) if (done + failed) else 0.0
    self_done = 0
    try:
        self_done = sum(1 for x in (it.get("recent") or [])
                        if x.get("status") == "done" and x.get("source") == "agent")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    autonomy = (self_done / done * 100.0) if done else 0.0
    cost_ratio = float(meta.get("cost_ratio") or 0)
    economy = max(0.0, min(100.0, 100.0 - cost_ratio * 100.0))
    memory_score = 100.0
    if mem_total:
        memory_score = max(0.0, 100.0 - (mem_archived / mem_total) * 40.0)
    # 连续性：有 tick、最近 10 分钟内有心跳、epoch 可追溯
    last_tick = st.get("last_tick")
    continuous = 0.0
    if st.get("tick_count"):
        continuous += 50.0
    if last_tick:
        try:
            age = (datetime.now().astimezone() - datetime.fromisoformat(str(last_tick)).astimezone()).total_seconds()
            continuous += 50.0 if age <= 600 else max(0.0, 50.0 - age / 1200.0)
        except Exception:
            continuous += 20.0
    continuous = min(100.0, continuous)
    health = round((continuous + autonomy + competence + economy + memory_score) / 5.0, 1)
    return {
        "generated_at": _now_iso(),
        "days": days,
        "continuity": {
            "score": round(continuous, 1),
            "tick_count": int(st.get("tick_count") or 0),
            "boot_count": int(st.get("boot_count") or 0),
            "epoch": int(st.get("epoch") or 1),
            "last_tick": last_tick,
            "last_wake": st.get("last_wake"),
            "last_sleep": st.get("last_sleep"),
        },
        "autonomy": {
            "score": round(autonomy, 1),
            "intentions_done": done,
            "intentions_failed": failed,
            "self_initiated_done": self_done,
            "active_goals": goals_active,
        },
        "competence": {
            "score": round(competence, 1),
            "done": done,
            "failed": failed,
            "active": int(counts.get("active") or 0),
        },
        "economy": {
            "score": round(economy, 1),
            "used_tokens": meta.get("used_tokens"),
            "budget_tokens": meta.get("budget_tokens"),
            "used_cost": meta.get("used_cost"),
            "budget_cost": meta.get("budget_cost"),
            "energy": meta.get("energy"),
        },
        "memory": {
            "score": round(memory_score, 1),
            "total": mem_total,
            "archived": mem_archived,
        },
        "sleep": {
            "last_sleep": st.get("last_sleep"),
            "last_sleep_day": st.get("last_sleep_day"),
            "fatigue": meta.get("fatigue"),
        },
        "drives": dr.get("drives") or {},
        "top_drive": dr.get("top"),
        "timeline": tl,
        "scars": sc,
        "health": health,
    }


def render_markdown(rep=None) -> str:
    rep = rep if isinstance(rep, dict) else report()
    lines = [
        "# 生命循环报告",
        "",
        f"- 健康度：**{rep.get('health')}** / 100",
        f"- 连续性：{rep['continuity']['score']}（tick {rep['continuity']['tick_count']}，epoch {rep['continuity']['epoch']}）",
        f"- 自主性：{rep['autonomy']['score']}（自主完成 {rep['autonomy']['self_initiated_done']} / {rep['autonomy']['intentions_done']}）",
        f"- 完成率：{rep['competence']['score']}（完成 {rep['competence']['done']}，失败 {rep['competence']['failed']}）",
        f"- 代谢：{rep['economy']['used_tokens']} / {rep['economy']['budget_tokens']} tokens，"
        f"¥{rep['economy']['used_cost']} / ¥{rep['economy']['budget_cost']}，能量 {rep['economy']['energy']}",
        f"- 记忆：{rep['memory']['total']} 条（归档 {rep['memory']['archived']}）",
        f"- 上次睡眠：{rep['sleep']['last_sleep'] or '未记录'}",
        "",
        "## 内驱",
    ]
    for k, v in (rep.get("drives") or {}).items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## 时间线", f"- 最近 {rep['timeline']['days']} 天：{rep['timeline']['events']} 个事件"]
    for k, v in sorted((rep["timeline"].get("counts") or {}).items()):
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines)
