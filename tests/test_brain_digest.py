"""②c 记忆周报回归：memory_digest 聚合口径 + Markdown 渲染 + brain_action 分发。"""
from datetime import datetime, timedelta, timezone

import brain_api

NOW = 1_700_000_000.0


def _iso(days_ago):
    return (datetime.fromtimestamp(NOW, tz=timezone.utc) - timedelta(days=days_ago)).isoformat()


def _items():
    return [
        {"id": "a", "text": "新事实A", "type": "偏好", "importance": 5, "ts": _iso(1), "archived": False},
        {"id": "b", "text": "新事实B", "type": "偏好", "importance": 3, "ts": _iso(2), "archived": False},
        {"id": "c", "text": "旧事实", "type": "事实", "importance": 4, "ts": _iso(100), "archived": False},
        {"id": "d", "text": "被替换的旧版", "type": "事实", "importance": 2, "ts": _iso(50), "archived": True},
        {"id": "e", "text": "新版本", "type": "事实", "importance": 4, "ts": _iso(1),
         "archived": False, "supersedes": "d"},
    ]


def test_memory_digest_counts(monkeypatch):
    monkeypatch.setattr(brain_api.bk, "load_memories", lambda include_archived=False: _items())
    monkeypatch.setattr(brain_api.bk, "now_iso", lambda: "2026-01-01T00:00:00")
    d = brain_api.memory_digest(days=7, now=NOW)
    assert d["new_count"] == 3            # a, b, e
    assert d["active_total"] == 4          # a, b, c, e
    assert d["types"]["偏好"] == 2
    assert d["types"]["事实"] == 1
    assert d["superseded_count"] == 1      # d 被 e 取代
    assert d["top"][0]["id"] == "a"        # importance 最高


def test_render_memory_digest_markdown(monkeypatch):
    monkeypatch.setattr(brain_api.bk, "load_memories", lambda include_archived=False: _items())
    monkeypatch.setattr(brain_api.bk, "now_iso", lambda: "2026-01-01T00:00:00")
    md = brain_api.render_memory_digest(brain_api.memory_digest(days=7, now=NOW))
    assert "记忆周报" in md
    assert "新增类型分布" in md
    assert "本周最值得记住" in md
    assert "新事实A" in md


def test_brain_action_memory_digest(monkeypatch):
    captured = {}
    monkeypatch.setattr(brain_api, "memory_digest",
                        lambda days=7: captured.update(days=days) or
                        {"days": days, "new_count": 0, "active_total": 0, "types": {},
                         "superseded_count": 0, "new": [], "top": []})
    r = brain_api.brain_action("memory-digest", {"days": 3})
    assert r["ok"] and r["data"]["days"] == 3
    assert captured["days"] == 3
