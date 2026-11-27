"""Telegram Bot 通道（getUpdates 长轮询）。

重构前它是一段 90 行内联在 `api_server._im_loop()` 里的循环，只支持**单个
chat_id**、没有角色绑定、没有限流、没有命令。现在它和其他平台一样是一个
`ChannelAdapter`，因此**自动获得**共享层的全部能力：

    - 联系人 → 会话 → 角色绑定（每条私聊各自独立）
    - 白名单、限流、去重、静默时段
    - /角色 /重置 /记忆 /帮助 命令
    - 长回复自动分片

配置来自 DATA_DIR/im_config.json 的 telegram_bot_token / telegram_chat_id。
`telegram_chat_id` 若填写，则只接收该会话（与旧行为兼容）；留空则接受任意
私聊（受白名单约束）。
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import urllib.request
from contextlib import suppress
from typing import Any, Dict, Optional, Tuple

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

logger = logging.getLogger("whaletalk.telegram")

CONFIG_FILE: Optional[str] = None
STATE_FILE: Optional[str] = None
API_BASE = "https://api.telegram.org"

_lock = threading.RLock()
_active_cfg: Optional[Dict[str, Any]] = None

_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "bot_token": "",
    "chat_id": "",
    "default_persona_id": "",
    "allowed_users": [],
    "max_reply_chars": 4000,
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
    out["bot_token"] = str(out.get("bot_token") or "").strip()[:200]
    out["chat_id"] = str(out.get("chat_id") or "").strip()[:64]
    out["default_persona_id"] = str(out.get("default_persona_id") or "")[:64]
    out["allowed_users"] = clean_users(out.get("allowed_users"))
    out["max_reply_chars"] = clamp_int(out.get("max_reply_chars"), 4000, 100, 4000)
    out["rate_limit_per_minute"] = clamp_int(out.get("rate_limit_per_minute"), 10, 1, 120)
    out["quiet_hours"] = normalize_quiet_hours(out.get("quiet_hours"))
    return out


def load_config() -> Dict[str, Any]:
    with _lock:
        if not CONFIG_FILE or not __import__("os").path.exists(CONFIG_FILE):
            return _normalize({})
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            raw = raw if isinstance(raw, dict) else {}
            import crypto
            out = dict(raw)
            if raw.get("telegram_bot_token"):
                out["bot_token"] = crypto.decrypt(str(raw["telegram_bot_token"]))
            if raw.get("telegram_chat_id"):
                out["chat_id"] = str(raw["telegram_chat_id"])
            return _normalize(out)
        except Exception:
            logger.exception("读取 Telegram 配置失败")
            return _normalize({})


def get_active_config() -> Dict[str, Any]:
    with _lock:
        if _active_cfg is not None:
            return dict(_active_cfg)
    return load_config()


def _load_state() -> Dict[str, Any]:
    if not STATE_FILE or not __import__("os").path.exists(STATE_FILE):
        return {"users": {}}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
            return {"users": {}}
        return data
    except Exception:
        logger.exception("读取 Telegram 联系人状态失败")
        return {"users": {}}


def _save_state(state: Dict[str, Any]) -> bool:
    if not STATE_FILE:
        return False
    try:
        return bool(atomic_json_write(STATE_FILE, state, indent=1))
    except Exception:
        logger.exception("写入 Telegram 联系人状态失败")
        return False


# ══════════════════════════════════════════════════════════════════════════
# 适配器
# ══════════════════════════════════════════════════════════════════════════

class TelegramAdapter(ChannelAdapter):
    """Telegram 适配器：getUpdates 长轮询入站 + sendMessage 出站。"""

    name = "telegram"
    display_name = "Telegram"

    def __init__(self) -> None:
        super().__init__()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._offset: Optional[int] = None

    def capabilities(self) -> Capability:
        return Capability(private=True, group=True, media=True, voice=False)

    # ── 出站 ──

    def send(self, msg: OutboundMessage, cfg: Dict[str, Any]) -> Tuple[bool, str]:
        token = str(cfg.get("bot_token") or "")
        if not token:
            return False, "未配置 Telegram Bot Token"
        payload = urllib.parse.urlencode(
            {"chat_id": msg.user_id, "text": msg.text, "disable_web_page_preview": "true"}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{API_BASE}/bot{token}/sendMessage", data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if not data.get("ok"):
                return False, str(data.get("description") or "发送失败")
            return True, ""
        except Exception as e:
            logger.exception("Telegram 发送失败")
            return False, str(e)

    def probe(self, cfg: Dict[str, Any]) -> Tuple[bool, str]:
        token = str(cfg.get("bot_token") or "")
        if not token:
            return False, "请先填写 Bot Token"
        try:
            with urllib.request.urlopen(
                    f"{API_BASE}/bot{token}/getMe", timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            return False, f"连接 Telegram 失败：{e}"
        if not data.get("ok"):
            return False, str(data.get("description") or "Token 无效")
        user = data.get("result") or {}
        return True, f"Telegram Bot 连接正常：@{user.get('username') or user.get('id')}"

    # ── 入站翻译 ──

    def parse_event(self, raw: Any) -> Optional[InboundMessage]:
        if not isinstance(raw, dict):
            return None
        msg = raw.get("message") or raw.get("edited_message") or {}
        if not isinstance(msg, dict):
            return None
        text = str(msg.get("text") or "").strip()
        chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
        chat_id = str(chat.get("id") or "")
        if not text or not chat_id:
            return None
        if chat.get("type") not in ("private", "group", "supergroup"):
            return None
        images = []
        photos = msg.get("photo")
        if isinstance(photos, list) and photos:
            last = photos[-1] if isinstance(photos[-1], dict) else {}
            file_id = str(last.get("file_id") or "")
            if file_id:
                images.append(file_id)   # 由 fetch_media 解析为真实 URL
        return InboundMessage(
            user_id=chat_id,
            text=text,
            message_id=str(msg.get("message_id") or ""),
            images=images,
            is_group=chat.get("type") != "private",
            group_id=chat_id if chat.get("type") != "private" else "",
            raw=raw,
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
            self.set_error("")
            self._connected = False
            if not c.get("enabled") or not c.get("bot_token"):
                if c.get("enabled"):
                    self.set_error("请先填写 Bot Token")
                return
            self._stop_event.clear()
            th = threading.Thread(target=self._loop, args=(c,), daemon=True,
                                  name="telegram-poll")
            self._thread = th
            th.start()
            logger.info("Telegram 通道已启动")

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            th = self._thread
            self._thread = None
            self._connected = False
        if th is not None and th.is_alive():
            with suppress(Exception):
                th.join(timeout=5)

    def _loop(self, cfg: Dict[str, Any]) -> None:
        token = str(cfg.get("bot_token") or "")
        pinned = str(cfg.get("chat_id") or "")
        while not self._stop_event.is_set():
            try:
                params = [("offset", str(self._offset))] if self._offset else [("timeout", "25")]
                q = urllib.parse.urlencode(params)
                req = urllib.request.Request(
                    f"{API_BASE}/bot{token}/getUpdates?{q}", timeout=35)
                with urllib.request.urlopen(req, timeout=35) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                self._connected = True
                for update in data.get("result") or []:
                    self._offset = max(self._offset or 0,
                                       int(update.get("update_id") or 0)) + 1
                    msg = update.get("message") or {}
                    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
                    from_chat = str(chat.get("id") or "")
                    # 与旧行为兼容：配置了 chat_id 就只认这一个会话
                    if pinned and from_chat and from_chat != pinned:
                        continue
                    self.dispatch(update)
            except Exception:
                self._connected = False
                logger.exception("Telegram 轮询异常")
                if self._stop_event.wait(30):
                    return
            if self._stop_event.wait(3):
                return


# ══════════════════════════════════════════════════════════════════════════
# 注册与对外接口
# ══════════════════════════════════════════════════════════════════════════

_ADAPTER: Optional[TelegramAdapter] = None


def _ensure_registered() -> TelegramAdapter:
    global _ADAPTER
    with _lock:
        if _ADAPTER is None:
            _ADAPTER = registry.register(
                TelegramAdapter,
                build_runtime(
                    TelegramAdapter,
                    load_cfg=get_active_config,
                    load_state=_load_state,
                    save_state=_save_state,
                    session_prefix="tg",
                ),
                replace=registry.has(TelegramAdapter.name),
            )
        return _ADAPTER


def adapter() -> TelegramAdapter:
    return _ensure_registered()


def public_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = dict(cfg or load_config())
    token = str(c.pop("bot_token", "") or "")
    c["has_token"] = bool(token)
    a = _ensure_registered()
    c["running"] = bool(a.is_running())
    c["connected"] = bool(a._connected)
    c["last_error"] = a._last_error
    return c


def contacts(cfg: Optional[Dict[str, Any]] = None) -> list:
    rt = registry.runtime(TelegramAdapter.name)
    ctx = rt.context if rt is not None else PersonaContext("tg")
    return ctx.list_contacts(_load_state())


def status() -> Dict[str, Any]:
    cfg = load_config()
    data = public_config(cfg)
    data["contacts"] = contacts(cfg)
    return data


def probe(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    return _ensure_registered().probe(cfg or load_config())


def send_test(chat_id: str, text: str = "🐋 鲸语 Telegram 通道测试消息") -> Tuple[bool, str]:
    return _ensure_registered().send(
        OutboundMessage(user_id=str(chat_id), text=str(text)), load_config())


def start(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    a = _ensure_registered()
    c = _normalize(cfg or load_config())
    try:
        a.start(c)
    except Exception as e:
        a.set_error(str(e))
        logger.exception("Telegram 通道启动失败")
    return public_config(c)


def stop() -> None:
    _ensure_registered().stop()


def start_if_enabled() -> Dict[str, Any]:
    return start(load_config())


# 模块导入即注册：让注册表能统一发现全部通道
_ensure_registered()


__all__ = [
    "CONFIG_FILE",
    "STATE_FILE",
    "TelegramAdapter",
    "contacts",
    "get_active_config",
    "init",
    "load_config",
    "probe",
    "public_config",
    "send_test",
    "start",
    "start_if_enabled",
    "status",
    "stop",
]
