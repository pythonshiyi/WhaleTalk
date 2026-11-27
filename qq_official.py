"""QQ 官方 Bot 接入（腾讯 QQ Bot API v2，WebSocket + C2C）。

本模块现在只是 `channel_base.ChannelAdapter` 的一个适配器实现——
**业务逻辑全部继承自共享层**，这里只剩官方 Bot 特有的传输细节：
Access Token 获取 → WebSocket Gateway → 心跳 → C2C 被动回复。

与本地 OneBot 通道的区别（这才是唯一该存在的差异）：
- 入站走 WebSocket（不是 HTTP 回调），需要心跳与断线重连；
- 用户标识是 `user_openid` 而不是 QQ 号；
- 回复是在原消息上的**被动回复**，有 `msg_id` + `msg_seq`，且条数受限。

配置：在 q.qq.com 创建机器人后填写 AppID / AppSecret。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import suppress
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import httpx

import channel_base as cb
import crypto
from channel_base import (
    Capability,
    ChannelAdapter,
    InboundMessage,
    OutboundMessage,
    PersonaContext,
    clean_users,
    clamp_int,
    normalize_quiet_hours,
)
from channel_registry import build_runtime, registry
from persistence import atomic_json_write

logger = logging.getLogger("whaletalk.qqofficial")

CONFIG_FILE: Optional[str] = None
STATE_FILE: Optional[str] = None
DEFAULT_API_BASE = "https://api.bot.qq.com"
DEFAULT_INTENTS = 1 << 25  # GROUP_AND_C2C_EVENT
MAX_PASSIVE_REPLIES = 4

_lock = threading.RLock()
_active_cfg: Optional[Dict[str, Any]] = None

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


# ══════════════════════════════════════════════════════════════════════════
# 配置 / 状态
# ══════════════════════════════════════════════════════════════════════════

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
    out["intents"] = clamp_int(out.get("intents"), DEFAULT_INTENTS, 0, 2 ** 31)
    out["default_persona_id"] = str(out.get("default_persona_id") or "")[:64]
    out["allowed_users"] = clean_users(out.get("allowed_users"))
    out["max_reply_chars"] = clamp_int(out.get("max_reply_chars"), 1200, 100, 4000)
    out["rate_limit_per_minute"] = clamp_int(out.get("rate_limit_per_minute"), 10, 1, 120)
    out["quiet_hours"] = normalize_quiet_hours(out.get("quiet_hours"))
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


# ══════════════════════════════════════════════════════════════════════════
# 官方 Bot HTTP 接口（Token / Gateway / 被动回复）
# ══════════════════════════════════════════════════════════════════════════

def _api_base(cfg: Dict[str, Any]) -> str:
    base = str(cfg.get("api_base") or DEFAULT_API_BASE).strip().rstrip("/")
    if base and not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base


def _get_token(cfg: Dict[str, Any], force: bool = False) -> str:
    """获取 Access Token（带进程内缓存）。"""
    a = _ensure_registered()
    with a._token_lock:
        now = time.time()
        if not force and a._token_cache.get("token") and now < float(a._token_cache.get("exp") or 0):
            return str(a._token_cache["token"])
    base = _api_base(cfg)
    try:
        r = httpx.post(
            f"{base}/app/getAppAccessToken",
            json={"appId": cfg.get("app_id") or "", "clientSecret": cfg.get("client_secret") or ""},
            timeout=15.0,
        )
        data = r.json() if r.status_code < 400 else {}
    except Exception:
        logger.exception("获取 QQ 官方 Access Token 失败")
        raise
    token = str((data or {}).get("access_token") or "")
    if not token:
        raise RuntimeError(f"获取 Access Token 失败：{data}")
    try:
        exp = now + max(60, int((data or {}).get("expires_in") or 7200) - 120)
    except (TypeError, ValueError):
        exp = now + 7000
    with a._token_lock:
        a._token_cache["token"] = token
        a._token_cache["exp"] = exp
    return token


def _get_gateway(cfg: Dict[str, Any], token: str) -> str:
    base = _api_base(cfg)
    r = httpx.get(f"{base}/gateway", headers={"Authorization": f"QQBot {token}"}, timeout=15.0)
    data = r.json() if r.status_code < 400 else {}
    url = str((data or {}).get("url") or "")
    if not url:
        raise RuntimeError(f"获取 Gateway 失败：{data}")
    return url


def _send_c2c_sync(cfg: Dict[str, Any], openid: str, text: str, *,
                   msg_id: str = "", seq: Optional[int] = None) -> Tuple[bool, str]:
    """发送 C2C 消息（被动回复：带 msg_id + msg_seq）。"""
    base = _api_base(cfg)
    try:
        token = _get_token(cfg)
    except Exception as e:
        return False, f"获取 Access Token 失败：{e}"
    body: Dict[str, Any] = {"content": str(text or ""), "msg_type": 0}
    if msg_id:
        body["msg_id"] = msg_id
    if seq is not None:
        body["msg_seq"] = int(seq)
    try:
        r = httpx.post(
            f"{base}/v2/users/{openid}/messages",
            json=body,
            headers={"Authorization": f"QQBot {token}", "Content-Type": "application/json"},
            timeout=15.0,
        )
    except Exception as e:
        logger.exception("QQ 官方发送失败")
        return False, f"发送异常：{e}"
    if r.status_code in (401, 403):
        return False, "鉴权失败：AppID/AppSecret 可能不正确或已过期"
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:200]
        return False, f"HTTP {r.status_code}：{detail}"
    return True, ""


def _attachment_text_and_images(attachments: Any) -> Tuple[str, List[str]]:
    """把官方 Bot 的 attachments 翻译成 (语音转写提示, 图片 URL 列表)。"""
    voice_text = ""
    images: List[str] = []
    for att in attachments if isinstance(attachments, list) else []:
        if not isinstance(att, dict):
            continue
        ctype = str(att.get("content_type") or "").lower()
        url = str(att.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        if ctype.startswith("image/"):
            images.append(url)
        elif ctype.startswith("voice/") or ctype.startswith("audio/"):
            if not voice_text:
                voice_text = "（用户发来一条语音）"
    return voice_text, images[:5]


# ══════════════════════════════════════════════════════════════════════════
# 适配器
# ══════════════════════════════════════════════════════════════════════════

class QQOfficialAdapter(ChannelAdapter):
    """QQ 官方 Bot 适配器：WebSocket 入站 + 被动回复出站。"""

    name = "qq_official"
    display_name = "QQ 官方"

    def __init__(self) -> None:
        super().__init__()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._token_cache: Dict[str, Any] = {"token": "", "exp": 0.0}
        self._token_lock = threading.Lock()
        self._seq: Dict[str, int] = {}
        self._seq_lock = threading.Lock()

    def capabilities(self) -> Capability:
        return Capability(private=True, group=False, media=True, voice=True)

    def _next_seq(self, msg_id: str) -> int:
        with self._seq_lock:
            n = self._seq.get(msg_id, 0) + 1
            self._seq[msg_id] = n
            if len(self._seq) > 500:
                for k in list(self._seq.keys())[:250]:
                    self._seq.pop(k, None)
            return n

    def media_dir(self) -> str:
        if not CONFIG_FILE:
            return ""
        d = os.path.join(os.path.dirname(CONFIG_FILE), "uploads", "qq_official")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            logger.exception("创建 QQ 官方媒体目录失败")
            return ""
        return d

    # ── 探活 ──

    def probe(self, cfg: Dict[str, Any]) -> Tuple[bool, str]:
        if not cfg.get("app_id") or not cfg.get("client_secret"):
            return False, "请先填写 AppID 和 AppSecret"
        try:
            token = _get_token(cfg, force=True)
        except Exception as e:
            return False, f"获取 Access Token 失败：{e}"
        try:
            url = _get_gateway(cfg, token)
        except Exception as e:
            return False, f"获取 Gateway 失败：{e}"
        return True, f"官方 Bot 连接正常（Gateway：{url[:60]}）"

    # ── 出站 ──

    def send(self, msg: OutboundMessage, cfg: Dict[str, Any]) -> Tuple[bool, str]:
        seq = self._next_seq(msg.reply_to) if msg.reply_to else None
        return _send_c2c_sync(cfg, msg.user_id, msg.text, msg_id=msg.reply_to, seq=seq)

    # ── 入站翻译 ──

    def parse_event(self, raw: Any) -> Optional[InboundMessage]:
        if not isinstance(raw, dict):
            return None
        author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
        openid = str(author.get("user_openid") or author.get("id") or "").strip()
        if not openid:
            return None
        content = str(raw.get("content") or "").strip()
        voice_text, images = _attachment_text_and_images(raw.get("attachments"))
        return InboundMessage(
            user_id=openid,
            text=content,
            message_id=str(raw.get("id") or ""),
            images=images,
            event=cb.EV_MESSAGE,
            is_group=False,
            raw=raw,
            extra={"voice_text": voice_text},
        )

    # ── 生命周期 ──

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, cfg: Dict[str, Any]) -> None:
        c = _normalize(cfg)
        global _active_cfg
        with self._lock:
            _active_cfg = c
            self.stop()
            self._token_cache["token"] = ""
            self._token_cache["exp"] = 0.0
            self._connected = False
            self.set_error("")
            self._bot_info = ""
            if not c.get("enabled"):
                return
            if not c.get("app_id") or not c.get("client_secret"):
                self.set_error("请先填写 AppID 和 AppSecret")
                return
            self._stop_event.clear()
            th = threading.Thread(target=self._thread_main, args=(c,), daemon=True,
                                  name="qq-official-ws")
            self._thread = th
            th.start()
            time.sleep(0.05)
            logger.info("QQ 官方 Bot 通道启动（AppID=%s）", str(c.get("app_id"))[:8])

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            th = self._thread
            self._thread = None
            self._connected = False
        if th is not None and th.is_alive():
            with suppress(Exception):
                th.join(timeout=5)

    # ── WebSocket 主循环 ──

    def _thread_main(self, cfg: Dict[str, Any]) -> None:
        try:
            asyncio.run(self._runner(cfg))
        except Exception:
            logger.exception("QQ 官方 WebSocket 线程退出")

    async def _heartbeat(self, ws: Any, interval: float, seq_ref: List[int]) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(max(1.0, interval))
                if self._stop_event.is_set():
                    return
                try:
                    await ws.send_json({"op": 1, "d": seq_ref[0] or None})
                except Exception:
                    return
        except asyncio.CancelledError:
            return

    async def _runner(self, cfg: Dict[str, Any]) -> None:
        backoff = 1
        while not self._stop_event.is_set():
            try:
                token = await asyncio.to_thread(_get_token, cfg)
                gateway = await asyncio.to_thread(_get_gateway, cfg, token)
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=None)
                async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                    async with session.ws_connect(gateway, heartbeat=None) as ws:
                        self.set_error("")
                        self._connected = False
                        hb_task = None
                        seq_ref = [0]
                        try:
                            while not self._stop_event.is_set():
                                try:
                                    msg = await ws.receive(timeout=1.0)
                                except TimeoutError:
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
                                        interval = float(
                                            (payload.get("d") or {}).get("heartbeat_interval")
                                            or 30000) / 1000.0
                                        await ws.send_json({
                                            "op": 2,
                                            "d": {
                                                "token": f"QQBot {token}",
                                                "intents": int(cfg.get("intents")
                                                               or self.DEFAULT_INTENTS_FALLBACK),
                                                "shard": [0, 1],
                                                "properties": {"$os": "windows",
                                                               "$browser": "whaletalk",
                                                               "$device": "whaletalk"},
                                            },
                                        })
                                        if hb_task is None:
                                            hb_task = asyncio.create_task(
                                                self._heartbeat(ws, interval, seq_ref))
                                    elif op == 0:
                                        event_type = payload.get("t")
                                        data = payload.get("d")
                                        data = data if isinstance(data, dict) else {}
                                        if event_type == "READY":
                                            self._connected = True
                                            user = data.get("user")
                                            user = user if isinstance(user, dict) else {}
                                            self._bot_info = str(
                                                user.get("username") or user.get("id") or "")
                                        elif event_type == "RESUMED":
                                            self._connected = True
                                        elif event_type == "C2C_MESSAGE_CREATE":
                                            self.dispatch(data)
                                    elif op in (7, 9):
                                        await ws.close()
                                        break
                                elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                                  aiohttp.WSMsgType.CLOSING,
                                                  aiohttp.WSMsgType.ERROR):
                                    break
                        finally:
                            if hb_task is not None:
                                hb_task.cancel()
                                with suppress(Exception):
                                    await hb_task
                            self._connected = False
                backoff = 1
            except Exception as e:
                self._connected = False
                self.set_error(str(e))
                logger.warning("QQ 官方连接异常：%s", e)
            if not self._stop_event.is_set():
                with suppress(Exception):
                    await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    DEFAULT_INTENTS_FALLBACK = DEFAULT_INTENTS


# ══════════════════════════════════════════════════════════════════════════
# 注册与对外接口（保持与原模块签名一致）
# ══════════════════════════════════════════════════════════════════════════

_ADAPTER: Optional[QQOfficialAdapter] = None


def _ensure_registered() -> QQOfficialAdapter:
    global _ADAPTER
    with _lock:
        if _ADAPTER is None:
            _ADAPTER = registry.register(
                QQOfficialAdapter,
                build_runtime(
                    QQOfficialAdapter,
                    load_cfg=get_active_config,
                    load_state=_load_state,
                    save_state=_save_state,
                    session_prefix="qqo",
                ),
                replace=registry.has(QQOfficialAdapter.name),
            )
        return _ADAPTER


def adapter() -> QQOfficialAdapter:
    return _ensure_registered()


def public_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = dict(cfg or load_config())
    secret = str(c.pop("client_secret", "") or "")
    c["has_secret"] = bool(secret)
    a = _ensure_registered()
    c["running"] = bool(a.is_running())
    c["connected"] = bool(a._connected)
    c["bot_info"] = a._bot_info
    c["last_error"] = a._last_error
    return c


def save_config(patch: Any) -> Dict[str, Any]:
    """保存配置；client_secret 留空表示保持原值，clear_secret=True 可清空。"""
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
            raise ValueError("QQ 官方配置路径未初始化")
        if not atomic_json_write(CONFIG_FILE, to_store, indent=1):
            raise RuntimeError("QQ 官方配置保存失败")
        _active_cfg = normalized
    return public_config(normalized)


def _context() -> PersonaContext:
    rt = registry.runtime(QQOfficialAdapter.name)
    if rt is not None:
        return rt.context
    return PersonaContext("qqo")


def _command_reply(openid: str, text: str, cfg: Dict[str, Any]) -> Tuple[str, bool]:
    state = _load_state()
    return _context().command_reply(openid, text, cfg, state, _save_state)


def contacts(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    return _context().list_contacts(_load_state())


def status() -> Dict[str, Any]:
    cfg = load_config()
    data = public_config(cfg)
    data["contacts"] = contacts(cfg)
    return data


def probe(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    return _ensure_registered().probe(cfg or load_config())


def send_test(openid: str, text: str = "🐋 鲸语 QQ 官方通道测试消息") -> Tuple[bool, str]:
    cfg = load_config()
    return _send_c2c_sync(cfg, str(openid), str(text))


def start(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    a = _ensure_registered()
    c = _normalize(cfg or load_config())
    try:
        a.start(c)
    except Exception as e:
        a.set_error(str(e))
        logger.exception("QQ 官方通道启动失败")
    return public_config(c)


def stop() -> None:
    _ensure_registered().stop()


def start_if_enabled() -> Dict[str, Any]:
    return start(load_config())


async def _handle_c2c(data: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    """向后兼容：把官方事件交给运行时（异步签名保留）。"""
    a = _ensure_registered()
    rt = registry.runtime(QQOfficialAdapter.name)
    if rt is not None:
        rt.handle_raw(a, data)


# 模块导入即注册：让注册表能统一发现全部通道
_ensure_registered()


__all__ = [
    "CONFIG_FILE",
    "QQOfficialAdapter",
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
