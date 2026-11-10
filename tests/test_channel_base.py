"""通道抽象层：能力声明、共享管线、注册表、新增平台零改动验证。

这些测试保护的正是重构的核心承诺：
**新增一个平台 = 写一个适配器子类 + 注册，共享层一行不改。**
"""
import threading

import pytest

import channel_base as cb
from channel_base import (
    Capability,
    ChannelAdapter,
    ChannelRuntime,
    Deduper,
    InboundMessage,
    OutboundMessage,
    PersonaContext,
    RateLimiter,
    in_quiet_hours,
    split_reply,
)
from channel_registry import ChannelRegistry


# ══════════════════════════════════════════════════════════════════════════
# 一个完全虚构的新平台：证明添加平台不需要碰共享层
# ══════════════════════════════════════════════════════════════════════════

class FakeLiveAdapter(ChannelAdapter):
    """模拟「抖音直播」：只有传输层实现，业务逻辑全部来自共享层。"""

    name = "fake_live"
    display_name = "测试直播"

    def __init__(self):
        super().__init__()
        self.sent = []
        self.running = False

    def capabilities(self):
        # 直播：公开、有礼物、有持续状态
        return Capability(private=False, group=True, media=False, voice=False,
                          streaming=True, gifts=True, public=True)

    def start(self, cfg):
        self.running = True

    def stop(self):
        self.running = False

    def is_running(self):
        return self.running

    def send(self, msg, cfg):
        self.sent.append(msg.text)
        return True, ""

    def parse_event(self, raw):
        if not isinstance(raw, dict) or "viewer" not in raw:
            return None
        return InboundMessage(
            user_id=str(raw["viewer"]),
            text=str(raw.get("say") or ""),
            message_id=str(raw.get("id") or ""),
            is_group=True,
            group_id="room1",
        )


@pytest.fixture()
def live():
    """装配一个完整可用的新平台通道（零改动共享层）。"""
    from channel_registry import build_runtime

    cfg = {"enabled": True, "allowed_users": [], "max_reply_chars": 1200,
           "rate_limit_per_minute": 10, "quiet_hours": ""}
    state = {"users": {}}
    reg = ChannelRegistry()
    adapter = reg.register(
        FakeLiveAdapter,
        build_runtime(FakeLiveAdapter,
                      load_cfg=lambda: dict(cfg),
                      load_state=lambda: state,
                      save_state=lambda s: True,
                      session_prefix="live"),
    )
    return adapter, reg, cfg, state


def test_new_platform_gets_full_pipeline(live, monkeypatch):
    """新平台无需实现任何业务逻辑，即可获得角色/限流/分片/命令。"""
    adapter, reg, cfg, state = live
    replies = []

    # 只需接入「回复生成」这一处外部依赖
    rt = reg.runtime("fake_live")
    monkeypatch.setattr(rt, "_companion",
                        lambda text, sid, pid, images=None: (replies.append((text, pid)), f"回:{text}")[1])

    adapter.dispatch({"viewer": "u1", "id": "m1", "say": "你好"})
    # dispatch 起后台线程，等它结束
    for t in threading.enumerate():
        if t.name.startswith("ch-fake_live"):
            t.join(timeout=5)

    assert replies and replies[0][0] == "你好"
    assert adapter.sent == ["回:你好"]
    # 角色会话自动建立（共享层负责）
    assert state["users"]["u1"]["session_id"].startswith("live_u1_")


def test_new_platform_inherits_commands(live, monkeypatch):
    """/帮助 /角色 等命令对任何新平台自动可用。

    用 monkeypatch 临时替换 personas 模块（测试结束自动还原），
    不直接改 sys.modules —— 否则会污染同进程内后续测试的角色数据。
    """
    import types
    import sys

    adapter, reg, cfg, state = live

    fake = types.ModuleType("personas")
    fake.list_personas = lambda: [{"id": "p1", "name": "大肥鱼"}]
    fake.get_persona = lambda pid: {"id": "p1", "name": "大肥鱼"} if pid == "p1" else None
    monkeypatch.setitem(sys.modules, "personas", fake)

    adapter.dispatch({"viewer": "u2", "id": "m9", "say": "/帮助"})
    for t in threading.enumerate():
        if t.name.startswith("ch-fake_live"):
            t.join(timeout=5)
    assert adapter.sent and "可用命令" in adapter.sent[0]


def test_new_platform_respects_rate_limit(live, monkeypatch):
    adapter, reg, cfg, state = live
    cfg["rate_limit_per_minute"] = 2
    rt = reg.runtime("fake_live")
    monkeypatch.setattr(rt, "_companion", lambda *a, **k: "ok")

    for i in range(5):
        adapter.dispatch({"viewer": "u3", "id": f"r{i}", "say": "hi"})
    for t in threading.enumerate():
        if t.name.startswith("ch-fake_live"):
            t.join(timeout=5)
    # 限流 2 次/分钟：最多 2 条回复
    assert len(adapter.sent) <= 2


def test_new_platform_respects_quiet_hours(live, monkeypatch):
    adapter, reg, cfg, state = live
    from datetime import datetime
    # 构造一个「当前一定在静默时段」的区间
    now = datetime.now()
    cfg["quiet_hours"] = f"{now.hour:02d}:00-{(now.hour + 1) % 24:02d}:59"
    rt = reg.runtime("fake_live")
    monkeypatch.setattr(rt, "_companion", lambda *a, **k: "ok")

    adapter.dispatch({"viewer": "u4", "id": "q1", "say": "在吗"})
    for t in threading.enumerate():
        if t.name.startswith("ch-fake_live"):
            t.join(timeout=5)
    assert adapter.sent == []


# ══════════════════════════════════════════════════════════════════════════
# 共享基础件
# ══════════════════════════════════════════════════════════════════════════

