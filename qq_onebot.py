"""本地 QQ 陪伴接入（OneBot v11 / NapCat HTTP 模式）。

本模块现在只是 `channel_base.ChannelAdapter` 的一个适配器实现——
**业务逻辑（角色绑定/限流/静默/命令/分片）全部继承自共享层**，
这里只剩 OneBot 特有的传输细节：HTTP 事件回调入站 + HTTP API 出站。

设计边界（与重构前一致）：
- **只支持私聊**：`message.private` 才处理，群消息一律忽略；
- **只走本机**：事件监听固定绑定 127.0.0.1，出站 API 默认也是 127.0.0.1；
- **无工具**：陪伴回复走 dialog 模式（persona + 角色记忆），不注入 Agent 工具；
- **每个 QQ 独立会话**：联系人 ↔ 会话 ↔ 角色，记忆按角色隔离；
- **不内置协议/不碰 QQ 账号**：由 NapCat 等 OneBot 实现负责登录。

NapCat 侧配置（任选 HTTP 模式）：
1. 开启 HTTP API（默认 `http://127.0.0.1:3000`）；
2. 开启 HTTP POST 事件，目标填本程序的监听地址（默认 `http://127.0.0.1:8756/onebot`）；
3. 若本程序配置了令牌，NapCat 的 API / 上报令牌填同一个值。
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

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
    now_iso,
    split_reply,
)
from channel_registry import build_runtime, registry
from persistence import atomic_json_write

logger = logging.getLogger("whaletalk.qq")

CONFIG_FILE: Optional[str] = None
STATE_FILE: Optional[str] = None
DEFAULT_API_BASE = "http://127.0.0.1:3000"
DEFAULT_LISTEN_PORT = 8756

_lock = threading.RLock()
_active_cfg: Optional[Dict[str, Any]] = None

_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "api_base": DEFAULT_API_BASE,
    "listen_port": DEFAULT_LISTEN_PORT,
    "access_token": "",
    "default_persona_id": "p_xiaojing",
    "allowed_users": [],
    "max_reply_chars": 1200,
    "rate_limit_per_minute": 10,
    "quiet_hours": "",
}

_CQ_RE = re.compile(r"\[CQ:[^\]]+\]")


# ══════════════════════════════════════════════════════════════════════════
# 配置 / 状态（保持原有对外语义）
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
    api_base = str(out.get("api_base") or DEFAULT_API_BASE).strip()
    if api_base and not api_base.startswith(("http://", "https://")):
        api_base = "http://" + api_base
    out["api_base"] = api_base[:300]
    out["listen_port"] = clamp_int(out.get("listen_port"), DEFAULT_LISTEN_PORT, 1, 65535)
    out["access_token"] = str(out.get("access_token") or "")[:200]
    out["default_persona_id"] = str(out.get("default_persona_id") or "")[:64]
    out["allowed_users"] = clean_users(out.get("allowed_users"), digits_only=True)
    out["max_reply_chars"] = clamp_int(out.get("max_reply_chars"), 1200, 100, 4000)
    out["rate_limit_per_minute"] = clamp_int(out.get("rate_limit_per_minute"), 10, 1, 120)
    out["quiet_hours"] = normalize_quiet_hours(out.get("quiet_hours"))
    return out


def load_config() -> Dict[str, Any]:
    """读取配置（access_token 已解密）；文件不存在返回默认关闭配置。"""
    with _lock:
        if not CONFIG_FILE or not os.path.exists(CONFIG_FILE):
            return _normalize({})
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            raw = raw if isinstance(raw, dict) else {}
            raw["access_token"] = crypto.decrypt(str(raw.get("access_token") or ""))
            return _normalize(raw)
        except Exception:
            logger.exception("读取 QQ 配置失败，使用默认关闭配置")
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
        logger.exception("读取 QQ 联系人状态失败")
        return {"users": {}}


def _save_state(state: Dict[str, Any]) -> bool:
    if not STATE_FILE:
        return False
    try:
        return bool(atomic_json_write(STATE_FILE, state, indent=1))
    except Exception:
        logger.exception("写入 QQ 联系人状态失败")
        return False


# ══════════════════════════════════════════════════════════════════════════
# OneBot 事件解析（平台特异的翻译层）
# ══════════════════════════════════════════════════════════════════════════

def _segments(event: Any) -> List[Dict[str, Any]]:
    ev = event if isinstance(event, dict) else {}
    msg = ev.get("message")
    return [s for s in msg if isinstance(s, dict)] if isinstance(msg, list) else []


def extract_text(event: Any) -> str:
    """从 OneBot 事件提取纯文本（兼容 message 数组 / 字符串 / raw_message）。"""
    ev = event if isinstance(event, dict) else {}
    msg = ev.get("message")
    if isinstance(msg, str):
        return _CQ_RE.sub("", msg).strip()
    if isinstance(msg, list):
        parts = []
        for seg in msg:
            if not isinstance(seg, dict):
                continue
            typ = str(seg.get("type") or "")
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            if typ == "text":
                parts.append(str(data.get("text") or ""))
            elif typ == "face":
                parts.append("[表情]")
            elif typ == "at":
                qq = str(data.get("qq") or "")
                parts.append(f"@{qq}" if qq else "@")
        text = "".join(parts).strip()
        if text:
            return text
    return _CQ_RE.sub("", str(ev.get("raw_message") or "")).strip()


def _extract_urls(event: Any, types: Tuple[str, ...], limit: int) -> List[str]:
    out = []
    for seg in _segments(event):
        if str(seg.get("type") or "") not in types:
            continue
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        for key in ("url", "file"):
            val = str(data.get(key) or "").strip()
            if val.startswith(("http://", "https://")):
                out.append(val)
                break
    return out[:limit]


def extract_images(event: Any) -> List[str]:
    """提取图片段的远程 URL（优先 url，其次 file 的 http 地址）。"""
    return _extract_urls(event, ("image",), 5)


def extract_voice(event: Any) -> List[str]:
    """提取语音段的远程 URL。"""
    return _extract_urls(event, ("record", "voice"), 2)


# ══════════════════════════════════════════════════════════════════════════
# 适配器
# ══════════════════════════════════════════════════════════════════════════

class OneBotAdapter(ChannelAdapter):
    """OneBot v11 适配器：入站 HTTP 回调 + 出站 HTTP API。"""

    name = "qq"
    display_name = "QQ 本地"

    def __init__(self) -> None:
        super().__init__()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._listen_error = ""

    def capabilities(self) -> Capability:
        return Capability(private=True, group=False, media=True, voice=True)

    # ── 媒体 ──

    def media_dir(self) -> str:
        if not CONFIG_FILE:
            return ""
        d = os.path.join(os.path.dirname(CONFIG_FILE), "uploads", "qq")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            logger.exception("创建 QQ 媒体目录失败")
            return ""
        return d

    # ── 探活 ──

    def probe(self, cfg: Dict[str, Any]) -> Tuple[bool, str]:
        base = _api_base(cfg)
        if not base:
            return False, "未配置 OneBot API 地址"
        headers = _api_headers(cfg)
        last_err = ""
        for endpoint in ("get_login_info", "get_status"):
            try:
                r = httpx.get(f"{base}/{endpoint}", headers=headers, timeout=5.0)
            except httpx.ConnectError:
                return False, (f"无法连接 OneBot API（{base}）：请确认 NapCat 已启动、"
                               "HTTP API 已开启，且端口与这里一致")
            except httpx.TimeoutException:
                return False, f"连接 OneBot API 超时（{base}）：请检查 NapCat 是否卡住或端口是否正确"
            except Exception as e:
                return False, f"OneBot API 连接异常（{base}）：{e}"
            if r.status_code in (401, 403):
                return False, "OneBot API 鉴权失败：访问令牌与 NapCat 的 API 令牌不一致"
            if r.status_code == 404:
                last_err = "404"
                continue
            if r.status_code >= 400:
                return False, f"OneBot API 返回 HTTP {r.status_code}（{base}）"
            try:
                data = r.json()
            except Exception:
                data = {}
            if isinstance(data, dict):
                if data.get("status") == "failed" or data.get("retcode") not in (None, 0):
                    return False, str(data.get("message") or data.get("wording") or data.get("retcode"))
                info = data.get("data") if isinstance(data.get("data"), dict) else {}
                who = info.get("nickname") or info.get("user_id") or ""
                return True, f"OneBot API 连接正常（{base}）" + (f"：{who}" if who else "")
            return True, f"OneBot API 连接正常（{base}）"
        return False, f"已连接 {base}，但未找到 get_login_info/get_status（HTTP {last_err or '404'}）"

    # ── 出站 ──

    def send(self, msg: OutboundMessage, cfg: Dict[str, Any]) -> Tuple[bool, str]:
        return send_private(msg.user_id, msg.text, cfg)

    # ── 入站翻译 ──

    def parse_event(self, raw: Any) -> Optional[InboundMessage]:
        if not isinstance(raw, dict):
            return None
        if raw.get("post_type") != "message" or raw.get("message_type") != "private":
            return None
        uid = str(raw.get("user_id") or "").strip()
        self_id = str(raw.get("self_id") or "").strip()
        if not uid or (self_id and uid == self_id):
            return None
        return InboundMessage(
            user_id=uid,
            text=extract_text(raw),
            message_id=str(raw.get("message_id") or ""),
            images=extract_images(raw),
            voice_urls=extract_voice(raw),
            event=cb.EV_MESSAGE,
            is_group=False,
            raw=raw,
        )

    # ── 生命周期 ──

    def is_running(self) -> bool:
        return bool(self._server and self._thread and self._thread.is_alive())

    def start(self, cfg: Dict[str, Any]) -> None:
        c = _normalize(cfg)
        global _active_cfg
        with self._lock:
            _active_cfg = c
            self._stop_locked()
            self._listen_error = ""
            if not c.get("enabled"):
                return
            port = int(c.get("listen_port") or DEFAULT_LISTEN_PORT)
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", port), self._make_handler())
            except Exception as e:
                self._listen_error = f"监听 127.0.0.1:{port} 失败：{e}"
                self.set_error(self._listen_error)
                logger.warning(self._listen_error)
                return
            self._server = srv
            th = threading.Thread(target=srv.serve_forever, daemon=True, name="qq-onebot-inbound")
            self._thread = th
            th.start()
            self._connected = True
            logger.info("QQ OneBot 监听已启动：http://127.0.0.1:%s/onebot", port)

    def _stop_locked(self) -> None:
        srv, th = self._server, self._thread
        self._server = None
        self._thread = None
        self._connected = False
        if srv is not None:
            with suppress(Exception):
                srv.shutdown()
            with suppress(Exception):
                srv.server_close()
        if th is not None and th.is_alive():
            with suppress(Exception):
                th.join(timeout=3)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()
            self._listen_error = ""
            self.set_error("")

    def connection_state(self) -> Dict[str, Any]:
        d = super().connection_state()
        d["running"] = self.is_running()
        d["last_error"] = self._listen_error or self._last_error
        return d

    # ── 入站 HTTP 服务器 ──

    def _auth_ok(self, headers: Dict[str, str], token: str) -> bool:
        if not token:
            return True
        got = str(headers.get("Authorization") or headers.get("authorization") or "")
        expected = f"Bearer {token}"
        try:
            return hmac.compare_digest(got.strip(), expected)
        except Exception:
            return False

    def _make_handler(self):
        adapter = self

        class _QQHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _reply(self, code: int, payload: Dict[str, Any]):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                with suppress(Exception):
                    self.wfile.write(data)

            def do_POST(self):
                cfg = get_active_config()
                if not adapter._auth_ok(dict(self.headers.items()),
                                        str(cfg.get("access_token") or "")):
                    self._reply(401, {"ok": False, "error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                except (TypeError, ValueError):
                    length = 0
                if length <= 0 or length > 1_000_000:
                    self._reply(400, {"ok": False, "error": "invalid body"})
                    return
                try:
                    event = json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
                except Exception:
                    self._reply(400, {"ok": False, "error": "invalid json"})
                    return
                # 先回执，避免 OneBot 上报超时；实际处理在后台线程。
                self._reply(200, {"ok": True})
                adapter.dispatch(event)

        return _QQHandler


# ══════════════════════════════════════════════════════════════════════════
# OneBot HTTP API 辅助
# ══════════════════════════════════════════════════════════════════════════

def _api_base(cfg: Dict[str, Any]) -> str:
    base = str(cfg.get("api_base") or DEFAULT_API_BASE).strip().rstrip("/")
    if base and not base.startswith(("http://", "https://")):
        base = "http://" + base
    return base


def _api_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = str(cfg.get("access_token") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def send_private(user_id: Any, text: str, cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    c = cfg or load_config()
    base = _api_base(c)
    if not base:
        return False, "未配置 OneBot API 地址"
    token = str(c.get("access_token") or "")
    headers = _api_headers(c)
    uid: Any = int(user_id) if str(user_id or "").isdigit() else user_id
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(f"{base}/send_private_msg",
                            json={"user_id": uid, "message": str(text or ""), "auto_escape": True},
                            headers=headers)
            if r.status_code in (401, 403) and token:
                # 兼容只认 access_token 查询参数的 OneBot 实现（本地回环，风险可控）。
                r = client.post(f"{base}/send_private_msg",
                                json={"user_id": uid, "message": str(text or ""), "auto_escape": True},
                                headers=headers, params={"access_token": token})
        if r.status_code in (401, 403):
            return False, "OneBot API 鉴权失败：访问令牌与 NapCat 的 API 令牌不一致"
        if r.status_code == 404:
            return False, f"OneBot API 未找到 send_private_msg（{base}）"
        if r.status_code >= 400:
            return False, f"OneBot API 返回 HTTP {r.status_code}（{base}）"
        try:
            data = r.json()
        except Exception:
            data = {}
        if isinstance(data, dict):
            if data.get("status") == "failed" or (data.get("retcode") not in (None, 0)):
                return False, str(data.get("message") or data.get("wording") or data.get("retcode"))
        return True, ""
    except httpx.ConnectError:
        return False, (f"无法连接 OneBot API（{base}）：请确认 NapCat 已启动、"
                       "HTTP API 已开启，且端口与这里一致")
    except httpx.TimeoutException:
        return False, f"连接 OneBot API 超时（{base}）：请检查 NapCat 是否卡住或端口是否正确"
    except Exception as e:
        logger.exception("OneBot 发送失败")
        return False, f"OneBot 发送异常（{base}）：{e}"


def send_test(user_id: Any, text: str = "🐋 鲸语 QQ 通道测试消息") -> Tuple[bool, str]:
    return send_private(user_id, text)


# ══════════════════════════════════════════════════════════════════════════
# 注册与对外接口（保持与原模块签名一致）
# ══════════════════════════════════════════════════════════════════════════

_ADAPTER: Optional[OneBotAdapter] = None


def _ensure_registered() -> OneBotAdapter:
    global _ADAPTER
    with _lock:
        if _ADAPTER is None:
            _ADAPTER = registry.register(
                OneBotAdapter,
                build_runtime(
                    OneBotAdapter,
                    load_cfg=get_active_config,
                    load_state=_load_state,
                    save_state=_save_state,
                    session_prefix="qq",
                ),
                replace=registry.has(OneBotAdapter.name),
            )
        return _ADAPTER


def adapter() -> OneBotAdapter:
    return _ensure_registered()


def _make_handler():
    """向后兼容：旧代码直接取 handler 类（主要用于测试）。"""
    return _ensure_registered()._make_handler()


def _auth_ok(headers: Dict[str, str], token: str) -> bool:
    return _ensure_registered()._auth_ok(headers, token)


def public_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = dict(cfg or load_config())
    token = str(c.pop("access_token", "") or "")
    c["has_token"] = bool(token)
    a = _ensure_registered()
    c["running"] = bool(a.is_running())
    c["last_error"] = a._listen_error or a._last_error
    return c


def save_config(patch: Any) -> Dict[str, Any]:
    """保存配置；access_token 留空表示保持原值，clear_token=True 可清空。"""
    global _active_cfg
    patch = patch if isinstance(patch, dict) else {}
    with _lock:
        old = load_config()
        raw = dict(old)
        for key in _DEFAULTS:
            if key in patch:
                raw[key] = patch[key]
        if patch.get("clear_token"):
            raw["access_token"] = ""
        elif not str(patch.get("access_token") or "").strip():
            raw["access_token"] = old.get("access_token") or ""
        else:
            raw["access_token"] = str(patch.get("access_token")).strip()
        normalized = _normalize(raw)
        to_store = dict(normalized)
        token = str(to_store.get("access_token") or "")
        if token:
            try:
                to_store["access_token"] = crypto.encrypt(token)
            except Exception as e:
                raise ValueError(f"访问令牌加密失败：{e}") from None
        if not CONFIG_FILE:
            raise ValueError("QQ 配置路径未初始化")
        if not atomic_json_write(CONFIG_FILE, to_store, indent=1):
            raise RuntimeError("QQ 配置保存失败")
        _active_cfg = normalized
    return public_config(normalized)


def _context() -> PersonaContext:
    rt = registry.runtime(OneBotAdapter.name)
    if rt is not None:
        return rt.context
    return PersonaContext("qq")


def _command_reply(uid: str, text: str, cfg: Dict[str, Any]) -> Tuple[str, bool]:
    """向后兼容：处理私聊命令；返回 (回复, 是否已处理)。"""
    state = _load_state()
    return _context().command_reply(uid, text, cfg, state, _save_state)


def contacts(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    return _context().list_contacts(_load_state())


def status() -> Dict[str, Any]:
    cfg = load_config()
    data = public_config(cfg)
    data["contacts"] = contacts(cfg)
    return data


def probe(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    return _ensure_registered().probe(cfg or load_config())


def download_media(url: str, kind: str = "image") -> str:
    return _ensure_registered().fetch_media(url, kind)


def start(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按配置启动监听；已启动则先停再起。失败只记录 last_error，不抛给主程序。"""
    a = _ensure_registered()
    c = _normalize(cfg or load_config())
    try:
        a.start(c)
    except Exception as e:
        a.set_error(str(e))
        logger.exception("QQ 通道启动失败")
    return public_config(c)


