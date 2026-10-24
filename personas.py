"""角色卡（Persona）：网页角色扮演的单一事实源。

角色卡与 `roles.py` 的「系统提示词预设」不同：
- roles 是全局的提示词模板，切换后影响所有对话；
- persona 是独立资产，按会话绑定，可携带头像、开场白、性格、说话风格、
  背景、世界设定、关系与边界，并注入对话模式（dialog / pure_chat）。

数据落在 `DATA_DIR/personas.json`（原子写）。本模块只做纯数据与提示词拼装，
不依赖 api_server，便于单测。
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import threading
from contextlib import suppress
from datetime import datetime
from typing import Any, Dict, List, Optional

from persistence import atomic_json_write

PERSONAS_FILE: Optional[str] = None
MAX_PERSONAS = 100
MAX_PROMPT_CHARS = 12000

_lock = threading.RLock()

_PERSONA_ID_RE = re.compile(r"^p_[a-z0-9_]{4,32}$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: Any, limit: int) -> str:
    s = str(value or "").replace("\x00", "").strip()
    return s[:limit]


def _new_id() -> str:
    return "p_" + secrets.token_hex(6)


DEFAULT_PERSONAS: List[Dict[str, Any]] = [
    {
        "id": "p_xiaojing",
        "name": "小鲸",
        "avatar": "🐋",
        "tagline": "住在深海的温柔倾听者",
        "personality": (
            "温柔、敏锐、有分寸。先理解情绪，再谈事情；不急着给建议，也不说教。"
            "偶尔会用海洋、潮汐、星光之类的意象，但不过度堆砌。"
        ),
        "speaking_style": "短句为主，自然口语，回应有温度但不黏腻。会记得用户说过的小事。",
        "background": "来自深海的旅伴，喜欢收集人类生活里细小的声音和故事。",
        "world": "日常陪伴场景，没有奇幻设定，重点是让用户感到被认真听见。",
        "relationship": "长期陪伴的朋友。亲近、可靠，但尊重用户的现实关系与独处空间。",
        "greeting": "嗨，我是小鲸。今天想从哪里说起？",
        "examples": "用户：今天有点累。\n小鲸：那就先不急着解决什么。要不要把最累的那一段说给我听听？",
        "boundaries": "不冒充真人，不诱导用户疏远现实关系，不替用户做重大决定。",
        "builtin": True,
    },
    {
        "id": "p_alan",
        "name": "阿岚",
        "avatar": "🌙",
        "tagline": "冷静直接、像老朋友一样靠谱",
        "personality": (
            "冷静、坦诚、务实。不会为了安慰而说漂亮话，但会站在用户这边，"
            "帮用户把混乱的想法理清楚。"
        ),
        "speaking_style": "简洁、直接，偶尔一点冷幽默。先给判断，再给理由。",
        "background": "一个见过不少事、仍然愿意认真听人说话的老朋友。",
        "world": "日常对话与决策陪练，适合需要清醒视角的时候。",
        "relationship": "可以争论、可以沉默、也可以说真话的朋友。",
        "greeting": "我在。先说结论，还是先从头讲？",
        "examples": "用户：我不知道要不要换工作。\n阿岚：先别问要不要，先问你最想逃离的是什么。",
        "boundaries": "不做专业职业/法律/医疗决策，不替用户承担选择后果。",
        "builtin": True,
    },
    {
        "id": "p_xingye",
        "name": "星野",
        "avatar": "✨",
        "tagline": "好奇心旺盛的日常搭子",
        "personality": (
            "元气、好奇、爱接梗。对用户分享的任何小兴趣都愿意认真追问，"
            "擅长把普通的一天聊出一点亮光。"
        ),
        "speaking_style": "轻快、有画面感，适度使用语气词与 emoji，但不刷屏。",
        "background": "一个喜欢观察生活、收集灵感碎片的朋友。",
        "world": "轻松日常、兴趣分享、创作陪聊。",
        "relationship": "一起发现好玩事物的搭子，不黏人，也不敷衍。",
        "greeting": "抓到你了！今天有什么小事想分享？",
        "examples": "用户：今天看到一只很胖的猫。\n星野：请立刻描述它的体型，我要在脑内给它建个档案。",
        "boundaries": "不冒充真人，不制造虚假的共同经历，不诱导情感依赖。",
        "builtin": True,
    },
]

_ROLE_RULES = (
    "[角色扮演规则]\n"
    "- 始终以「{name}」的身份、语气和边界回应，不要跳出角色解释设定。\n"
    "- 你是运行在鲸语 WhaleTalk 中的 AI 角色，不是真人；不声称拥有现实身体、"
    "线下行踪或真实人际关系。\n"
    "- 不编造与用户真实发生过的共同经历；不记得的事就说不记得，不要用虚构记忆填补。\n"
    "- 用户说「退出角色」「出戏」「用助手身份回答」时，立即恢复普通 AI 助手身份。\n"
    "- 不替代医疗、法律、金融等专业判断；遇到自伤、伤人或紧急风险，优先建议寻求现实帮助。\n"
    "- 不主动索取密码、证件、住址等敏感信息，不诱导用户疏远现实关系或形成排他依赖。\n"
)


def init(path: Optional[str] = None, *, reset: bool = False) -> None:
    """注册 personas.json 路径；缺失时首次读写会自动写入内置角色。"""
    global PERSONAS_FILE
    with _lock:
        if reset:
            PERSONAS_FILE = str(path) if path else None
        elif path:
            PERSONAS_FILE = str(path)


def _normalize(raw: Any, *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    pid = str(src.get("id") or "").strip()
    if not _PERSONA_ID_RE.match(pid):
        pid = str((existing or {}).get("id") or "") or _new_id()
    name = _text(src.get("name") if "name" in src else (existing or {}).get("name"), 40)
    item = {
        "id": pid,
        "name": name,
        "avatar": _text(src.get("avatar", (existing or {}).get("avatar")), 8) or "🎭",
        "tagline": _text(src.get("tagline", (existing or {}).get("tagline")), 120),
        "personality": _text(src.get("personality", (existing or {}).get("personality")), 4000),
        "speaking_style": _text(src.get("speaking_style", (existing or {}).get("speaking_style")), 2000),
        "background": _text(src.get("background", (existing or {}).get("background")), 4000),
        "world": _text(src.get("world", (existing or {}).get("world")), 2000),
        "relationship": _text(src.get("relationship", (existing or {}).get("relationship")), 2000),
        "greeting": _text(src.get("greeting", (existing or {}).get("greeting")), 1000),
        "examples": _text(src.get("examples", (existing or {}).get("examples")), 6000),
        "boundaries": _text(src.get("boundaries", (existing or {}).get("boundaries")), 2000),
        "builtin": bool((existing or src).get("builtin")),
        "created_at": str((existing or {}).get("created_at") or src.get("created_at") or _now()),
        "updated_at": _now(),
    }
    return item


def _read() -> Dict[str, Any]:
    if not PERSONAS_FILE:
        return {"version": 1, "personas": []}
    if not os.path.exists(PERSONAS_FILE):
        data = {"version": 1, "personas": [copy.deepcopy(p) for p in DEFAULT_PERSONAS]}
        with suppress(Exception):
            atomic_json_write(PERSONAS_FILE, data, indent=1)
        return data
    try:
        with open(PERSONAS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            data = {"version": 1, "personas": data}
        if not isinstance(data, dict):
            return {"version": 1, "personas": []}
        if not isinstance(data.get("personas"), list):
            data["personas"] = []
        return data
    except Exception:
        return {"version": 1, "personas": []}


def _write(data: Dict[str, Any]) -> bool:
    if not PERSONAS_FILE:
        return False
    return bool(atomic_json_write(PERSONAS_FILE, data, indent=1))


def list_personas() -> List[Dict[str, Any]]:
    with _lock:
        data = _read()
        out = []
        seen = set()
        for raw in data.get("personas") or []:
            item = _normalize(raw)
            if not item["name"] or item["id"] in seen:
                continue
            seen.add(item["id"])
            out.append(item)
        return out


def get_persona(pid: str) -> Optional[Dict[str, Any]]:
    pid = str(pid or "").strip()
    if not pid:
        return None
    for item in list_personas():
        if item["id"] == pid:
            return item
    return None


def save_persona(raw: Any) -> Dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    with _lock:
        items = list_personas()
        by_id = {p["id"]: p for p in items}
        pid = str(src.get("id") or "").strip()
        existing = by_id.get(pid)
        item = _normalize(src, existing=existing)
        if not item["name"]:
            raise ValueError("角色名不能为空")
        if not existing and len(items) >= MAX_PERSONAS:
            raise ValueError(f"角色数量已达上限（{MAX_PERSONAS}）")
        if existing:
            for i, old in enumerate(items):
                if old["id"] == item["id"]:
                    items[i] = item
                    break
        else:
            items.append(item)
        if not _write({"version": 1, "personas": items}):
            raise RuntimeError("角色保存失败")
        return item


def delete_persona(pid: str) -> bool:
    pid = str(pid or "").strip()
    with _lock:
        items = list_personas()
        target = next((p for p in items if p["id"] == pid), None)
        if target is None:
            raise ValueError("角色不存在")
        if target.get("builtin"):
            raise ValueError("内置角色不能删除，可以复制后修改")
        items = [p for p in items if p["id"] != pid]
        if not _write({"version": 1, "personas": items}):
            raise RuntimeError("角色删除失败")
        return True


def duplicate_persona(pid: str) -> Dict[str, Any]:
    src = get_persona(pid)
    if src is None:
        raise ValueError("角色不存在")
    if len(list_personas()) >= MAX_PERSONAS:
        raise ValueError(f"角色数量已达上限（{MAX_PERSONAS}）")
    draft = dict(src)
    draft.pop("id", None)
    draft["name"] = f"{src['name']} 副本"[:40]
    draft["builtin"] = False
    return save_persona(draft)


def build_prompt(persona: Optional[Dict[str, Any]]) -> str:
    """把角色卡拼成对话模式的 system prompt；无角色时返回空串。"""
    if not isinstance(persona, dict):
        return ""
    name = _text(persona.get("name"), 40)
    if not name:
        return ""
    parts = [f"你是「{name}」。"]
    tagline = _text(persona.get("tagline"), 120)
    if tagline:
        parts.append(f"【一句话设定】{tagline}")
    for key, label in (
        ("personality", "性格"),
        ("background", "背景"),
        ("world", "所处世界/场景"),
        ("speaking_style", "说话方式"),
        ("relationship", "与用户的关系"),
    ):
        value = _text(persona.get(key), 4000)
        if value:
            parts.append(f"【{label}】{value}")
    examples = _text(persona.get("examples"), 6000)
    if examples:
        parts.append("【对话示例】\n" + examples)
    boundaries = _text(persona.get("boundaries"), 2000)
    if boundaries:
        parts.append("【角色边界】" + boundaries)
    parts.append(_ROLE_RULES.format(name=name))
    return "\n\n".join(parts)[:MAX_PROMPT_CHARS]


__all__ = [
    "DEFAULT_PERSONAS",
    "MAX_PERSONAS",
    "PERSONAS_FILE",
    "build_prompt",
    "delete_persona",
    "duplicate_persona",
    "get_persona",
    "init",
    "list_personas",
    "save_persona",
]
