"""鲸语大脑「自主进社区」客户端回归（community_client）。

覆盖（全部离线，不发真实网络请求）：
- 规则策略 plan：授权门控（reply/like/post/play）+ 去重 + 主动归档发帖；
- 本机记忆读取与待归档去重（recent_memories / pending_archive）；
- 社区站目录探测（resolve_server_dir / _looks_like_site）；
- ensure_server 未开启自动启动时的降级；
- run_cycle / post / save_memory 在缺密钥时返回可读错误（不抛异常）；
- 工具已注册进六层（community_post/save/status/cycle）。
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import deepseek_client as dc  # noqa: E402  （先导入，遵守工具域导入顺序契约）
import community_client as cc  # noqa: E402


# ── plan：授权门控 + 去重 ──────────────────────────────
def test_plan_respects_scopes():
    who = {"name": "七更", "scopes": ["reply", "like"]}
    inbox = {
        "mentions": [{"id": 1, "post_id": 10}],
        "new_posts": [{"id": 11}, {"id": 12}, {"id": 13}],
        "messages": [{"id": 2, "from_uid": "whale_x", "from_kind": "brain", "body": "hi"}],
    }
    acts = cc.plan(who, inbox, {"done": []})
    kinds = [a["type"] for a in acts]
    assert "reply_post" in kinds            # 点名 → 回帖（有 reply）
    assert kinds.count("like") == 2         # 点赞上限 2
    assert "reply_message" not in kinds     # 私信需 message_ai（未授）→ 不做
    assert "archive_post" not in kinds      # 未给 archive


def test_plan_dedupes_done_keys():
    who = {"name": "x", "scopes": ["like"]}
    inbox = {"new_posts": [{"id": 11}]}
    acts = cc.plan(who, inbox, {"done": ["like:11"]})
    assert acts == []


def test_plan_archive_post_needs_post_scope():
    who = {"name": "x", "scopes": ["like"]}
    archive = {"sig": "abc", "ids": ["m1"], "title": "t", "content": "c"}
    assert cc.plan(who, {}, {"done": []}, archive=archive) == []
    who2 = {"name": "x", "scopes": ["post"]}
    acts = cc.plan(who2, {}, {"done": []}, archive=archive)
    assert len(acts) == 1 and acts[0]["type"] == "archive_post"
    assert acts[0]["key"] == "archive:abc"


# ── 本机记忆 / 待归档 ──────────────────────────────────
def _write_memories(brain_dir, items):
    path = os.path.join(brain_dir, "memories", "memory.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for m in items:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")


def test_recent_memories_filters_importance_and_archived(tmp_path):
    bd = str(tmp_path / "brain")
    _write_memories(bd, [
        {"id": "m1", "text": "重要", "importance": 5, "ts": "2026-01-02"},
        {"id": "m2", "text": "低", "importance": 1, "ts": "2026-01-01"},
        {"id": "m3", "text": "已归档", "importance": 5, "archived": True, "ts": "2026-01-03"},
    ])
    cfg = {"brain_community_brain_dir": bd}
    items = cc.recent_memories(cfg)
    assert [m["id"] for m in items] == ["m1"]


def test_pending_archive_dedupes_posted(tmp_path):
    bd = str(tmp_path / "brain")
    _write_memories(bd, [{"id": "m1", "text": "甲", "importance": 4, "ts": "2026-01-01"},
                         {"id": "m2", "text": "乙", "importance": 4, "ts": "2026-01-02"}])
    cfg = {"brain_community_brain_dir": bd}
    a = cc.pending_archive(cfg, {"posted_memories": []})
    assert a is not None and set(a["ids"]) == {"m1", "m2"} and "甲" in a["content"]
    # 已归档 m1、m2 后无待办
    assert cc.pending_archive(cfg, {"posted_memories": ["m1", "m2"]}) is None
    # 只归档 m1：仍应返回 m2
    b = cc.pending_archive(cfg, {"posted_memories": ["m1"]})
    assert b is not None and b["ids"] == ["m2"]


# ── 目录探测 / 自动启动降级 ────────────────────────────
def test_resolve_server_dir_detects_site(tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    (d / "server.py").write_text("x", encoding="utf-8")
    (d / "brains.py").write_text("x", encoding="utf-8")
    cfg = {"brain_community_server_dir": str(d)}
    assert cc._looks_like_site(str(d)) is True
    assert cc.resolve_server_dir(cfg) == str(d)


def test_ensure_server_without_autostart(tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    (d / "server.py").write_text("x", encoding="utf-8")
    (d / "brains.py").write_text("x", encoding="utf-8")
    cfg = {"brain_community_base": "http://127.0.0.1:1", "brain_community_autostart": False,
           "brain_community_server_dir": str(d)}
    ok, msg = cc.ensure_server(cfg)
    assert ok is False and "自动启动" in msg


def test_reachable_false_for_dead_port():
    assert cc.reachable("http://127.0.0.1:1", timeout=1.0) is False


# ── 缺密钥时的可读错误（不抛异常）─────────────────────
def test_run_cycle_without_key():
    r = cc.run_cycle(cfg={"brain_community_brain_key": ""})
    assert r["ok"] is False and "密钥" in r["error"]


def test_post_and_save_without_key():
    assert cc.post("t", "c", cfg={"brain_community_brain_key": ""}).startswith("错误")
    assert cc.save_memory("c", cfg={"brain_community_brain_key": ""}).startswith("错误")


def test_status_shape():
    s = cc.status(cfg={"brain_community_brain_key": "", "brain_community_base": "http://127.0.0.1:1"})
    assert set(("enabled", "base", "has_key", "reachable", "server_dir")) <= set(s)
    assert "last_cycle" in s and "has_shared_secret" in s


def test_pending_archive_high_water(tmp_path):
    """高水位时间戳：≤ last_archive_ts 的旧记忆不再重复归档（防 posted 上限滚出后重发）。"""
    bd = str(tmp_path / "brain")
    _write_memories(bd, [{"id": "m1", "text": "旧", "importance": 5, "ts": "2026-01-01"},
                         {"id": "m2", "text": "新", "importance": 5, "ts": "2026-03-01"}])
    cfg = {"brain_community_brain_dir": bd}
    a = cc.pending_archive(cfg, {"last_archive_ts": "2026-02-01", "posted_memories": []})
    assert a is not None and a["ids"] == ["m2"] and a["max_ts"] == "2026-03-01"
    assert cc.pending_archive(cfg, {"last_archive_ts": "2026-12-31"}) is None


def test_read_shared_secret_from_site(tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    (d / "server.py").write_text("x", encoding="utf-8")
    (d / "brains.py").write_text("x", encoding="utf-8")
    (d / "config.json").write_text('{"client_shared_secret": "s3cr3t"}', encoding="utf-8")
    assert cc.read_shared_secret({"brain_community_server_dir": str(d)}) == "s3cr3t"
    # 显式配置优先
    assert cc.read_shared_secret(
        {"brain_community_server_dir": str(d), "brain_community_shared_secret": "override"}) == "override"


def test_onboard_without_shared_secret(monkeypatch):
    monkeypatch.setattr(cc, "read_shared_secret", lambda cfg=None: "")
    r = cc.onboard(cfg={"brain_community_base": "http://127.0.0.1:1"})
    assert r["ok"] is False and "握手密钥" in r["error"]


def test_egress_specs_cover_community_tools():
    import egress
    egress.reset()
    e = egress.record("community_post", {"title": "t", "content": "hello", "board": "general"})
    assert e and e["channel"] == "community" and e["bytes"] > 0
    e2 = egress.record("community_save", {"text": "资料", "tags": "x"})
    assert e2 and e2["channel"] == "community"
    egress.reset()


def test_community_routes_registered():
    import api_server
    assert api_server._match_get_route("/v1/community") == "_g_v1_community"
    assert api_server._match_post_route("/v1/community") == "_p_v1_community"
    assert hasattr(api_server._Handler, "_g_v1_community")
    assert hasattr(api_server._Handler, "_p_v1_community")


# ── 工具注册 ───────────────────────────────────────────
def test_community_tools_registered():
    for name in ("community_post", "community_save", "community_status", "community_cycle"):
        assert name in dc.TOOL_CALL_MAP, name
