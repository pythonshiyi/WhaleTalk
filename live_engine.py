"""直播服务层：把生存引擎、角色台词生成、TTS 串成一条可用的链路。

职责边界
--------
- `live_state.py`  ：纯数值（吃饱度/打赏/额度），不依赖任何外部服务
- `live_engine.py` ：本模块，负责「事件 → 台词 → 语音」，把 AI 接进来
- `api_server`     ：HTTP 接口（/v1/live/*）与静态 HUD 服务

设计原则
--------
1. **失败可降级**：LLM 挂了就用兜底台词，TTS 挂了就只出文字，绝不卡住直播。
2. **合规内建**：提示词层禁止索要打赏/诱导消费；台词出稿后再做一次敏感过滤。
3. **限速保守**：默认每分钟最多 3 句，既防刷屏也降低平台风控关注度。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import live_state

logger = logging.getLogger("whaletalk.live")

CONFIG_FILE: Optional[str] = None
_state: Optional[live_state.LiveState] = None
_lock = threading.RLock()

# 兜底台词：LLM 不可用时不至于哑火（也避免直播出现长时间静默）
_FALLBACK_FEED = {
    "starving": "谢谢你……我这就去吃点东西，真的快饿扁了。",
    "hungry": "谢谢！肚子刚好叫了一声，你就送来了。",
    "normal": "谢谢你的礼物，今天过得还不错。",
    "full": "谢谢你，我今天吃得很饱，心情特别好。",
}
_FALLBACK_CHAT = "嗯，我在听，你继续说。"

# 台词后置过滤：命中这些模式就丢弃重来（防止模型不听话去要礼物）
_FORBIDDEN = re.compile(
    r"(送礼物|刷礼物|打个赏|上票|充值|继续送|再多送|送礼|冲榜|帮我冲|还差.*元|差.*块钱)",
    re.IGNORECASE,
)
# 绝不出现的自我暴露
_SELF_EXPOSE = re.compile(r"(AI|人工智能|模型|程序|开发者|代码|提示词)", re.IGNORECASE)


def init(config_path: Optional[str] = None,
         state_path: Optional[str] = None, *,
         reset: bool = False) -> None:
    """接线：配置与状态文件路径。"""
    global CONFIG_FILE, _state
    with _lock:
        if reset or _state is None:
            CONFIG_FILE = str(config_path) if config_path else CONFIG_FILE
            live_state.STATE_FILE = str(state_path) if state_path else live_state.STATE_FILE
            _state = live_state.LiveState().load()


def state() -> live_state.LiveState:
    global _state
    with _lock:
        if _state is None:
            _state = live_state.LiveState().load()
        return _state


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


def snapshot_dict() -> Dict[str, Any]:
    return state().snapshot().as_dict()


# ══════════════════════════════════════════════════════════════════════════
# 台词生成
# ══════════════════════════════════════════════════════════════════════════

def _clean_line(text: str) -> str:
    """清洗模型输出：去引号/前缀/多余空白，截断到一句话。"""
    s = str(text or "").strip()
    s = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", s)
    s = re.sub(r"^(台词|回复|回答)[:：]\s*", "", s)
    s = s.split("\n")[0].strip()
    return s[:120]


def _is_safe_line(text: str) -> bool:
    """合规过滤：不含诱导消费、不自我暴露。"""
    if not text:
        return False
    if _FORBIDDEN.search(text):
        return False
    if _SELF_EXPOSE.search(text):
        return False
    return True


def _call_llm(prompt: str) -> str:
    """调用一次对话模型拿台词。失败返回空串（由调用方降级）。"""
    try:
        import config_utils
        import deepseek_client as dc
    except Exception:
        logger.exception("直播台词：依赖导入失败")
        return ""
    try:
        cfg = config_utils.load_config()
        key = str(cfg.get("api_key") or "").strip()
        if not key:
            return ""
        client = dc.DeepSeekClient(
            key,
            base_url=cfg.get("base_url") or dc.DEFAULT_BASE_URL,
            model=cfg.get("model") or dc.DEFAULT_MODEL,
            timeout=float(cfg.get("timeout") or 30),
        )
        parts: List[str] = []
        client.chat(
            [{"role": "user", "content": prompt}],
            scenario="通用",
            thinking="low",          # 直播要快，不要深思考
            max_tokens=200,
            tools_enabled=False,
            smart_tools=False,
            on_content=lambda t: parts.append(t),
        )
        return _clean_line("".join(parts))
    except Exception:
        logger.exception("直播台词生成失败")
        return ""


def _speak(text: str) -> Dict[str, Any]:
    """把台词合成语音（复用现有 TTS 三级引擎）。失败只返回空 URL。

    直接调用 api_server 的合成函数，不经过 HTTP，避免自己请求自己。
    """
    if not text:
        return {"audio": "", "engine": ""}
    try:
        import api_server as srv
        import hashlib
        import json as _json

        vc = srv._voice_cfg()
        rate = max(-10, min(10, int(vc.get("rate") or 0)))
        volume = max(0, min(100, int(vc.get("volume") or 100)))
        voice = str(vc.get("voice") or "")[:80]
        digest = hashlib.sha1(
            _json.dumps([text, rate, volume, voice, ""], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        cache = srv._VOICE_CACHE_DIR
        os.makedirs(cache, exist_ok=True)
        for ext in (".mp3", ".wav"):
            fp = os.path.join(cache, digest + ext)
            if os.path.isfile(fp) and os.path.getsize(fp) > 200:
                return {"audio": f"/v1/tts/audio/{digest}{ext}", "engine": "cache"}

        edge_ok = srv._edge_available()
        plan: List[Tuple[str, str]] = []
        if edge_ok:
            plan.append(("edge", f"{digest}.mp3"))
        plan.append(("sapi", f"{digest}.wav"))
        for eng, fn in plan:
            fp = os.path.join(cache, fn)
            with srv._TTS_SEM:
                if eng == "edge":
                    msg = srv._synthesize_edge(text, fp, rate, volume, voice)
                else:
                    msg = srv._synthesize_sapi(text, fp, rate, volume, voice)
            if not msg and os.path.isfile(fp) and os.path.getsize(fp) > 200:
                return {"audio": f"/v1/tts/audio/{fn}", "engine": eng}
        return {"audio": "", "engine": ""}
    except Exception:
        logger.exception("直播语音合成失败（降级为纯文字）")
        return {"audio": "", "engine": ""}


def _deliver(text: str, mood: str, *, say: bool) -> Dict[str, Any]:
    """统一的"说一句"出口：限速 + 安全过滤 + 语音。"""
    st = state()
    if not st.can_speak():
        return {"text": text, "spoken": False, "reason": "rate_limited"}
    if not _is_safe_line(text):
        logger.warning("直播台词被合规过滤：%s", text[:60])
        return {"text": text, "spoken": False, "reason": "filtered"}
    st.mark_spoken()
    audio = _speak(text) if say else {"audio": "", "engine": ""}
    audio_url = audio.get("audio", "")
    _publish_line(text, audio_url)
    return {"text": text, "spoken": True, "audio": audio_url,
            "engine": audio.get("engine", "")}


# ══════════════════════════════════════════════════════════════════════════
# 对外动作
# ══════════════════════════════════════════════════════════════════════════

def on_gift(giver: str, doucoin: float, gift_name: str = "礼物",
            count: int = 1, *, say: bool = True) -> Dict[str, Any]:
    """收到打赏：更新数值 → 生成台词 → 合成语音。"""
    st = state()
    change = st.feed(giver, doucoin, gift_name, count)
    snap = live_state.LiveSnapshot(**change["after"])

    persona = _persona_name()
    prompt = live_state.build_feed_prompt(snap, change["record"], persona)
    line = _call_llm(prompt)
    if not _is_safe_line(line):
        line = _FALLBACK_FEED.get(snap.mood, _FALLBACK_FEED["normal"])

    spoken = _deliver(line, snap.mood, say=say)
    return {
        "ok": True,
        "change": change,
        "line": spoken,
        "snapshot": change["after"],
    }


def on_chat(user: str, text: str, *, say: bool = True) -> Dict[str, Any]:
    """收到弹幕：生成一句回应（含单人冷却，避免一人刷屏）。"""
    st = state()
    uid = str(user or "")
    if not st.user_can_talk(uid):
        return {"ok": True, "skipped": True, "reason": "user_cooldown"}
    snapped = st.snapshot()
    persona = _persona_name()
    prompt = live_state.build_chat_prompt(snapped, uid or "观众", str(text or "")[:200], persona)
    line = _call_llm(prompt)
    if not _is_safe_line(line):
        line = _FALLBACK_CHAT

    st.mark_user_talked(uid)
    spoken = _deliver(line, snapped.mood, say=say)
    return {"ok": True, "line": spoken, "snapshot": st.snapshot().as_dict()}


def _persona_name() -> str:
    """取当前默认角色名，让台词口吻对上人设。角色库不可用时回退默认名。"""
    try:
        import personas
        items = personas.list_personas()
        if items:
            return str(items[0].get("name") or "大肥鱼")
    except Exception:
        logger.debug("读取角色名失败，使用默认名", exc_info=True)
    return "大肥鱼"


# ── 最新台词：HUD 拉取后显示气泡并播放语音 ──
_latest: Dict[str, Any] = {"seq": 0, "text": "", "audio": "", "at": 0.0}


def _publish_line(text: str, audio: str) -> None:
    """把刚说的台词发布出去，供 HUD 拉取。"""
    with _lock:
        _latest["seq"] = int(_latest.get("seq") or 0) + 1
        _latest["text"] = str(text or "")
        _latest["audio"] = str(audio or "")
        _latest["at"] = time.time()


def latest_line() -> Dict[str, Any]:
    """HUD 轮询：返回最近一句台词（带自增序号，前端据此判断是否新台词）。"""
    with _lock:
        return dict(_latest)


def podium_data() -> Dict[str, Any]:
    """HUD 专用的只读数据聚合：一次请求拿到全部画面所需信息。

    ⚠️ 这个接口**不需要 token**（OBS 浏览器源无法带 Authorization 头），
    因此只暴露画面用得到的字段，不含任何凭据、联系人身份或配置项。
    """
    st = state()
    snap = st.snapshot().as_dict()
    with _lock:
        line = dict(_latest)
    return {
        "snapshot": snap,
        "events": list(snap.get("recent") or [])[:12],
        "line": line,
    }


def recent_events(limit: int = 20) -> List[Dict[str, Any]]:
    return list(state().snapshot().recent)[:max(1, min(50, limit))]


__all__ = [
    "CONFIG_FILE",
    "init",
    "on_chat",
    "on_gift",
    "recent_events",
    "reload_settings",
    "snapshot_dict",
    "state",
]
