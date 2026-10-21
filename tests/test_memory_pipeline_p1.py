"""记忆管线（P1）：命中增量、去重索引、显式目录读取、待确认双写、睡眠唤醒时间。"""
import json

import brainkit as bk


def _lines():
    return [x for x in bk.MEMORY_JSONL.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_hits_append_instead_of_rewriting_memory(tmp_path):
    bk.remember_structured("用户偏好表格化输出", type="偏好", importance=4)
    before = bk.MEMORY_JSONL.read_bytes()
    bk.search_memories("表格化", limit=5, record_hits=True)
    # 主文件字节不变；命中写到增量文件
    assert bk.MEMORY_JSONL.read_bytes() == before
    hits = bk.MEMORY_HITS
    assert hits.exists() and any(x.strip() for x in hits.read_text(encoding="utf-8").splitlines())
    assert bk.load_memories()[0]["hit_count"] == 1

    # 睡眠折叠后，增量清空，计数仍保留且不重复
    assert bk.compact_hits() == 1
    assert not hits.read_text(encoding="utf-8").strip()
    assert bk.load_memories()[0]["hit_count"] == 1
    bk.search_memories("表格化", limit=5, record_hits=True)
    assert bk.load_memories()[0]["hit_count"] == 2
    bk.compact_hits()
    assert bk.load_memories()[0]["hit_count"] == 2


def test_remember_dedupe_uses_index_and_keeps_single_line(tmp_path):
    a = bk.remember_structured("去重测试条目", type="测试")
    b = bk.remember_structured("去重测试条目", type="测试")
    assert a and b and a["id"] == b["id"]
    assert len(_lines()) == 1


def test_load_memories_from_does_not_switch_global(tmp_path):
    other = tmp_path / "other_brain"
    (other / "memories").mkdir(parents=True)
    (other / "manifest.json").write_text("{}", encoding="utf-8")
    (other / "memories" / "memory.jsonl").write_text(
        json.dumps({"id": "m-other", "text": "另一个大脑的记忆", "type": "测试",
                    "importance": 3, "archived": False}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    bk.remember_structured("当前大脑的记忆", type="测试")
    cur = bk.BRAIN_DIR
    got = bk.load_memories_from(other)
    assert [e["text"] for e in got] == ["另一个大脑的记忆"]
    assert cur == bk.BRAIN_DIR


def test_pending_accept_can_sync_to_brain(tmp_path, monkeypatch):
    import memory_facade as mf
    mp = tmp_path / "memory.json"
    pp = tmp_path / "pending.json"
    mp.write_text(json.dumps({"enabled": True, "facts": []}, ensure_ascii=False), encoding="utf-8")
    mf.init(memory_path=str(mp))
    mf.init_pending(str(pp))
    called = []
    monkeypatch.setattr(mf, "_sync_item_to_brain", lambda x: called.append(x.get("text")) or True)
    mf.add_pending(["用户偏好中文回复"], origin="agent")
    item = mf.list_pending()[0]
    ok, n, err = mf.resolve_pending([item["id"]], "accept", sync=True)
    assert ok and n == 1 and err is None
    assert called == ["用户偏好中文回复"]


def test_brain_switch_rebinds_unified_memory(tmp_path, monkeypatch):
    import brain_api
    import memory_store as ms
    other = tmp_path / "switched_brain"
    (other / "memories").mkdir(parents=True)
    (other / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(brain_api, "_ACTIVE_FILE", tmp_path / ".brain_active")
    old = bk.BRAIN_DIR
    old_ms = ms.BRAIN_DIR
    try:
        r = brain_api.brain_action("brain-switch", {"dir": str(other)})
        assert r["ok"], r
        assert other == bk.BRAIN_DIR
        assert str(ms.BRAIN_DIR) == str(other)
    finally:
        bk.set_brain_dir(old)
        ms.configure(brain_dir=old_ms)
        ms.invalidate_cache()


def test_sleep_sets_next_wake_and_gates_empty_proposal(tmp_path, monkeypatch):
    from life import sleep, state
    bk.save_json(bk.BRAIN_DIR / "manifest.json", {"brain_id": "testbrain", "fingerprint": "x"})
    cfg = {"life_wake_time": "07:00", "life_sleep_duration_minutes": 480,
           "life_sleep_llm": False, "life_sleep_consolidate_llm": False}
    monkeypatch.setattr(sleep, "_cfg", lambda: cfg)
    r = sleep.run(reason="test", use_llm=False)
    assert r["ok"] and not r["skipped"]
    assert state.load().get("next_wake_at")
    # 没有任何整理增量时不应产生空日结提案
    assert r["proposal"] == ""
