"""注意力：决定一条事件值不值得回、半个房间该不该出声。

这是"像不像活的"最关键的一层：真人主播经常不接某些弹幕、只接一部分人、
对刷屏视而不见。**"不接"必须是一等公民**，而不是兜底分支。
"""
from __future__ import annotations

import re
import time
from typing import List, Optional, Sequence

from .events import EV_DANMAKU, EV_ENTER, EV_FOLLOW, EV_GIFT, EV_LIKE, EV_SC, LiveEvent
from .room import Room

# 提问信号（中英文）
_QUESTION_RE = re.compile(r"[?？]|吗|呢|怎么|为什么|是不是|有没有|能不能|谁|什么|哪|咋|如何")

# 基础分：信息密度越高越值得回
_BASE = {
    EV_SC: 4.2,
    EV_DANMAKU: 2.0,
    EV_GIFT: 1.6,
    EV_FOLLOW: 1.3,
    EV_ENTER: 0.6,
    EV_LIKE: 0.2,
}

# 低于这个分数就不接（沉默）
MIN_REPLY_SCORE = 1.6


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def mentions_name(text: str, persona_name: str) -> bool:
    name = str(persona_name or "").strip()
    return bool(name) and name in str(text or "")


def score(ev: LiveEvent, room: Room, now: Optional[float] = None,
          persona_name: str = "") -> float:
    """给一条事件打显著性分数；<=0 表示完全不值得回。"""
    now = time.time() if now is None else float(now)
    s = float(_BASE.get(ev.kind, 1.0))
    text = str(ev.text or "")
    clean = _clean(text)
    age = max(0.0, now - float(ev.ts or now))

    if ev.kind == EV_DANMAKU:
        if not clean:
            return 0.0
        if len(clean) <= 1:
            s -= 1.2
        if _QUESTION_RE.search(text):
            s += 1.6
        if mentions_name(text, persona_name):
            s += 2.2
        if room.spam(ev.user_id, now=now):
            return -1.0                     # 刷屏：直接无视，不回也不报错
        if room.repeated(ev.user_id, text, now=now, before=ev.ts):
            s -= 2.4                        # 同一人复读：大概率不值得重复回
    elif ev.kind in (EV_GIFT, EV_SC):
        # 礼物值得自然道谢，但**不是**必须回；大额也不改变人格
        s += min(1.5, float(ev.value or 0.0) / 100.0)
    elif ev.kind == EV_ENTER:
        info = room.viewer(ev.user_id)
        s += 0.8 if int((info or {}).get("visits") or 0) <= 1 else -0.4
    elif ev.kind == EV_LIKE:
        s = 0.1                          # 点赞批量忽略，不逐条谢

    # 新观众稍微加权，常客减一点（不需要每次都招呼）
    info = room.viewer(ev.user_id) or {}
    visits = int(info.get("visits") or 0)
    if visits <= 1:
        s += 0.6
    elif visits >= 5:
        s -= 0.3

    # 太旧的事件不再回：弹幕有强时效，25 秒前的提问已经"过期"了
    if age > 25:
        s -= 3.5
    elif age > 12:
        s -= 1.0
    return round(s, 3)


def pick(events: Sequence[LiveEvent], room: Room, now: Optional[float] = None,
         persona_name: str = "") -> Optional[LiveEvent]:
    """从一批事件里挑一条最值得回的；都不值得就返回 None（沉默）。"""
    now = time.time() if now is None else float(now)
    scored: List[tuple] = []
    for ev in events:
        s = score(ev, room, now, persona_name)
        if s >= MIN_REPLY_SCORE:
            scored.append((s, ev))
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1].ts))
    return scored[-1][1]


__all__ = ["MIN_REPLY_SCORE", "mentions_name", "pick", "score"]
