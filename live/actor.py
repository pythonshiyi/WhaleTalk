"""演员：把「此刻该说什么」变成一句能直接念出来的台词。

分工：导演决定「说/不说、对谁说、要不要起话题」，演员只负责表达。
表达依赖角色卡全量 + 房间状态 + 最近台词，而不是单条事件的孤立问答。
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from . import safety, stage, voice
from .prompts import reply_user_message, self_talk_user_message, system_prompt

logger = logging.getLogger("whaletalk.live.actor")

# 兜底台词：模型不可用时不至于哑火；刻意保持"不像客服"。
_FALLBACK_REPLY = (
    "嗯，我在听。",
    "哈哈，这个我记下了。",
    "你这么说我倒想起点别的。",
    "刚走神了一下，你刚说啥？",
    "这个话题有点意思，再讲两句？",
)
_FALLBACK_SELF = (
    "……突然安静下来了。",
    "说起来，今天过得还挺快的。",
    "你们有没有那种，明明很困但就是不想睡的时候？",
    "我先喝口水，你们随意。",
)

_persona_cache: Dict[str, Any] = {"id": None, "at": 0.0, "persona": None}


# ── 配置 / 角色 ─────────────────────────────────────────────

def _cfg() -> Dict[str, Any]:
    try:
        import config_utils
        return config_utils.load_config()
    except Exception:
        return {}


def load_persona(persona_id: str = "") -> Optional[Dict[str, Any]]:
    """取当前直播角色卡（缓存 30s，避免每句都读文件）。"""
    pid = str(persona_id or "").strip()
    now = time.time()
    if _persona_cache.get("persona") is not None and now - float(_persona_cache.get("at") or 0) < 30 \
            and str(_persona_cache.get("id") or "") == pid:
        return _persona_cache["persona"]
    persona = None
    try:
        import personas
        if pid:
            persona = personas.get_persona(pid)
        if persona is None:
            items = personas.list_personas()
            persona = items[0] if items else None
    except Exception:
        logger.debug("读取角色卡失败，使用默认人格", exc_info=True)
    _persona_cache.update({"id": pid, "at": now, "persona": persona})
    return persona


def persona_name(persona: Optional[Dict[str, Any]]) -> str:
    name = str((persona or {}).get("name") or "").strip()
    return name or "大肥鱼"


# ── 生成 ────────────────────────────────────────────────────

def generate(messages: List[Dict[str, Any]], max_tokens: int = 160) -> Tuple[str, str]:
    """调一次对话模型，返回 (台词, 错误)。失败返回空串，由调用方兜底。"""
    try:
        import deepseek_client as dc
    except Exception as e:  # noqa: BLE001
        return "", f"依赖不可用：{e}"
    cfg = _cfg()
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        return "", "未配置 API Key"
    try:
        client = dc.DeepSeekClient(
            key,
            base_url=cfg.get("base_url") or dc.DEFAULT_BASE_URL,
            model=cfg.get("model") or dc.DEFAULT_MODEL,
            timeout=min(12.0, max(4.0, float(cfg.get("timeout") or 30))),
        )
        parts: List[str] = []
        client.chat(
            messages,
            scenario="通用",
            thinking="none",
            max_tokens=max_tokens,
            temperature=0.92,
            tools_enabled=False,
            smart_tools=False,
            on_content=lambda t: parts.append(t),
        )
        return "".join(parts).strip(), ""
    except Exception as e:  # noqa: BLE001
        logger.warning("直播台词生成失败（走兜底）：%s", e)
        return "", f"{type(e).__name__}: {e}"


def _messages(system: str, user: str) -> List[Dict[str, Any]]:
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _fallback(pool, salt: int) -> str:
    return pool[salt % len(pool)]


def _finalize(raw: str, room, pool, salt: int) -> Dict[str, Any]:
    """清洗 + 安全 + 复读检查，返回统一结果。"""
    text = safety.clean_line(raw)
    if not text or not safety.is_safe_line(text):
        text = _fallback(pool, salt)
        source = "fallback"
    else:
        source = "model"
    last = room.recent_lines(1)
    if last and last[-1].get("text") == text:
        text = _fallback(pool, salt + 1)
        source = "fallback"
    return {"text": text, "source": source}


# ── 对外动作 ────────────────────────────────────────────────

def say(text: str, kind: str = "reply", target: str = "", *, speak: bool = True) -> Dict[str, Any]:
    """发布一句台词：上屏 + 记入房间 + 后台合成语音。"""
    seq = stage.publish(text, "")
    room_line = {"text": text, "seq": seq, "spoken": True, "audio": "", "audio_pending": bool(speak)}
    if speak:
        voice.speak_async(text, seq)
    return room_line


def reply_to(ev, room, persona_id: str = "") -> Dict[str, Any]:
    """回应一条事件，返回 {text, source, seq, spoken, audio_pending}。"""
    persona = load_persona(persona_id)
    name = persona_name(persona)
    system = system_prompt(persona, room, name)
    user = reply_user_message(ev, room)
    raw, err = generate(_messages(system, user))
    picked = _finalize(raw, room, _FALLBACK_REPLY, int(time.time()) + len(ev.text or ""))
    out = say(picked["text"], kind="reply", target=str(ev.user_name or ev.user_id or ""))
    room.add_line(picked["text"], kind="reply", target=str(ev.user_name or ev.user_id or ""))
    out.update({"source": picked["source"], "error": err})
    return out


def self_talk(room, persona_id: str = "") -> Dict[str, Any]:
    """冷场时自己起个话头（话题由房间议程给出，但不强制照读）。"""
    persona = load_persona(persona_id)
    name = persona_name(persona)
    system = system_prompt(persona, room, name)
    user = self_talk_user_message(room)
    raw, err = generate(_messages(system, user), max_tokens=180)
    picked = _finalize(raw, room, _FALLBACK_SELF, int(time.time()))
    out = say(picked["text"], kind="self_talk")
    room.add_line(picked["text"], kind="self_talk")
    out.update({"source": picked["source"], "error": err})
    return out


__all__ = ["generate", "load_persona", "persona_name", "reply_to", "say", "self_talk"]