def test_deduper():
    d = Deduper(capacity=3)
    assert d.first_time("a") is True
    assert d.first_time("a") is False
    assert d.first_time("") is True          # 空 ID 不去重
    for i in range(10):
        d.first_time(f"k{i}")
    # 容量有界：老记录被淘汰后再次出现视为首次
    assert d.first_time("k1") is True


def test_rate_limiter():
    r = RateLimiter(window=60)
    assert r.allow("u", 2) is True
    assert r.allow("u", 2) is True
    assert r.allow("u", 2) is False
    assert r.allow("other", 2) is True        # 按用户隔离


def test_quiet_hours_and_split():
    from datetime import datetime
    assert in_quiet_hours("") is False
    assert in_quiet_hours("bad") is False
    # 跨零点：23:00-08:00 在 23:30 应命中
    assert in_quiet_hours("23:00-08:00", datetime(2026, 1, 1, 23, 30)) is True
    assert in_quiet_hours("23:00-08:00", datetime(2026, 1, 1, 7, 59)) is True
    assert in_quiet_hours("23:00-08:00", datetime(2026, 1, 1, 12, 0)) is False
    # 同起止视为不限
    assert in_quiet_hours("08:00-08:00", datetime(2026, 1, 1, 8, 0)) is False

    chunks = split_reply("第一段\n\n" + "长" * 300, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert chunks[0].startswith("第一段")


def test_capability_serialization():
    cap = Capability(private=True, group=True, gifts=True, streaming=True)
    d = cap.as_dict()
    assert d["private"] is True and d["gifts"] is True
    # 未显式声明的能力保持默认（宽松），显式关闭的必须为 False
    assert d["voice"] is True
    assert Capability(voice=False).as_dict()["voice"] is False


# ══════════════════════════════════════════════════════════════════════════
# 注册表
# ══════════════════════════════════════════════════════════════════════════

def test_registry_register_and_describe():
    reg = ChannelRegistry()
    reg.register(FakeLiveAdapter)
    assert reg.names() == ["fake_live"]
    assert reg.get("fake_live") is not None
    desc = reg.describe()
    assert desc[0]["name"] == "fake_live"
    assert desc[0]["capabilities"]["gifts"] is True


def test_registry_rejects_duplicate():
    reg = ChannelRegistry()
    reg.register(FakeLiveAdapter)
    with pytest.raises(ValueError):
        reg.register(FakeLiveAdapter)
    reg.register(FakeLiveAdapter, replace=True)   # 显式覆盖可以
    assert reg.names() == ["fake_live"]


def test_registry_start_all_isolates_failures():
    """一个通道启动失败不能拖垮其他通道。"""
    reg = ChannelRegistry()

    class Boom(FakeLiveAdapter):
        name = "boom"

        def start(self, cfg):
            raise RuntimeError("炸了")

    reg.register(FakeLiveAdapter)
    reg.register(Boom)
    results = reg.start_all(lambda n: {"enabled": True})
    assert results["fake_live"] == "started"
    assert results["boom"].startswith("error")
    assert reg.get("fake_live").is_running() is True


def test_registry_skips_disabled():
    reg = ChannelRegistry()
    reg.register(FakeLiveAdapter)
    results = reg.start_all(lambda n: {"enabled": False})
    assert results["fake_live"] == "disabled"
    assert reg.get("fake_live").is_running() is False


# ══════════════════════════════════════════════════════════════════════════
# 真实通道的注册状态
# ══════════════════════════════════════════════════════════════════════════

def test_real_channels_registered():
    import channel_telegram
    import qq_official
    import qq_onebot
    from channel_registry import registry

    names = registry.names()
    assert "qq" in names and "qq_official" in names and "telegram" in names

    caps = {c["name"]: c["capabilities"] for c in registry.describe()}
    # QQ 只支持私聊；Telegram 支持群聊
    assert caps["qq"]["private"] is True and caps["qq"]["group"] is False
    assert caps["telegram"]["group"] is True


def test_persona_context_session_prefix():
    ctx = PersonaContext("live")
    assert ctx.new_session("u1").startswith("live_u1_")
    # 保留既有会话 ID 前缀兼容
    assert PersonaContext("qq").new_session("10001").startswith("qq_10001_")
    assert PersonaContext("qqo").new_session("openid").startswith("qqo_openid_")


def test_channel_routes_registered():
    """统一通道路由必须注册，且带 query 的必须用 qpath（否则永远 404）。"""
    import api_server

    get_paths = [str(r[0]) if not isinstance(r[0], tuple) else str(r[0][1])
                 for r in api_server._GET_ROUTES]
    post_paths = [str(r[0]) if not isinstance(r[0], tuple) else str(r[0][1])
                  for r in api_server._POST_ROUTES]
    assert "/v1/channels" in get_paths
    assert "/v1/channels/status" in get_paths
    assert "/v1/channels" in post_paths
    # 关键：/v1/channels/status 必须走 qpath 分支，否则带 ?name= 时匹配不到
    qpath_routes = [r[0][1] for r in api_server._GET_ROUTES
                    if isinstance(r[0], tuple) and r[0][0] == "qpath"]
    assert "/v1/channels/status" in qpath_routes


def test_channels_list_includes_all_registered():
    """api_server 统一列表必须能看到全部通道（含延迟导入的 telegram）。"""
    import api_server

    data = api_server._channels_list()
    names = {c["name"] for c in data["channels"]}
    assert {"qq", "qq_official", "telegram"} <= names
    assert data["count"] >= 3


def test_channel_action_rejects_unknown():
    import api_server

    result, err = api_server._channel_action("nonexistent", {"action": "probe"})
    assert result is None
    assert "未知通道" in err
