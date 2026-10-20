#!/usr/bin/env python3
"""生命循环 soak：24 小时 / 7 天连续运行验证。

默认是**加速模拟**：用假时钟和假模型在几秒内跑完 24h/7d，验证
tick、反思、睡眠、预算、重启连续性、时间线唯一性。
``--real`` 才按真实时间运行（需要真实 API Key，默认不跑）。

用法::

    python tools/life_soak.py --hours 24
    python tools/life_soak.py --hours 168 --step-minutes 5
    python tools/life_soak.py --real --hours 24

模拟不会写真实 ``brain/``：使用临时大脑目录。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time as real_time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import brainkit as bk  # noqa: E402

CFG = {
    "life_enabled": True,
    "life_pulse_minutes": 30,
    "life_reflect_idle_minutes": 30,
    "life_reflect_cooldown_minutes": 60,
    "life_reflect_min_drive": 15,
    "life_reflect_max_tokens": 500,
    "life_sleep_idle_minutes": 120,
    "life_quiet_hours": "22:00-07:00",
    "life_auto_sleep": True,
    "life_sleep_llm": True,
    "life_max_active_intentions": 3,
    "life_budget_scope": "self",
    "life_daily_token_budget": 200000,
    "life_daily_cost_budget": 1.0,
    "life_fatigue_per_action": 8.0,
    "life_off_peak_only": False,
    "brain_community_enabled": False,
}

USAGE = {"prompt": 900, "completion": 120, "cache_hit": 200, "cache_miss": 700}


def _iso(clock):
    return clock["now"].isoformat(timespec="seconds")


def _patch_clock(clock):
    """把生命模块的时间源统一到假时钟。"""
    from life import drives, intentions, loop, metabolism, scars, sleep, state, timeline

    def iso(ts=None):
        return _iso(clock)

    timeline._iso = iso
    state._now_iso = iso
    intentions._iso = iso
    scars._iso = iso
    metabolism._now_iso = iso
    sleep._now_iso = iso
    loop._now_iso = iso
    loop._now = lambda now=None: now if isinstance(now, datetime) else clock["now"]
    metabolism._today = lambda: clock["now"].date().isoformat()
    sleep._today = lambda: clock["now"].date().isoformat()

    def days_since(value, default=7.0):
        try:
            dt = datetime.fromisoformat(str(value))
            now = clock["now"]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=now.tzinfo)
            return max(0.0, (now - dt.astimezone(now.tzinfo)).total_seconds() / 86400.0)
        except Exception:
            return float(default)

    drives._days_since = days_since

    class FakeTime:
        def time(self):
            return clock["now"].timestamp()

        def sleep(self, _seconds):
            return None

    loop.time = FakeTime()
    drives.time = FakeTime()
    # 不允许旧缓存跨小时复用
    drives._CACHE.update({"sig": None, "at": 0.0, "data": None})


def _fake_llm(prompt, max_tokens=700, thinking="low", json_output=True):
    p = str(prompt or "")
    if "睡眠巩固" in p or "睡眠叙事" in p:
        return "今天整理了记忆，也确认了下一步仍从最小可验证的动作开始。", dict(USAGE), "deepseek-flash", ""
    if "【最强内驱】coherence" in p:
        return json.dumps({
            "kind": "update_self_model", "drive": "coherence",
            "title": "校准自我模型", "why": "自我模型仍是模板",
            "first_step": "列出当前确实知道/不知道/做不到的事",
            "expected": "self_model.source 变为 llm",
        }, ensure_ascii=False), dict(USAGE), "deepseek-flash", ""
    if "【最强内驱】maintenance" in p:
        return json.dumps({
            "kind": "write_thought", "drive": "maintenance",
            "title": "记录维护动作为什么没有效果",
            "why": "连续整理没有产生变化",
            "first_step": "读最近一次 outcome",
            "expected": "下一次维护换成观察或睡眠",
        }, ensure_ascii=False), dict(USAGE), "deepseek-flash", ""
    if "【最强内驱】curiosity" in p:
        return json.dumps({
            "kind": "write_thought", "drive": "curiosity",
            "title": "推进一个未解问题",
            "why": "仍有未完成目标",
            "first_step": "写下当前已知与未知",
            "expected": "留下可继续的下一步",
        }, ensure_ascii=False), dict(USAGE), "deepseek-flash", ""
    return json.dumps({
        "kind": "reflect", "drive": "mastery",
        "title": "复盘当前状态", "why": "没有更高优先级内驱",
        "first_step": "读最近时间线", "expected": "留下下一步",
    }, ensure_ascii=False), dict(USAGE), "deepseek-flash", ""


def _fake_refresh_self_model():
    bk.save_json(bk.BRAIN_DIR / "self_model.json", {
        "knows": ["我在一个隔离的 soak 环境里"],
        "unknowns": ["真实用户下一次会给什么任务"],
        "limits": ["工具型自主尚未启用"],
        "source": "llm", "calibrated_at": bk.now_iso(),
    })
    return True


def _prepare_brain(root):
    brain = root / "brain"
    bk.set_brain_dir(brain)
    bk.save_json(brain / "manifest.json", {"brain_id": "soak-" + uuid.uuid4().hex[:8], "fingerprint": "soak"})
    bk.save_json(brain / "self_model.json", {
        "knows": ["我知道测试"], "unknowns": ["未知"], "limits": ["局限"],
        "source": "template",
    })
    bk.save_json(brain / "heartbeat.json", {"resume_hint": "soak start"})
    bk.add_goal("推进生命循环验证", source="user")
    bk.add_goal("观察一次完整睡眠", source="user")
    bk.record_decision("先跑 soak 再加功能", reason="验证优先", expected="发现真实断点")
    bk.record_decision("不引入审批", reason="产品取舍", expected="自主优先")
    # 一条真实记忆，让 consolidate 不是永远空集
    bk.remember_structured("生命循环 soak 使用假时钟，不写真实大脑。", type="测试", importance=3,
                            tags=["soak"], source="测试", origin="system")
    return brain


def _runtime_reset():
    from life import loop
    loop._REFLECTING = False
    loop._SLEEPING = False
    loop._REPORT_CACHE.update({"at": 0.0, "data": None})


def simulate(hours=24, step_minutes=5, restarts=True, keep=False):
    from life import drives, intentions, llm, loop, metabolism, sleep, state, timeline
    import brain_api
    import deepseek_client as dc

    root = Path(tempfile.mkdtemp(prefix="wt_life_soak_"))
    clock = {"now": datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)}
    _patch_clock(clock)
    brain = _prepare_brain(root)
    stats_path = root / "stats.json"
    dc.STATS_FILE = str(stats_path)

    loop._cfg = lambda: dict(CFG)
    sleep._cfg = lambda: dict(CFG)
    metabolism._cfg = lambda: dict(CFG)
    drives._cfg = lambda: dict(CFG)
    intentions._max_active = lambda: 3
    llm.call = _fake_llm
    llm.available = lambda: True
    brain_api.refresh_self_model = _fake_refresh_self_model
    brain_api.brain_action = lambda *a, **k: {"ok": True}

    start = clock["now"]
    total_min = max(1, int(hours * 60))
    step = max(1, int(step_minutes))
    boot_expected = 1 + (max(0, (total_min - 1) // 360) if restarts else 0)
    loop.start("soak")
    actions = {"reflections": 0, "sleeps": 0, "restarts": 0}
    last_tick = None

    for minute in range(step, total_min + 1, step):
        clock["now"] = start + timedelta(minutes=minute)
        # 每 4 小时一次用户活动；其余时间让个体自己走
        if minute % 240 == 0:
            loop.note_activity("soak-chat")
        if restarts and minute % 360 == 0 and minute < total_min:
            loop.stop("soak-restart")
            _runtime_reset()
            loop.start("soak-restart")
            actions["restarts"] += 1
        res = loop.tick(now=clock["now"], synchronous=True)
        if isinstance(res, dict):
            if res.get("action") == "reflect_spawned":
                actions["reflections"] += 1
            elif res.get("action") == "sleep_spawned":
                actions["sleeps"] += 1
        last_tick = res

    loop.stop("soak-end")
    life = state.load()
    ints = intentions.list_all()
    counts = intentions.counts()
    events = timeline.read()
    kinds = {}
    for ev in events:
        kinds[ev.get("kind")] = kinds.get(ev.get("kind"), 0) + 1
    meta = metabolism.snapshot(CFG)
    report = {
        "ok": True,
        "hours": hours,
        "step_minutes": step,
        "ticks": life.get("tick_count"),
        "boot_count": life.get("boot_count"),
        "last_sleep": life.get("last_sleep"),
        "last_state": life.get("state"),
        "intentions": counts,
        "intentions_done": len([x for x in ints if x.get("status") == "done"]),
        "actions": actions,
        "timeline_events": len(events),
        "timeline_kinds": kinds,
        "noop_intentions": len([x for x in ints if "无变化" in str(x.get("outcome") or "")]),
        "metabolism": {k: meta.get(k) for k in (
            "used_tokens", "budget_tokens", "used_cost", "budget_cost",
            "energy", "fatigue", "can_think", "scope")},
        "brain_dir": str(brain),
    }
    failures = []
    if int(report["ticks"] or 0) < total_min // step:
        failures.append("tick 数不足")
    if not report["last_sleep"]:
        failures.append("没有发生睡眠")
    if hours >= 6 and int(report["boot_count"] or 0) < boot_expected:
        failures.append("重启后 boot_count 未递增")
    if report["intentions_done"] < 1:
        failures.append("没有完成任何意图")
    if report["noop_intentions"] > max(2, report["intentions_done"] // 2):
        failures.append("空转意图过多")
    if not meta.get("can_think"):
        failures.append("代谢预算在 soak 结束时不可思考")
    if not kinds.get("sleep_end"):
        failures.append("timeline 缺少 sleep_end")
    if len({ev.get("id") for ev in events}) != len(events):
        failures.append("timeline 存在重复 id")
    report["failures"] = failures
    report["ok"] = not failures
    if not keep:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    return report


def run_real(hours=24):
    """真实时间模式：使用当前大脑，每 30 秒 tick 一次。"""
    from life import loop, state
    end = real_time.time() + hours * 3600
    loop.start("real-soak")
    try:
        while real_time.time() < end:
            loop.tick()
            real_time.sleep(30)
    finally:
        loop.stop("real-soak-end")
    return {"ok": True, "hours": hours, "state": state.load()}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Life Loop soak")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--step-minutes", type=int, default=5)
    ap.add_argument("--real", action="store_true", help="按真实时间运行（需要 API Key）")
    ap.add_argument("--no-restarts", action="store_true")
    ap.add_argument("--keep", action="store_true", help="保留临时大脑目录")
    args = ap.parse_args(argv)
    if args.real:
        report = run_real(args.hours)
    else:
        report = simulate(args.hours, args.step_minutes, restarts=not args.no_restarts, keep=args.keep)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
