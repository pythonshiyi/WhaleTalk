"""本地弹幕模拟器：没有平台也能验证"像不像活的"。

剧本刻意包含真实直播里最考验决策的情形：
新观众进场、常客复读、礼物、刷屏、键盘侠、以及**长时间冷场**。
"""
from __future__ import annotations

import itertools
import logging
import threading
from typing import Any, Dict, List, Optional

from . import director as director_mod
from .events import (EV_DANMAKU, EV_ENTER, EV_FOLLOW, EV_GIFT, EV_LIKE, EV_SC, LiveEvent)

logger = logging.getLogger("whaletalk.live.sim")

# delay = 距上一条事件的秒数
SCRIPT: List[Dict[str, Any]] = [
    {"delay": 2, "kind": EV_ENTER, "uid": "sim_xiaoyu", "name": "小鱼"},
    {"delay": 3, "kind": EV_DANMAKU, "uid": "sim_xiaoyu", "name": "小鱼", "text": "新人报道，主播在干嘛呀"},
    {"delay": 6, "kind": EV_DANMAKU, "uid": "sim_laowang", "name": "老王", "text": "又来了，今天吃什么了"},
    {"delay": 5, "kind": EV_GIFT, "uid": "sim_tuhao", "name": "土豪哥",
     "gift": "火箭", "value": 100},
    {"delay": 6, "kind": EV_DANMAKU, "uid": "sim_laowang", "name": "老王", "text": "又来了，今天吃什么了"},
    {"delay": 4, "kind": EV_DANMAKU, "uid": "sim_jiantu", "name": "键盘侠", "text": "这也太无聊了吧"},
    {"delay": 3, "kind": EV_LIKE, "uid": "sim_qianshui", "name": "潜水员", "value": 3},
    {"delay": 4, "kind": EV_DANMAKU, "uid": "sim_guo", "name": "过路人", "text": "主播声音好听"},
    {"delay": 3, "kind": "wait", "delay_extra": 22},          # 冷场：她应该自己开麦
    {"delay": 2, "kind": EV_DANMAKU, "uid": "sim_xiaoyu", "name": "小鱼", "text": "你叫什么名字呀"},
    {"delay": 8, "kind": EV_FOLLOW, "uid": "sim_follow", "name": "新来的"},
    {"delay": 6, "kind": EV_SC, "uid": "sim_tuhao", "name": "土豪哥",
     "text": "今天状态不错，继续加油", "value": 30},
    {"delay": 5, "kind": EV_DANMAKU, "uid": "sim_laowang", "name": "老王", "text": "问你呢，怎么不理人"},
    {"delay": 10, "kind": EV_DANMAKU, "uid": "sim_jiantu", "name": "键盘侠", "text": "刷屏刷屏刷屏刷屏"},
    {"delay": 1, "kind": EV_DANMAKU, "uid": "sim_jiantu", "name": "键盘侠", "text": "刷屏刷屏刷屏刷屏"},
    {"delay": 1, "kind": EV_DANMAKU, "uid": "sim_jiantu", "name": "键盘侠", "text": "刷屏刷屏刷屏刷屏"},
    {"delay": 1, "kind": EV_DANMAKU, "uid": "sim_jiantu", "name": "键盘侠", "text": "刷屏刷屏刷屏刷屏"},
    {"delay": 4, "kind": "wait", "delay_extra": 18},
    {"delay": 2, "kind": EV_DANMAKU, "uid": "sim_guo", "name": "过路人", "text": "你是AI吗"},
]


class Simulator:
    """按剧本把事件喂给导演；可 step 单步、可调速度、可循环。"""

    def __init__(self, director=None, speed: float = 1.0, loop: bool = True) -> None:
        self.director = director if director is not None else director_mod.current()
        self.speed = max(0.1, float(speed))
        self.loop = bool(loop)
        self.emitted = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._idx = 0
        self._last_event: Optional[LiveEvent] = None

    # ── 单步 ────────────────────────────────────────────────
    def step(self) -> Optional[LiveEvent]:
        """立即发出剧本里的下一条事件（wait 项直接跳过并返回 None）。"""
        with self._lock:
            if not SCRIPT:
                return None
            item = SCRIPT[self._idx % len(SCRIPT)]
            self._idx += 1
        ev = self._emit(item)
        self._last_event = ev
        return ev

    def _emit(self, item: Dict[str, Any]) -> Optional[LiveEvent]:
        if str(item.get("kind")) == "wait":
            return None
        ev = LiveEvent(
            kind=str(item.get("kind") or EV_DANMAKU),
            user_id=str(item.get("uid") or ""),
            user_name=str(item.get("name") or ""),
            text=str(item.get("text") or ""),
            gift_name=str(item.get("gift") or ""),
            value=float(item.get("value") or 0.0),
        )
        if self.director is not None:
            self.director.submit(ev)
        self.emitted += 1
        logger.info("模拟事件：%s %s %s", ev.kind, ev.user_name, ev.text or ev.gift_name)
        return ev

    # ── 自动播放 ────────────────────────────────────────────
    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="live-sim")
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            th, self._thread = self._thread, None
        if th is not None and th.is_alive():
            th.join(timeout=3)

    def running(self) -> bool:
        th = self._thread
        return bool(th is not None and th.is_alive())

    def _loop(self) -> None:
        rounds = itertools.count()
        while not self._stop.is_set():
            for item in SCRIPT:
                if self._stop.is_set():
                    return
                delay = float(item.get("delay") or 0.0)
                if str(item.get("kind")) == "wait":
                    delay += float(item.get("delay_extra") or 0.0)
                if delay > 0 and self._stop.wait(delay / self.speed):
                    return
                self._emit(item)
            if not self.loop:
                return
            next(rounds)


__all__ = ["SCRIPT", "Simulator"]
