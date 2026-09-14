# -*- coding: utf-8 -*-
"""自我洞察（Self Insight）：能力热力图 + 自我述职（AI 周报）——纯函数层。

设计立场与本项目「会进化」的定位一致：让智能体**看见自己**——
哪里用得多、哪里总出错、这周做了哪些决定、完成了哪些目标、结晶了哪些技能。
原料早已存在（tasklog / failures / patterns / prompts / hint_hits / 大脑决策目标），
本模块只补上「把流水账变成自我认知」这一环。

纯函数、仅标准库（不依赖 deepseek_client / api_server / brainkit），便于单测：
    build_heatmap     能力热力图（工具使用频率 / 失败率 / 结晶 / 预激活命中）
    build_self_report 自我述职（决策 / 目标 / 进化 / 任务 / 成长 / 用量）
    render_report     把述职 dict 渲染为可写盘的 Markdown
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _date_of(ts):
    """从任意时间戳提取 YYYY-MM-DD；无法解析返回 ""。"""
    m = _DATE_RE.search(str(ts or ""))
    return m.group(1) if m else ""


def _today(now=None):
    if now is None:
        return date.today()
    d = _date_of(now)
    if d:
        try:
            return date.fromisoformat(d)
        except ValueError:
            pass
    return date.today()


def _cutoff(days, now=None):
    """窗口起始日（含）；days<=0 返回 None = 不过滤。"""
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = 0
    if d <= 0:
        return None
    return (_today(now) - timedelta(days=d - 1)).isoformat()


def _within(ts, cutoff):
    if not cutoff:
        return True
    d = _date_of(ts)
    return bool(d) and d >= cutoff


def _as_list(x):
    return [i for i in (x if isinstance(x, (list, tuple)) else []) if isinstance(i, dict)]


# ── 能力热力图 ────────────────────────────────────────────────────────────

def build_heatmap(tasks=None, failures=None, patterns=None, prompt_items=None,
                  hint_hits=None, days=30, now=None, hot_limit=8):
    """聚合「工具能力画像」。

    输入（均为已解析对象，路径解析由调用方负责）：
      tasks        tasklog 任务列表 [{title, chain:[工具名], ts}]
      failures     failures.json 条目 [{tool, hits, resolved, last_ts}]
      patterns     patterns.json 条目 [{tool, ts}]
      prompt_items prompts.json 条目（含 auto_skill 草稿）
      hint_hits    preactivate_hits.json {关键词: 命中次数}
      days         统计窗口天数（<=0 = 全部）
    """
    cutoff = _cutoff(days, now)
    jobs = {}
    total_calls = 0
    task_count = 0
    for task in _as_list(tasks):
        chain = [str(c).strip() for c in (task.get("chain") or []) if str(c or "").strip()]
        if not chain or not _within(task.get("ts"), cutoff):
            continue
        task_count += 1
        total_calls += len(chain)
        seen = set()
        for t in chain:
            row = jobs.setdefault(t, {"tool": t, "calls": 0, "tasks": 0, "last_ts": ""})
            row["calls"] += 1
            if t not in seen:
                row["tasks"] += 1
                seen.add(t)
            ts = str(task.get("ts") or "")
            if ts > row["last_ts"]:
                row["last_ts"] = ts

    def _job(tool):
        return jobs.setdefault(str(tool), {"tool": str(tool), "calls": 0, "tasks": 0, "last_ts": ""})

    failure_rows = []
    unresolved_total = 0
    for f in _as_list(failures):
        if not _within(f.get("last_ts") or f.get("first_ts"), cutoff):
            continue
        row = _job(f.get("tool") or "（未知）")
        row["failure_hits"] = int(row.get("failure_hits") or 0) + int(f.get("hits") or 1)
        row["failures"] = int(row.get("failures") or 0) + 1
        if not f.get("resolved"):
            row["unresolved"] = int(row.get("unresolved") or 0) + 1
            unresolved_total += 1
        failure_rows.append({
            "tool": str(f.get("tool") or ""),
            "hits": int(f.get("hits") or 1),
            "resolved": bool(f.get("resolved")),
            "error": str(f.get("error") or "")[:120],
            "last_ts": str(f.get("last_ts") or ""),
        })

    success_total = 0
    for p in _as_list(patterns):
        if not _within(p.get("ts"), cutoff):
            continue
        row = _job(p.get("tool") or "（未知）")
        row["success_patterns"] = int(row.get("success_patterns") or 0) + 1
        success_total += 1

    crystallized = []
    for p in _as_list(prompt_items):
        if not p.get("auto_skill"):
            continue
        item = {
            "name": str(p.get("name") or "")[:60],
            "hits": int(p.get("hits") or 0),
            "chain": [str(x) for x in (p.get("source_chain") or [])][:12],
            "enabled": bool(p.get("enabled")),
        }
        crystallized.append(item)

    tools = sorted(jobs.values(), key=lambda r: (-int(r.get("calls") or 0),
                                                 -int(r.get("failure_hits") or 0),
                                                 str(r.get("tool"))))
    hot = [r["tool"] for r in tools if int(r.get("calls") or 0) > 0][:hot_limit]
    struggles = sorted(
        [r for r in tools if int(r.get("failure_hits") or 0) > 0],
        key=lambda r: (-int(r.get("failure_hits") or 0), str(r.get("tool"))),
    )[:hot_limit]

    pre = []
    if isinstance(hint_hits, dict):
        pre = [{"keyword": str(k), "hits": int(v or 0)}
               for k, v in sorted(hint_hits.items(), key=lambda kv: (-int(kv[1] or 0), str(kv[0])))][:10]

    distinct = len([r for r in tools if int(r.get("calls") or 0) > 0])
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": int(days) if str(days).lstrip("-").isdigit() else 0,
        "window_start": cutoff or "",
        "summary": {
            "tasks": task_count,
            "tool_calls": total_calls,
            "distinct_tools": distinct,
            "avg_chain_len": round(total_calls / task_count, 1) if task_count else 0.0,
            "failure_hits": sum(int(r.get("failure_hits") or 0) for r in tools),
            "unresolved": unresolved_total,
            "success_patterns": success_total,
            "crystallized": len(crystallized),
        },
        "tools": tools,
        "hot": hot,
        "struggles": struggles,
        "crystallized": crystallized,
        "preactivate": pre,
    }


# ── 自我述职（AI 周报）────────────────────────────────────────────────────

def _count_decisions(decisions):
    out = {"total": 0, "open": 0, "kept": 0, "reversed": 0, "items": []}
    for d in _as_list(decisions):
        out["total"] += 1
        st = str(d.get("status") or "open")
        if st in out:
            out[st] += 1
        out["items"].append({
            "ts": str(d.get("ts") or ""),
            "decision": str(d.get("decision") or "")[:160],
            "reason": str(d.get("reason") or "")[:160],
            "expected": str(d.get("expected") or "")[:160],
            "outcome": str(d.get("outcome") or "")[:160],
            "status": st,
        })
    return out


def _summarize_goals(goals):
    active, done = [], []
    for g in _as_list(goals):
        item = {"title": str(g.get("title") or "")[:80],
                "progress": str(g.get("progress") or "")[:40],
                "status": str(g.get("status") or "active")}
        (done if item["status"] == "done" else active).append(item)
    return {"active": active, "done": done, "active_count": len(active), "done_count": len(done)}


def _summarize_evolution(evolution, cutoff):
    proposals = _as_list((evolution or {}).get("proposals")) if isinstance(evolution, dict) else []
    adopted = _as_list((evolution or {}).get("adopted")) if isinstance(evolution, dict) else []
    items = []
    for e in adopted:
        if _within(e.get("date"), cutoff):
            title = str(e.get("title") or "").strip()
            impl = str(e.get("implemented") or "").strip()
            items.append({"date": str(e.get("date") or ""),
                          "title": (title or impl or str(e.get("id") or ""))[:120],
                          "implemented": impl[:160]})
    return {"proposed": len(proposals), "adopted": len(adopted), "items": items[-10:]}


def build_self_report(decisions=None, goals=None, evolution=None, tasks=None,
                      usage=None, prompt_items=None, self_model=None, days=7, now=None):
    """汇总「这段时间我做了什么」——供 AI 生成周报/述职。"""
    cutoff = _cutoff(days, now)
    today = _today(now)

    dec = _count_decisions([d for d in _as_list(decisions) if _within(d.get("ts"), cutoff)])
    goal = _summarize_goals(goals)
    evo = _summarize_evolution(evolution, cutoff)

    task_items = [t for t in _as_list(tasks) if _within(t.get("ts"), cutoff)]
    task_chain_total = sum(len([c for c in (t.get("chain") or []) if str(c or "").strip()]) for t in task_items)
    tasks_out = {
        "total": len(task_items),
        "tool_calls": task_chain_total,
        "items": [{"title": str(t.get("title") or "")[:80],
                   "chain_len": len([c for c in (t.get("chain") or []) if str(c or "").strip()]),
                   "ts": str(t.get("ts") or "")} for t in task_items][-20:],
    }

    skills = [{"name": str(p.get("name") or "")[:60], "hits": int(p.get("hits") or 0)}
              for p in _as_list(prompt_items) if p.get("auto_skill")]

    usage_out = {"prompt_tokens": 0, "completion_tokens": 0, "cache_hit": 0, "cache_miss": 0, "days": 0}
    if isinstance(usage, dict):
        for day, models in usage.items():
            if not _within(day, cutoff):
                continue
            usage_out["days"] += 1
            for _model, u in (models or {}).items():
                if not isinstance(u, dict):
                    continue
                usage_out["prompt_tokens"] += int(u.get("prompt") or 0)
                usage_out["completion_tokens"] += int(u.get("completion") or 0)
                usage_out["cache_hit"] += int(u.get("cache_hit") or 0)
                usage_out["cache_miss"] += int(u.get("cache_miss") or 0)

    sm = self_model if isinstance(self_model, dict) else {}
    self_out = {
        "knows": [str(x)[:120] for x in (sm.get("knows") or [])][:5],
        "unknowns": [str(x)[:120] for x in (sm.get("unknowns") or [])][:5],
        "limits": [str(x)[:120] for x in (sm.get("limits") or [])][:5],
        "source": str(sm.get("source") or ""),
    }

    start = cutoff or ""
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": int(days) if str(days).lstrip("-").isdigit() else 0,
        "period": {"start": start, "end": today.isoformat()},
        "summary": {
            "tasks": tasks_out["total"],
            "tool_calls": tasks_out["tool_calls"],
            "decisions": dec["total"],
            "decisions_open": dec["open"],
            "goals_active": goal["active_count"],
            "evolutions_adopted": len(evo["items"]),
            "crystallized": len(skills),
        },
        "decisions": dec,
        "goals": goal,
        "evolution": evo,
        "tasks": tasks_out,
        "growth": {"crystallized": len(skills), "skills": skills},
        "usage": usage_out,
        "self_model": self_out,
    }


def render_report(report, title="自我述职"):
    """把 build_self_report 的结果渲染为 Markdown（可直接落盘/朗读）。"""
    if not isinstance(report, dict):
        return f"# {title}\n\n（无可述职数据）\n"
    p = report.get("period") or {}
    s = report.get("summary") or {}
    lines = [
        f"# {title}（近 {report.get('days', 0)} 天）",
        "",
        f"- 区间：{p.get('start') or '（全部）'} ~ {p.get('end') or ''}",
        f"- 生成时间：{report.get('generated_at') or ''}",
        "",
        "## 一、这段时间我做了什么",
        f"- 完成任务 **{s.get('tasks', 0)}** 项，累计工具调用 **{s.get('tool_calls', 0)}** 次",
        "- 任务链（tasklog）最近记录见下",
        "",
    ]
    tasks = (report.get("tasks") or {}).get("items") or []
    if tasks:
        for t in tasks[-10:]:
            lines.append(f"  - {t.get('ts', '')} · {t.get('title') or '未命名任务'}（{t.get('chain_len', 0)} 步）")
    else:
        lines.append("  - （本区间无任务记录）")

    dec = report.get("decisions") or {}
    lines += ["", "## 二、我做了哪些决定", f"- 共 {dec.get('total', 0)} 条（未回执 {dec.get('open', 0)}）"]
    for d in (dec.get("items") or [])[-8:]:
        lines.append(f"  - [{d.get('status')}] {d.get('decision')}"
                     + (f"（理由：{d.get('reason')}）" if d.get('reason') else ""))
    if not (dec.get("items") or []):
        lines.append("  - （本区间无决策记录）")

    goals = report.get("goals") or {}
    lines += ["", "## 三、我的目标进度",
              f"- 进行中 {goals.get('active_count', 0)} · 已完成 {goals.get('done_count', 0)}"]
    for g in (goals.get("active") or [])[:8]:
        lines.append(f"  - ⏳ {g.get('title')}" + (f"（{g.get('progress')}）" if g.get('progress') else ""))
    for g in (goals.get("done") or [])[:4]:
        lines.append(f"  - ✅ {g.get('title')}")

    evo = report.get("evolution") or {}
    lines += ["", "## 四、我的成长与进化",
              f"- 技能结晶 { (report.get('growth') or {}).get('crystallized', 0) } 项 · "
              f"本区间采纳进化 {len(evo.get('items') or [])} 项"]
    for sk in ((report.get("growth") or {}).get("skills") or [])[:8]:
        lines.append(f"  - 🧩 {sk.get('name')}（历史成功 {sk.get('hits', 0)} 次）")
    for e in (evo.get("items") or [])[-6:]:
        lines.append(f"  - 🧬 {e.get('title')}" + (f"：{e.get('implemented')}" if e.get('implemented') else ""))

    usage = report.get("usage") or {}
    if usage.get("days"):
        total = int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))
        hit = int(usage.get("cache_hit", 0))
        ratio = (hit / usage["prompt_tokens"] * 100) if usage.get("prompt_tokens") else 0
        lines += ["", "## 五、用量",
                  f"- 覆盖 {usage.get('days')} 天 · 总 token ≈ {total:,} · 缓存命中率 {ratio:.1f}%"]

    sm = report.get("self_model") or {}
    if sm.get("limits") or sm.get("unknowns"):
        lines += ["", "## 六、我的局限与不确定"]
        for x in (sm.get("limits") or [])[:3]:
            lines.append(f"  - 局限：{x}")
        for x in (sm.get("unknowns") or [])[:3]:
            lines.append(f"  - 不确定：{x}")

    lines += ["", "---", "> 本报告由鲸语 WhaleTalk 自动汇总自身行为数据生成；数据来源："
              "tasklog / 决策日志 / 目标 / 进化账本 / 技能结晶 / 用量统计。"]
    return "\n".join(lines) + "\n"
