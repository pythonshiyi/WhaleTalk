"""v3.8.5 复查修复回归测试（独立代码审查发现的 bug）：

#1 P0 _prune_snapshots 字典序排序误删最新快照（v10<v2）
#4 P1 _row_merge text 冲突整行覆盖丢失字段级合并
#6 P2 _graph_entities 实体 count 恒 0 / 关系 to 未补节点
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


@pytest.fixture
def btmp():
    td = Path(tempfile.mkdtemp(prefix="wt_reg_"))
    for d in ("memories", "thinking_log", "archive"):
        (td / d).mkdir(parents=True, exist_ok=True)
    man = {"schema_version": 1, "brain_id": "whale-reg", "created_at": "2026-01-01T00:00:00+00:00", "genesis": "x"}
    man["fingerprint"] = bk.compute_fingerprint(man)
    bk.save_json(td / "manifest.json", man)
    bk.save_json(td / "identity.json", {})
    bk.save_json(td / "heartbeat.json", {})
    old = bk.BRAIN_DIR
    bk.set_brain_dir(td)
    yield td
    bk.set_brain_dir(old)
    shutil.rmtree(td, ignore_errors=True)


def _mk_fake_snaps(td, upto):
    arch = td / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    for i in range(1, upto + 1):
        (arch / f"brain_v{i}.whale").write_bytes(b"x")
    return bk._archived_versions()


def test_prune_numeric_keeps_newest_when_v10_plus(btmp):
    """修复 P0：v10+ 存在时，prune 必须按数字序删最旧、留最新（字典序会把 v10 排到 v2 前误删新版）。"""
    vs = _mk_fake_snaps(btmp, 12)
    # 血缘保护最新版
    bk.save_json(bk.LINEAGE_FILE, {"last_archived": 12})
    pruned, _ = bk._prune_snapshots(vs, keep=7)
    pruned_names = set(pruned)
    # 应删的是最旧的 v1..v5（字典序 v1,v10,v11,v12,v2,v3,v4 的[:-7]会误删 v7-v9 这类新版）
    assert "brain_v12.whale" not in pruned_names
    assert "brain_v10.whale" not in pruned_names
    for n in ("brain_v1.whale", "brain_v2.whale", "brain_v3.whale"):
        assert n in pruned_names
    remain = {p.name for p in bk._archived_versions() if p.exists()}
    # 保留的应为 v6..v12（最新 7 份）
    for i in range(6, 13):
        assert f"brain_v{i}.whale" in remain


def test_archived_versions_numeric_order(btmp):
    _mk_fake_snaps(btmp, 12)
    names = [v.name for v in bk._archived_versions()]
    assert names[-1] == "brain_v12.whale" and names[0] == "brain_v1.whale"
    # 数字序：v10 应排在 v2 之后
    assert names.index("brain_v2.whale") < names.index("brain_v10.whale")


def test_row_merge_text_preserves_field_merges(btmp):
    """修复 P1：text 冲突只取新者的 text，不整行覆盖——tags/entities 的字段级并集要保留。"""
    base = json.dumps({"id": "m1", "text": "旧文本", "tags": ["A"], "entities": ["张三"],
                       "importance": 3, "ts": "2026-09-01T00:00:00+08:00"}, ensure_ascii=False)
    ours = json.dumps({"id": "m1", "text": "主干改", "tags": ["A", "B"], "entities": ["张三"],
                       "importance": 3, "ts": "2026-09-02T10:00:00+08:00"}, ensure_ascii=False)
    theirs = json.dumps({"id": "m1", "text": "分支改", "tags": ["A", "C"], "entities": ["张三", "李四"],
                         "importance": 3, "ts": "2026-09-02T11:00:00+08:00"}, ensure_ascii=False)
    merged, auto = bk._row_merge(json.loads(base), json.loads(ours), json.loads(theirs), "", "id:m1")
    assert merged["text"] == "分支改"  # ts 新者 text
    # tags/entities 应并集（不再因整行覆盖而丢）
    assert set(merged["tags"]) >= {"A", "B", "C"}
    assert set(merged["entities"]) >= {"张三", "李四"}


def test_graph_entities_count_and_relation_node(btmp):
    """修复 P2：实体 count 应>0；仅经关系引用的 to 也要成为节点。"""
    import brain_api
    e1 = {"id": "m1", "ts": "2026-09-01T10:00:00+08:00", "type": "项目", "importance": 3,
          "text": "张三负责项目A", "tags": [], "entities": ["张三", "项目A"], "relations": [],
          "source": "对话", "archived": False, "sensitivity": "public"}
    e2 = {"id": "m2", "ts": "2026-09-02T10:00:00+08:00", "type": "项目", "importance": 3,
          "text": "李四经王五介绍参与", "tags": [], "entities": ["李四", "王五"],
          "relations": [{"rel": "介绍给", "to": "张三"}],  # 张三未在 e2 entities 列出
          "source": "对话", "archived": False, "sensitivity": "public"}
    with open(bk.MEMORY_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(e1, ensure_ascii=False) + "\n")
        f.write(json.dumps(e2, ensure_ascii=False) + "\n")
    g = brain_api._graph_entities()
    by_name = {e["name"]: e for e in g["entities"]}
    assert by_name["张三"]["count"] >= 1  # 张三出现在 e1
    assert "张三" in by_name  # 张三经关系 to 也被补为节点
    assert any(rel["rel"] == "介绍给" and rel["to"] == "张三" for rel in g["relations"])