def stop() -> None:
    _ensure_registered().stop()


def start_if_enabled() -> Dict[str, Any]:
    return start(load_config())


def _handle_event(event: Any) -> None:
    """向后兼容：直接把原生事件交给运行时处理。"""
    a = _ensure_registered()
    rt = registry.runtime(OneBotAdapter.name)
    if rt is None:
        return
    rt.handle_raw(a, event)


def _send_chunks(uid: str, text: str, cfg: Dict[str, Any]) -> None:
    """向后兼容：分片发送（运行时内部已做，此处供外部直接调用）。"""
    for chunk in split_reply(text, int(cfg.get("max_reply_chars") or 1200)):
        ok, err = send_private(uid, chunk, cfg)
        if not ok:
            logger.warning("QQ 回复发送失败（%s）：%s", uid, err)
            break


# 模块导入即注册：让注册表能统一发现全部通道（api_server 只需 import 各模块）
_ensure_registered()


__all__ = [
    "CONFIG_FILE",
    "STATE_FILE",
    "OneBotAdapter",
    "contacts",
    "download_media",
    "extract_images",
    "extract_text",
    "extract_voice",
    "get_active_config",
    "init",
    "load_config",
    "probe",
    "public_config",
    "save_config",
    "send_private",
    "send_test",
    "split_reply",
    "start",
    "start_if_enabled",
    "status",
    "stop",
]
