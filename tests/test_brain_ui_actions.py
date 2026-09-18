"""大脑 UI 强化批次回归：思考日志 / 自我认知 / 演化账本 / 待复习 / 血缘图 /
全局检索 / 重复合并 / 恢复前对比 / 记忆实体关系编辑 / 状态懒加载。

对应 brain_api 新增 action 与 brainkit.load_thinking / update_memory 扩展。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import brainkit as bk  # noqa: E402


@pytest.fixture
def btmp():
    td = Path(tempfile.mkdtemp(prefix="wt_bui_"))
    for d in ("memories", "thinking_log", "archive"):
        (td / d).mkdir(parents=True, exist_ok=True)
    man = {"schema_version": 1, "brain_id": "whale-ui", "created_at": "2026-09-01T00:00:00+00:00", "genesis": "x"}
    man["fingerprint"] = bk.compute_fingerprint(man)
    bk.save_json(td / "manifest.json", man)
    bk.save_json(td / "identity.json", {"name": "澜", "vessel": "鲸语", "nature": "", "vibe": "x"})
    bk.save_json(td / "heartbeat.json", {})
    old = bk.BRAIN_DIR
    bk.set_brain_dir(td)
    yield td
    bk.set_brain_dir(old)
    shutil.rmtree(td, ignore_errors=True)


# ── 思考日志 ────────────────────────────────────────────────

def test_load_thinking_parses_blocks(btmp):
    (btmp / "thinking_log" / "2026-09-10.md").write_text(
        "## 2026-09-10T09:00:00\n普通想法一\n\n## 2026-09-10T18:00:00\n[心跳] 今天在整理大脑\n",
        encoding="utf-8")
    items = bk.load_thinking(days=30, limit=50)
    assert len(items) == 2
    assert items[0]["ts"].startswith("2026-09-10T18:00")
    assert items[0]["tag"] == "心跳"
    assert "整理大脑" in items[0]["text"]


def test_thinking_list_action(btmp):
    import brain_api
    (btmp / "thinking_log" / "2026-09-11.md").write_text("## 2026-09-11T08:00:00\n[收工] 收工断点\n", encoding="utf-8")
    r = brain_api.brain_action("thinking-list", {"days": 30, "limit": 10})
    assert r["ok"] and r["data"]["items"]
    assert r["data"]["items"][0]["tag"] == "收工"


# ── 记忆实体/关系编辑 ──────────────────────────────────────

def test_update_memory_entities_relations(btmp):
    e = bk.remember_structured("鲸语依赖 DeepSeek", type="事实", importance=4, source="测试")
    ok = bk.update_memory(e["id"], entities=["鲸语", "DeepSeek"],
                          relations=[{"rel": "依赖", "to": "DeepSeek"}])
    assert ok
    item = next(m for m in bk.load_memories() if m["id"] == e["id"])
    assert item["entities"] == ["鲸语", "DeepSeek"]
    assert item["relations"] == [{"rel": "依赖", "to": "DeepSeek"}]


# ── 自我认知 / 演化账本 ────────────────────────────────────

def test_self_model_and_evolution_actions(btmp):
    import brain_api
    bk.save_json(btmp / "self_model.json", {"source": "llm", "knows": ["会检索"],
                                            "unknowns": ["未来"], "limits": ["整脑替换"]})
    bk.save_json(btmp / "evolution.json", {"proposals": [{"id": "P-1", "title": "x"}],
                                           "adopted": [{"id": "P-1", "implemented": "y"}]})
    sm = brain_api.brain_action("self-model", {})
    assert sm["ok"] and sm["data"]["self_model"]["source"] == "llm"
    evo = brain_api.brain_action("evolution-list", {})
    assert evo["ok"] and evo["data"]["proposals"][0]["id"] == "P-1"
    assert evo["data"]["adopted"][0]["implemented"] == "y"


# ── 待复习（F4） ───────────────────────────────────────────

def test_review_due_action(btmp):
    import brain_api
    e = bk.remember_structured("需定期复习的高价值记忆", type="规则", importance=5, source="测试")
    items = bk.load_memories(include_archived=True)
    for it in items:
        if it["id"] == e["id"]:
            it["ts"] = "2026-08-01T10:00:00+08:00"  # 约 47 天前，落在 7~90 天窗口
    bk.save_memories(items)
    r = brain_api.brain_action("review-due", {"limit": 10})
    assert r["ok"]
    assert any("高价值" in x["text"] for x in r["data"]["items"])


# ── 血缘图 ────────────────────────────────────────────────

def test_lineage_action(btmp):
    import brain_api
    for v in (1, 2):
        (btmp / "archive" / f"brain_v{v}.whale").write_bytes(b"x")
    bk.save_json(btmp / ".lineage.json", {"last_archived": 2, "restored_from_version": 1, "ancestors": [2, 1]})
    bk.save_json(btmp / "merge_log.json", {"merges": [{"merged_at": "2026-09-12T10:00:00", "a_version": 1,
                                                       "b_version": 2, "lca": 1, "strategy": "auto"}]})
    r = brain_api.brain_action("lineage", {})
    assert r["ok"]
    kinds = {n["kind"] for n in r["data"]["nodes"]}
    assert {"snapshot", "merge"} <= kinds
    assert any(e["kind"] == "restore" for e in r["data"]["edges"])
    assert any(e["kind"] == "merge" for e in r["data"]["edges"])


# ── 全局检索 ──────────────────────────────────────────────

def test_brain_search_action(btmp):
    import brain_api
    bk.remember_structured("数据库每天凌晨备份", type="约定", importance=4, source="测试")
    bk.record_decision("采用每日备份策略", reason="防丢数据")
    (btmp / "thinking_log" / "2026-09-12.md").write_text("## 2026-09-12T09:00:00\n在思考备份方案\n", encoding="utf-8")
    r = brain_api.brain_action("brain-search", {"q": "备份", "limit": 20})
    assert r["ok"]
    d = r["data"]
    assert any("备份" in (m.get("text") or "") for m in d["memories"])
    assert d["decisions"] and d["thoughts"]


# ── 重复记忆合并 ──────────────────────────────────────────

def test_doctor_merge_dups_action(btmp):
    import brain_api
    a = bk.remember_structured("用户偏好表格化输出", type="偏好", importance=3, source="测试")
    b = bk.remember_structured("用户偏好表格化输出。", type="偏好", importance=5, source="测试")
    r = brain_api.brain_action("doctor-merge-dups", {})
    assert r["ok"] and r["data"]["merged"] >= 1
    active = bk.load_memories()
    # 保留重要度更高者（b），a 被归档
    assert len(active) == 1
    assert active[0]["id"] in (a["id"], b["id"])


# ── 状态懒加载 / 上下文预览 ──────────────────────────────

def test_brain_status_lazy_context(btmp):
    import brain_api
    s = brain_api.brain_status(with_context=False)
    assert s is not None and s["context_preview"] is None
    r = brain_api.brain_action("context-preview", {"max_memories": 2})
    assert r["ok"]


# ── 恢复前对比（当前 ↔ 快照） ─────────────────────────────

def test_diff_current_action(btmp):
    import brain_api
    bk.remember_structured("归档前存在的事实", type="事实", importance=3, source="测试")
    code, out = brain_api._run(bk.cmd_archive, passphrase="", keep=bk.DEFAULT_KEEP)
    assert code == 0, out
    assert list((btmp / "archive").glob("brain_v*.whale")), "归档未产出快照"
    r = brain_api.brain_action("diff-current", {"version": 1})
    assert r["ok"], r.get("message")
    assert "快照对比" in (r["data"]["output"] or "")
