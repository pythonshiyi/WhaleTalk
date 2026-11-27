"""虚拟角色生存状态引擎：打赏 → 吃饱度 → 角色反应。

设计边界
--------
本模块只管「数值」和「该说什么」，**不碰任何直播平台接口**：
- 打赏事件由外部（抖音官方礼物，平台结算）通过 `feed()` 喂进来；
- 弹幕事件同样由外部喂给 `on_chat()`；
- 它产出的是「状态快照」和「角色台词」，由 HUD 网页 + TTS 呈现。

为什么单独成模块
----------------
这套数值系统是整个付费玩法的核心（观众送礼物 → 角色吃饱 → 画面变化），
与平台接入完全无关。把它独立出来，既能单独测试，也能换任何平台复用。

叙事设定：角色要靠观众打赏"吃上饭"活下来。这是虚构角色扮演，
观众明确知道它是 AI 虚拟角色，画面持续标注（合规要求）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from persistence import atomic_json_write

logger = logging.getLogger("whaletalk.livestate")

STATE_FILE: Optional[str] = None

_lock = threading.RLock()

# 抖币 → 人民币：10 抖币 = 1 元
DOUCOIN_PER_YUAN = 10.0
# 音浪/抖币 → 主播实收：平台抽 50%，签公会后实际到手约 25%~35%（取中位 30% 做保守估算）
PLATFORM_TAKE = 0.5
GUILD_TAKE = 0.2

DEFAULTS: Dict[str, Any] = {
    # ── 数值参数 ──
    "daily_quota": 20.0,          # 今日"食量"额度（元）：吃满 = 吃饱
    "hunger_hours": 6.0,          # 多久从满饿到空（越小越催打赏）
    "yuan_per_doucoin": 0.1,
    "reset_hour": 5,              # 每日重置时刻（凌晨 5 点，避开直播高峰）
    # ── 情绪阈值（占比）──
    "threshold_starving": 0.2,    # < 20% 极度饥饿
    "threshold_hungry": 0.5,      # < 50% 有点饿
    "threshold_full": 0.95,       # >= 95% 吃饱
    # ── 对话上限 ──
    "max_lines_per_minute": 3,    # 每分钟最多说几句（防刷屏 + 降风控）
    "max_chat_reply_seconds": 20, # 同一观众多少秒内不重复回
}

_MOODS = ("starving", "hungry", "normal", "full")


@dataclass
class LiveSnapshot:
    """一次性喂给 HUD 的完整状态。"""

    mood: str = "normal"            # starving / hungry / normal / full
    filled: float = 0.0             # 0..1 吃饱度
    today_yuan: float = 0.0         # 今日观众实付（元）
    today_doucoin: float = 0.0      # 今日抖币
    today_net_yuan: float = 0.0     # 今日主播预计实收（元，保守估算）
    quota_yuan: float = 0.0         # 今日额度
    remaining_yuan: float = 0.0     # 还差多少吃饱
    gift_count: int = 0             # 今日礼物次数
    unique_givers: int = 0          # 今日打赏人数
    top_gifter: str = ""            # 今日大金主
    top_gifter_yuan: float = 0.0
    last_gift: Dict[str, Any] = field(default_factory=dict)
    day: str = ""                   # 归属日期
    updated_at: str = ""
    # 近期事件（HUD 滚动条用）
    recent: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _today_key(now: Optional[datetime] = None, reset_hour: Optional[int] = None) -> str:
    """按 reset_hour 切分"业务日"（重置时刻之前算前一天）。

    reset_hour 由调用方从**实例配置**传入：模块级 DEFAULTS 只是出厂默认，
    用户在设置里改的值不能在这里被忽略（否则该配置项形同虚设）。
    """
    n = now or datetime.now()
    try:
        rh = int(DEFAULTS["reset_hour"] if reset_hour is None else reset_hour)
    except (TypeError, ValueError):
        rh = int(DEFAULTS["reset_hour"])
    rh = max(0, min(23, rh))
    if n.hour < rh:
        n = n - timedelta(days=1)
    return n.strftime("%Y-%m-%d")


def _blank_day(day: str, quota: float) -> Dict[str, Any]:
    return {
        "day": day,
        "quota_yuan": float(quota),
        "today_yuan": 0.0,
        "today_doucoin": 0.0,
        "gift_count": 0,
        "givers": {},          # name -> yuan
        "last_gift": {},
        "recent": [],
        "started_at": time.time(),
        # 剩余饱腹度（元）+ 结算时刻：每笔打赏各自从到达时刻起按 hunger_hours 流失，
        # 而不是从当日重置时刻统一起算（后者会让下午之后任何打赏都填不饱）。
        "satiety_yuan": 0.0,
        "satiety_at": time.time(),
    }


class LiveState:
    """虚拟角色的生存状态。线程安全。"""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        self.cfg = dict(DEFAULTS)
        if cfg:
            self.cfg.update({k: v for k, v in cfg.items() if k in DEFAULTS})
        self._data: Dict[str, Any] = {}
        self._loaded = False
        self._last_spoke = 0.0
        self._per_user: Dict[str, float] = {}

    # ── 持久化 ──

    def load(self) -> "LiveState":
        global STATE_FILE
        with _lock:
            if self._data:
                return self
            if STATE_FILE and os.path.exists(STATE_FILE):
                try:
                    with open(STATE_FILE, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and data.get("day"):
                        self._data = data
                except Exception:
                    logger.exception("读取直播状态失败，使用新的一天")
            self._roll_day()
            self._loaded = True
            return self

    def save(self) -> bool:
        with _lock:
            if not STATE_FILE:
                return False
            try:
                return bool(atomic_json_write(STATE_FILE, self._data, indent=1))
            except Exception:
                logger.exception("写入直播状态失败")
                return False

    def _roll_day(self) -> None:
        """跨日（按实例配置的 reset_hour）自动重置今日计数。"""
        today = _today_key(reset_hour=self.cfg.get("reset_hour"))
        cur = str(self._data.get("day") or "")
        if cur != today:
            quota = float(self.cfg.get("daily_quota") or 20.0)
            self._data = _blank_day(today, quota)
        else:
            # 配置变了要同步额度
            self._data["quota_yuan"] = float(self.cfg.get("daily_quota") or 20.0)
            # 旧状态文件兼容：没有饱腹度字段时把当日已收金额视为「刚吃下」，
            # 不让升级当天的角色凭空变饿。
            self._data.setdefault("satiety_yuan", float(self._data.get("today_yuan") or 0.0))
            self._data.setdefault("satiety_at", time.time())

    # ── 数值 ──

    def _satiety(self, now: Optional[float] = None) -> float:
        """当前剩余饱腹度（元）：每笔打赏各自按 hunger_hours 线性流失。

        旧实现用「当日重置时刻」统一起算，导致开播 6 小时后衰减量 ≥ 1 个额度，
        哪怕观众打赏满额度，`filled` 也只能是 0（角色永远饿着）。改为按
        「最后一次结算」起算后，晚间的打赏照样能喂饱——这才是打赏→饱腹的本意。
        """
        hours = max(0.1, float(self.cfg.get("hunger_hours") or 6.0))
        window = hours * 3600.0
        sat = max(0.0, float(self._data.get("satiety_yuan") or 0.0))
        at = float(self._data.get("satiety_at") or 0.0)
        cur = time.time() if now is None else float(now)
        if sat > 0 and at > 0 and window > 0:
            elapsed = max(0.0, cur - at)
            sat = sat * max(0.0, 1.0 - elapsed / window)
        return sat

    def _decay_filled(self) -> float:
        """随时间线性"变饿"。返回当前吃饱度 0..1。"""
        quota = max(0.01, float(self._data.get("quota_yuan") or 20.0))
        return max(0.0, min(1.0, self._satiety() / quota))

    def _mood(self, filled: float) -> str:
        if filled < float(self.cfg.get("threshold_starving") or 0.2):
            return "starving"
        if filled < float(self.cfg.get("threshold_hungry") or 0.5):
            return "hungry"
        if filled >= float(self.cfg.get("threshold_full") or 0.95):
            return "full"
        return "normal"

    def snapshot(self) -> LiveSnapshot:
        with _lock:
            self._roll_day()
            sat = self._satiety()
            quota = max(0.01, float(self._data.get("quota_yuan") or 20.0))
            filled = max(0.0, min(1.0, sat / quota))
            givers = self._data.get("givers") or {}
            top_name, top_yuan = "", 0.0
            if givers:
                top_name, top_yuan = max(givers.items(), key=lambda kv: kv[1])
            today_yuan = float(self._data.get("today_yuan") or 0.0)
            net = today_yuan * (1.0 - PLATFORM_TAKE) * (1.0 - GUILD_TAKE)
            return LiveSnapshot(
                mood=self._mood(filled),
                filled=round(filled, 4),
                today_yuan=round(today_yuan, 2),
                today_doucoin=round(float(self._data.get("today_doucoin") or 0.0), 1),
                today_net_yuan=round(net, 2),
                quota_yuan=round(quota, 2),
                # 「还差多少吃饱」按当前剩余饱腹度算：与 mood/filled 同源，
                # 否则角色饿着、HUD 却显示"还差 0 元"（旧口径按今日累计收入）。
                remaining_yuan=round(max(0.0, quota - sat), 2),
                gift_count=int(self._data.get("gift_count") or 0),
                unique_givers=len(givers),
                top_gifter=str(top_name or ""),
                top_gifter_yuan=round(float(top_yuan or 0.0), 2),
                last_gift=dict(self._data.get("last_gift") or {}),
                day=str(self._data.get("day") or ""),
                updated_at=datetime.now().isoformat(timespec="seconds"),
                recent=list(self._data.get("recent") or [])[:20],
            )

    # ── 事件输入 ──

    def feed(
        self,
        giver: str,
        doucoin: float,
        gift_name: str = "礼物",
        count: int = 1,
    ) -> Dict[str, Any]:
        """记录一次打赏。返回变化前后，供 HUD 做动画判断。

        金额单位是抖币（平台赠送通知里就是抖币），内部换算成人民币。
        """
        with _lock:
            self._roll_day()
            before = self.snapshot()
            per_yuan = float(self.cfg.get("yuan_per_doucoin") or 0.1)
            try:
                doucoin = max(0.0, float(doucoin))
                count = max(1, int(count))
            except (TypeError, ValueError):
                doucoin, count = 0.0, 1
            total_doucoin = doucoin * count
            yuan = total_doucoin * per_yuan

            # 先把这段间隔内已经发生的流失结清，再把本笔打赏加到饱腹度上：
            # 这样「谁在什么时候喂了多少」各自计衰减，晚到的打赏不会再被白扣。
            now_ts = time.time()
            self._data["satiety_yuan"] = self._satiety(now_ts) + yuan
            self._data["satiety_at"] = now_ts

            self._data["today_doucoin"] = float(self._data.get("today_doucoin") or 0.0) + total_doucoin
            self._data["today_yuan"] = float(self._data.get("today_yuan") or 0.0) + yuan
            self._data["gift_count"] = int(self._data.get("gift_count") or 0) + 1
            givers = self._data.setdefault("givers", {})
            name = str(giver or "神秘观众")[:40]
            givers[name] = float(givers.get(name) or 0.0) + yuan
            record = {
                "type": "gift",
                "who": name,
                "gift": str(gift_name or "礼物")[:20],
                "count": count,
                "doucoin": round(total_doucoin, 1),
                "yuan": round(yuan, 2),
                "at": datetime.now().strftime("%H:%M:%S"),
                "ts": time.time(),
            }
            self._data["last_gift"] = record
            recent = self._data.setdefault("recent", [])
            recent.insert(0, record)
            del recent[40:]
            after = self.snapshot()
            self.save()
            return {
                "before": before.as_dict(),
                "after": after.as_dict(),
                "delta": {
                    "yuan": round(after.today_yuan - before.today_yuan, 2),
                    "filled": round(after.filled - before.filled, 4),
                },
                "mood_changed": before.mood != after.mood,
                "just_full": after.mood == "full" and before.mood != "full",
                "record": record,
            }

    # ── 对话限速 ──

    def can_speak(self) -> bool:
        """全局发言限速：防止刷屏（也降低平台风控关注）。"""
        limit = max(1, int(self.cfg.get("max_lines_per_minute") or 3))
        now = time.time()
        recent = [t for t in getattr(self, "_speak_times", []) if now - t < 60]
        self._speak_times = recent
        return len(recent) < limit

    def mark_spoken(self) -> None:
        now = time.time()
        times = [t for t in getattr(self, "_speak_times", []) if now - t < 60]
        times.append(now)
        self._speak_times = times
        self._last_spoke = now

    def user_can_talk(self, uid: str) -> bool:
        """同一观众冷却，避免一个人刷屏占满对话。"""
        gap = float(self.cfg.get("max_chat_reply_seconds") or 20)
        now = time.time()
        last = self._per_user.get(str(uid) or "", 0.0)
        return now - last >= gap

    def mark_user_talked(self, uid: str) -> None:
        self._per_user[str(uid) or ""] = time.time()
        if len(self._per_user) > 500:
            keep = sorted(self._per_user.items(), key=lambda kv: kv[1], reverse=True)[:250]
            self._per_user = dict(keep)


# ══════════════════════════════════════════════════════════════════════════
# 角色台词：按情绪与事件给出该说什么
# ══════════════════════════════════════════════════════════════════════════

_MOOD_HINT = {
    "starving": "你现在饿得前胸贴后背，说话有气无力，提到吃的会非常渴望。",
    "hungry": "你有点饿了，还撑得住，但会忍不住惦记吃的。",
    "normal": "你已经吃了一些，状态正常，可以正常聊天。",
    "full": "你今天吃饱了，心满意足，语速轻快，愿意多聊几句。",
}


def mood_hint(mood: str) -> str:
    return _MOOD_HINT.get(str(mood or ""), _MOOD_HINT["normal"])


def build_feed_prompt(
    snap: LiveSnapshot,
    record: Dict[str, Any],
    persona_name: str = "大肥鱼",
) -> str:
    """构造"收到礼物后角色该说什么"的提示词。"""
    return (
        f"【你是直播间的虚拟角色「{persona_name}」，靠观众打赏吃饭活下去】\n"
        f"当前状态：{mood_hint(snap.mood)}"
        f"（吃饱度 {snap.filled*100:.0f}%，今日已收到 {snap.today_yuan:.1f} 元，"
        f"离吃饱还差 {snap.remaining_yuan:.1f} 元）\n\n"
        f"刚刚发生：观众「{record.get('who')}」送出了 {record.get('gift')}"
        f"×{record.get('count')}（折合 {record.get('yuan')} 元）。\n\n"
        "请用**一到两句话**回应这位观众，要求：\n"
        "1. 真诚感谢，但不要像客服话术，要符合角色性格；\n"
        "2. 把礼物和「吃饭/饱腹」自然联系起来；\n"
        "3. 如果这是今天第一次收到礼物，可以表现出惊喜；\n"
        "4. 如果还没吃饱，可以不经意地表达还饿；\n"
        "5. 不要提到 AI、模型、程序、开发者；\n"
        "6. 不要索要打赏、不要诱导消费（这是平台红线）。\n"
        "直接输出台词，不要加引号或说明。"
    )


def build_chat_prompt(
    snap: LiveSnapshot,
    user: str,
    text: str,
    persona_name: str = "大肥鱼",
) -> str:
    """构造"回复一条弹幕"的提示词。"""
    return (
        f"【你是直播间的虚拟角色「{persona_name}」，靠观众打赏吃饭活下去】\n"
        f"当前状态：{mood_hint(snap.mood)}"
        f"（吃饱度 {snap.filled*100:.0f}%）\n\n"
        f"观众「{user}」发来弹幕：{text}\n\n"
        "请用**一句话**回复这条弹幕，要求：\n"
        "1. 口语化、简短，像真人在直播间随口接话；\n"
        "2. 符合角色当前饥饿状态；\n"
        "3. 不要提 AI、模型、程序；\n"
        "4. 不要索要礼物、不要诱导消费；\n"
        "5. 遇到低俗、辱骂、敏感话题，礼貌转移话题即可。\n"
        "直接输出台词，不要加引号或说明。"
    )


__all__ = [
    "DEFAULTS",
    "LiveSnapshot",
    "LiveState",
    "STATE_FILE",
    "build_chat_prompt",
    "build_feed_prompt",
    "mood_hint",
]
