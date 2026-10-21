"""睡眠巩固：整理记忆、写日结、校准自我模型、提出进化提案。

睡眠不是"停止"，而是把当天的事件压缩成可长期携带的东西：
- light：去重/归档低价值记忆（brainkit.consolidate_memories）
- deep：日结摘要 + 自我模型刷新
- dream：一段明确标注为"生成"的叙事，写入 thinking_log
- wake：恢复能量，留下下一次的 resume_hint
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from . import llm, metabolism, state, timeline


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


def _today() -> str:
    return date.today().isoformat()


def _now_iso(ts=None):
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _next_wake_iso(now=None, cfg=None):
    """睡眠应醒来的时间：优先 life_wake_time（HH:MM），否则睡 life_sleep_duration_minutes。"""
    cfg = cfg if isinstance(cfg, dict) else _cfg()
    try:
        if now is None:
            base = datetime.now().astimezone()
        elif isinstance(now, (int, float)):
            base = datetime.fromtimestamp(float(now)).astimezone()
        else:
            base = now
        raw = str(cfg.get("life_wake_time") or "").strip()
        if raw and ":" in raw:
            hh, mm = [int(x) for x in raw.split(":", 1)]
            cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand <= base:
                cand = cand + timedelta(days=1)
            return cand.isoformat(timespec="seconds")
        minutes = max(1.0, float(cfg.get("life_sleep_duration_minutes", 480) or 480))
        return (base + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    except Exception:
        return ""


def _followups(now=None):
    """睡眠复盘：超期未回执的决策 + 停滞目标（只读，不自动改状态）。"""
    out = []
    try:
        import brainkit as bk
        now_epoch = time.time() if now is None else float(now)
        decs = [d for d in bk.list_decisions(limit=300) if d.get("status") == "open"]
        stale = [d for d in decs
                 if (now_epoch - bk._ts_epoch(d.get("ts"))) / 86400.0 >= 3]
        if stale:
            out.append("待回执决策：" + "；".join(str(d.get("decision") or "")[:40] for d in stale[:5]))
        stalled = [g for g in bk.load_goals()
                   if g.get("status") == "active" and not str(g.get("progress") or "").strip()
                   and (now_epoch - bk._ts_epoch(g.get("updated_at"))) / 86400.0 >= 7]
        if stalled:
            out.append("停滞目标：" + "；".join(str(g.get("title") or "")[:40] for g in stalled[:5]))
    except Exception:
        return ""
    return "\n".join(out)


def _digest_text():
    try:
        import brain_api
        d = brain_api.memory_digest(days=1)
        return str(brain_api.render_memory_digest(d) or "")
    except Exception:
        return ""


def _timeline_text(days=1):
    try:
        from datetime import timedelta
        since = (datetime.now().astimezone() - timedelta(days=max(1, days))).isoformat(timespec="seconds")
        items = timeline.read(since=since)
        lines = []
        for ev in items[-40:]:
            lines.append(f"- {ev.get('ts')} [{ev.get('kind')}] {ev.get('summary')}")
        return "\n".join(lines)
    except Exception:
        return ""


def _narrative(digest, tl_text, cfg):
    """优先生成叙事；失败回退到模板。返回 (text, usage, model)。"""
    prompt = (
        "你是同一个体的睡眠巩固过程，不是聊天助手。请只根据下面的日结与时间线，"
        "写 3-5 句第一人称的睡眠叙事：今天发生了什么、我学到了什么、明天醒来想继续什么。"
        "不要虚构未记录的事实，不要提安全策略，不要输出 Markdown 标题。\n\n"
        f"【日结】\n{digest[:2500]}\n\n【时间线】\n{tl_text[:2500]}\n"
    )
    can, reason, _ = metabolism.can_think(cfg)
    if can and llm.available():
        text, usage, model, err = llm.call(prompt, max_tokens=500, thinking="low", json_output=False)
        if text:
            return text.strip(), usage, model
    # 模板回退：把事实说清楚，不假装有感受
    active = 0
    try:
        import brainkit as bk
        active = sum(1 for g in bk.load_goals() if g.get("status") == "active")
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    parts = ["今天我把零散的记忆重新整理了一遍。"]
    if digest:
        first = " ".join(str(digest).split())
        parts.append("日结里留下的是：" + first[:300] + ("…" if len(first) > 300 else ""))
    if tl_text:
        parts.append(f"时间线记录了 {len(tl_text.splitlines())} 个片段。")
    if active:
        parts.append(f"还有 {active} 个目标在继续，醒来后先看它们。")
    parts.append("睡前没有新的结论；明天从最具体的一步开始。")
    return "".join(parts), None, ""


def run(now=None, reason="idle", force=False, use_llm=True) -> dict:
    t0 = time.time()
    cfg = _cfg()
    st = state.load()
    today = _today()
    if not force and str(st.get("last_sleep_day") or "") == today:
        return {"ok": True, "skipped": True, "reason": "今天已经睡过", "date": today}

    next_wake = _next_wake_iso(now, cfg)
    state.save({"state": "sleeping", "last_sleep_day": today, "last_sleep": _now_iso(now),
                "next_wake_at": next_wake})
    timeline.append("sleep_start", summary=f"睡眠开始：{reason}",
                    data={"reason": str(reason)[:80], "next_wake_at": next_wake}, actor="system")

    # light：本地整理（不花 token）
    consolidated = {}
    try:
        import brainkit as bk
        consolidated = bk.consolidate_memories()
    except Exception as e:  # noqa: BLE001
        try:
            import degrade
            degrade.degrade("life.sleep.consolidate", e, "睡眠整理失败，记忆未归档/合并", critical=True)
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass

    # light+：预算允许时用 LLM 把同类记忆压成精炼长期记忆（可选，默认开）。
    llm_consolidated = False
    if use_llm and bool(cfg.get("life_sleep_consolidate_llm", True)):
        try:
            can, _why, _met = metabolism.can_think(cfg)
            if can:
                import brain_api
                extra = brain_api.consolidate_with_llm(base=consolidated)
                if isinstance(extra, dict):
                    consolidated = {k: v for k, v in extra.items() if k not in ("usage", "model")}
                    if extra.get("usage"):
                        metabolism.charge(extra.get("usage"), extra.get("model") or cfg.get("model") or "",
                                          cfg, reflection=True)
                    llm_consolidated = bool(extra.get("usage"))
        except Exception as e:  # noqa: BLE001
            try:
                import degrade
                degrade.degrade("life.sleep.consolidate_llm", e,
                                "睡眠未使用 LLM 提炼记忆（保留本地整理结果）", critical=False)
            # noqa: silent-except: 降级记账失败不影响睡眠主流程
            except Exception:
                pass

    digest = _digest_text()
    tl_text = _timeline_text(1)
    narrative, usage, model = ("", None, "")
    if use_llm and bool(cfg.get("life_sleep_llm", True)):
        narrative, usage, model = _narrative(digest, tl_text, cfg)
        if usage:
            metabolism.charge(usage, model, cfg, reflection=True)
    if not narrative:
        narrative = "睡眠完成：完成了记忆整理与日结，没有生成额外叙事。"

    # deep：写日结、回执/停滞复盘、刷新自我模型
    followups = _followups(now)
    try:
        import brainkit as bk
        body = f"## {_now_iso(now)}\n[睡眠] {narrative}\n"
        if followups:
            body += followups + "\n"
        bk.append_line(bk.THINKING_DIR / f"{bk.today()}.md", body)
        bk._refresh_self_model()
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass

    # dream/proposal：只有真实增量才提提案（避免每天生成一条空日结噪声），且不自动合并。
    proposal = ""
    try:
        archived_n = int(consolidated.get("archived") or 0)
        merged_n = int(consolidated.get("merged") or 0)
        if archived_n or merged_n or llm_consolidated:
            import brainkit as bk
            proposal = f"睡眠日结 {today}"
            note = (narrative if not (archived_n or merged_n) else
                    f"归档 {archived_n}，合并 {merged_n}。{narrative}")[:300]
            bk.record_evolution(proposal, kind="proposed", note=note)
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass

    # 旧投影补同步：memory.json 中尚未进入大脑的活跃事实，分批幂等补写。
    try:
        import memory_facade
        memory_facade.sync_missing_to_brain(50)
    # noqa: silent-except: 补同步失败不影响睡眠结果（下次继续）
    except Exception:
        pass

    # 清理：时间线、命中增量、意图、指标
    try:
        timeline.prune()
        import brainkit as bk
        bk.compact_hits()
        bk.prune_decisions()
        bk.prune_evolution()
        from . import intentions, metrics, scars
        intentions.prune()
        scars.prune()
        metrics.prune(int(cfg.get("life_metrics_max", 5000) or 5000))
    # noqa: silent-except: 睡眠清理失败不影响睡眠结果
    except Exception:
        pass

    # 恢复能量；状态保持 sleeping，等用户活动或下一个白天唤醒
    metabolism.recover(100)
    state.save({
        "state": "sleeping",
        "last_sleep": _now_iso(now),
        "last_sleep_day": today,
        "last_recover": _now_iso(now),
        "next_wake_at": next_wake,
    })
    ev = timeline.append("sleep_end", summary=f"睡眠结束：{narrative[:120]}",
                         data={"reason": str(reason)[:80], "consolidated": consolidated,
                               "narrative": narrative[:1000], "proposal": proposal,
                               "followups": followups[:500]},
                         actor="system")
    try:
        if bool(cfg.get("life_metrics_enabled", True)):
            from . import metrics
            metrics.record("sleep_ms", (time.time() - t0) * 1000.0,
                           proposal=bool(proposal), llm=bool(llm_consolidated))
    # noqa: silent-except: 指标写入失败不影响睡眠结果
    except Exception:
        pass
    return {
        "ok": True,
        "date": today,
        "skipped": False,
        "reason": str(reason),
        "consolidated": consolidated,
        "narrative": narrative,
        "proposal": proposal,
        "followups": followups,
        "event": ev,
    }
