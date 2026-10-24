"""本地 QQ 陪伴接入（OneBot v11 / NapCat HTTP 模式）。

设计边界：
- **只支持私聊**：`message.private` 才处理，群消息一律忽略；
- **只走本机**：事件监听固定绑定 127.0.0.1，出站 API 默认也是 127.0.0.1；
- **无工具**：陪伴回复走 dialog 模式（persona + 角色记忆），不注入 Agent 工具；
- **每个 QQ 独立会话**：联系人 ↔ 会话 ↔ 角色，记忆按角色隔离；
- **不内置协议/不碰 QQ 账号**：由 NapCat 等 OneBot 实现负责登录，本模块只收发标准事件。

NapCat 侧配置（任选 HTTP 模式）：
1. 开启 HTTP API（默认 `http://127.0.0.1:3000`）；
2. 开启 HTTP POST 事件，目标填本程序的监听地址（默认 `http://127.0.0.1:8756/onebot`）；
3. 若本程序配置了访问令牌，NapCat 的 API / 上报令牌填同一个值。
"""
from __future__ import annotations

import hmac
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

import crypto
from persistence import atomic_json_write

logger = logging.getLogger("whaletalk.qq")

CONFIG_FILE: Optional[str] = None
STATE_FILE: Optional[str] = None
DEFAULT_API_BASE = "http://127.0.0.1:3000"
DEFAULT_LISTEN_PORT = 8756

_lock = threading.RLock()
_state_lock = threading.RLock()
_user_locks: Dict[str, threading.Lock] = {}
_user_locks_guard = threading.Lock()
_server: Optional[ThreadingHTTPServer] = None
_thread: Optional[threading.Thread] = None
_listen_error = ""
_active_cfg: Optional[Dict[str, Any]] = None
_seen: "OrderedDict[str, bool]" = OrderedDict()
_seen_lock = threading.Lock()
_rate: Dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()

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
    api_base = str(out.get("api_base") or DEFAULT_API_BASE).strip()
    if api_base and not api_base.startswith(("http://", "https://")):
        api_base = "http://" + api_base
    out["api_base"] = api_base[:300]
    try:
        out["listen_port"] = max(1, min(65535, int(out.get("listen_port") or DEFAULT_LISTEN_PORT)))
    except (TypeError, ValueError):
        out["listen_port"] = DEFAULT_LISTEN_PORT
    out["access_token"] = str(out.get("access_token") or "")[:200]
    out["default_persona_id"] = str(out.get("default_persona_id") or "")[:64]
    users = out.get("allowed_users")
    if isinstance(users, str):
        users = re.split(r"[\s,，]+", users)
    out["allowed_users"] = [str(x).strip() for x in (users or []) if str(x).strip().isdigit()][:200]
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


def public_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = dict(cfg or load_config())
    token = str(c.pop("access_token", "") or "")
    c["has_token"] = bool(token)
    c["running"] = bool(_server and _thread and _thread.is_alive())
    c["last_error"] = _listen_error
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


def _new_session_id(uid: str) -> str:
    return f"qq_{uid}_{int(time.time())}_{secrets.token_hex(2)}"[:64]


def _user_lock(uid: str) -> threading.Lock:
    with _user_locks_guard:
        lk = _user_locks.get(uid)
        if lk is None:
            lk = threading.Lock()
            _user_locks[uid] = lk
        return lk


