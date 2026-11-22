"""直播间状态：观众关系、能量/情绪、话题与短期记忆（可持久化）。

与 `live_state.LiveState` 的分工：
- LiveState  ：打赏账本（今日收入/额度/榜单），纯数值，不再驱动人格；
- Room       ：直播间的"在场状态"——谁来过、气氛如何、此刻在聊什么、刚才说过什么。
  角色的人格与表达依赖这里，而不是依赖"今天收了多少钱"。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from persistence import atomic_json_write

# 默认议程：不是逐条朗读的剧本，而是"她今晚可以聊什么"的种子。
AGENDA_SEED = (
    "聊聊今天遇到的一件小事",
    "接着刚才弹幕里的话题往下说",
    "随口问观众一个问题（比如今天过得怎么样）",
    "讲一个很短的小事或冷笑话",
)

MAX_LINES = 8          # 最近台词（给模型做上下文）
MAX_EVENTS = 30        # 最近事件（给 HUD 滚动条）
MAX_SEEN = 300         # 去重/防刷窗口


def _blank() -> Dict[str, Any]:
    return {
        "version": 1,
        "day": time.strftime("%Y-%m-%d"),
        "energy": 70.0,
        "last_activity": time.time(),
        "last_line_ts": 0.0,
        "topic": "",
        "agenda": list(AGENDA_SEED),
        "agenda_i": 0,
        "viewers": {},          # uid -> {name, visits, first_seen, last_seen, affinity}
        "lines": [],            # [{text, kind, target, ts}]
        "events": [],           # [{kind, user, text, gift, value, ts}]
        "seen": [],             # [{uid, sig, ts}]
        "stats": {"danmaku": 0, "gift": 0, "enter": 0, "replies": 0,
                  "self_talk": 0, "ignored": 0},
    }


class Room:
    """线程安全的直播间状态。"""

    def __init__(self, path: Optional[str] = None, persona_name: str = "大肥鱼") -> None:
        self.path = str(path) if path else None
        self.persona_name = str(persona_name or "大肥鱼")
        self._lock = threading.RLock()
        self._data = _blank()
        self._loaded = False
        self._dirty = False
        self._last_save = 0.0
        self.running = False

    # ── 持久化 ──────────────────────────────────────────────
    def load(self) -> "Room":
        with self._lock:
            if self._loaded:
                return self
            data = None
            if self.path and os.path.exists(self.path):
                try:
                    with open(self.path, encoding="utf-8") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict):
                        data = raw
                except Exception:
                    data = None
            base = _blank()
            if data:
                base.update({k: v for k, v in data.items() if k in base})
                base["stats"].update(data.get("stats") or {})
            self._data = base
            now = time.time()
            if str(base.get("day") or "") != time.strftime("%Y-%m-%d"):
                # 跨天：保留观众关系，重置当天计数与台词记忆
                self._data.update({"day": time.strftime("%Y-%m-%d"), "topic": "",
                                   "lines": [], "events": [], "seen": [],
                                   "stats": {k: 0 for k in self._data["stats"]}})
            # 离线衰减最多扣 30 点，避免隔天回来角色"累瘫"
            idle = max(0.0, now - float(self._data.get("last_activity") or now))
            self._data["energy"] = max(
                0.0, min(100.0, float(self._data.get("energy") or 70.0) - min(30.0, idle * 0.02)))
            # 会话级字段不跨进程保留：上次的事件/台词/限速时间戳只是一次直播的现场，
            # 留着会让新一场直播一开始就"刚说过话、弹幕已经刷过"。
            self._data.update({"last_activity": now, "last_line_ts": 0.0,
                               "lines": [], "events": [], "seen": []})
            self._loaded = True
            return self

    def save(self, force: bool = False) -> bool:
        with self._lock:
            if not self.path:
                return False
            if not force and not self._dirty:
                return False
            try:
                ok = bool(atomic_json_write(self.path, self._data, indent=1))
            except Exception:
                return False
            self._dirty = not ok
            self._last_save = time.time()
            return ok

    def maybe_save(self, interval: float = 5.0) -> bool:
        """导演 tick 里调用：最多每 interval 秒落一次盘。"""
        with self._lock:
            if not self._dirty or (time.time() - self._last_save) < interval:
                return False
        return self.save()

    # ── 时间/能量/情绪 ──────────────────────────────────────
    def tick(self, now: Optional[float] = None) -> None:
        """随时间衰减精力（有互动时由 on_activity 回补）。"""
        now = time.time() if now is None else float(now)
        with self._lock:
            idle = max(0.0, now - float(self._data.get("last_activity") or now))
            if idle > 0.5:
                self._data["energy"] = max(
                    0.0, float(self._data.get("energy") or 0.0) - idle * 0.02)
            self._dirty = True

    def on_activity(self, amount: float = 0.6, now: Optional[float] = None) -> None:
        now = time.time() if now is None else float(now)
        with self._lock:
            self._data["energy"] = min(100.0, float(self._data.get("energy") or 0.0) + amount)
            self._data["last_activity"] = now
            self._dirty = True

    def mood(self) -> str:
        with self._lock:
            e = float(self._data.get("energy") or 0.0)
        if e >= 80:
            return "兴致很高"
        if e >= 55:
            return "还算有精神"
        if e >= 30:
            return "有点安静"
        return "有些倦了"

    def next_topic(self) -> str:
        with self._lock:
            agenda = list(self._data.get("agenda") or AGENDA_SEED)
            if not agenda:
                agenda = list(AGENDA_SEED)
            i = int(self._data.get("agenda_i") or 0) % len(agenda)
            self._data["agenda_i"] = i + 1
            topic = str(agenda[i])
            self._data["topic"] = topic
            self._dirty = True
            return topic

    def set_topic(self, topic: str) -> None:
        with self._lock:
            self._data["topic"] = str(topic or "")[:80]
            self._dirty = True

    # ── 观众 ────────────────────────────────────────────────
    def touch_viewer(self, uid: str, name: str = "", now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else float(now)
        uid = str(uid or "").strip()
        with self._lock:
            viewers = self._data.setdefault("viewers", {})
            if not uid:
                # 没有稳定 id 的观众（模拟器里的路人）用名字兜底
                uid = "匿名:" + str(name or "观众")
            item = viewers.get(uid)
            if not isinstance(item, dict):
                item = {"name": "", "visits": 0, "first_seen": now,
                        "last_seen": now, "affinity": 0}
                viewers[uid] = item
            if name:
                item["name"] = str(name)[:32]
            item["visits"] = int(item.get("visits") or 0) + 1
            item["last_seen"] = now
            item["affinity"] = min(100, int(item.get("affinity") or 0) + 1)
            self._dirty = True
            return dict(item)

    def viewer(self, uid: str) -> Dict[str, Any]:
        with self._lock:
            item = (self._data.get("viewers") or {}).get(str(uid or ""))
            return dict(item) if isinstance(item, dict) else {}

    def relation(self, ev) -> str:
        """给提示词用的一句关系描述：新观众 / 常客 / 熟人。"""
        info = self.viewer(ev.user_id) or self.viewer("匿名:" + (ev.user_name or "观众"))
        visits = int((info or {}).get("visits") or 0)
        if visits <= 1:
            return "第一次来"
        if visits <= 3:
            return f"来过 {visits} 次"
        return f"常客（来过 {visits} 次）"

    # ── 台词/事件/去重 ──────────────────────────────────────
    def add_line(self, text: str, kind: str = "reply", target: str = "",
                 now: Optional[float] = None) -> None:
        now = time.time() if now is None else float(now)
        with self._lock:
            lines = self._data.setdefault("lines", [])
            lines.append({"text": str(text or ""), "kind": kind,
                          "target": str(target or ""), "ts": now})
            del lines[:-MAX_LINES]
            self._data["last_line_ts"] = now
            self._data["last_activity"] = now
            key = "self_talk" if kind == "self_talk" else "replies"
            self._data["stats"][key] = int(self._data["stats"].get(key) or 0) + 1
            self._dirty = True

    def add_event(self, ev) -> None:
        with self._lock:
            events = self._data.setdefault("events", [])
            events.append(ev.as_dict())
            del events[:-MAX_EVENTS]
            st = self._data["stats"]
            if ev.kind in st:
                st[ev.kind] = int(st.get(ev.kind) or 0) + 1
            self._dirty = True

    def note_seen(self, uid: str, text: str, now: Optional[float] = None) -> None:
        import re
        now = time.time() if now is None else float(now)
        sig = re.sub(r"\s+", "", str(text or ""))[:60]
        with self._lock:
            seen = self._data.setdefault("seen", [])
            seen.append({"uid": str(uid or ""), "sig": sig, "ts": now})
            del seen[:-MAX_SEEN]
            self._dirty = True

    def repeated(self, uid: str, text: str, window: float = 180.0,
                 now: Optional[float] = None, before: Optional[float] = None) -> bool:
        """同一人复读检测。

        before：只统计**这条事件之前**的记录。事件在 submit 时已经记入 seen，
        若不排除自己，每条第一次出现的弹幕都会被判成复读（实测扣到 −0.4 分，
        结果"几乎所有弹幕都不回"）。
        """
        import re
        now = time.time() if now is None else float(now)
        cutoff = now if before is None else float(before)
        sig = re.sub(r"\s+", "", str(text or ""))[:60]
        if not sig:
            return False
        with self._lock:
            for item in reversed(self._data.get("seen") or []):
                ts = float(item.get("ts") or 0)
                if now - ts > window:
                    break
                if ts >= cutoff - 1e-6:
                    continue          # 这条事件自己（或更晚的），不算复读
                if item.get("uid") == str(uid or "") and item.get("sig") == sig:
                    return True
        return False

    def spam(self, uid: str, window: float = 10.0, limit: int = 4,
             now: Optional[float] = None) -> bool:
        now = time.time() if now is None else float(now)
        with self._lock:
            n = sum(1 for item in (self._data.get("seen") or [])
                    if item.get("uid") == str(uid or "")
                    and now - float(item.get("ts") or 0) <= window)
        return n >= limit

    # ── 快照 ────────────────────────────────────────────────
    def recent_lines(self, n: int = 6) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(x) for x in (self._data.get("lines") or [])[-max(1, n):]]

    def recent_events(self, n: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(x) for x in (self._data.get("events") or [])[-max(1, n):]]

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            viewers = self._data.get("viewers") or {}
            regulars = sorted(
                ({"name": (v or {}).get("name") or k, "visits": int((v or {}).get("visits") or 0)}
                 for k, v in viewers.items()),
                key=lambda x: -x["visits"])[:3]
            return {
                "persona": self.persona_name,
                "running": bool(self.running),
                "energy": round(float(self._data.get("energy") or 0.0), 1),
                "mood": self.mood(),
                "topic": str(self._data.get("topic") or ""),
                "viewers": len(viewers),
                "regulars": regulars,
                "stats": dict(self._data.get("stats") or {}),
                "last_line_ts": float(self._data.get("last_line_ts") or 0.0),
                "lines": self.recent_lines(4),
            }

    def data(self) -> Dict[str, Any]:
        """调试/测试用：直接拿内部数据（只读语义）。"""
        with self._lock:
            return self._data


__all__ = ["AGENDA_SEED", "MAX_EVENTS", "MAX_LINES", "Room"]
