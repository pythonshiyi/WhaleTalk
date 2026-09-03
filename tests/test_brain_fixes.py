"""大脑功能修复系列测试（v3.8.5 批次）：

P0-1 jsonl 行级三路合并（不整文件冲突）
P0-2 prune 豁免血缘引用快照
P0-3 记忆写路径：原子追加 + 去重
P1-1 self_model / 决策注入上下文（不再只写不读）
P1-2/P1-3 时间衰减打分 + 混时区 epoch 比较
P2 演化账本追加 / merge-resolve 拒绝 jsonl 整文件覆盖
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


def _mk_entry(mid, text, ts="2026-09-01T10:00:00+08:00", imp=3, archived=False):
    return {"id": mid, "ts": ts, "type": "测试", "importance": imp, "text": text,
            "tags": [], "entities": [], "relations": [], "source": "测试", "archived": archived}


def _jline(e):
    return json.dumps(e, ensure_ascii=False)


@pytest.fixture
def brain_tmp():
    """临时大脑目录：构造合法 manifest，测试期间让 brainkit 指向它。"""
    td = Path(tempfile.mkdtemp(prefix="wt_brain_"))
    for d in ("memories", "thinking_log", "archive"):
        (td / d).mkdir(parents=True, exist_ok=True)
    man = {"schema_version": 1, "brain_id": "whale-test", "created_at": "2026-09-01T00:00:00+00:00",
           "genesis": "测试大脑"}
    man["fingerprint"] = bk.compute_fingerprint(man)
    bk.save_json(td / "manifest.json", man)
    old_dir = bk.BRAIN_DIR
    bk.set_brain_dir(td)
    yield td
    bk.set_brain_dir(old_dir)
    shutil.rmtree(td, ignore_errors=True)


# ===== P1-3 混时区 epoch 比较 =====

def test_ts_epoch_mixed_offsets_equal():
    # 同一时刻、不同历史偏移（+08:00 与 UTC）必须解析为同一 epoch
    a = bk._ts_epoch("2026-08-31T20:25:50+08:00")
    b = bk._ts_epoch("2026-08-31T12:25:50+00:00")
    assert a == pytest.approx(b, abs=0.001)
    # 晚于的时刻 epoch 更大（跨偏移仍成立）
    later = bk._ts_epoch("2026-09-01T09:00:00+01:00")
    assert later > a


def test_search_memories_empty_sorts_by_epoch(brain_tmp):
    # 混写 +08/+01 时，字符串倒序会失真；epoch 排序必须把真·最新放前面
    old = _mk_entry("m-old", "旧记忆", ts="2026-08-31T23:00:00+08:00", imp=1)
    new = _mk_entry("m-new", "新记忆", ts="2026-09-02T01:00:00+01:00", imp=1)  # UTC 2026-09-02 00:00
    with open(bk.MEMORY_JSONL, "a", encoding="utf-8") as f:
        f.write(_jline(old) + "\n" + _jline(new) + "\n")
    res = bk.search_memories("")
    assert res[0]["id"] == "m-new"


# ===== P0-3 记忆写路径 =====

def test_remember_dedup_and_append(brain_tmp):
    assert bk.remember_structured("约定A", type="约定", source="测试")
    assert bk.remember_structured("约定B", type="约定", source="测试")
    dup = bk.remember_structured("约定A", type="约定", source="测试")  # 同文本去重
    assert dup["text"] == "约定A"
    assert len(bk.load_memories()) == 2
    # 文件每行一条合法 JSON（原子 append 不整文件重写，行数与条数一致）
    lines = [ln for ln in bk.MEMORY_JSONL.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    assert all(json.loads(ln)["id"].startswith("m-") for ln in lines)


def test_remember_after_delete(brain_tmp):
    e = bk.remember_structured("可删除", type="测试")
    assert bk.delete_memory(e["id"])
    assert len(bk.load_memories()) == 0
    # 删除后可重新写入同文本（去重只看未归档）
    e2 = bk.remember_structured("可删除", type="测试")
    assert e2 and e2["id"] != e["id"]


# ===== P0-1 jsonl 行级三路合并 =====

def test_merge_jsonl_union_of_new_rows():
    base = "\n".join([_jline(_mk_entry("m1", "甲")), _jline(_mk_entry("m2", "乙"))]) + "\n"
    ours = base + _jline(_mk_entry("m3", "主干新增")) + "\n"
    theirs = base + _jline(_mk_entry("m4", "分支新增")) + "\n"
    text, auto = bk._merge_jsonl_text(base, ours, theirs, "auto")
    rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    ids = {r["id"] for r in rows}
    assert ids == {"m1", "m2", "m3", "m4"}  # 两分支新增全部并入，不整文件冲突
    assert auto == 0


def test_merge_jsonl_one_side_edit_taken():
    base = _jline(_mk_entry("m1", "旧文本")) + "\n"
    ours = _jline(_mk_entry("m1", "主干改", ts="2026-09-02T08:00:00+08:00")) + "\n"
    text, auto = bk._merge_jsonl_text(base, ours, base, "auto")
    rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    assert rows[0]["text"] == "主干改"
    assert auto == 0


def test_merge_jsonl_both_edit_take_newer_ts():
    base = _jline(_mk_entry("m1", "旧", ts="2026-09-01T00:00:00+08:00")) + "\n"
    ours = _jline(_mk_entry("m1", "主干版", ts="2026-09-02T10:00:00+08:00")) + "\n"
    theirs = _jline(_mk_entry("m1", "分支版", ts="2026-09-02T11:00:00+08:00")) + "\n"
    text, auto = bk._merge_jsonl_text(base, ours, theirs, "auto")
    rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    assert rows[0]["text"] == "分支版"  # ts 更新者胜
    assert auto == 1


def test_merge_jsonl_single_side_delete_kept():
    # 并集哲学：仅一方删除不传播（记忆不丢）；两方都删才删除
    m2 = _mk_entry("m2", "共享")
    base = _jline(_mk_entry("m1", "甲")) + "\n" + _jline(m2) + "\n"
    ours = _jline(_mk_entry("m1", "甲")) + "\n"  # 主干删了 m2
    text, auto = bk._merge_jsonl_text(base, ours, base, "auto")
    ids = {json.loads(ln)['id'] for ln in text.splitlines() if ln.strip()}
    assert ids == {"m1", "m2"}  # 保留（m2 在 theirs 未动）
    assert auto == 0


def test_merge_file_dispatches_jsonl(brain_tmp):
    # 经 _merge_file 入口（cmd_merge 实际路径）不抛 _Conflict
    base = _jline(_mk_entry("m1", "同")) + "\n"
    ours = base + _jline(_mk_entry("m9", "主干")) + "\n"
    theirs = base + _jline(_mk_entry("m8", "分支")) + "\n"
    out = bk._merge_file("memories/memory.jsonl", base, ours, theirs, "auto")
    assert "m9" in out and "m8" in out


# ===== P0-2 prune 豁免血缘 =====

def _fake_versions(td, n):
    arch = td / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (arch / f"brain_v{i}.whale").write_bytes(b"x")
    return sorted(arch.glob("brain_v*.whale"))


def test_prune_skips_lineage_referenced(brain_tmp):
    versions = _fake_versions(brain_tmp, 8)
    bk.save_json(bk.LINEAGE_FILE, {"last_archived": 8, "ancestors": [7, 6, 5, 4, 3, 2, 1]})
    pruned, skipped = bk._prune_snapshots(versions, keep=4)  # keep=4 → 仅 v1-v4 是候选
    assert pruned == []  # 候选全部被血缘豁免
    assert skipped == ["1", "2", "3", "4"]


def test_prune_removes_unreferenced_when_allowed(brain_tmp):
    versions = _fake_versions(brain_tmp, 6)
    pruned, skipped = bk._prune_snapshots(versions, keep=3, protected=set())
    assert len(pruned) == 3  # v1-v3 被清理
    assert skipped == []
    remain = sorted(p.stem for p in versions if p.exists())
    assert remain == ["brain_v4", "brain_v5", "brain_v6"]


# ===== P1-1 自我模型 / 决策注入上下文 =====

def test_brain_context_injects_self_model_and_decisions(brain_tmp):
    bk.save_json(brain_tmp / "identity.json", {"name": "澄", "vessel": "鲸语", "nature": "", "vibe": ""})
    bk.save_json(brain_tmp / "heartbeat.json", {"resume_hint": "正在打磨记忆检索", "last_beat": None})
    bk.save_json(brain_tmp / "self_model.json", {
        "knows": ["我了解合并引擎"],
        "unknowns": ["我不知道未来会学到什么"],
        "limits": ["恢复仍是整脑替换"],
        "source": "template",
    })
    bk.record_decision("采用镜像源", "直连超时", "提速稳定")
    bk.remember_structured("用户偏好表格化输出", type="偏好", importance=4, source="测试")
    ctx = __import__("brain_api").brain_context()
    assert "自我认知 · 我知道" in ctx and "我不确定" in ctx and "我的局限" in ctx
    assert "未决决策" in ctx and "采用镜像源" in ctx
    assert "近期记忆" in ctx and "表格化输出" in ctx


def test_brain_context_decay_prefers_fresh_over_stale_high_imp(brain_tmp):
    # 120 天前的 imp5 记忆 vs 今日 imp2 记忆：衰减后今日记忆应排前（破陈旧固化）
    old = _mk_entry("m-old", "过时的高价值结论", ts="2026-05-01T10:00:00+08:00", imp=5)
    fresh = _mk_entry("m-new", "今天的新约定", ts="2026-09-03T10:00:00+08:00", imp=2)
    with open(bk.MEMORY_JSONL, "a", encoding="utf-8") as f:
        f.write(_jline(old) + "\n" + _jline(fresh) + "\n")
    ctx = __import__("brain_api").brain_context(max_memories=1)
    assert "今天的新约定" in ctx
    assert "过时的高价值结论" not in ctx


# ===== P2 演化账本 =====

def test_record_evolution_appends(brain_tmp):
    # 空账本首个记录为 P-001（cmd_init 预置 3 条后则从 P-004 起算）
    rec = bk.record_evolution("记忆检索接通话题", kind="adopted", note="brain_context(query)")
    assert rec["id"] == "P-001"
    evo = bk.load_json(bk._evolution_path(), {})
    assert any(r["id"] == "P-001" for r in evo.get("adopted", []))
    # 提议路径
    rec2 = bk.record_evolution("未来：向量检索", kind="proposed")
    assert rec2["id"] == "P-002"
    evo2 = bk.load_json(bk._evolution_path(), {})
    assert any(r["id"] == "P-002" and r.get("status") == "proposed" for r in evo2.get("proposals", []))


def test_merge_resolve_rejects_jsonl_conflict(brain_tmp, capsys):
    # 旧版遗留的 jsonl 整文件冲突不得被裁决整文件覆盖（样本已截断会毁库）
    bk.save_json(bk.MERGE_CONFLICT_FILE, {"conflicts": [{
        "id": "c-jsonl1", "file": "memories/memory.jsonl", "path": "memories/memory.jsonl",
        "base": "…", "ours": "…", "theirs": "…", "status": "open",
    }]})
    import argparse
    args = argparse.Namespace(id="c-jsonl1", keep="ours", value=None, dir=str(brain_tmp))
    code = bk.cmd_merge_resolve(args)
    assert code == 1
    out = capsys.readouterr().err
    assert "无法安全裁决" in out or "拒绝" in out
