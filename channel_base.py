"""通道抽象层：AI 角色接入任意平台的统一地基。

设计目标
--------
鲸语要让「大肥鱼」同时存在于 QQ、Telegram、抖音直播、网站客服……这些平台
**形态差异极大**（消息驱动 vs 状态驱动、私聊 vs 公开弹幕、文本 vs 礼物事件），
但下面这些事情与平台无关，不应该每个平台重写一遍：

    - 联系人 → 会话 → 角色的映射与记忆边界
    - 限流、去重、静默时段
    - 命令解析（/角色 /重置 /记忆 /帮助）
    - 长回复分片
    - 媒体下载（SSRF 校验 + 大小上限）
    - 回复生成（接 api_server._companion_reply）

本模块把这部分收敛为 `ChannelAdapter`（传输层契约）与 `ChannelRuntime`
（与平台无关的事件管线）。**新增一个平台 = 实现一个 Adapter 子类 + 注册**，
共享层一行不改。

三层结构
--------
    ChannelAdapter   抽象基类：只管「收消息」「发消息」「探活」「启停」
         ↑
    ChannelRuntime   与平台无关：身份→角色→限流→命令→回复→分片发送
         ↑
    ChannelRegistry  注册表：按名字取通道，统一启停与状态汇总

平台差异用「能力声明」表达，而不是 if 分支：

    adapter.capabilities() -> Capability(
        private=True, group=False, media=True, voice=True,
        streaming=False, gifts=False, reactions=False, ...
    )

直播这类「状态驱动」平台未来可以声明 `gifts=True / streaming=True`，由
`ChannelRuntime.on_event` 的事件类型分发处理，仍然复用本模块的会话与记忆。
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict, defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("whaletalk.channel")

_QUIET_RE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


# ══════════════════════════════════════════════════════════════════════════
# 能力声明：平台的差异必须被建模，而不是散落成 if 分支
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Capability:
    """一个平台「能干什么」。共享层据此决定走哪条处理路径。

    新增平台时如实声明，而不是在共享层加 if：

        QQ 私聊      Capability(private=True, media=True, voice=True)
        Telegram     Capability(private=True, group=True, media=True)
        抖音直播      Capability(group=True, gifts=True, streaming=True, public=True)
        网站客服      Capability(private=True, media=True, streaming=True)
    """

    private: bool = True      # 支持点对点私聊
    group: bool = False       # 支持群聊 / 多人场合
    media: bool = True        # 支持接收图片等媒体
    voice: bool = True        # 支持语音（接收或转写）
    streaming: bool = False   # 支持持续状态（直播、在线客服会话）
    gifts: bool = False       # 支持礼物 / 打赏事件（直播）
    public: bool = False      # 输出对所有人可见（弹幕、群发）
    reactions: bool = False   # 支持表情回应等轻量交互

    def as_dict(self) -> Dict[str, bool]:
        return {
            "private": self.private,
            "group": self.group,
            "media": self.media,
            "voice": self.voice,
            "streaming": self.streaming,
            "gifts": self.gifts,
            "public": self.public,
            "reactions": self.reactions,
        }


# ══════════════════════════════════════════════════════════════════════════
# 统一事件模型：各平台把原生事件翻译成它
# ══════════════════════════════════════════════════════════════════════════

# 事件类型常量。消息类目前够用，直播类预留但由适配器自行声明能力。
EV_MESSAGE = "message"
EV_GIFT = "gift"
EV_JOIN = "join"
EV_LEAVE = "leave"


@dataclass
class InboundMessage:
    """平台原生事件 → 归一化后的入站消息。

    适配器只负责填这个结构，之后的限流/去重/命令/回复全由 ChannelRuntime 处理。
    """

    user_id: str                            # 平台用户标识（QQ 号 / openid / uid）
    text: str = ""
    message_id: str = ""                    # 用于去重；空则不去重
    images: List[str] = field(default_factory=list)   # 远程 URL 或本地路径
    voice_urls: List[str] = field(default_factory=list)
    event: str = EV_MESSAGE                 # EV_MESSAGE / EV_GIFT / ...
    is_group: bool = False
    group_id: str = ""
    raw: Any = None                         # 保留原始事件，便于适配器回填
    extra: Dict[str, Any] = field(default_factory=dict)

    def has_content(self) -> bool:
        return bool(self.text or self.images or self.voice_urls)


@dataclass
class OutboundMessage:
    """归一化后的出站内容（分片由运行时负责）。"""

    user_id: str
    text: str
    group_id: str = ""
    reply_to: str = ""      # 被动回复所需的原消息 ID（QQ 官方 Bot 用）
    extra: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
# 共享基础设施（原 qq_onebot / qq_official 各自一份的那 148 行）
# ══════════════════════════════════════════════════════════════════════════

class Deduper:
    """消息去重（有界 LRU）。线程安全。"""

    def __init__(self, capacity: int = 1000) -> None:
        self._seen: "OrderedDict[str, bool]" = OrderedDict()
        self._lock = threading.Lock()
        self._capacity = max(1, int(capacity))

    def first_time(self, message_id: str) -> bool:
        """首次见到返回 True；重复或空 ID 返回 True（空 ID 视为不去重）。"""
        mid = str(message_id or "")
        if not mid:
            return True
        with self._lock:
            if mid in self._seen:
                return False
            self._seen[mid] = True
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
        return True


class RateLimiter:
    """按用户滑动窗口限流。线程安全。"""

    def __init__(self, window: float = 60.0) -> None:
        self._rate: Dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        self._window = float(window)

    def allow(self, uid: str, limit: int) -> bool:
        """允许则占用一个配额并返回 True。"""
        now = time.time()
        with self._lock:
            dq = self._rate[uid]
            while dq and now - dq[0] > self._window:
                dq.popleft()
            if len(dq) >= max(1, int(limit)):
                return False
            dq.append(now)
        return True


def in_quiet_hours(quiet_hours: str, now: Optional[datetime] = None) -> bool:
    """静默时段判定，支持跨零点（如 23:00-08:00）。"""
    m = _QUIET_RE.match(str(quiet_hours or ""))
    if not m:
        return False
    sh, sm, eh, em = (int(x) for x in m.groups())
    cur_dt = now or datetime.now()
    cur = cur_dt.hour * 60 + cur_dt.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False
    return start <= cur < end if start < end else (cur >= start or cur < end)


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


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_session_id(prefix: str, uid: str) -> str:
    return f"{prefix}_{uid}_{int(time.time())}_{secrets.token_hex(2)}"[:64]


def clean_users(value: Any, *, digits_only: bool = False) -> List[str]:
    """白名单清洗：接受字符串（逗号/空格/中文逗号分隔）或列表。"""
    users = value
    if isinstance(users, str):
        users = re.split(r"[\s,，]+", users)
    out = [str(x).strip() for x in (users or []) if str(x).strip()]
    if digits_only:
        out = [x for x in out if x.isdigit()]
    return out[:200]


def clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value or default)))
    except (TypeError, ValueError):
        return default


def normalize_quiet_hours(value: Any) -> str:
    quiet = str(value or "").strip()
    return quiet if _QUIET_RE.match(quiet) else ""


# ══════════════════════════════════════════════════════════════════════════
# 角色上下文服务：联系人 ↔ 会话 ↔ 角色
# ══════════════════════════════════════════════════════════════════════════

class PersonaContext:
    """联系人状态存取 + 角色解析 + 命令处理。与平台完全无关。

    子类化只为改 session_id 前缀（qq_ / qqo_ / tg_ ...），保持既有会话 ID 兼容。
    """

    def __init__(self, session_prefix: str = "ch") -> None:
        self.session_prefix = session_prefix

    # ── 角色解析 ──

    def resolve_default_persona(self, cfg: Dict[str, Any]) -> str:
        try:
            import personas
            items = personas.list_personas()
        except Exception:
            return ""
        if not items:
            return ""
        wanted = str(cfg.get("default_persona_id") or "")
        if wanted and any(p["id"] == wanted for p in items):
            return wanted
        return items[0]["id"]

    def persona_name(self, pid: str) -> str:
        try:
            import personas
            p = personas.get_persona(pid)
            return str((p or {}).get("name") or pid)
        except Exception:
            return pid

    def personas_brief(self) -> List[Dict[str, str]]:
        try:
            import personas
            return [{"id": p["id"], "name": p["name"]} for p in personas.list_personas()]
        except Exception:
            return []

    def find_persona(self, query: str) -> Optional[Dict[str, Any]]:
        try:
            import personas
            items = personas.list_personas()
        except Exception:
            return None
        q = str(query or "").strip()
        if not q:
            return None
        for p in items:
            if p["id"] == q:
                return p
        for p in items:
            if p["name"] == q:
                return p
        hits = [p for p in items if q.lower() in p["name"].lower()]
        return hits[0] if len(hits) == 1 else None

    def new_session(self, uid: str) -> str:
        return new_session_id(self.session_prefix, uid)

    # ── 联系人状态 ──

    def entry(self, state: Dict[str, Any], uid: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
        users = state.setdefault("users", {})
        entry = users.get(uid)
        if not isinstance(entry, dict):
            entry = {}
            users[uid] = entry
        pid = str(entry.get("persona_id") or "")
        try:
            import personas
            if not pid or not personas.get_persona(pid):
                entry["persona_id"] = self.resolve_default_persona(cfg)
        except Exception:
            entry["persona_id"] = pid
        if not str(entry.get("session_id") or ""):
            entry["session_id"] = self.new_session(uid)
        return entry

    def list_contacts(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for uid, info in (state.get("users") or {}).items():
            if not isinstance(info, dict):
                continue
            pid = str(info.get("persona_id") or "")
            out.append({
                "user_id": str(uid),
                "persona_id": pid,
                "persona_name": self.persona_name(pid),
                "session_id": str(info.get("session_id") or ""),
                "last_seen": str(info.get("last_seen") or ""),
            })
        out.sort(key=lambda x: x.get("last_seen") or "", reverse=True)
        return out[:100]

    # ── 命令 ──

    @staticmethod
    def parse_command(text: str) -> Tuple[str, str]:
        s = str(text or "").strip()
        if not s.startswith("/"):
            return "", ""
        parts = s.split(maxsplit=1)
        return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

    def command_reply(
        self,
        uid: str,
        text: str,
        cfg: Dict[str, Any],
        state: Dict[str, Any],
        save_state: Callable[[Dict[str, Any]], Any],
    ) -> Tuple[str, bool]:
        """处理私聊命令；返回 (回复, 是否已处理)。

        命令集与平台无关，故收敛在此。适配器只提供 state 的读写。
        """
        cmd, arg = self.parse_command(text)
        if not cmd:
            return "", False
        entry = self.entry(state, uid, cfg)
        if cmd in ("/help", "/帮助", "/start"):
            current = self.persona_name(str(entry.get("persona_id") or ""))
            return (
                "可用命令：\n"
                "/角色 <名字> 切换角色\n"
                "/重置 重新开始对话\n"
                "/记忆 查看当前角色记忆条数\n"
                "/帮助 显示本说明\n"
                f"当前角色：{current or '未设置'}"
            ), True
        if cmd in ("/reset", "/重置", "/重置对话", "/new"):
            entry["session_id"] = self.new_session(uid)
            entry["last_seen"] = now_iso()
            save_state(state)
            return "好，我们重新开始。你想聊点什么？", True
        if cmd in ("/persona", "/角色", "/role"):
            if not arg:
                items = self.personas_brief()
                lines = ["可用角色："] + [f"- {p['name']}（{p['id']}）" for p in items]
                return "\n".join(lines), True
            p = self.find_persona(arg)
            if not p:
                return f"没有找到角色「{arg}」。用 /角色 查看可用角色。", True
            entry["persona_id"] = p["id"]
            entry["session_id"] = self.new_session(uid)
            entry["last_seen"] = now_iso()
            save_state(state)
            return f"好，接下来由「{p['name']}」陪你聊。", True
        if cmd in ("/memory", "/记忆"):
            try:
                import persona_memory
                n = persona_memory.count(str(entry.get("persona_id") or ""))
                name = self.persona_name(str(entry.get("persona_id") or ""))
                return f"「{name}」目前记住了 {n} 条关于你的事。", True
            except Exception:
                return "角色记忆暂时不可用。", True
        return "", False


# ══════════════════════════════════════════════════════════════════════════
# 传输层契约
# ══════════════════════════════════════════════════════════════════════════

class ChannelAdapter(ABC):
    """一个平台的适配器。**只实现传输差异**，业务逻辑交给 ChannelRuntime。

    必须实现：
        name / display_name / capabilities
        send()        发一条消息
        start()/stop() 启停
    可选覆盖：
        probe()       连通性自检
        parse_event() 原生事件 → InboundMessage
        fetch_media() 下载媒体（默认走共享的 SSRF 安全下载）
        public_config() 脱敏后的配置（前端用）
    """

    name: str = "channel"
    display_name: str = "通道"

    def __init__(self) -> None:
        self.runtime: Optional["ChannelRuntime"] = None
        self._last_error = ""
        self._connected = False
        self._bot_info = ""
        self._lock = threading.RLock()

    # ── 元信息 ──

    @abstractmethod
    def capabilities(self) -> Capability:
        ...

    # ── 生命周期 ──

    @abstractmethod
    def start(self, cfg: Dict[str, Any]) -> None:
        """按配置启动。失败只记录 self._last_error，不向上抛。"""
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    def is_running(self) -> bool:
        return False

    # ── 收 / 发 ──

    @abstractmethod
    def send(self, msg: OutboundMessage, cfg: Dict[str, Any]) -> Tuple[bool, str]:
        """发送一条已分片的消息。返回 (成功, 错误信息)。"""
        ...

    def parse_event(self, raw: Any) -> Optional[InboundMessage]:
        """平台原生事件 → InboundMessage。返回 None 表示不处理。"""
        return None

    def probe(self, cfg: Dict[str, Any]) -> Tuple[bool, str]:
        """连通性自检（不发送消息）。"""
        return False, "该通道未实现探活"

    def fetch_media(self, url: str, kind: str = "image") -> str:
        """下载媒体到本地。默认实现：SSRF 校验 + 大小上限。"""
        return _safe_download(url, kind, self.media_dir())

    def media_dir(self) -> str:
        return ""

    # ── 状态 ──

    def set_error(self, err: str) -> None:
        self._last_error = str(err or "")

    def connection_state(self) -> Dict[str, Any]:
        return {
            "running": self.is_running(),
            "connected": bool(self._connected),
            "bot_info": self._bot_info,
            "last_error": self._last_error,
        }

    # ── 入站调度（适配器收到原生事件后调用）──

    def dispatch(self, raw: Any) -> None:
        """适配器把原生事件交给运行时处理（自动做后台线程）。"""
        rt = self.runtime
        if rt is None:
            return
        threading.Thread(
            target=rt.handle_raw, args=(self, raw), daemon=True,
            name=f"ch-{self.name}",
        ).start()


# ══════════════════════════════════════════════════════════════════════════
# 运行时：与平台无关的事件管线
# ══════════════════════════════════════════════════════════════════════════

class ChannelRuntime:
    """把「收到一条消息」到「回复发回去」的整条链路做成与平台无关的实现。

    适配器只需把原生事件翻译成 InboundMessage，剩下的身份解析、白名单、
    去重、限流、命令、静默时段、角色绑定、回复生成、分片发送全部在此完成。
    """

    def __init__(
        self,
        adapter: ChannelAdapter,
        *,
        load_cfg: Callable[[], Dict[str, Any]],
        load_state: Callable[[], Dict[str, Any]],
        save_state: Callable[[Dict[str, Any]], Any],
        context: Optional[PersonaContext] = None,
        reply_fn: Optional[Callable[..., str]] = None,
    ) -> None:
        self.adapter = adapter
        self.load_cfg = load_cfg
        self.load_state = load_state
        self.save_state = save_state
        self.context = context or PersonaContext(adapter.name)
        self._reply_fn = reply_fn
        self._deduper = Deduper()
        self._limiter = RateLimiter()
        self._user_locks: Dict[str, threading.Lock] = {}
        self._user_locks_guard = threading.Lock()
        adapter.runtime = self

    # ── 回复生成 ──

    def _companion(self, text: str, session_id: str, persona_id: str,
                   images: Optional[List[str]] = None) -> str:
        if self._reply_fn is not None:
            return self._reply_fn(text, session_id, persona_id, images=images)
        try:
            import api_server as srv
            return srv._companion_reply(text, session_id, persona_id, images=images)
        except Exception:
            logger.exception("[%s] 陪伴回复失败", self.adapter.name)
            return "（我这边刚才走神了一下，稍后再跟我说一句？）"

    def _user_lock(self, uid: str) -> threading.Lock:
        with self._user_locks_guard:
            lk = self._user_locks.get(uid)
            if lk is None:
                lk = threading.Lock()
                self._user_locks[uid] = lk
            return lk

    # ── 主链路 ──

    def handle_raw(self, adapter: ChannelAdapter, raw: Any) -> None:
        try:
            msg = adapter.parse_event(raw)
        except Exception:
            logger.exception("[%s] 事件解析失败", adapter.name)
            return
        if msg is None:
            return
        self.handle_message(adapter, msg)

    def handle_message(self, adapter: ChannelAdapter, msg: InboundMessage) -> None:
        cfg = self.load_cfg()
        if not cfg.get("enabled"):
            return
        uid = str(msg.user_id or "").strip()
        if not uid:
            return

        # 1) 白名单
        allowed = cfg.get("allowed_users") or []
        if allowed and uid not in allowed:
            return

        # 2) 去重
        if not self._deduper.first_time(msg.message_id):
            return

        # 3) 限流
        if not self._limiter.allow(uid, int(cfg.get("rate_limit_per_minute") or 10)):
            logger.warning("[%s] 用户 %s 触发限流，已忽略", adapter.name, uid[:12])
            return

        # 4) 空内容
        if not msg.has_content():
            return

        # 5) 命令（不受静默时段限制）
        if self.context.parse_command(msg.text)[0]:
            state = self.load_state()
            try:
                reply, handled = self.context.command_reply(
                    uid, msg.text, cfg, state, self.save_state)
            except Exception:
                logger.exception("[%s] 命令处理失败", adapter.name)
                return
            if handled:
                self._send(adapter, uid, reply, cfg, msg)
                return

        # 6) 静默时段
        if in_quiet_hours(str(cfg.get("quiet_hours") or "")):
            return

        # 7) 生成回复（同一联系人串行，不排队刷屏）
        lk = self._user_lock(uid)
        if not lk.acquire(blocking=False):
            return
        try:
            text, images = self._resolve_content(adapter, msg)
            state = self.load_state()
            entry = self.context.entry(state, uid, cfg)
            pid = str(entry.get("persona_id") or "")
            sid = str(entry.get("session_id") or "")
            reply = self._companion(text, sid, pid, images=images)
            entry["last_seen"] = now_iso()
            self.save_state(state)
            self._send(adapter, uid, reply, cfg, msg)
        finally:
            with suppress(Exception):
                lk.release()

    def _resolve_content(self, adapter: ChannelAdapter,
                         msg: InboundMessage) -> Tuple[str, List[str]]:
        """把媒体/语音解析成可交给模型的文本 + 本地图片路径。"""
        images = [p for p in (adapter.fetch_media(u, "image") for u in msg.images) if p]
        text = msg.text
        if not text and msg.voice_urls:
            voice_path = adapter.fetch_media(msg.voice_urls[0], "voice")
            if voice_path:
                try:
                    import deepseek_client as dc
                    tr = str(dc.speech_to_text(voice_path, "base") or "").strip()
                    if tr and not tr.startswith(
                        ("错误", "语音识别失败", "音频文件不存在", "语音需要", "未识别")
                    ):
                        text = tr
                    else:
                        text = "（用户发来一条语音，但当前本地转写不可用）"
                except Exception:
                    logger.exception("[%s] 语音转写失败", adapter.name)
                    text = "（用户发来一条语音，但当前本地转写不可用）"
            else:
                text = "（用户发来一条语音，但下载失败）"
        if not text:
            text = "看看这张图片" if images else "（用户发来一条消息，但下载失败）"
        return text, images

    def _send(self, adapter: ChannelAdapter, uid: str, text: str,
              cfg: Dict[str, Any], inbound: InboundMessage) -> None:
        cap = int(cfg.get("max_reply_chars") or 1200)
        for chunk in split_reply(text, cap):
            out = OutboundMessage(
                user_id=uid,
                text=chunk,
                group_id=inbound.group_id or "",
                reply_to=inbound.message_id or "",
            )
            try:
                ok, err = adapter.send(out, cfg)
            except Exception as e:
                logger.exception("[%s] 发送异常", adapter.name)
                ok, err = False, str(e)
            if not ok:
                logger.warning("[%s] 回复发送失败（%s）：%s", adapter.name, uid[:12], err)
                break


# ══════════════════════════════════════════════════════════════════════════
# 共享媒体下载（SSRF 校验 + 大小上限）
# ══════════════════════════════════════════════════════════════════════════

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
_VOICE_EXTS = (".amr", ".silk", ".mp3", ".wav", ".ogg", ".m4a", ".flac")


def _safe_download(url: str, kind: str, dest_dir: str) -> str:
    """下载媒体到本地。任何一步失败都返回空串，绝不抛给调用方。"""
    import os
    from urllib.parse import urlparse

    u = str(url or "").strip()
    if not u.startswith(("http://", "https://")) or not dest_dir:
        return ""
    try:
        from security import _safe_url
        reason = _safe_url(u)
        if reason:
            logger.warning("媒体 URL 被安全策略拒绝：%s", reason)
            return ""
    except Exception:
        logger.exception("媒体 URL 安全校验失败")
        return ""
    try:
        import httpx
        os.makedirs(dest_dir, exist_ok=True)
    except Exception:
        logger.exception("创建媒体目录失败")
        return ""

    is_image = kind == "image"
    max_bytes = 12 * 1024 * 1024 if is_image else 20 * 1024 * 1024
    ext = os.path.splitext(urlparse(u).path)[1].lower()
    allowed = _IMAGE_EXTS if is_image else _VOICE_EXTS
    if ext not in allowed:
        ext = ".jpg" if is_image else ".amr"
    path = os.path.join(dest_dir, f"{int(time.time() * 1000)}_{secrets.token_hex(3)}{ext}")
    total = 0
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
                    logger.warning("媒体超过大小上限（%s bytes）", size)
                    return ""
                ctype = str(r.headers.get("Content-Type") or "").lower()
                if is_image and ctype and not ctype.startswith(
                    ("image/", "application/octet-stream")
                ):
                    logger.warning("图片 Content-Type 异常：%s", ctype)
                    return ""
                with open(path, "wb") as f:
                    for chunk in r.iter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("媒体超过大小上限")
                        f.write(chunk)
        return path if total > 0 else ""
    except Exception:
        logger.exception("媒体下载失败")
        with suppress(Exception):
            os.remove(path)
        return ""


__all__ = [
    "Capability",
    "ChannelAdapter",
    "ChannelRuntime",
    "Deduper",
    "EV_GIFT",
    "EV_JOIN",
    "EV_LEAVE",
    "EV_MESSAGE",
    "InboundMessage",
    "OutboundMessage",
    "PersonaContext",
    "RateLimiter",
    "clamp_int",
    "clean_users",
    "in_quiet_hours",
    "new_session_id",
    "normalize_quiet_hours",
    "now_iso",
    "split_reply",
]
