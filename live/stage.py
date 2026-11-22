"""舞台发布：当前台词 / 音频回填 / HUD 快照。

HUD（OBS 浏览器源）只读这里的数据：不需要 token，也不含凭据。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict

_lock = threading.RLock()
# 公开 dict：live_engine 兼容历史 `_latest`（共享同一对象）
LATEST: Dict[str, Any] = {"seq": 0, "text": "", "audio": "", "audio_seq": 0, "at": 0.0}


def publish(text: str, audio: str = "") -> int:
    """发布一句新台词，返回序号（HUD 据此判断是否是新台词）。"""
    with _lock:
        seq = int(LATEST.get("seq") or 0) + 1
        LATEST.update({"seq": seq, "text": str(text or ""),
                       "audio": str(audio or ""), "at": time.time()})
        return seq


def attach_audio(seq: int, audio: str) -> bool:
    """回填某一句的语音；只有当前台词仍是它时才生效（避免串台）。"""
    url = str(audio or "")
    if not url:
        return False
    with _lock:
        if int(LATEST.get("seq") or 0) != int(seq):
            return False
        LATEST["audio"] = url
        LATEST["audio_seq"] = int(seq)
        return True


def latest() -> Dict[str, Any]:
    with _lock:
        return dict(LATEST)


def reset() -> None:
    with _lock:
        LATEST.update({"seq": 0, "text": "", "audio": "", "audio_seq": 0, "at": 0.0})


__all__ = ["LATEST", "attach_audio", "latest", "publish", "reset"]
