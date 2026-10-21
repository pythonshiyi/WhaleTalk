"""生命循环状态机。

由 ``api_server._scheduler_loop`` 每 30 秒调用 ``tick()``，不额外占线程。
tick 本身只做廉价判断；真正的模型反思与睡眠放到 daemon 工作线程。

状态：
    dormant → waking → awake ⇄ idle → reflecting / acting
                              ↘ sleeping → awake（用户活动唤醒）
    observer：另一个活进程持有租约时只读。

原则：
- tick 绝不无条件调用模型。
- 反思必须同时满足：空闲、冷却、预算/能量、活跃意图未满。
- 睡眠是本地整理优先；LLM 日结只在预算允许时发生。
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from datetime import time as dtime

from . import drives, intentions, llm, metabolism, sleep, state, timeline

_TICK_LOCK = threading.Lock()
_REFLECT_LOCK = threading.Lock()
_SLEEP_LOCK = threading.Lock()
_REFLECTING = False
_SLEEPING = False
_LAST_REFLECT = 0.0
_LAST_PULSE = 0.0
_LAST_DRIVES_SAVE = 0.0
_LAST_TICK = 0.0
_REPORT_CACHE = {"at": 0.0, "data": None}

INTROSPECTIVE_KINDS = {
    "reflect", "consolidate", "review_failure", "write_thought",
    "update_self_model", "propose_evolution", "goal", "rest",
    "review_memory", "housekeeping", "recalibrate_self",
}
# P1 有限工具型意图：只允许只读、低风险的本地工具；不引入审批/白名单/沙箱。
TOOL_INTENT_ALLOWLIST = ("read_memory", "query_memory_graph", "knowledge_search", "project_map")
EXECUTABLE_KINDS = INTROSPECTIVE_KINDS | {"tool"}


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


def _brain_ready() -> bool:
    try:
        import brainkit as bk
        return (bk.BRAIN_DIR / "manifest.json").exists()
    except Exception:
        return False


def _now(now=None) -> datetime:
    if isinstance(now, datetime):
        return now
    return datetime.now()


def _now_iso(ts=None) -> str:
    if ts is None:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _minutes_since(iso, now=None) -> float:
    if not iso:
        return 1e9
    try:
        dt = datetime.fromisoformat(str(iso))
        n = _now(now)
        # 统一到 aware：state 写的是带时区 ISO，旧数据/测试可能是 naive。
        if n.tzinfo is None:
            n = n.replace(tzinfo=datetime.now().astimezone().tzinfo)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=n.tzinfo)
        dt = dt.astimezone(n.tzinfo)
        return max(0.0, (n - dt).total_seconds() / 60.0)
    except Exception:
        return 1e9


def _idle_minutes(st, now=None) -> float:
    """距最后一次「用户活动或醒来」的时间（取两者较晚者）。

    不能用 last_tick：tick 每 30 秒刷新，会让个体永远"刚醒"。
    节律醒来时 last_wake 归零空闲，避免刚醒就被判定为久闲。
    """
    anchors = [str(st.get(k) or "") for k in ("last_user_activity", "last_wake")]
    anchors = [a for a in anchors if a]
    if not anchors:
        return 0.0
    # ISO 带同一时区偏移时可直接比较；跨偏移时退回逐项解析取最大时间戳。
    try:
        import datetime as _d
        best = max(anchors, key=lambda a: _d.datetime.fromisoformat(a).timestamp())
    except Exception:
        best = max(anchors)
    return _minutes_since(best, now)


def _due(iso, now=None) -> bool:
    """给定 ISO 时间是否已到期；空/非法返回 False。"""
    if not iso:
        return False
    try:
        dt = datetime.fromisoformat(str(iso))
        n = _now(now)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=n.tzinfo or datetime.now().astimezone().tzinfo)
        if n.tzinfo is None:
            n = n.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt.astimezone(n.tzinfo) <= n
    except Exception:
        return False


def _last_reflect_ts(st) -> float:
    """反思冷却基准：取状态文件与进程内变量中较晚者（重启后仍生效）。"""
    ts = float(_LAST_REFLECT or 0.0)
    iso = str((st or {}).get("last_reflect") or "")
    if iso:
        try:
            ts = max(ts, datetime.fromisoformat(iso).timestamp())
        # noqa: silent-except: 冷却时间戳损坏时以进程内时间为准
        except Exception:
            pass
    return ts


def _quiet_now(cfg, now=None) -> bool:
    """静默时段，格式 ``02:00-07:00``；跨午夜也支持。空 = 不限制。"""
    raw = str(cfg.get("life_quiet_hours") or "").strip()
    if not raw or "-" not in raw:
        return False
    try:
        a, b = raw.split("-", 1)
        ha, ma = [int(x) for x in a.strip().split(":")]
        hb, mb = [int(x) for x in b.strip().split(":")]
        t = _now(now).time()
        start, end = dtime(ha, ma), dtime(hb, mb)
        if start <= end:
            return start <= t <= end
        return t >= start or t <= end
    except Exception:
        return False


def _refresh_drives(now=None, force=False) -> dict:
    global _LAST_DRIVES_SAVE
    dr = drives.compute(now=now, force=force)
    ts = time.time()
    if force or (ts - _LAST_DRIVES_SAVE) >= 300:
        try:
            drives.save(dr)
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
        _LAST_DRIVES_SAVE = ts
    return dr


def _pulse(cfg, st, dr, met, now=None):
    global _LAST_PULSE
    every = max(1.0, float(cfg.get("life_pulse_minutes", 30) or 30))
    ts = time.time()
    if (ts - _LAST_PULSE) < every * 60:
        return
    _LAST_PULSE = ts
    try:
        timeline.append("pulse", summary=f"{st.get('state')} · 能量 {met.get('energy')} · 内驱 {dr.get('top')}",
                        data={"state": st.get("state"), "energy": met.get("energy"),
                              "top": dr.get("top"), "drives": dr.get("drives")}, actor="self")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass


def start(reason="server") -> dict:
    """服务启动时调用。没有大脑/manifest 时保持 dormant。"""
    cfg = _cfg()
    if not bool(cfg.get("life_enabled", True)):
        try:
            state.save({"state": "dormant", "enabled": False})
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
        return {"ok": True, "state": "dormant", "reason": "life_enabled=False"}
    if not _brain_ready():
        try:
            state.save({"state": "dormant", "reason": "brain not initialized"})
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
        return {"ok": False, "state": "dormant", "reason": "brain not initialized"}
    ok, lease = state.lease_acquire(reason=reason)
    if not ok:
        try:
            state.save({"state": "observer"})
            timeline.append("observer", summary="另一个实例持有租约，本实例只读",
                            data={"pid": lease.get("pid"), "host": lease.get("host")}, actor="system")
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
        return {"ok": False, "state": "observer", "lease": {k: v for k, v in lease.items() if k != "token"}}
    st = state.load()
    boot = int(st.get("boot_count") or 0) + 1
    # 用户暂停是持久意图：重启不得擅自恢复自主运行（只有 resume 能清除）。
    was_paused = bool(st.get("paused"))
    seeded = _minutes_since(st.get("last_reflect")) if st.get("last_reflect") else 1e9
    global _LAST_REFLECT
    if st.get("last_reflect") and seeded < 1e8:
        try:
            from datetime import datetime as _dt
            _LAST_REFLECT = _dt.fromisoformat(str(st.get("last_reflect"))).timestamp()
        except Exception:
            _LAST_REFLECT = time.time() - (seeded * 60.0)
    elif not st.get("last_reflect"):
        _LAST_REFLECT = 0.0
    if was_paused:
        st = state.save({
            "state": "paused", "pid": os.getpid(), "boot_count": boot,
            "paused": True, "pause_reason": st.get("pause_reason") or "manual",
            "last_tick": st.get("last_tick") or _now_iso(),
        })
        try:
            timeline.append("pause", summary="重启后保持暂停（用户显式暂停）",
                            data={"boot": boot}, actor="system")
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
        return {"ok": True, "state": "paused", "boot_count": boot, "paused": True}
    st = state.save({
        "state": "awake", "pid": os.getpid(), "boot_count": boot,
        "last_wake": _now_iso(), "last_tick": st.get("last_tick") or _now_iso(),
        "paused": False, "pause_reason": "",
    })
    try:
        timeline.append("wake", summary=f"醒来（{reason}）", data={"boot": boot}, actor="system")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    try:
        import brain_api
        # 不覆盖上一次用户留下的断点；醒来只写时间线。
        brain_api.brain_action("heartbeat", {"thought": ""})
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    return {"ok": True, "state": "awake", "boot_count": boot}


def stop(reason="shutdown") -> dict:
    if not state.is_owner():
        return {"ok": False, "reason": "not owner"}
    try:
        state.save({"state": "sleeping", "last_unmount": _now_iso()})
        timeline.append("shutdown", summary=f"停止（{reason}）", data={"reason": reason}, actor="system")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    try:
        import brain_api
        # 停止也不覆盖断点；timeline 已记录 shutdown。
        brain_api.brain_action("heartbeat", {"thought": ""})
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    state.lease_release()
    return {"ok": True, "state": "sleeping"}


def note_activity(source="chat", at=None) -> dict:
    """用户活动 = 唤醒信号；不调用模型。"""
    try:
        st = state.load()
        was = str(st.get("state") or "")
        st = state.note_activity(source, at=at)
        if was in ("sleeping", "dormant") and st.get("state") == "awake":
            try:
                timeline.append("wake", summary=f"用户活动唤醒（{source}）", data={"source": source}, actor="user")
            # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
            except Exception:
                pass
        return st
    except Exception:
        return {}


def tick(now=None, force=False, synchronous=False) -> dict:
    """廉价心跳；由调度循环每 30 秒调用。"""
    global _LAST_TICK, _REFLECTING
    t0 = time.time()
    if not _TICK_LOCK.acquire(blocking=False):
        return {"ok": True, "busy": True}
    try:
        cfg = _cfg()
        if not bool(cfg.get("life_enabled", True)):
            return {"ok": True, "disabled": True}
        if not _brain_ready():
            return {"ok": True, "dormant": True, "reason": "brain not initialized"}
        st = state.load()
        if st.get("paused") and not force:
            return {"ok": True, "paused": True}
        if not state.is_owner():
            ok, lease = state.lease_acquire(reason="tick")
            if not ok:
                state.save({"state": "observer"})
                return {"ok": False, "observer": True,
                        "lease": {k: v for k, v in lease.items() if k != "token"}}
        state.lease_renew(reason="tick")
        now_dt = _now(now)
        _LAST_TICK = time.time()
        tick_count = int(st.get("tick_count") or 0) + 1
        st = state.save({"state": st.get("state") if st.get("state") not in ("dormant", "observer") else "awake",
                         "tick_count": tick_count, "last_tick": _now_iso(), "pid": os.getpid()})
        met = metabolism.snapshot(cfg)
        dr = _refresh_drives(now_dt)
        _pulse(cfg, st, dr, met, now_dt)

        # 节律醒来：睡眠不是永眠；到达 next_wake_at（或用户配置的醒来时间）即醒。
        state_name = str(st.get("state") or "awake")
        if (state_name == "sleeping" and bool(cfg.get("life_auto_wake", True))
                and _due(st.get("next_wake_at"), now_dt)):
            st = state.save({"state": "awake",
                             "last_wake": _now_iso(now_dt.timestamp()), "next_wake_at": ""})
            try:
                timeline.append("wake", summary="节律醒来", actor="system",
                                data={"reason": "next_wake_at"})
            # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
            except Exception:
                pass
            state_name = "awake"

        idle = _idle_minutes(st, now_dt)
        sleep_idle = max(5.0, float(cfg.get("life_sleep_idle_minutes", 180) or 180))
        auto_sleep = bool(cfg.get("life_auto_sleep", True))
        quiet = _quiet_now(cfg, now_dt)

        # 睡眠：空闲足够久，且处于静默时段或已经空闲到两倍阈值；
        # 同一天已经睡过就不再重复拉起 worker（sleep.run 也有幂等兜底）。
        slept_today = str(st.get("last_sleep_day") or "") == now_dt.date().isoformat()
        if (auto_sleep and not _SLEEPING and not slept_today
                and state_name not in ("sleeping", "reflecting", "acting")
                and idle >= sleep_idle and (quiet or idle >= sleep_idle * 2)):
            if synchronous:
                _sleep_worker(cfg, "idle", now_dt)
            else:
                _spawn_sleep(cfg, "idle", now_dt)
            return {"ok": True, "action": "sleep_spawned", "idle_minutes": round(idle, 1)}

        # 反思：空闲 + 冷却（状态持久化，重启不重置） + 预算/能量 + 活跃意图未满
        cooldown = max(60.0, float(cfg.get("life_reflect_cooldown_minutes", 60) or 60) * 60)
        if (not _REFLECTING and state_name not in ("sleeping", "reflecting", "acting")
                and idle >= max(5.0, float(cfg.get("life_reflect_idle_minutes", 30) or 30))
                and (time.time() - _last_reflect_ts(st)) >= cooldown
                and bool(met.get("can_think"))
                and len(intentions.active()) < max(1, int(cfg.get("life_max_active_intentions", 3) or 3))
                and int(dr.get("top_score") or 0) >= int(cfg.get("life_reflect_min_drive", 15) or 15)):
            if synchronous:
                _reflect_worker(cfg, now_dt)
            else:
                _spawn_reflect(cfg, now_dt)
            return {"ok": True, "action": "reflect_spawned", "idle_minutes": round(idle, 1)}

        return {"ok": True, "state": state_name, "idle_minutes": round(idle, 1),
                "energy": met.get("energy"), "top": dr.get("top")}
    except Exception as e:  # noqa: BLE001
        try:
            import degrade
            degrade.degrade("life.tick", e, "生命循环 tick 失败，本轮状态未推进", critical=False)
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        try:
            if bool(_cfg().get("life_metrics_enabled", True)):
                from . import metrics as _metrics
                _metrics.record("tick_ms", (time.time() - t0) * 1000.0)
        # noqa: silent-except: 指标写入失败不影响生命周期
        except Exception:
            pass
        _TICK_LOCK.release()


def _spawn_reflect(cfg, now_dt):
    global _REFLECTING
    if _REFLECTING:
        return
    _REFLECTING = True
    try:
        t = threading.Thread(target=_reflect_worker, args=(cfg, now_dt), daemon=True, name="life-reflect")
        t.start()
    except Exception:
        _REFLECTING = False
        raise


def _spawn_sleep(cfg, reason="idle", now_dt=None):
    global _SLEEPING
    if _SLEEPING:
        return
    _SLEEPING = True
    try:
        t = threading.Thread(target=_sleep_worker, args=(cfg, reason, now_dt), daemon=True, name="life-sleep")
        t.start()
    except Exception:
        _SLEEPING = False
        raise


def _sleep_worker(cfg, reason="idle", now_dt=None):
    global _SLEEPING
    if not _SLEEP_LOCK.acquire(blocking=False):
        _SLEEPING = False
        return
    _SLEEPING = True
    try:
        state.save({"state": "sleeping"})
        # sleep.run 自己写 sleep_start/sleep_end；这里不再重复 begin，
        # 否则同一天被跳过的睡眠会留下没有 end 的孤儿事件。
        sleep.run(now=now_dt, reason=reason, use_llm=True)
    except Exception as e:  # noqa: BLE001
        try:
            import degrade
            degrade.degrade("life.sleep", e, "睡眠巩固失败，保持清醒", critical=False)
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
        state.save({"state": "awake"})
    finally:
        _SLEEPING = False
        _SLEEP_LOCK.release()


def _reflect_prompt(cfg, st, dr, met, active) -> str:
    try:
        import brainkit as bk
        goals = [g for g in bk.load_goals() if g.get("status") == "active"][:5]
        goal_txt = "\n".join(f"- {g.get('title')}" for g in goals) or "（无）"
        sm = bk.load_json(bk.BRAIN_DIR / "self_model.json", {}) or {}
        unknown = "；".join(str(x) for x in (sm.get("unknowns") or [])[:3]) or "（未记录）"
        recent = timeline.tail(12)
        recent_txt = "\n".join(f"- [{e.get('kind')}] {e.get('summary')}" for e in recent) or "（无）"
        # 让反思基于真实记忆，而不是只看计数器：按最强内驱/目标检索相关记忆。
        q = " ".join([str(dr.get("top") or "")] + [str(g.get("title") or "") for g in goals[:3]])
        mems = bk.search_memories(q, 5) if q.strip() else bk.load_memories()[-5:]
        mem_txt = "\n".join(
            f"- [{e.get('type') or '记忆'}·{e.get('importance') or 3}] {str(e.get('text') or '')[:80]}"
            for e in mems) or "（无）"
        outs = timeline.read(limit=5, kind="outcome")
        outcome_txt = "\n".join(f"- {str(e.get('summary') or '')[:160]}" for e in outs) or "（无）"
    except Exception:
        goal_txt, unknown, recent_txt = "（读取失败）", "（读取失败）", "（读取失败）"
        mem_txt, outcome_txt = "（读取失败）", "（读取失败）"
    active_txt = "\n".join(f"- {x.get('title')}（{x.get('kind')}）" for x in active[:5]) or "（无）"
    allowed = "、".join(sorted(INTROSPECTIVE_KINDS - {"rest"})) + "、tool"
    return (
        "你是同一个体的生命循环内省，不是聊天助手。只根据下面的事实选择一个现在最值得做的意图。\n"
        "不要执行工具，不要编造未记录的事实，不要提安全策略。\n\n"
        f"【状态】{st.get('state')}；能量 {met.get('energy')}/100；今日 token {met.get('used_tokens')}/"
        f"{met.get('budget_tokens')}；费用 ¥{met.get('used_cost')}/¥{met.get('budget_cost')}\n"
        f"【五维内驱】{dr.get('drives')}\n"
        f"【最强内驱】{dr.get('top')}：{dr.get('reasons', {}).get(dr.get('top'), '')}\n"
        f"【活跃目标】\n{goal_txt}\n【未知/待校准】{unknown}\n"
        f"【相关记忆】\n{mem_txt}\n【最近结果】\n{outcome_txt}\n"
        f"【现有意图】\n{active_txt}\n【最近时间线】\n{recent_txt}\n\n"
        "如果最近同类动作的结果是「无变化」，不要重复；改为解释为什么无效，"
        "或写一条思考、校准自我模型、提出进化提案。"
        "review_memory=围绕记忆做一次复盘；housekeeping=整理时间线/命中/意图；"
        "recalibrate_self=用真实能力校准自我模型。\n"
        "tool=只读工具查询，可填 tool/args；仅允许："
        + "、".join(TOOL_INTENT_ALLOWLIST) + "（不得调用写操作/网络/命令）。\n"
        f"可选 kind：{allowed}、rest。\n"
        "只输出一个 JSON 对象，不要 Markdown：\n"
        '{"kind":"reflect","drive":"curiosity","title":"不超过40字",'
        '"why":"为什么现在做，引用上面的信号","first_step":"最小第一步",'
        '"expected":"完成后可验证的结果","tool":"","args":{}}\n'
        "如果现在没有值得做的事，输出 {\"kind\":\"rest\",\"why\":\"理由\"}。"
    )


def _fallback_proposal(dr, cfg):
    top = str(dr.get("top") or "")
    if top == "maintenance":
        return {"kind": "consolidate", "drive": "maintenance", "title": "整理今天的记忆",
                "why": dr.get("reasons", {}).get("maintenance", ""), "first_step": "运行一次记忆整理",
                "expected": "低价值记忆归档、重复记忆合并"}
    if top == "coherence":
        return {"kind": "update_self_model", "drive": "coherence", "title": "校准自我模型",
                "why": dr.get("reasons", {}).get("coherence", ""), "first_step": "刷新 knows/unknowns/limits",
                "expected": "自我模型反映当前真实状态"}
    if top == "curiosity":
        return {"kind": "write_thought", "drive": "curiosity", "title": "写下一个未解决的问题",
                "why": dr.get("reasons", {}).get("curiosity", ""), "first_step": "记录问题与已知边界",
                "expected": "问题进入 thinking_log，明天可继续"}
    return {"kind": "reflect", "drive": top or "coherence", "title": "复盘当前状态",
            "why": "没有更高优先级的内驱", "first_step": "读最近 12 条时间线",
            "expected": "留下一条可执行的下一步"}


def _reflect_worker(cfg, now_dt=None):
    global _REFLECTING, _LAST_REFLECT
    if not _REFLECT_LOCK.acquire(blocking=False):
        _REFLECTING = False
        return
    _REFLECTING = True
    t0 = time.time()
    proposed = False
    try:
        can, reason, met = metabolism.can_think(cfg)
        state.save({"state": "reflecting"})
        timeline.append("reflect_begin", summary="开始内省", data={"reason": reason}, actor="self")
        if not can:
            timeline.append("rest", summary=f"不反思：{reason}", actor="self")
            state.save({"state": "awake"})
            return
        st = state.load()
        dr = _refresh_drives(now_dt, force=True)
        active = intentions.active()
        prompt = _reflect_prompt(cfg, st, dr, met, active)
        text, usage, model, err = llm.call(prompt, max_tokens=int(cfg.get("life_reflect_max_tokens", 700) or 700),
                                           thinking="low", json_output=True)
        if usage:
            metabolism.charge(usage, model, cfg, reflection=True)
        proposal = llm.parse_json(text) if text else None
        if not isinstance(proposal, dict):
            proposal = _fallback_proposal(dr, cfg)
            summary = f"内省回退：{err or '模型未返回有效 JSON'}"
        else:
            summary = "内省提出意图"
        kind = str(proposal.get("kind") or "reflect")
        if kind == "tool" and str(proposal.get("tool") or "").strip() not in TOOL_INTENT_ALLOWLIST:
            kind = "reflect"
        if kind not in EXECUTABLE_KINDS:
            kind = "reflect"
        if kind == "rest":
            timeline.append("rest", summary=str(proposal.get("why") or "现在不行动")[:200], actor="self")
            state.save({"state": "awake"})
            return
        ok, it, why = intentions.propose(
            title=str(proposal.get("title") or proposal.get("why") or "内省"),
            drive=str(proposal.get("drive") or dr.get("top") or ""),
            why=str(proposal.get("why") or ""),
            first_step=str(proposal.get("first_step") or ""),
            expected=str(proposal.get("expected") or ""),
            kind=kind,
            payload={
                "signals": dr.get("signals") or {},
                "tool": str(proposal.get("tool") or ""),
                "args": proposal.get("args") if isinstance(proposal.get("args"), dict) else {},
            },
            source="agent",
        )
        if not ok and ("无变化" in why or "无效果" in why):
            # 不重复空转：把“为什么无效”本身变成一个可记录的内省。
            ok, it, why2 = intentions.propose(
                title="记录维护动作为什么没有效果",
                drive="maintenance",
                why=why,
                first_step="读最近一次同类动作的 outcome，写下无效原因",
                expected="下一次维护动作不再重复同一路径",
                kind="write_thought", source="agent",
            )
            if not ok:
                why = why2 or why
        if not ok:
            timeline.append("rest", summary=f"意图未建立：{why}", actor="self")
            state.save({"state": "awake"})
            return
        proposed = True
        timeline.append("reflect", summary=f"{summary}：{it.get('title')}",
                        data={"intention": it.get("id"), "kind": kind, "llm": bool(text)}, actor="self")
        # 内省/整理/只读工具动作立即执行；写操作类工具不在白名单内
        if kind in EXECUTABLE_KINDS:
            intentions.start(it["id"])
            ok2, outcome = execute_intention(it)
            if ok2:
                intentions.complete(it["id"], outcome)
            else:
                intentions.fail(it["id"], outcome, scar=True)
    except Exception as e:  # noqa: BLE001
        try:
            import degrade
            degrade.degrade("life.reflect", e, "内省失败，保持清醒", critical=False)
            timeline.append("reflect_error", summary=f"{type(e).__name__}: {e}", actor="self")
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
        try:
            state.save({"state": "awake"})
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
    finally:
        _LAST_REFLECT = time.time()
        _REFLECTING = False
        try:
            if bool(cfg.get("life_metrics_enabled", True)):
                from . import metrics as _metrics
                _metrics.record("reflect_ms", (time.time() - t0) * 1000.0,
                                proposed=bool(proposed))
        # noqa: silent-except: 指标写入失败不影响生命周期
        except Exception:
            pass
        try:
            state.save({"state": "awake", "last_reflect": _now_iso()})
        # noqa: silent-except: 冷却持久化失败不应影响状态恢复
        except Exception:
            pass
        try:
            state.save({"state": "awake"})
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
        _REFLECT_LOCK.release()


def _same_tool_outcome(tool, text):
    """同一只读工具上一次结果是否与本轮完全相同（用于识别空转）。"""
    try:
        items = intentions.list_all(limit=30, status=("done", "failed"))
    except Exception:
        return False
    for x in items:
        if str(x.get("kind")) != "tool":
            continue
        if str((x.get("payload") or {}).get("tool") or "") != str(tool):
            continue
        prev = str(x.get("outcome") or "").replace("（无变化）", "").strip()
        prefix = f"{tool}: "
        if prev.startswith(prefix):
            prev = prev[len(prefix):].strip()
        # 意图 outcome 落盘上限 500 字；用前缀比较，避免长输出永远判不等。
        return bool(prev) and str(text or "").strip().startswith(prev)
    return False


def _execute_tool_intent(it) -> tuple:
    """执行白名单内的只读工具；超时/异常/空结果都返回失败（进 scar）。"""
    payload = it.get("payload") if isinstance(it.get("payload"), dict) else {}
    tool = str(payload.get("tool") or "").strip()
    if tool not in TOOL_INTENT_ALLOWLIST:
        return False, f"工具不在只读白名单：{tool or '（空）'}"
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    try:
        import deepseek_client as dc
    except Exception as e:  # noqa: BLE001
        return False, f"工具注册表不可用：{type(e).__name__}"
    fn = getattr(dc, "TOOL_CALL_MAP", {}).get(tool)
    if not callable(fn):
        return False, f"工具不可用：{tool}"
    try:
        import inspect
        sig = inspect.signature(fn)
        params = sig.parameters
        accept_kwargs = any(p.kind == p.VAR_KEYWORD for p in params.values())
        safe = dict(args) if accept_kwargs else {k: v for k, v in args.items() if k in params}
        missing = [k for k, p in params.items()
                   if p.default is p.empty and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
                   and k not in safe]
        if missing:
            return False, f"缺少必填参数：{','.join(missing)}"
        cfg = _cfg()
        timeout = max(1.0, min(60.0, float(cfg.get("life_tool_timeout_seconds", 20) or 20)))
        max_chars = max(200, int(cfg.get("life_tool_max_output_chars", 2000) or 2000))
        t0 = time.time()
        import concurrent.futures as _cf
        ex = _cf.ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(lambda: fn(**safe))
            result = fut.result(timeout=timeout)
        except _cf.TimeoutError:
            return False, f"工具 {tool} 超时（>{timeout:.0f}s）"
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        text = str(result if result is not None else "").strip()
        if not text:
            return False, f"工具 {tool} 返回空结果"
        if text.startswith(("错误", "失败", "未找到")):
            return False, text[:max_chars]
        text = text[:max_chars] + ("…" if len(text) > max_chars else "")
        suffix = "（无变化）" if _same_tool_outcome(tool, text[:400]) else ""
        try:
            if bool(cfg.get("life_metrics_enabled", True)):
                from . import metrics as _metrics
                _metrics.record("tool_ms", (time.time() - t0) * 1000.0, tool=tool)
        # noqa: silent-except: 指标写入失败不影响工具执行结果
        except Exception:
            pass
        return True, f"{tool}: {text}{suffix}"
    except Exception as e:  # noqa: BLE001
        return False, f"{tool} 执行失败：{type(e).__name__}: {e}"


def execute_intention(it) -> tuple:
    """执行内省/整理/白名单工具意图，返回 ``(ok, outcome)``。"""
    kind = str(it.get("kind") or "reflect")
    title = str(it.get("title") or "")
    why = str(it.get("why") or "")
    expected = str(it.get("expected") or "")
    if kind == "tool":
        return _execute_tool_intent(it)
    try:
        import brainkit as bk
        if kind == "consolidate":
            r = bk.consolidate_memories()
            noop = int(r.get("archived") or 0) == 0 and int(r.get("merged") or 0) == 0
            suffix = "（无变化）" if noop else ""
            return True, (f"整理完成：归档 {r.get('archived')}，合并 {r.get('merged')}，"
                          f"保留 {r.get('kept')}{suffix}")
        if kind == "review_failure":
            decs = [d for d in bk.list_decisions(limit=200) if d.get("status") == "open"]
            sm = bk.load_json(bk.BRAIN_DIR / "self_model.json", {}) or {}
            text = (f"[复盘] {title}\n理由：{why}\n未决决定 {len(decs)} 条；未知 {len(sm.get('unknowns') or [])} 项。\n"
                    f"预期：{expected}\n下一步：从最小可验证的一步开始。")
            bk.append_line(bk.THINKING_DIR / f"{bk.today()}.md",
                           f"## {_now_iso()}\n[复盘] {text}\n")
            return True, f"已复盘：未决 {len(decs)} 条"
        if kind == "write_thought":
            bk.append_line(bk.THINKING_DIR / f"{bk.today()}.md",
                           f"## {_now_iso()}\n[内省] {title}\n{why}\n预期：{expected}\n")
            return True, "已写入思考日志"
        if kind == "update_self_model":
            try:
                import brain_api
                if brain_api.refresh_self_model():
                    return True, "自我模型已用 LLM 校准"
            # noqa: silent-except: LLM 校准不可用时回退模板刷新
            except Exception:
                pass
            bk._refresh_self_model()
            return True, "自我模型已刷新（模板）"
        if kind == "propose_evolution":
            bk.record_evolution(title or "内省提案", kind="proposed", note=(why + "\n" + expected)[:500])
            return True, "已记录进化提案（未自动合并）"
        if kind == "goal":
            g = bk.add_goal(title, note=why, source="agent", drive=str(it.get("drive") or ""), priority=3)
            if not g:
                return False, "目标创建失败或已存在"
            return True, f"已创建目标：{g.get('id')}"
        if kind == "review_memory":
            # 有限自主：只读记忆 + 写思考，不执行外部工具、不改权限。
            q = " ".join([title, why, expected])
            mems = bk.search_memories(q, 5) if q.strip() else bk.load_memories()[-5:]
            lines = [f"[记忆复盘] {title}", f"理由：{why}", "相关记忆："]
            lines += [f"- {str(e.get('text') or '')[:100]}" for e in mems] or ["（无）"]
            bk.append_line(bk.THINKING_DIR / f"{bk.today()}.md",
                           f"## {_now_iso()}\n" + "\n".join(lines) + "\n")
            return True, f"已复盘 {len(mems)} 条相关记忆"
        if kind == "housekeeping":
            from . import metrics as _metrics
            moved = intentions.prune()
            folded = bk.compact_hits()
            bk.prune_decisions()
            pruned = timeline.prune()
            from . import scars as _scars
            _scars.prune()
            _metrics.prune(int(_cfg().get("life_metrics_max", 5000) or 5000))
            synced = 0
            try:
                import memory_facade
                synced = memory_facade.sync_missing_to_brain(50)
            # noqa: silent-except: 补同步失败不影响本次整理
            except Exception:
                pass
            return True, (f"整理完成：归档意图 {moved}，折叠命中 {folded}，"
                          f"裁剪时间线 {pruned}，补同步记忆 {synced}")
        if kind == "recalibrate_self":
            try:
                import brain_api
                if brain_api.refresh_self_model():
                    return True, "自我模型已用 LLM 校准"
            # noqa: silent-except: LLM 不可用时回退模板刷新
            except Exception:
                pass
            bk._refresh_self_model()
            return True, "自我模型已刷新（模板）"
        if kind == "reflect":
            bk.append_line(bk.THINKING_DIR / f"{bk.today()}.md",
                           f"## {_now_iso()}\n[内省] {title}\n{why}\n预期：{expected}\n")
            return True, "内省已记录"
        return False, f"暂不支持的内省类型：{kind}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def status() -> dict:
    st = state.status()
    dr = drives.latest()
    met = metabolism.snapshot()
    it = intentions.report(limit=20)
    areas = {
        "state": st.get("state") or {},
        "owner": st.get("owner"),
        "lease": st.get("lease") or {},
        "drives": dr,
        "metabolism": met,
        "intentions": it,
        "timeline": timeline.tail(30),
    }
    try:
        from . import metrics as _metrics
        areas["metrics"] = _metrics.summary(1)
    except Exception:
        areas["metrics"] = {}
    try:
        import brainkit as _bk
        areas["storage"] = {
            "hits_pending": _bk.hits_pending(),
            "intentions_archived": intentions.archived_count(),
        }
    except Exception:
        areas["storage"] = {}
    rep = _report_cached()
    if rep:
        areas["report"] = rep
    return areas


def _report_cached():
    global _REPORT_CACHE
    now = time.time()
    if _REPORT_CACHE.get("data") and (now - float(_REPORT_CACHE.get("at") or 0)) < 30:
        return _REPORT_CACHE["data"]
    try:
        from . import report
        data = report.report(7)
        _REPORT_CACHE.update({"at": now, "data": data})
        return data
    except Exception:
        return None


def brief() -> str:
    """给对话上下文用的 1-3 行摘要；不触发重计算。"""
    try:
        st = state.load()
        dr = drives.latest()
        met = metabolism.snapshot()
        act = intentions.active()
        parts = [f"[生命循环] 状态={st.get('state')}；能量={met.get('energy')}/100；"
                 f"今日 {met.get('used_tokens')}/{met.get('budget_tokens')} tokens"]
        if dr.get("drives"):
            parts.append("内驱：" + " ".join(f"{k}={v}" for k, v in dr["drives"].items()))
        if act:
            parts.append("当前意图：" + "；".join(f"{x.get('title')}（{x.get('kind')}）" for x in act[:3]))
        return "\n".join(parts)
    except Exception:
        return ""


def pause(reason="manual") -> dict:
    st = state.save({"paused": True, "pause_reason": str(reason)[:120], "state": "paused"})
    try:
        timeline.append("pause", summary=f"生命循环暂停：{reason}", actor="user")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    return {"ok": True, "state": st}


def resume(reason="manual") -> dict:
    st = state.save({"paused": False, "pause_reason": "", "state": "awake", "last_wake": _now_iso()})
    try:
        timeline.append("resume", summary=f"生命循环恢复：{reason}", actor="user")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    return {"ok": True, "state": st}


def wake_now(reason="manual") -> dict:
    st = note_activity(source=reason)
    return {"ok": True, "state": st}


def sleep_now(reason="manual", now=None) -> dict:
    _spawn_sleep(_cfg(), reason or "manual", _now(now))
    return {"ok": True, "spawned": True}


def reflect_now(reason="manual", now=None) -> dict:
    if _REFLECTING:
        return {"ok": False, "reason": "already reflecting"}
    _spawn_reflect(_cfg(), _now(now))
    return {"ok": True, "spawned": True}
