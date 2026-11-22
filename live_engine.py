"""直播门面：把「打赏账本 / 导演 / 演员 / 舞台 / 模拟器」串成对外接口。

分层
----
- `live_state.py`     打赏账本（今日收入/额度/榜单），纯数值，**不再驱动人格**；
- `live/`             直播角色 v2：事件总线 / 房间状态 / 注意力 / 演员 / 导演 / 舞台；
- `live_engine.py`    本模块（薄门面）：旧 API 兼容 + `/v1/live/*` 的调用面；
- `api_server`        HTTP 接口与静态 HUD 服务。

兼容约定
--------
历史接口（on_gift / on_chat / latest_line / podium_data / _deliver …）保持可用：
- 导演未运行时，手工送礼/弹幕走同步回复（网页测试页行为不变）；
- 导演运行时，事件入总线，由导演统一决定什么时候、对谁说。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import live_state
from live import actor as live_actor
from live import director as live_director
from live import safety, stage, voice
from live.events import EV_DANMAKU, EV_GIFT, EV_SC, LiveEvent
from live.room import Room
from live.simulator import Simulator

logger = logging.getLogger("whaletalk.live")

CONFIG_FILE: Optional[str] = None
_state: Optional[live_state.LiveState] = None
_lock = threading.RLock()
_sim: Optional[Simulator] = None

# 舞台当前台词（与 live.stage 共享同一个 dict，兼容历史测试直接改 _latest）
_latest: Dict[str, Any] = stage.LATEST

# 历史导出：live_engine._is_safe_line / _clean_line 仍可用
_is_safe_line = safety.is_safe_line
_clean_line = safety.clean_line


# ── 配置/路径 ──────────────────────────────────────────────

def _cfg() -> Dict[str, Any]:
    try:
        import config_utils
        return config_utils.load_config()
    except Exception:
        return {}


def _persona_id() -> str:
    return str(_cfg().get("live_persona_id") or "").strip()


def init(config_path: Optional[str] = None,
         state_path: Optional[str] = None, *,
         room_path: Optional[str] = None,
         reset: bool = False) -> None:
    """接线：配置 / 打赏账本 / 房间记忆路径，并（重新）构造导演。"""
    global CONFIG_FILE, _state
    with _lock:
        if reset or _state is None:
            CONFIG_FILE = str(config_path) if config_path else CONFIG_FILE
            live_state.STATE_FILE = str(state_path) if state_path else live_state.STATE_FILE
            _state = live_state.LiveState().load()
        room = Room(room_path).load()
        live_director.configure(room=room, persona_id=_persona_id())


def state() -> live_state.LiveState:
    global _state
    with _lock:
        if _state is None:
            _state = live_state.LiveState().load()
        return _state


def room() -> Room:
    d = live_director.current()
    if d is not None:
        return d.room
    return Room(None).load()


def reload_settings(cfg: Optional[Dict[str, Any]] = None) -> live_state.LiveState:
    """热更新数值参数（额度/饥饿速度等），不丢当天数据。"""
    global _state
    with _lock:
        old = state()
        new = live_state.LiveState(cfg or {})
        new._data = old._data          # 保留当天计数
        new._loaded = True
        new._roll_day()
        _state = new
        new.save()
        return new


# ── 兼容：舞台/账本快照 ────────────────────────────────────

def snapshot_dict() -> Dict[str, Any]:
    return state().snapshot().as_dict()


def latest_line() -> Dict[str, Any]:
    return stage.latest()


def recent_events(limit: int = 20) -> List[Dict[str, Any]]:
    return room().recent_events(max(1, min(50, int(limit or 20))))


def podium_data() -> Dict[str, Any]:
    """HUD 专用聚合（免 token）：账本快照 + 最近事件 + 当前台词 + 房间状态。"""
    snap = state().snapshot().as_dict()
    r = room()
    return {
        "snapshot": snap,
        "events": r.recent_events(12),
        "line": stage.latest(),
        "room": r.snapshot(),
    }


def room_snapshot() -> Dict[str, Any]:
    return room().snapshot()


# ── 兼容：台词发布 / 语音 ──────────────────────────────────

def _publish_line(text: str, audio: str = "") -> int:
    return stage.publish(text, audio)


def _speak(text: str) -> Dict[str, Any]:
    return voice.speak(text)


def _speak_into_published(text: str, seq: int) -> None:
    """后台合成语音并回填（只有台词没被覆盖时才生效）。"""
    if not voice.ENABLED:
        return
    audio = _speak(text)
    url = str(audio.get("audio") or "")
    if url:
        stage.attach_audio(seq, url)


def _deliver(text: str, mood: str, *, say: bool) -> Dict[str, Any]:
    """统一的"说一句"出口：限速 + 安全过滤 + 后台语音（历史接口）。"""
    st = state()
    if not st.can_speak():
        return {"text": text, "spoken": False, "reason": "rate_limited"}
    if not _is_safe_line(text):
        logger.warning("直播台词被合规过滤：%s", text[:60])
        return {"text": text, "spoken": False, "reason": "filtered"}
    st.mark_spoken()
    seq = _publish_line(text, "")
    if say and voice.ENABLED:
        threading.Thread(target=_speak_into_published, args=(text, seq),
                         daemon=True, name="live-tts").start()
    return {"text": text, "spoken": True, "audio": "", "engine": "",
            "audio_pending": bool(say)}


# ── 手工接口（网页测试页 / 外部调用）──────────────────────

def _manual_can_speak() -> bool:
    st = state()
    if not st.can_speak():
        return False
    st.mark_spoken()
    return True


def on_gift(giver: str, doucoin: float, gift_name: str = "礼物",
            count: int = 1, *, say: bool = True) -> Dict[str, Any]:
    """收到打赏：记账 → 导演运行时入总线；否则同步回一句。"""
    st = state()
    change = st.feed(giver, doucoin, gift_name, count)
    ev = LiveEvent(kind=EV_GIFT, user_id="", user_name=str(giver or "神秘观众"),
                   gift_name=str(gift_name or "礼物"),
                   value=float((change.get("record") or {}).get("yuan") or 0.0))
    d = live_director.current()
    if d is not None and d.running():
        d.submit(ev)
        return {"ok": True, "queued": True, "change": change, "snapshot": change["after"]}
    if not _manual_can_speak():
        return {"ok": True, "change": change,
                "line": {"text": "", "spoken": False, "reason": "rate_limited"},
                "snapshot": change["after"]}
    line = live_actor.reply_to(ev, room(), _persona_id())
    return {"ok": True, "change": change, "line": line, "snapshot": change["after"]}


def on_chat(user: str, text: str, *, say: bool = True) -> Dict[str, Any]:
    """收到弹幕：导演运行时入总线；否则同步回一句。"""
    ev = LiveEvent(kind=EV_DANMAKU, user_id="", user_name=str(user or "观众"),
                   text=str(text or "")[:200])
    d = live_director.current()
    r = room()
    if d is not None and d.running():
        d.submit(ev)
        return {"ok": True, "queued": True, "snapshot": r.snapshot()}
    if not _manual_can_speak():
        return {"ok": True, "skipped": True, "reason": "rate_limited", "snapshot": r.snapshot()}
    line = live_actor.reply_to(ev, r, _persona_id())
    return {"ok": True, "line": line, "snapshot": r.snapshot()}


# ── 导演/模拟器 ───────────────────────────────────────────

def start() -> bool:
    return live_director.start()


def stop() -> None:
    sim_stop()
    live_director.stop()


def running() -> bool:
    return live_director.running()


def tick() -> Dict[str, Any]:
    return live_director.tick_once()


def status() -> Dict[str, Any]:
    out = live_director.status()
    out["sim_running"] = bool(_sim is not None and _sim.running())
    out["line"] = stage.latest()
    return out


def _get_sim(speed: float = 1.0, loop: bool = True) -> Simulator:
    d = live_director.current()
    if d is None:
        raise RuntimeError("直播引擎未初始化")
    global _sim
    if _sim is None:
        _sim = Simulator(d, speed=speed, loop=loop)
    return _sim


def sim_start(speed: float = 1.0, loop: bool = True) -> Dict[str, Any]:
    d = live_director.current()
    if d is None:
        raise RuntimeError("直播引擎未初始化")
    d.start()
    _get_sim(speed, loop).start()
    return status()


def sim_stop() -> Dict[str, Any]:
    global _sim
    if _sim is not None:
        _sim.stop()
        _sim = None
    live_director.stop()
    return status()


def sim_step() -> Dict[str, Any]:
    """单步：发一条剧本事件并立刻跑一次决策（调试/测试用）。"""
    d = live_director.current()
    if d is None:
        raise RuntimeError("直播引擎未初始化")
    ev = _get_sim().step()
    action = d.tick_once()
    return {"event": ev.as_dict() if ev is not None else None, "tick": action,
            "status": status()}


__all__ = [
    "CONFIG_FILE",
    "init",
    "latest_line",
    "on_chat",
    "on_gift",
    "podium_data",
    "recent_events",
    "reload_settings",
    "room",
    "room_snapshot",
    "running",
    "sim_start",
    "sim_step",
    "sim_stop",
    "snapshot_dict",
    "start",
    "state",
    "status",
    "stop",
    "tick",
]