def _user_entry(state: Dict[str, Any], uid: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    users = state.setdefault("users", {})
    entry = users.get(uid)
    if not isinstance(entry, dict):
        entry = {}
        users[uid] = entry
    pid = str(entry.get("persona_id") or "")
    try:
        import personas
        if not pid or not personas.get_persona(pid):
            entry["persona_id"] = _default_persona_id(cfg)
    except Exception:
        entry["persona_id"] = pid
    if not str(entry.get("session_id") or ""):
        entry["session_id"] = _new_session_id(uid)
    return entry


def contacts(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    c = cfg or load_config()
    state = _load_state()
    out = []
    for uid, info in (state.get("users") or {}).items():
        if not isinstance(info, dict):
            continue
        pid = str(info.get("persona_id") or "")
        out.append({
            "user_id": str(uid),
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


# ── OneBot 事件解析 ──────────────────────────────────────────────────────

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


def _segments(event: Any) -> List[Dict[str, Any]]:
    ev = event if isinstance(event, dict) else {}
    msg = ev.get("message")
    return [s for s in msg if isinstance(s, dict)] if isinstance(msg, list) else []


def extract_images(event: Any) -> List[str]:
    """提取图片段的远程 URL（优先 url，其次 file 的 http 地址）。"""
    out = []
    for seg in _segments(event):
        if str(seg.get("type") or "") != "image":
            continue
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        for key in ("url", "file"):
            val = str(data.get(key) or "").strip()
            if val.startswith(("http://", "https://")):
                out.append(val)
                break
    return out[:5]


def extract_voice(event: Any) -> List[str]:
    """提取语音段的远程 URL。"""
    out = []
    for seg in _segments(event):
        if str(seg.get("type") or "") not in ("record", "voice"):
            continue
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        for key in ("url", "file"):
            val = str(data.get(key) or "").strip()
            if val.startswith(("http://", "https://")):
                out.append(val)
                break
    return out[:2]


def _media_dir() -> str:
    if not CONFIG_FILE:
        return ""
    d = os.path.join(os.path.dirname(CONFIG_FILE), "uploads", "qq")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        logger.exception("创建 QQ 媒体目录失败")
        return ""
    return d


def download_media(url: str, kind: str = "image") -> str:
    """下载 OneBot 媒体到本地（SSRF 校验 + 大小上限），返回本地路径。"""
    u = str(url or "").strip()
    if not u.startswith(("http://", "https://")):
        return ""
    try:
        from security import _safe_url
        reason = _safe_url(u)
        if reason:
            logger.warning("QQ 媒体 URL 被安全策略拒绝：%s", reason)
            return ""
    except Exception:
        logger.exception("QQ 媒体 URL 安全校验失败")
        return ""
    d = _media_dir()
    if not d:
        return ""
    is_image = kind == "image"
    max_bytes = 12 * 1024 * 1024 if is_image else 20 * 1024 * 1024
    ext = os.path.splitext(urlparse(u).path)[1].lower()
    allowed = (".jpg", ".jpeg", ".png", ".gif", ".webp") if is_image else (
        ".amr", ".silk", ".mp3", ".wav", ".ogg", ".m4a", ".flac")
    if ext not in allowed:
        ext = ".jpg" if is_image else ".amr"
    path = os.path.join(d, f"{int(time.time() * 1000)}_{secrets.token_hex(3)}{ext}")
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            with client.stream("GET", u) as r:
                if r.status_code >= 400:
                    return ""
                try:
                    size = int(r.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    size = 0
                if size > max_bytes:
                    logger.warning("QQ 媒体超过大小上限（%s bytes）", size)
                    return ""
                ctype = str(r.headers.get("Content-Type") or "").lower()
                if is_image and ctype and not ctype.startswith(("image/", "application/octet-stream")):
                    logger.warning("QQ 图片 Content-Type 异常：%s", ctype)
                    return ""
                total = 0
                with open(path, "wb") as f:
                    for chunk in r.iter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("媒体超过大小上限")
                        f.write(chunk)
        return path if total > 0 else ""
    except Exception:
        logger.exception("QQ 媒体下载失败")
        with suppress(Exception):
            os.remove(path)
        return ""


def split_reply(text: str, max_chars: int = 1200) -> List[str]:
    """按段落/行切分长回复，避免单条消息过长。"""
    s = str(text or "").strip()
    if not s:
        return []
    cap = max(100, min(4000, int(max_chars or 1200)))
    out, buf = [], ""
    for line in s.splitlines(keepends=True):
        while len(line) > cap:
            if buf:
                out.append(buf)
                buf = ""
            out.append(line[:cap])
            line = line[cap:]
        if len(buf) + len(line) > cap and buf:
            out.append(buf)
            buf = ""
        buf += line
    if buf.strip():
        out.append(buf)
    return [x.strip() for x in out if x.strip()]


def _mark_seen(message_id: str) -> bool:
    mid = str(message_id or "")
    if not mid:
        return True
    with _seen_lock:
        if mid in _seen:
            return False
        _seen[mid] = True
        while len(_seen) > 1000:
            _seen.popitem(last=False)
    return True


def _rate_ok(uid: str, limit: int) -> bool:
    now = time.time()
    with _rate_lock:
        dq = _rate[uid]
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


def _command_reply(uid: str, text: str, cfg: Dict[str, Any]) -> Tuple[str, bool]:
    """处理私聊命令；返回 (回复, 是否已处理)。"""
    cmd, arg = _command(text)
    if not cmd:
        return "", False
    state = _load_state()
    entry = _user_entry(state, uid, cfg)
    if cmd in ("/help", "/帮助", "/start"):
        current = _persona_name(str(entry.get("persona_id") or ""))
        return (f"可用命令：\n/角色 <名字> 切换角色\n/重置 重新开始对话\n/记忆 查看当前角色记忆条数\n/帮助 显示本说明\n当前角色：{current or '未设置'}"), True
    if cmd in ("/reset", "/重置", "/重置对话", "/new"):
        entry["session_id"] = _new_session_id(uid)
        entry["last_seen"] = _now()
        _save_state(state)
        return "好，我们重新开始。你想聊点什么？", True
    if cmd in ("/persona", "/角色", "/role"):
        if not arg:
            items = _personas_brief()
            lines = ["可用角色："] + [f"- {p['name']}（{p['id']}）" for p in items]
            return "\n".join(lines), True
        p = _find_persona(arg)
        if not p:
            return f"没有找到角色「{arg}」。用 /角色 查看可用角色。", True
        entry["persona_id"] = p["id"]
        entry["session_id"] = _new_session_id(uid)
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


# ── OneBot 出站 ──────────────────────────────────────────────────────────

def send_private(user_id: Any, text: str, cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    c = cfg or load_config()
    base = str(c.get("api_base") or DEFAULT_API_BASE).rstrip("/")
    if not base:
        return False, "未配置 OneBot API 地址"
    token = str(c.get("access_token") or "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}"
        try:
            data = r.json()
        except Exception:
            data = {}
        if isinstance(data, dict):
            if data.get("status") == "failed" or (data.get("retcode") not in (None, 0)):
                return False, str(data.get("message") or data.get("wording") or data.get("retcode"))
        return True, ""
    except Exception as e:
        logger.exception("OneBot 发送失败")
        return False, str(e)


def send_test(user_id: Any, text: str = "🐋 鲸语 QQ 通道测试消息") -> Tuple[bool, str]:
    return send_private(user_id, text)


# ── 入站服务器 ────────────────────────────────────────────────────────────

def _auth_ok(headers: Dict[str, str], token: str) -> bool:
    if not token:
        return True
    got = str(headers.get("Authorization") or headers.get("authorization") or "")
    expected = f"Bearer {token}"
    try:
        return hmac.compare_digest(got.strip(), expected)
    except Exception:
        return False


def _make_handler():
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
            if not _auth_ok(dict(self.headers.items()), str(cfg.get("access_token") or "")):
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
            threading.Thread(target=_handle_event, args=(event,), daemon=True).start()

    return _QQHandler


def start(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按配置启动监听；已启动则先停再起。失败只记录 last_error，不抛给主程序。"""
    global _server, _thread, _listen_error, _active_cfg
    c = _normalize(cfg or load_config())
    with _lock:
        _active_cfg = c
        _stop_locked()
        _listen_error = ""
        if not c.get("enabled"):
            return public_config(c)
        port = int(c.get("listen_port") or DEFAULT_LISTEN_PORT)
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), _make_handler())
        except Exception as e:
            _listen_error = f"监听 127.0.0.1:{port} 失败：{e}"
            logger.warning(_listen_error)
            return public_config(c)
        _server = srv
        th = threading.Thread(target=srv.serve_forever, daemon=True, name="qq-onebot-inbound")
        _thread = th
        th.start()
        logger.info("QQ OneBot 监听已启动：http://127.0.0.1:%s/onebot", port)
    return public_config(c)


def _stop_locked() -> None:
    global _server, _thread
    srv, th = _server, _thread
    _server = None
    _thread = None
    if srv is not None:
        with suppress(Exception):
            srv.shutdown()
        with suppress(Exception):
            srv.server_close()
    if th is not None and th.is_alive():
        with suppress(Exception):
            th.join(timeout=3)


def stop() -> None:
    global _listen_error
    with _lock:
        _stop_locked()
        _listen_error = ""


def start_if_enabled() -> Dict[str, Any]:
    try:
        return start(load_config())
    except Exception as e:
        global _listen_error
        _listen_error = str(e)
        logger.exception("QQ 通道启动失败")
        return public_config()


def _handle_event(event: Any) -> None:
    if not isinstance(event, dict):
        return
    cfg = get_active_config()
    if not cfg.get("enabled"):
        return
    if event.get("post_type") != "message" or event.get("message_type") != "private":
        return
    uid = str(event.get("user_id") or "").strip()
    self_id = str(event.get("self_id") or "").strip()
    if not uid or (self_id and uid == self_id):
        return
    allowed = cfg.get("allowed_users") or []
    if allowed and uid not in allowed:
        return
    if not _mark_seen(str(event.get("message_id") or "")):
        return
    if not _rate_ok(uid, int(cfg.get("rate_limit_per_minute") or 10)):
        logger.warning("QQ 用户 %s 触发限流，已忽略", uid)
        return
    text = extract_text(event)
    image_urls = extract_images(event)
    voice_urls = extract_voice(event)
    if not text and not image_urls and not voice_urls:
        return

    if _command(text)[0]:
        try:
            reply, handled = _command_reply(uid, text, cfg)
        except Exception:
            logger.exception("QQ 命令处理失败")
            return
        if handled:
            _send_chunks(uid, reply, cfg)
            return

    if _in_quiet_hours(cfg):
        return

    lk = _user_lock(uid)
    if not lk.acquire(blocking=False):
        # 同一联系人上一条还在生成：不排队刷屏，直接跳过。
        return
    try:
        images = [p for p in (download_media(u, "image") for u in image_urls) if p]
        if not text and voice_urls:
            voice_path = download_media(voice_urls[0], "voice")
            if voice_path:
                try:
                    import deepseek_client as dc
                    tr = str(dc.speech_to_text(voice_path, "base") or "").strip()
                    if tr and not tr.startswith(("错误", "语音识别失败", "音频文件不存在", "语音需要", "未识别")):
                        text = tr
                    else:
                        text = "（用户发来一条语音，但当前本地转写不可用）"
                except Exception:
                    logger.exception("QQ 语音转写失败")
                    text = "（用户发来一条语音，但当前本地转写不可用）"
            else:
                text = "（用户发来一条语音，但下载失败）"
        if not text:
            text = "看看这张图片" if images else "（用户发来一张图片，但下载失败）"

        state = _load_state()
        entry = _user_entry(state, uid, cfg)
        pid = str(entry.get("persona_id") or "")
        sid = str(entry.get("session_id") or "")
        try:
            import api_server as srv
            reply = srv._companion_reply(text, sid, pid, images=images)
        except Exception:
            logger.exception("QQ 陪伴回复失败")
            reply = "（我这边刚才走神了一下，稍后再跟我说一句？）"
        entry["last_seen"] = _now()
        _save_state(state)
        _send_chunks(uid, reply, cfg)
    finally:
        with suppress(Exception):
            lk.release()


def _send_chunks(uid: str, text: str, cfg: Dict[str, Any]) -> None:
    for chunk in split_reply(text, int(cfg.get("max_reply_chars") or 1200)):
        ok, err = send_private(uid, chunk, cfg)
        if not ok:
            logger.warning("QQ 回复发送失败（%s）：%s", uid, err)
            break


__all__ = [
    "CONFIG_FILE",
    "STATE_FILE",
    "contacts",
    "download_media",
    "extract_images",
    "extract_text",
    "extract_voice",
    "get_active_config",
    "init",
    "load_config",
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
