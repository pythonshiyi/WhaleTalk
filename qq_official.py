"""QQ 官方机器人（QQ Bot API v2）本地陪伴接入。

与 `qq_onebot.py` 的区别：
- 走腾讯官方 QQ Bot API：AppID + AppSecret → Access Token → WebSocket Gateway；
- 私聊事件是 `C2C_MESSAGE_CREATE`，用户标识是 `user_openid`（不是 QQ 号）；
- 发送用 `POST /v2/users/{openid}/messages`，回复必须带事件 `msg_id`（被动消息）；
- 不依赖 NapCat / OpenClaw / Hermes，鲸语自己完成连接、会话、角色与记忆。

官方限制（2026 文档）：
- 单聊被动消息有效期 60 分钟，同一条消息最多回复 4 次；
- 未认证机器人主动消息 5 QPS / 30 QPM，单关系 20 QPM、每天 1000 条；
- 需要机器人拥有 C2C / GROUP_AND_C2C_EVENT intent（1<<25）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import suppress
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import httpx

import crypto
from persistence import atomic_json_write

logger = logging.getLogger("whaletalk.qq_official")

CONFIG_FILE: Optional[str] = None
STATE_FILE: Optional[str] = None
DEFAULT_API_BASE = "https://api.bot.qq.com"
DEFAULT_INTENTS = 1 << 25  # GROUP_AND_C2C_EVENT
MAX_PASSIVE_REPLIES = 4

_lock = threading.RLock()
_state_lock = threading.RLock()
_user_locks: Dict[str, asyncio.Lock] = {}
_user_locks_guard = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_active_cfg: Optional[Dict[str, Any]] = None
_connected = False
_last_error = ""
_bot_info = ""
_seen: "OrderedDict[str, bool]" = OrderedDict()
_seen_lock = threading.Lock()
_rate: Dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()
_seq: Dict[str, int] = {}
_seq_lock = threading.Lock()
_token_cache: Dict[str, Any] = {"token": "", "exp": 0.0}
_token_lock = threading.Lock()

_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "app_id": "",
    "client_secret": "",
    "api_base": DEFAULT_API_BASE,
    "intents": DEFAULT_INTENTS,
    "default_persona_id": "p_xiaojing",
    "allowed_users": [],
    "max_reply_chars": 1200,
    "rate_limit_per_minute": 10,
    "quiet_hours": "",
}

_QUIET_RE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def init(config_path: Optional[str] = None, state_path: Optional[str] = None, *,
         reset: bool = False) -> None:
    global CONFIG_FILE, STATE_FILE, _active_cfg
    with _lock:
        if reset:
            CONFIG_FILE = str(config_path) if config_path else None
            STATE_FILE = str(state_path) if state_path else None
            _active_cfg = None
        else:
            if config_path:
                CONFIG_FILE = str(config_path)
            if state_path:
                STATE_FILE = str(state_path)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize(cfg: Any) -> Dict[str, Any]:
    src = cfg if isinstance(cfg, dict) else {}
    out = dict(_DEFAULTS)
    out.update({k: src[k] for k in _DEFAULTS if k in src})
    out["enabled"] = bool(out.get("enabled"))
    out["app_id"] = str(out.get("app_id") or "").strip()[:64]
    out["client_secret"] = str(out.get("client_secret") or "").strip()[:200]
    base = str(out.get("api_base") or DEFAULT_API_BASE).strip().rstrip("/")
    if base and not base.startswith(("http://", "https://")):
        base = "https://" + base
    out["api_base"] = base[:300]
    try:
        out["intents"] = max(0, int(out.get("intents") or DEFAULT_INTENTS))
    except (TypeError, ValueError):
        out["intents"] = DEFAULT_INTENTS
    out["default_persona_id"] = str(out.get("default_persona_id") or "")[:64]
    users = out.get("allowed_users")
    if isinstance(users, str):
        users = re.split(r"[\s,，]+", users)
    out["allowed_users"] = [str(x).strip() for x in (users or []) if str(x).strip()][:200]
    try:
        out["max_reply_chars"] = max(100, min(4000, int(out.get("max_reply_chars") or 1200)))
    except (TypeError, ValueError):
        out["max_reply_chars"] = 1200
    try:
        out["rate_limit_per_minute"] = max(1, min(120, int(out.get("rate_limit_per_minute") or 10)))
    except (TypeError, ValueError):
        out["rate_limit_per_minute"] = 10
    quiet = str(out.get("quiet_hours") or "").strip()
    out["quiet_hours"] = quiet if _QUIET_RE.match(quiet) else ""
    return out


def load_config() -> Dict[str, Any]:
    with _lock:
        if not CONFIG_FILE or not os.path.exists(CONFIG_FILE):
            return _normalize({})
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            raw = raw if isinstance(raw, dict) else {}
            raw["client_secret"] = crypto.decrypt(str(raw.get("client_secret") or ""))
            return _normalize(raw)
        except Exception:
            logger.exception("读取 QQ 官方 Bot 配置失败，使用默认关闭配置")
            return _normalize({})


def get_active_config() -> Dict[str, Any]:
    with _lock:
        if _active_cfg is not None:
            return dict(_active_cfg)
    return load_config()


def public_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = dict(cfg or load_config())
    secret = str(c.pop("client_secret", "") or "")
    c["has_secret"] = bool(secret)
    c["running"] = bool(_thread and _thread.is_alive())
    c["connected"] = bool(_connected)
    c["bot_info"] = _bot_info
    c["last_error"] = _last_error
    return c


def save_config(patch: Any) -> Dict[str, Any]:
    global _active_cfg
    patch = patch if isinstance(patch, dict) else {}
    with _lock:
        old = load_config()
        raw = dict(old)
        for key in _DEFAULTS:
            if key in patch:
                raw[key] = patch[key]
        if patch.get("clear_secret"):
            raw["client_secret"] = ""
        elif not str(patch.get("client_secret") or "").strip():
            raw["client_secret"] = old.get("client_secret") or ""
        else:
            raw["client_secret"] = str(patch.get("client_secret")).strip()
        normalized = _normalize(raw)
        to_store = dict(normalized)
        secret = str(to_store.get("client_secret") or "")
        if secret:
            try:
                to_store["client_secret"] = crypto.encrypt(secret)
            except Exception as e:
                raise ValueError(f"AppSecret 加密失败：{e}") from None
        if not CONFIG_FILE:
            raise ValueError("QQ 官方 Bot 配置路径未初始化")
        if not atomic_json_write(CONFIG_FILE, to_store, indent=1):
            raise RuntimeError("QQ 官方 Bot 配置保存失败")
        _active_cfg = normalized
        _token_cache["token"] = ""
        _token_cache["exp"] = 0.0
    return public_config(normalized)


# ── 官方 API ─────────────────────────────────────────────────────────────

def _api_base(cfg: Dict[str, Any]) -> str:
    return str(cfg.get("api_base") or DEFAULT_API_BASE).strip().rstrip("/")


def _get_token(cfg: Dict[str, Any], force: bool = False) -> str:
    with _token_lock:
        token = str(_token_cache.get("token") or "")
        exp = float(_token_cache.get("exp") or 0)
        if token and not force and time.time() < exp - 60:
            return token
        base = _api_base(cfg)
        try:
            r = httpx.post(
                f"{base}/app/getAppAccessToken",
                json={"appId": cfg.get("app_id") or "", "clientSecret": cfg.get("client_secret") or ""},
                timeout=15.0,
            )
            data = r.json()
        except Exception as e:
            raise RuntimeError(f"获取 QQ 官方 Access Token 失败：{e}") from e
        if not isinstance(data, dict):
            raise RuntimeError("QQ 官方 Access Token 返回格式异常")
        if data.get("code"):
            raise RuntimeError(f"QQ 官方鉴权失败：{data.get('message') or data.get('code')}")
        token = str(data.get("access_token") or "")
        if not token:
            raise RuntimeError(f"QQ 官方 Access Token 缺失：{data}")
        try:
            expires = int(data.get("expires_in") or 7200)
        except (TypeError, ValueError):
            expires = 7200
        _token_cache["token"] = token
        _token_cache["exp"] = time.time() + expires
        return token


def _get_gateway(cfg: Dict[str, Any], token: str) -> str:
    base = _api_base(cfg)
    try:
        r = httpx.get(f"{base}/gateway", headers={
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }, timeout=15.0)
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"获取 QQ 官方 WebSocket 网关失败：{e}") from e
    if not isinstance(data, dict) or not data.get("url"):
        raise RuntimeError(f"QQ 官方网关返回异常：{data}")
    return str(data["url"])


def probe(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    c = cfg or load_config()
    if not c.get("app_id") or not c.get("client_secret"):
        return False, "请先填写 AppID 和 AppSecret"
    try:
        token = _get_token(c, force=True)
        gateway = _get_gateway(c, token)
        return True, f"QQ 官方 Bot 鉴权正常，网关：{gateway}"
    except Exception as e:
        return False, str(e)


def _send_c2c_sync(cfg: Dict[str, Any], openid: str, text: str, *,
                   msg_id: str = "", seq: int = 1) -> Tuple[bool, str]:
    openid = str(openid or "").strip()
    if not openid:
        return False, "缺少 user_openid"
    body: Dict[str, Any] = {"content": str(text or "")[:4000], "msg_type": 0}
    if msg_id:
        body["msg_id"] = str(msg_id)
        body["msg_seq"] = max(1, int(seq or 1))
    for attempt in (0, 1):
        try:
            token = _get_token(cfg, force=attempt == 1)
            r = httpx.post(
                f"{_api_base(cfg)}/v2/users/{openid}/messages",
                headers={"Authorization": f"QQBot {token}", "Content-Type": "application/json"},
                json=body, timeout=15.0,
            )
        except Exception as e:
            return False, f"发送 QQ 官方消息失败：{e}"
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code in (401, 403) and attempt == 0:
            continue
        if r.status_code >= 400:
            return False, f"QQ 官方接口 HTTP {r.status_code}：{data}"
        if isinstance(data, dict) and data.get("code"):
            return False, f"QQ 官方接口错误：{data.get('message') or data.get('code')}"
        return True, ""
    return False, "QQ 官方鉴权失败"


def send_test(openid: str, text: str = "🐋 鲸语 QQ 官方通道测试消息") -> Tuple[bool, str]:
    return _send_c2c_sync(load_config(), openid, text)


# ── 联系人状态 ───────────────────────────────────────────────────────────

def _load_state() -> Dict[str, Any]:
    if not STATE_FILE or not os.path.exists(STATE_FILE):
        return {"users": {}}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
            return {"users": {}}
        return data
    except Exception:
        logger.exception("读取 QQ 官方联系人状态失败")
        return {"users": {}}


def _save_state(state: Dict[str, Any]) -> bool:
    if not STATE_FILE:
        return False
    try:
        return bool(atomic_json_write(STATE_FILE, state, indent=1))
    except Exception:
        logger.exception("写入 QQ 官方联系人状态失败")
        return False


def _default_persona_id(cfg: Dict[str, Any]) -> str:
    import personas
    items = personas.list_personas()
    if not items:
        return ""
    wanted = str(cfg.get("default_persona_id") or "")
    if wanted and any(p["id"] == wanted for p in items):
        return wanted
    return items[0]["id"]


def _persona_name(pid: str) -> str:
    try:
        import personas
        p = personas.get_persona(pid)
        return str((p or {}).get("name") or pid)
    except Exception:
        return pid


def _new_session_id(openid: str) -> str:
    safe = re.sub(r"[^0-9a-zA-Z_-]", "", str(openid))[:24] or "user"
    return f"qqo_{safe}_{int(time.time())}_{secrets.token_hex(2)}"[:64]


def _user_entry(state: Dict[str, Any], openid: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    users = state.setdefault("users", {})
    entry = users.get(openid)
    if not isinstance(entry, dict):
        entry = {}
        users[openid] = entry
    pid = str(entry.get("persona_id") or "")
    try:
        import personas
        if not pid or not personas.get_persona(pid):
            entry["persona_id"] = _default_persona_id(cfg)
    except Exception:
        entry["persona_id"] = pid
    if not str(entry.get("session_id") or ""):
        entry["session_id"] = _new_session_id(openid)
    return entry


def contacts(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    state = _load_state()
    out = []
    for openid, info in (state.get("users") or {}).items():
        if not isinstance(info, dict):
            continue
        pid = str(info.get("persona_id") or "")
        out.append({
            "user_id": str(openid),
            "persona_id": pid,
            "persona_name": _persona_name(pid),
            "session_id": str(info.get("session_id") or ""),
            "last_seen": str(info.get("last_seen") or ""),
        })
    out.sort(key=lambda x: x.get("last_seen") or "", reverse=True)
    return out[:100]


def status() -> Dict[str, Any]:
    cfg = load_config()
    data = public_config(cfg)
    data["contacts"] = contacts(cfg)
    return data


# ── 消息处理 ─────────────────────────────────────────────────────────────

def _mark_seen(msg_id: str) -> bool:
    mid = str(msg_id or "")
    if not mid:
        return True
    with _seen_lock:
        if mid in _seen:
            return False
        _seen[mid] = True
        while len(_seen) > 2000:
            _seen.popitem(last=False)
    return True


def _rate_ok(openid: str, limit: int) -> bool:
    now = time.time()
    with _rate_lock:
        dq = _rate[openid]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
    return True


def _in_quiet_hours(cfg: Dict[str, Any]) -> bool:
    m = _QUIET_RE.match(str(cfg.get("quiet_hours") or ""))
    if not m:
        return False
    sh, sm, eh, em = (int(x) for x in m.groups())
    now = datetime.now()
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False
    return start <= cur < end if start < end else (cur >= start or cur < end)


def _command(text: str) -> Tuple[str, str]:
    s = str(text or "").strip()
    if not s.startswith("/"):
        return "", ""
    parts = s.split(maxsplit=1)
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


def _find_persona(query: str) -> Optional[Dict[str, Any]]:
    import personas
    q = str(query or "").strip()
    if not q:
        return None
    items = personas.list_personas()
    for p in items:
        if p["id"] == q:
            return p
    for p in items:
        if p["name"] == q:
            return p
    hits = [p for p in items if q.lower() in p["name"].lower()]
    return hits[0] if len(hits) == 1 else None


def _command_reply(openid: str, text: str, cfg: Dict[str, Any]) -> Tuple[str, bool]:
    cmd, arg = _command(text)
    if not cmd:
        return "", False
    state = _load_state()
    entry = _user_entry(state, openid, cfg)
    if cmd in ("/help", "/帮助", "/start"):
        current = _persona_name(str(entry.get("persona_id") or ""))
        return (f"可用命令：\n/角色 <名字> 切换角色\n/重置 重新开始对话\n/记忆 查看当前角色记忆条数\n"
                f"/帮助 显示本说明\n当前角色：{current or '未设置'}"), True
    if cmd in ("/reset", "/重置", "/重置对话", "/new"):
        entry["session_id"] = _new_session_id(openid)
        entry["last_seen"] = _now()
        _save_state(state)
        return "好，我们重新开始。你想聊点什么？", True
    if cmd in ("/persona", "/角色", "/role"):
        if not arg:
            items = _personas_brief()
            return "\n".join(["可用角色："] + [f"- {p['name']}（{p['id']}）" for p in items]), True
        p = _find_persona(arg)
        if not p:
            return f"没有找到角色「{arg}」。用 /角色 查看可用角色。", True
        entry["persona_id"] = p["id"]
        entry["session_id"] = _new_session_id(openid)
        entry["last_seen"] = _now()
        _save_state(state)
        return f"好，接下来由「{p['name']}」陪你聊。", True
    if cmd in ("/memory", "/记忆"):
        try:
            import persona_memory
            n = persona_memory.count(str(entry.get("persona_id") or ""))
            return f"「{_persona_name(str(entry.get('persona_id') or ''))}」目前记住了 {n} 条关于你的事。", True
        except Exception:
            return "角色记忆暂时不可用。", True
    return "", False


def _personas_brief() -> List[Dict[str, str]]:
    try:
        import personas
        return [{"id": p["id"], "name": p["name"]} for p in personas.list_personas()]
    except Exception:
        return []


def _next_seq(msg_id: str) -> int:
    mid = str(msg_id or "")
    with _seq_lock:
        n = int(_seq.get(mid) or 0) + 1
        _seq[mid] = n
        if len(_seq) > 2000:
            for k in list(_seq)[:500]:
                _seq.pop(k, None)
        return n


def _attachment_text_and_images(attachments: Any) -> Tuple[str, List[str]]:
    """从官方附件提取语音 ASR 文本与图片本地路径。"""
    import qq_onebot
    text = ""
    images: List[str] = []
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        ctype = str(att.get("content_type") or "")
        if ctype.startswith("image/"):
            path = qq_onebot.download_media(str(att.get("url") or ""), "image")
            if path:
                images.append(path)
        elif ctype == "voice":
            asr = str(att.get("asr_refer_text") or "").strip()
            if asr:
                text = asr
                continue
            wav = str(att.get("voice_wav_url") or att.get("url") or "")
            if wav:
                path = qq_onebot.download_media(wav, "voice")
                if path:
                    try:
                        import deepseek_client as dc
                        tr = str(dc.speech_to_text(path, "base") or "").strip()
                        if tr and not tr.startswith(("错误", "语音需要", "未识别")):
                            text = tr
                    except Exception:
                        logger.exception("QQ 官方语音转写失败")
    return text, images


# ── WebSocket 运行器 ─────────────────────────────────────────────────────

def _user_async_lock(openid: str) -> asyncio.Lock:
    with _user_locks_guard:
        lk = _user_locks.get(openid)
        if lk is None:
            lk = asyncio.Lock()
            _user_locks[openid] = lk
        return lk


async def _heartbeat(ws: Any, interval: float, stop: threading.Event, seq_ref: List[int]) -> None:
    try:
        while not stop.is_set() and not ws.closed:
            await asyncio.sleep(max(1.0, interval))
            if stop.is_set() or ws.closed:
                return
            try:
                await ws.send_json({"op": 1, "d": seq_ref[0] or None})
            except Exception:
                return
    except asyncio.CancelledError:
        return


async def _handle_c2c(data: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    openid = str(author.get("user_openid") or author.get("id") or "").strip()
    if not openid:
        return
    msg_id = str(data.get("id") or "")
    if not _mark_seen(msg_id):
        return
    allowed = cfg.get("allowed_users") or []
    if allowed and openid not in allowed:
        return
    if not _rate_ok(openid, int(cfg.get("rate_limit_per_minute") or 10)):
        logger.warning("QQ 官方用户 %s 触发限流，已忽略", openid[:12])
        return

    content = str(data.get("content") or "").strip()
    voice_text, images = await asyncio.to_thread(
        _attachment_text_and_images, data.get("attachments"))
    if voice_text and not content:
        content = voice_text
    if not content and images:
        content = "看看这张图片"
    if not content:
        content = "（用户发来一条消息，但当前无法解析内容）"

    if _command(content)[0]:
        try:
            reply, handled = await asyncio.to_thread(_command_reply, openid, content, cfg)
        except Exception:
            logger.exception("QQ 官方命令处理失败")
            return
        if handled:
            await _send_chunks(openid, reply, cfg, msg_id)
            return
    if _in_quiet_hours(cfg):
        return

    lock = _user_async_lock(openid)
    if lock.locked():
        return
    async with lock:
        state = _load_state()
        entry = _user_entry(state, openid, cfg)
        pid = str(entry.get("persona_id") or "")
        sid = str(entry.get("session_id") or "")
        try:
            import api_server as srv
            reply = await asyncio.to_thread(srv._companion_reply, content, sid, pid, images)
        except Exception:
            logger.exception("QQ 官方陪伴回复失败")
            reply = "（我这边刚才走神了一下，稍后再跟我说一句？）"
        entry["last_seen"] = _now()
        _save_state(state)
        await _send_chunks(openid, reply, cfg, msg_id)


async def _send_chunks(openid: str, text: str, cfg: Dict[str, Any], msg_id: str) -> None:
    try:
        import qq_onebot
        chunks = qq_onebot.split_reply(text, int(cfg.get("max_reply_chars") or 1200))
    except Exception:
        chunks = [str(text or "")[:int(cfg.get("max_reply_chars") or 1200)]]
    for chunk in chunks[:MAX_PASSIVE_REPLIES]:
        seq = _next_seq(msg_id)
        ok, err = await asyncio.to_thread(
            _send_c2c_sync, cfg, openid, chunk, msg_id=msg_id, seq=seq)
        if not ok:
            logger.warning("QQ 官方回复发送失败：%s", err)
            break


async def _runner(cfg: Dict[str, Any]) -> None:
    global _connected, _last_error, _bot_info
    backoff = 1
    while not _stop_event.is_set():
        try:
            token = await asyncio.to_thread(_get_token, cfg)
            gateway = await asyncio.to_thread(_get_gateway, cfg, token)
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=None)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                async with session.ws_connect(gateway, heartbeat=None) as ws:
                    _last_error = ""
                    _connected = False
                    hb_task = None
                    seq_ref = [0]
                    try:
                        while not _stop_event.is_set():
                            try:
                                msg = await ws.receive(timeout=1.0)
                            except asyncio.TimeoutError:
                                continue
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    payload = json.loads(msg.data)
                                except Exception:
                                    continue
                                op = payload.get("op")
                                if isinstance(payload.get("s"), int):
                                    seq_ref[0] = payload["s"]
                                if op == 10:
                                    interval = float((payload.get("d") or {}).get("heartbeat_interval") or 30000) / 1000.0
                                    await ws.send_json({
                                        "op": 2,
                                        "d": {
                                            "token": f"QQBot {token}",
                                            "intents": int(cfg.get("intents") or DEFAULT_INTENTS),
                                            "shard": [0, 1],
                                            "properties": {"$os": "windows", "$browser": "whaletalk", "$device": "whaletalk"},
                                        },
                                    })
                                    if hb_task is None:
                                        hb_task = asyncio.create_task(_heartbeat(ws, interval, _stop_event, seq_ref))
                                elif op == 0:
                                    event_type = payload.get("t")
                                    data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
                                    if event_type == "READY":
                                        _connected = True
                                        user = data.get("user") if isinstance(data.get("user"), dict) else {}
                                        _bot_info = str(user.get("username") or user.get("id") or "")
                                    elif event_type == "RESUMED":
                                        _connected = True
                                    elif event_type == "C2C_MESSAGE_CREATE":
                                        asyncio.create_task(_handle_c2c(data, cfg))
                                elif op in (7, 9):
                                    await ws.close()
                                    break
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                              aiohttp.WSMsgType.ERROR):
                                break
                    finally:
                        if hb_task is not None:
                            hb_task.cancel()
                            with suppress(Exception):
                                await hb_task
                        _connected = False
            backoff = 1
        except Exception as e:
            _connected = False
            _last_error = str(e)
            logger.warning("QQ 官方连接异常：%s", e)
        if not _stop_event.is_set():
            with suppress(Exception):
                await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)


def _thread_main(cfg: Dict[str, Any]) -> None:
    try:
        asyncio.run(_runner(cfg))
    except Exception:
        logger.exception("QQ 官方 WebSocket 线程退出")


def start(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    global _thread, _active_cfg, _connected, _last_error, _bot_info
    c = _normalize(cfg or load_config())
    with _lock:
        _active_cfg = c
        stop()
        _token_cache["token"] = ""
        _token_cache["exp"] = 0.0
        _connected = False
        _last_error = ""
        _bot_info = ""
        if not c.get("enabled"):
            return public_config(c)
        if not c.get("app_id") or not c.get("client_secret"):
            _last_error = "请先填写 AppID 和 AppSecret"
            return public_config(c)
        _stop_event.clear()
        th = threading.Thread(target=_thread_main, args=(c,), daemon=True, name="qq-official-ws")
        _thread = th
        th.start()
        time.sleep(0.05)
        logger.info("QQ 官方 Bot 通道启动（AppID=%s）", str(c.get("app_id"))[:8])
    return public_config(c)


def stop() -> None:
    global _thread, _connected
    with _lock:
        _stop_event.set()
        th = _thread
        _thread = None
        _connected = False
    if th is not None and th.is_alive():
        with suppress(Exception):
            th.join(timeout=5)


def start_if_enabled() -> Dict[str, Any]:
    try:
        return start(load_config())
    except Exception as e:
        global _last_error
        _last_error = str(e)
        logger.exception("QQ 官方通道启动失败")
        return public_config()


__all__ = [
    "CONFIG_FILE",
    "STATE_FILE",
    "contacts",
    "get_active_config",
    "init",
    "load_config",
    "probe",
    "public_config",
    "save_config",
    "send_test",
    "start",
    "start_if_enabled",
    "status",
    "stop",
]
