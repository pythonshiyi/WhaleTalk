"""v3.8.5 大脑增强·二批（增强路线图实施）单元测试。

覆盖：F2 事实版本链 / F3 重要度自学习(hit) / F4 间隔复习 / F6 doctor /
F7 图谱多跳 / F8 merge dry-run / F9 身份历史 / F10 借贷 / L6 审计 / L8 敏感度。
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


def _mk(mid, text, ts="2026-09-01T10:00:00+08:00", imp=3, entities=None, relations=None, sens="public"):
    return {"id": mid, "ts": ts, "type": "测试", "importance": imp, "text": text,
            "tags": [], "entities": entities or [], "relations": relations or [],
            "source": "测试", "archived": False, "sensitivity": sens,
            "hit_count": 0, "last_hit": "", "supersedes": "", "version_id": mid}


def _jl(e):
    return json.dumps(e, ensure_ascii=False)


@pytest.fixture
def btmp():
    td = Path(tempfile.mkdtemp(prefix="wt_b2_"))
    for d in ("memories", "thinking_log", "archive"):
        (td / d).mkdir(parents=True, exist_ok=True)
    man = {"schema_version": 1, "brain_id": "whale-t2", "created_at": "2026-09-01T00:00:00+00:00", "genesis": "x"}
    man["fingerprint"] = bk.compute_fingerprint(man)
    bk.save_json(td / "manifest.json", man)
    bk.save_json(td / "identity.json", {"name": "澄", "vessel": "鲸语", "nature": "", "vibe": "认真温和"})
    bk.save_json(td / "heartbeat.json", {})
    old = bk.BRAIN_DIR
    bk.set_brain_dir(td)
    yield td
    bk.set_brain_dir(old)
    shutil.rmtree(td, ignore_errors=True)


# ---- F2 事实版本链 ----

def test_version_replace_keeps_lineage(btmp):
    e = bk.remember_structured("张三负责项目A报价", type="联系", importance=4,
                               entities=["张三", "项目A"], source="测试")
    newid = bk.version_replace_memory(e["id"], "张三已离职", source="对话")
    assert newid
    mems = {m["id"]: m for m in bk.load_memories(include_archived=True)}
    assert mems[newid]["supersedes"] == e["id"]
    assert mems[newid]["version_id"] == mems[e["id"]]["version_id"]
    assert mems[e["id"]]["archived"] is True  # 旧版归档不删（可溯源）
    # 当前可见记忆只剩新版
    active = bk.load_memories()
    assert len(active) == 1 and active[0]["id"] == newid


# ---- F3 重要度自学习 ----

def test_hit_count_recorded(btmp):
    bk.remember_structured("用户偏好表格化输出", type="偏好", importance=3, source="测试")
    bk.search_memories("表格化", limit=5, record_hits=True)
    hit = bk.load_memories()[0]
    assert hit.get("hit_count") == 1 and hit.get("last_hit")


def test_hit_boost_in_decay(btmp):
    """带命中奖励的记忆在注入打分中应高于无命中的同重要度记忆。"""
    import brain_api
    # 直接写两条：一条带命中，一条不带，年龄相近
    e_used = {"id": "m-used", "ts": "2026-09-03T10:00:00+08:00", "type": "约定", "importance": 3,
              "text": "每天备份数据库", "tags": [], "entities": [], "relations": [],
              "source": "对话", "archived": False, "sensitivity": "public",
              "hit_count": 10, "last_hit": "2026-09-03T10:00:00+08:00", "supersedes": "", "version_id": "m-used"}
    e_idle = {"id": "m-idle", "ts": "2026-09-03T10:00:00+08:00", "type": "约定", "importance": 3,
              "text": "很久没用的旧约定B", "tags": [], "entities": [], "relations": [],
              "source": "对话", "archived": False, "sensitivity": "public",
              "hit_count": 0, "last_hit": "", "supersedes": "", "version_id": "m-idle"}
    with open(bk.MEMORY_JSONL, "a", encoding="utf-8") as f:
        f.write(_jl(e_used) + "\n" + _jl(e_idle) + "\n")
    ctx = brain_api.brain_context(max_memories=2)
    # 命中记忆应排在前面（出现在注入中优先）
    assert ctx.index("每天备份数据库") < ctx.index("很久没用的旧约定B")


# ---- F4 间隔复习 ----

def test_spaced_review_picks_old_high_value(btmp):
    import brain_api
    # 一条 8 天前的高价值记忆（跨 7 天档中段）应入选复习
    old_hi = _mk("m-hi", "关键原则：先备份再改库", ts="2026-08-27T10:00:00+08:00", imp=5)
    fresh = _mk("m-fresh", "今天小记", ts="2026-09-03T10:00:00+08:00", imp=3)
    with open(bk.MEMORY_JSONL, "a", encoding="utf-8") as f:
        f.write(_jl(old_hi) + "\n" + _jl(fresh) + "\n")
    due = brain_api._spaced_review_due(__import__("time").time(), limit=5)
    assert any(m.get("id") == "m-hi" for m in due)


# ---- F6 doctor ----

def test_doctor_reports_and_fix(btmp):
    # 制造一条 >120 天的低价值记忆
    stale = _mk("m-stale", "过时琐碎记录", ts="2026-01-01T10:00:00+08:00", imp=1)
    with open(bk.MEMORY_JSONL, "a", encoding="utf-8") as f:
        f.write(_jl(stale) + "\n")
    import argparse
    args = argparse.Namespace(fix=True)
    code = bk.cmd_doctor(args)
    assert code == 0
    # fix 后陈旧记忆应归档
    active = bk.load_memories()
    assert all(m.get("id") != "m-stale" for m in active)


# ---- F7 图谱多跳 ----

def test_graph_multi_hop(btmp):
    entries = [
        _mk("m1", "张三负责项目A联调", imp=4, entities=["张三", "项目A"]),
        _mk("m2", "李四在项目A做测试", imp=3, entities=["李四", "项目A"],
            relations=[{"rel": "同事于", "to": "张三"}]),
        _mk("m3", "无关记忆", imp=2, entities=["王五"]),
    ]
    with open(bk.MEMORY_JSONL, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(_jl(e) + "\n")
    r1 = bk.query_graph_multi_hop("张三", hops=1)
    ids1 = {m["id"] for m in r1}
    assert "m1" in ids1  # 直接含张三
    r2 = bk.query_graph_multi_hop("张三", hops=2)
    ids2 = {m["id"] for m in r2}
    assert "m2" in ids2  # 2 跳（张三→项目A→李四）


# ---- F9 身份历史 ----

def test_identity_history_record_and_list(btmp):
    import argparse
    assert bk.cmd_identity_history(argparse.Namespace(identity_cmd="record")) == 0
    hist = bk.load_json(btmp / "identity_history.json", {})
    assert len(hist.get("versions") or []) == 1
    assert (hist["versions"][0]["identity"] or {}).get("name") == "澄"


# ---- F10 借贷 ----

def test_borrow_imports_public(btmp):
    # 造一个源大脑
    src = Path(tempfile.mkdtemp(prefix="wt_src_"))
    (src / "memories").mkdir(parents=True, exist_ok=True)
    (src / "archive").mkdir()
    man = {"schema_version": 1, "brain_id": "whale-src", "created_at": "2026-01-01T00:00:00+00:00", "genesis": "s"}
    man["fingerprint"] = bk.compute_fingerprint(man)
    bk.save_json(src / "manifest.json", man)
    (src / "identity.json").write_text("{}", encoding="utf-8")
    (src / "heartbeat.json").write_text("{}", encoding="utf-8")
    pub = _mk("s-pub", "源大脑关于预算的经验", imp=4)
    sec = _mk("s-sec", "源大脑的机密APIkey", imp=5, sens="secret")
    with open(src / "memories" / "memory.jsonl", "a", encoding="utf-8") as f:
        f.write(_jl(pub) + "\n" + _jl(sec) + "\n")
    import argparse
    args = argparse.Namespace(src_dir=str(src), keyword="预算")
    assert bk.cmd_borrow(args) == 0
    imported = bk.load_memories()
    assert any("预算" in m["text"] for m in imported)
    assert not any("APIkey" in m["text"] for m in imported)  # secret 不外借
    shutil.rmtree(src, ignore_errors=True)


# ---- L8 敏感度导出过滤 ----

def test_share_export_excludes_secret(btmp):
    import brain_api
    bk.remember_structured("公开想法", type="偏好", source="测试", sensitivity="public")
    bk.remember_structured("绝密凭证内容", type="凭证", source="测试", sensitivity="secret")
    r = brain_api.brain_action("share-export", {})
    assert r["ok"]
    import base64
    share = json.loads(base64.b64decode(r["data"]["download"]["data_b64"]).decode("utf-8"))
    texts = [m.get("text") for m in share.get("memories", [])]
    assert any("公开想法" in t for t in texts)
    assert not any("绝密凭证" in t for t in texts)
    assert "已排除 1 条 secret" in r["message"]


# ---- L6 审计 ----

def test_audit_op_writes(btmp):
    bk.audit_op("borrow", "test")
    log = btmp / "brain_ops.log"
    assert log.exists()
    assert "borrow" in log.read_text(encoding="utf-8")


# ---- F8 merge dry-run 不产生副作用 ----

def test_merge_dry_run_no_side_effect(btmp):
    # 造两个快照目录（无 archive 依赖，双路场景）
    a = btmp / "snapA"; b = btmp / "snapB"
    (a / "memories").mkdir(parents=True); (b / "memories").mkdir(parents=True)
    for d, base in ((a, "A主"), (b, "B主")):
        bk.save_json(d / "manifest.json", {"schema_version": 1, "brain_id": "whale-x",
                                           "created_at": "2026-01-01T00:00:00+00:00", "genesis": base})
        (d / "memories" / "memory.jsonl").write_text(
            json.dumps(_mk("m1", "共同记忆", imp=3, entities=[]), ensure_ascii=False) + "\n"
            + json.dumps(_mk("m-c", base + "新增" + "该分支新增", imp=3), ensure_ascii=False) + "\n", encoding="utf-8")
    # 打 zip 成 .whale 快照
    import zipfile
    for d, name in ((a, "a.whale"), (b, "b.whale")):
        ap = btmp / name
        with zipfile.ZipFile(ap, "w", zipfile.ZIP_DEFLATED) as z:
            for p in d.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(d).as_posix())
    import argparse
    args = argparse.Namespace(snap_a=str(btmp / "a.whale"), snap_b=str(btmp / "b.whale"),
                              strategy="auto", dir=None, dry_run=True, passphrase="")
    before = set(x.name for x in btmp.glob("brain_merged-*"))
    code = bk.cmd_merge(args)
    assert code == 0
    after = set(x.name for x in btmp.glob("brain_merged-*"))
    assert after == before  # dry-run 不产出合并目录
    assert not (btmp / "merge_conflicts.json").exists()


# ---- L5 跨进程锁 ----

def test_cross_process_lock_exclusive(btmp):
    target = btmp / "m.jsonl"
    assert bk.cross_process_lock(target)
    assert not bk.cross_process_lock(target, timeout=0.3)  # 已被占
    bk.release_lock(target)
    assert bk.cross_process_lock(target)  # 释放后可再取
    bk.release_lock(target)


# ---- L2 jsonl 高价值语义自动取舍留痕 ----

def test_l2_high_value_auto_flagged(btmp):
    """重要度≥4 的 text 双方都改：仍行级 auto（不整文件冲突），但 jsonl_auto_hi 计数留痕。"""
    base = json.dumps(_mk("m1", "旧文本", ts="2026-09-01T00:00:00+08:00", imp=4), ensure_ascii=False) + "\n"
    ours = json.dumps(_mk("m1", "主干改法", ts="2026-09-02T10:00:00+08:00", imp=4), ensure_ascii=False) + "\n"
    theirs = json.dumps(_mk("m1", "分支改法", ts="2026-09-02T11:00:00+08:00", imp=4), ensure_ascii=False) + "\n"
    old_hi = bk._MERGE_AUTO_CTX.get("jsonl_auto_hi")
    text, auto = bk._merge_jsonl_text(base, ours, theirs, "auto")
    assert auto == 1  # 行级 auto 取 ts 新者，无整文件冲突
    assert "分支改法" in text
    assert bk._MERGE_AUTO_CTX.get("jsonl_auto_hi", 0) == old_hi + 1  # 高价值取舍留痕
    # 复位避免污染其他用例
    bk._MERGE_AUTO_CTX["jsonl_auto_hi"] = old_hi


# ---- L7 记忆不变量：增删不丢 id / 全量可解析 ----

def test_memory_invariants(btmp):
    # 连续写 50 条 → 全量可解析、无重复 id、无丢失
    ids = set()
    for i in range(50):
        e = bk.remember_structured(f"第{i}条测试记忆", type="批量", importance=3, source="测试")
        ids.add(e["id"])
    items = bk.load_memories()
    assert len(items) == 50
    assert len({m["id"] for m in items}) == 50
    # 每行合法 JSON
    for line in bk.MEMORY_JSONL.read_text(encoding="utf-8").splitlines():
        assert json.loads(line)["id"]
