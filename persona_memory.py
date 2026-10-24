"""角色级记忆：每个角色卡一份独立的长期记忆。

与全局 `memory.json` / 大脑记忆**物理隔离**：
- 角色对话自动沉淀到 `DATA_DIR/persona_memory/<persona_id>.json`；
- 角色记忆只注入当前角色的对话，不跨角色、不写回全局记忆；
- 用户可在「角色」页查看与清空，避免角色设定/剧情污染通用记忆。

本模块只做存储与检索，不依赖 api_server；提示词拼装由 context_providers 负责。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from persistence import atomic_json_write

logger = logging.getLogger("whaletalk.persona_memory")

MEMORY_DIR: Optional[str] = None
MAX_ITEMS = 2000
MAX_TEXT = 500
MAX_FILE_BYTES = 2 * 1024 * 1024

_lock = threading.RLock()
_ID_RE = re.compile(r"^p_[a-z0-9_]{4,32}$")


def init(path: Optional[str] = None, *, reset: bool = False) -> None:
    """注册角色记忆目录；reset=True 且不传 path 时清空注册。"""
    global MEMORY_DIR
    with _lock:
        if reset:
            MEMORY_DIR = str(path) if path else None
        elif path:
            MEMORY_DIR = str(path)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _path(pid: str) -> Optional[str]:
    pid = str(pid or "").strip()
    if not MEMORY_DIR or not _ID_RE.match(pid):
        return None
    return os.path.join(MEMORY_DIR, f"{pid}.json")


def _confidence(origin: str) -> float:
    try:
        from memory_facade import ORIGIN_CONFIDENCE
        return float(ORIGIN_CONFIDENCE.get(origin, 0.5))
    except Exception:
        return 0.5


def _load(pid: str) -> List[Dict[str, Any]]:
    path = _path(pid)
    if not path or not os.path.exists(path):
        return []
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            logger.warning("角色记忆文件过大，已跳过读取：%s", path)
            return []
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        facts = data.get("facts") if isinstance(data, dict) else data
        if not isinstance(facts, list):
            return []
        out = []
        for f in facts:
            if not isinstance(f, dict):
                continue
            text = str(f.get("text") or f.get("value") or "").strip()
            if not text:
                continue
            out.append({
                "id": str(f.get("id") or ""),
                "text": text[:MAX_TEXT],
                "ts": str(f.get("ts") or ""),
                "origin": str(f.get("origin") or "agent"),
                "confidence": f.get("confidence", _confidence(str(f.get("origin") or "agent"))),
                "tags": str(f.get("tags") or ""),
                "type": str(f.get("type") or ""),
            })
        return out
    except Exception:
        logger.exception("读取角色记忆失败：%s", path)
        return []


def _write(pid: str, facts: List[Dict[str, Any]]) -> bool:
    path = _path(pid)
    if not path:
        return False
    if len(facts) > MAX_ITEMS:
        facts = facts[-MAX_ITEMS:]
    try:
        data = {"persona_id": pid, "updated_at": _now(), "facts": facts}
        return bool(atomic_json_write(path, data, indent=1))
    except Exception:
        logger.exception("写入角色记忆失败：%s", path)
        return False


def facts(pid: str) -> List[Dict[str, Any]]:
    """读取一个角色的全部有效记忆（最新在后）。"""
    with _lock:
        return _load(pid)


def count(pid: str) -> int:
    return len(facts(pid))


def add(pid: str, text: str, *, origin: str = "agent", confidence: Optional[float] = None,
        tags: str = "自动", type_: str = "对话") -> bool:
    """追加一条角色记忆；完全相同文本不重复写入。"""
    text = str(text or "").strip()[:MAX_TEXT]
    if not text or not _path(pid):
        return False
    origin = str(origin or "agent").strip().lower() or "agent"
    try:
        conf = float(confidence) if confidence is not None else _confidence(origin)
    except (TypeError, ValueError):
        conf = _confidence(origin)
    conf = max(0.0, min(1.0, conf))
    with _lock:
        items = _load(pid)
        if any(str(f.get("text") or "") == text for f in items):
            return True
        items.append({
            "id": "pm_" + os.urandom(6).hex(),
            "text": text,
            "ts": _now(),
            "origin": origin if origin in ("user", "agent", "web", "system", "unknown") else "unknown",
            "confidence": conf,
            "tags": str(tags or "")[:80],
            "type": str(type_ or "")[:40],
        })
        return _write(pid, items)


def delete(pid: str, fact_id: str) -> bool:
    fact_id = str(fact_id or "").strip()
    if not fact_id:
        return False
    with _lock:
        items = _load(pid)
        kept = [f for f in items if str(f.get("id") or "") != fact_id]
        if len(kept) == len(items):
            return False
        return _write(pid, kept)


def clear(pid: str) -> bool:
    with _lock:
        return _write(pid, [])


def rank(pid: str, query: str = "", limit: int = 6) -> List[Dict[str, Any]]:
    """按当前用户消息检索角色记忆；query 为空时返回最近 limit 条。"""
    items = facts(pid)
    if not items:
        return []
    try:
        lim = max(1, min(20, int(limit or 6)))
    except (TypeError, ValueError):
        lim = 6
    try:
        from memory_facade import rank as _rank
        picked = _rank(str(query or ""), items, limit=lim, min_score=0.0)
    except Exception:
        logger.exception("角色记忆召回失败，回退最近条目")
        picked = items[-lim:]
    if not picked:
        return []
    out = []
    for f in picked:
        text = str(f.get("value") or f.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "text": text,
            "origin": str(f.get("origin") or "agent"),
            "confidence": f.get("confidence", 0.5),
            "ts": str(f.get("ts") or ""),
        })
    return out


__all__ = [
    "MEMORY_DIR",
    "add",
    "clear",
    "count",
    "delete",
    "facts",
    "init",
    "rank",
]
