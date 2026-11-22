"""直播事件模型与本地事件总线。

任何直播平台（B 站/抖音/YouTube/本地模拟器）都只负责把原生消息翻译成
`LiveEvent` 塞进 `EventBus`；之后的注意力、决策、表达与平台无关。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List

EV_DANMAKU = "danmaku"
EV_GIFT = "gift"
EV_ENTER = "enter"
EV_FOLLOW = "follow"
EV_LIKE = "like"
EV_SC = "sc"          # 醒目留言 / Super Chat
KINDS = (EV_DANMAKU, EV_GIFT, EV_ENTER, EV_FOLLOW, EV_LIKE, EV_SC)


@dataclass
class LiveEvent:
    """归一化后的直播间事件。"""

    kind: str
    user_id: str = ""
    user_name: str = ""
    text: str = ""
    gift_name: str = ""
    value: float = 0.0          # 礼物价值（元），仅作展示/统计，不驱动人格
    ts: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "text": self.text,
            "gift_name": self.gift_name,
            "value": self.value,
            "ts": self.ts,
        }


class EventBus:
    """线程安全的事件队列：平台/模拟器写入，导演循环取走。"""

    def __init__(self, maxlen: int = 800) -> None:
        self._q: Deque[LiveEvent] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.dropped = 0

    def publish(self, ev: LiveEvent) -> None:
        if not isinstance(ev, LiveEvent):
            raise TypeError("publish 只接受 LiveEvent")
        with self._lock:
            if len(self._q) >= self._q.maxlen:
                self.dropped += 1
            self._q.append(ev)

    def drain(self) -> List[LiveEvent]:
        with self._lock:
            items = list(self._q)
            self._q.clear()
            return items

    def pending(self) -> int:
        with self._lock:
            return len(self._q)


__all__ = ["KINDS", "LiveEvent", "EventBus",
           "EV_DANMAKU", "EV_GIFT", "EV_ENTER", "EV_FOLLOW", "EV_LIKE", "EV_SC"]
