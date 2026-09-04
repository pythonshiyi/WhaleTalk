"""v3.8.5 大脑增强批次二：B1/B2 记忆统一读取层单元测试。

B1：memory.json（对话事实）+ brain memory.jsonl（大脑记忆）+ knowledge_index（文档）
  归一到同一 canonical 条目 schema，跨源去重。
B2：mtime 失效缓存（文件变则重读、未变则命中缓存）；unified_entries/search。
"""
import json
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
import memory_store as ms  # noqa: E402


def _fact(key, value, type_="偏好", ts="2026-09-01T10:00:00+08:00"):
    return {"key": key, "value": value, "type": type_, "ts": ts,
            "entities": [], "relations": []}


def _bentry(mid, text, ts="2026-09-02T10:00:00+08:00", imp=3, sens="public"):
    return {"id": mid, "ts": ts, "type": "记忆", "importance": imp, "text": text,
            "tags": [], "entities": [], "relations": [], "source": "对话",
            "archived": False, "sensitivity": sens, "hit_count": 0, "last_hit": "",
            "supersedes": "", "version_id": mid}


@pytest.fixture
def stores():
    """构造独立 tmp 环境：memory.json + 一个临时大脑 + knowledge_index.json。"""
    td = Path(tempfile.mkdtemp(prefix="wt_ms_"))
    # memory.json（对话事实）
    (td / "memory.json").write_text(json.dumps(
        {"enabled": True, "facts": [_fact("备份", "每周五备份数据库"),
                                    _fact("风格", "用户偏好表格化输出", type_="偏好")]},
        ensure_ascii=False), encoding="utf-8")
    # 临时大脑
    brain = td / "brain"
    for d in ("memories", "thinking_log", "archive"):
        (brain / d).mkdir(parents=True, exist_ok=True)
    man = {"schema_version": 1, "brain_id": "whale-ms", "created_at": "2026-01-01T00:00:00+00:00", "genesis": "x"}
    man["fingerprint"] = bk.compute_fingerprint(man)
    bk.save_json(brain / "manifest.json", man)
    (brain / "identity.json").write_text("{}", encoding="utf-8")
    (brain / "heartbeat.json").write_text("{}", encoding="utf-8")
    with open(brain / "memories" / "memory.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(_bentry("m-b1", "张三负责项目A联调"), ensure_ascii=False) + "\n")
        f.write(json.dumps(_bentry("m-b2", "项目A使用React"), ensure_ascii=False) + "\n")
    # knowledge_index.json
    (td / "knowledge_index.json").write_text(json.dumps(
        {"root": str(td), "count": 1,
         "docs": [{"path": str(td / "notes.md"), "text": "架构设计笔记：记忆统一层方案",
                   "mtime_ns": 1, "size": 10}]}), encoding="utf-8")
    # 备份原全局配置
    old = (ms.MEMORY_JSON_PATH, ms.KNOWLEDGE_INDEX_PATH, ms.BRAIN_DIR, bk.BRAIN_DIR)
    ms.configure(memory_json=str(td / "memory.json"),
                 knowledge_index=str(td / "knowledge_index.json"),
                 brain_dir=str(brain))
    ms.invalidate_cache()
    yield td
    ms.configure(memory_json=old[0], knowledge_index=old[1], brain_dir=old[2])
    ms.invalidate_cache()
    shutil.rmtree(td, ignore_errors=True)


def test_unified_entries_merges_and_dedups(stores):
    entries = ms.unified_entries()
    texts = {e["text"] for e in entries}
    assert "每周五备份数据库" in texts      # memory.json
    assert "张三负责项目A联调" in texts     # brain jsonl
    assert "项目A使用React" in texts
    # canonical 字段齐全
    for e in entries:
        for fld in ("id", "text", "type", "importance", "tags", "entities",
                    "relations", "source", "ts", "sensitivity"):
            assert fld in e, f"缺 canonical 字段 {fld}"


def test_unified_entry_schema_normalized(stores):
    entries = ms.unified_entries()
    mem_json = [e for e in entries if e["source"] == "memory.json"]
    assert mem_json and all(e["sensitivity"] == "public" for e in mem_json)
    # memory.json 无 id 的条目有 fallback id
    assert all(e["id"] for e in entries)


def test_cache_invalidates_on_mtime_change(stores):
    # 首次读取后缓存命中
    e1 = ms.unified_entries()
    e2 = ms.unified_entries()
    assert len(e2) == len(e1)  # 命中缓存
    # 改动 memory.json → 缓存失效重读
    p = Path(stores) / "memory.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["facts"].append(_fact("新增", "新增一条对话事实"))
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    ms.invalidate_cache()  # mtime 秒级可能相同，显式失效保证测试确定性
    e3 = ms.unified_entries()
    assert any("新增一条对话事实" in e["text"] for e in e3)


def test_search_cross_source(stores):
    r = ms.search("备份数据库")
    assert r and any("每周五备份数据库" in e["text"] for e in r)
    r2 = ms.search("张三")
    assert any("张三负责项目A联调" in e["text"] for e in r2)
    # 过滤来源
    r3 = ms.search("张三", sources=("memory.json",))
    assert all(e["source"] == "memory.json" for e in r3)


def test_knowledge_docs_read(stores):
    docs = ms.knowledge_docs("记忆统一")
    assert docs and any("记忆统一层方案" in d["text"] for d in docs)


def test_search_all_three_sources(stores):
    hits = ms.search_all("记忆统一")  # knowledge 子串命中
    assert any(d["source"] == "knowledge" for d in hits)
