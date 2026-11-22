"""语音：把台词合成音频（复用 api_server 的 TTS 三级引擎）。

合成放后台线程：台词先上屏，音频随后回填（`stage.attach_audio`），
不能因为 TTS 慢就把直播接口或导演循环卡住。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any, Dict

from . import stage

logger = logging.getLogger("whaletalk.live.voice")

# CLI/测试可关掉语音（不依赖 api_server 的 TTS 环境）
ENABLED = True

# 免 token 的语音路径：<audio> 标签/OBS 带不了 Authorization 头，
# 走 /v1/tts/audio 会 401（表现为"有字幕没声音"）。
AUDIO_URL_PREFIX = "/live/audio"


def audio_url(filename: str) -> str:
    return f"{AUDIO_URL_PREFIX}/{filename}"


def speak(text: str) -> Dict[str, Any]:
    """合成一句语音；失败返回空 URL（调用方降级为纯文字）。"""
    if not text:
        return {"audio": "", "engine": ""}
    try:
        import api_server as srv

        vc = srv._voice_cfg()
        rate = max(-10, min(10, int(vc.get("rate") or 0)))
        volume = max(0, min(100, int(vc.get("volume") or 100)))
        voice = str(vc.get("voice") or "")[:80]
        digest = hashlib.sha1(
            json.dumps([text, rate, volume, voice, ""], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        cache = srv._VOICE_CACHE_DIR
        os.makedirs(cache, exist_ok=True)
        for ext in (".mp3", ".wav"):
            fp = os.path.join(cache, digest + ext)
            if os.path.isfile(fp) and os.path.getsize(fp) > 200:
                return {"audio": audio_url(digest + ext), "engine": "cache"}

        plan = []
        if srv._edge_available():
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
                return {"audio": audio_url(fn), "engine": eng}
        return {"audio": "", "engine": ""}
    except Exception:
        logger.exception("直播语音合成失败（降级为纯文字）")
        return {"audio": "", "engine": ""}


def speak_async(text: str, seq: int) -> threading.Thread:
    """后台合成并回填到指定台词序号（串台由 attach_audio 负责拦截）。"""
    def _run() -> None:
        if not ENABLED:
            return
        audio = speak(text)
        url = str(audio.get("audio") or "")
        if url:
            stage.attach_audio(seq, url)

    th = threading.Thread(target=_run, daemon=True, name="live-tts")
    th.start()
    return th


__all__ = ["AUDIO_URL_PREFIX", "ENABLED", "audio_url", "speak", "speak_async"]
