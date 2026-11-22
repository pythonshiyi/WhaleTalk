"""导演：持续在线的决策循环。

每一秒做一次廉价判断：
    有没有值得回的事件 → 回；没有 → 看冷场多久 → 自己起个话头；否则什么都不做。

"什么都不做"是合法状态。真人直播里大部分时间没有台词，机械感恰恰来自"每来一条就回一句"。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from . import actor, attention
from .events import EV_DANMAKU, EV_GIFT, EV_SC, EventBus, LiveEvent
from .room import Room

logger = logging.getLogger("whaletalk.live.director")


def _cfg() -> Dict[str, Any]:
    try:
        import config_utils
        return config_utils.load_config()
    except Exception:
        return {}


class Director:
    def __init__(self, room: Optional[Room] = None, bus: Optional[EventBus] = None,
                 persona_id: str = "") -> None:
        self.room = room if room is not None else Room()
        self.bus = bus if bus is not None else EventBus()
        self.persona_id = str(persona_id or "")
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.ticks = 0
        self.last_action = ""
        # 限速等待期间"待回"的事件（限速不该等于丢问题，但过期弹幕也不该回）
        self._hold = []

    # ── 生命周期 ────────────────────────────────────────────
    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self.room.running = True
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="live-director")
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self.room.running = False
            th, self._thread = self._thread, None
        if th is not None and th.is_alive():
            th.join(timeout=3)

    def running(self) -> bool:
        th = self._thread
        return bool(th is not None and th.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
                self.room.maybe_save()
            except Exception:  # noqa: BLE001 - 导演循环不能因为单次异常退出
                logger.exception("直播导演 tick 失败，下一轮继续")
            self._stop.wait(1.0)

    # ── 事件入口（平台适配器/模拟器/手工接口都走这里）────────
    def submit(self, ev: LiveEvent) -> None:
        self.bus.publish(ev)
        self.room.add_event(ev)
        self.room.touch_viewer(ev.user_id, ev.user_name)
        self.room.on_activity(1.2 if ev.kind in (EV_GIFT, EV_SC) else 0.5)
        if ev.text:
            self.room.note_seen(ev.user_id, ev.text)

    # ── 决策 ────────────────────────────────────────────────
    def _rate_ok(self, now: float) -> bool:
        cfg = _cfg()
        try:
            per_min = max(1, min(30, int(cfg.get("live_max_lines_per_minute", 6) or 6)))
        except (TypeError, ValueError):
            per_min = 6
        last = float(self.room.data().get("last_line_ts") or 0.0)
        return (now - last) >= (60.0 / per_min)

    def _persona_name(self) -> str:
        try:
            return actor.persona_name(actor.load_persona(self.persona_id))
        except Exception:
            return "大肥鱼"

    def tick_once(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else float(now)
        self.ticks += 1
        self.room.tick(now)
        events = [e for e in (self._hold + self.bus.drain()) if (now - e.ts) <= 25]
        self._hold = []
        best = attention.pick(events, self.room, now, self._persona_name())

        if best is not None:
            if not self._rate_ok(now):
                # 刚说过话：把最有价值的几条留住，等限速窗口过去再回（不丢问题）
                self._hold = sorted(
                    events, key=lambda e: attention.score(e, self.room, now, self._persona_name()),
                    reverse=True)[:3]
                self._hold = [e for e in self._hold if (now - e.ts) <= 25]
                dropped = max(0, len(events) - len(self._hold))
                self.room.data()["stats"]["ignored"] = int(
                    self.room.data()["stats"].get("ignored") or 0) + dropped
                self.last_action = "idle"
                return {"action": "idle", "reason": "rate_limited",
                        "held": len(self._hold), "ignored": dropped}
            line = actor.reply_to(best, self.room, self.persona_id)
            if best.kind == EV_DANMAKU and best.text:
                self.room.set_topic(best.text[:60])
            self.last_action = "reply"
            return {"action": "reply", "event": best.as_dict(), "line": line,
                    "ignored": max(0, len(events) - 1)}

        cfg = _cfg()
        try:
            idle_seconds = max(8.0, float(cfg.get("live_idle_speak_seconds", 25) or 25))
        except (TypeError, ValueError):
            idle_seconds = 25.0
        data = self.room.data()
        idle = now - max(float(data.get("last_line_ts") or 0.0),
                         float(data.get("last_activity") or 0.0))
        if best is None and idle >= idle_seconds and float(data.get("energy") or 0.0) >= 25 \
                and self._rate_ok(now):
            line = actor.self_talk(self.room, self.persona_id)
            self.last_action = "self_talk"
            return {"action": "self_talk", "line": line, "ignored": len(events)}

        if events:
            self.room.data()["stats"]["ignored"] = int(
                self.room.data()["stats"].get("ignored") or 0) + len(events)
        self.last_action = "idle"
        return {"action": "idle", "ignored": len(events), "idle_seconds": round(idle, 1)}

    def status(self) -> Dict[str, Any]:
        snap = self.room.snapshot()
        snap.update({
            "running": self.running(),
            "persona_id": self.persona_id,
            "persona_name": self._persona_name(),
            "ticks": self.ticks,
            "last_action": self.last_action,
            "pending_events": self.bus.pending(),
        })
        return snap


# ── 进程级单例（api_server / CLI 共用）─────────────────────
_director: Optional[Director] = None
_singleton_lock = threading.RLock()


def configure(room_path: Optional[str] = None, persona_id: str = "",
              room: Optional[Room] = None) -> Director:
    """（重新）构造导演。传入 room 便于测试注入。"""
    global _director
    with _singleton_lock:
        if _director is not None and _director.running():
            _director.stop()
        r = room
        if r is None:
            r = Room(room_path).load()
        _director = Director(room=r, persona_id=persona_id)
        return _director


def current() -> Optional[Director]:
    return _director


def start() -> bool:
    d = _director
    return bool(d and d.start())


def stop() -> None:
    if _director is not None:
        _director.stop()


def running() -> bool:
    return bool(_director and _director.running())


def submit(ev: LiveEvent) -> None:
    if _director is not None:
        _director.submit(ev)


def tick_once(now: Optional[float] = None) -> Dict[str, Any]:
    if _director is None:
        return {"action": "idle", "reason": "director not configured"}
    return _director.tick_once(now)


def status() -> Dict[str, Any]:
    if _director is None:
        return {"running": False, "configured": False}
    return _director.status()


__all__ = ["Director", "configure", "current", "running", "start", "status", "stop", "submit", "tick_once"]
