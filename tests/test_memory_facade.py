# -*- coding: utf-8 -*-
"""记忆门面测试：血缘（origin）、作废（supersede）、冲突提示、向后兼容。

核心不变量：
- 每条新记忆都带 origin + confidence，旧记录读时补默认值（不改写文件）；
- 作废**只按显式 key** 触发，且双向可回溯、可 restore；
- 相似度**只提示冲突，绝不自动作废**——实测证明相似度无法区分
  「应作废」（0.556）与「绝不能作废」（0.600）；
- 注入文本对非 user 来源加标注（打断「外部内容→提炼→当作用户前提」的链）。
"""
import json

import pytest

import memory_facade as mf


@pytest.fixture
def mem(tmp_path):
    p = tmp_path / "memory.json"
    p.write_text(json.dumps({"enabled": True, "facts": []}, ensure_ascii=False),
                 encoding="utf-8")
    mf.init(memory_path=str(p))
    yield mf, p
    mf.init(memory_path=None)


def _raw(p):
    return json.loads(p.read_text(encoding="utf-8"))


# ── 写入与血缘 ───────────────────────────────────────────────────────────

def test_remember_records_origin_and_confidence(mem):
    m, p = mem
    r = m.remember("用户偏好中文回复", origin="user", type="偏好")
    assert r["ok"] and r["action"] == "added"
    f = _raw(p)["facts"][0]
    assert f["value"] == "用户偏好中文回复"
    assert f["origin"] == "user" and f["confidence"] == 1.0
    assert f["status"] == "active" and f["id"]


def test_agent_origin_gets_lower_confidence(mem):
    m, p = mem
    m.remember("用户下周要交报价单", origin="agent")
    f = _raw(p)["facts"][0]
    assert f["origin"] == "agent" and f["confidence"] == 0.5


def test_duplicate_not_written_twice(mem):
    m, p = mem
    m.remember("每周五备份数据库", origin="user")
    r = m.remember("每周五备份数据库", origin="agent")
    assert r["action"] == "duplicate"
    assert len(_raw(p)["facts"]) == 1


# ── 作废：必须显式传 supersede_key ───────────────────────────────────────

def test_explicit_supersede_key_supersedes_and_links_both_ways(mem):
    m, p = mem
    m.remember("用户偏好中文回复", origin="user", key="语言偏好")
    r = m.remember("用户偏好英文回复", origin="user", key="语言偏好",
                   supersede_key="语言偏好")
    assert r["action"] == "superseded" and r["superseded_id"]
    facts = _raw(p)["facts"]
    old = next(f for f in facts if f["value"] == "用户偏好中文回复")
    new = next(f for f in facts if f["value"] == "用户偏好英文回复")
    assert old["status"] == "superseded"
    assert old["superseded_by"] == new["id"]      # 双向链接
    assert new["supersedes"] == old["id"]
    assert new["status"] == "active"


def test_supersede_key_can_be_the_old_id(mem):
    """冲突提示给的是 id——用 id 取代必须可行（这是给模型的主路径）。"""
    m, p = mem
    first = m.remember("用户偏好中文回复", origin="user")
    r = m.remember("用户偏好英文回复", origin="user", supersede_key=first["id"])
    assert r["action"] == "superseded" and r["superseded_id"] == first["id"]


def test_without_supersede_key_similar_memory_is_never_superseded(mem):
    """回归：默认 key（自动记忆）是兜底值，**不能**用来互相作废——
    端到端实测抓到过「用户明说的偏好」被一条无关的 agent 推断顶掉。"""
    m, p = mem
    m.remember("用户偏好表格化输出", origin="user")
    r = m.remember("用户似乎在做报价项目", origin="agent", confidence=0.4)
    assert r["action"] == "added"
    assert len(m.active_facts()) == 2
    assert all(f["status"] == "active" for f in m.all_facts(include_superseded=True))


def test_older_entry_is_not_lost_but_retrievable(mem):
    m, p = mem
    m.remember("用户偏好中文回复", origin="user", key="语言偏好")
    m.remember("用户偏好英文回复", origin="user", key="语言偏好",
               supersede_key="语言偏好")
    assert len(m.active_facts()) == 1
    assert len(m.all_facts(include_superseded=True)) == 2   # 不作删，可回溯


def test_restore_brings_back_superseded(mem):
    m, p = mem
    m.remember("用户偏好中文回复", origin="user", key="语言偏好")
    m.remember("用户偏好英文回复", origin="user", key="语言偏好",
               supersede_key="语言偏好")
    ok, msg = m.restore("中文回复")
    assert ok and "已恢复" in msg
    assert len(m.active_facts()) == 2


def test_invalidate_and_restore(mem):
    m, p = mem
    m.remember("临时事项：明天开会", origin="agent")
    ok, msg = m.invalidate("明天开会", reason="已过期")
    assert ok and "已作废" in msg
    assert m.active_facts() == []
    ok2, _ = m.restore("明天开会")
    assert ok2 and len(m.active_facts()) == 1


def test_supersede_key_not_found_fails_softly(mem):
    m, p = mem
    r = m.remember("新内容", origin="user", supersede_key="不存在的id")
    assert r["ok"] is False and "未找到" in r["message"]
    assert _raw(p)["facts"] == []


# ── 相似度：只提示，不动作（实测结论的回归锚）─────────────────────────────

def test_similar_but_should_supersede_is_only_flagged(mem):
    """0.556 这类「应作废」的相似度**不得**触发自动作废——交回判断者。"""
    m, p = mem
    m.remember("用户偏好中文回复", origin="user", type="偏好")
    r = m.remember("用户偏好英文回复", origin="user", type="偏好")
    assert r["action"] == "added"              # 未自动作废
    assert r["conflict"] and r["conflict"]["similarity"] >= m.CONFLICT_SIM
    assert "supersede_key=" in r["message"]     # 提示里给出可执行的解决方式（带 id）
    assert len(m.active_facts()) == 2


def test_distinct_objects_are_not_superseded(mem):
    """实测最危险的误伤：项目A/项目B 相似度 0.600，绝不能互相作废。"""
    m, p = mem
    m.remember("项目A用PostgreSQL", origin="user")
    r = m.remember("项目B用PostgreSQL", origin="user")
    assert r["action"] == "added"
    assert len(m.active_facts()) == 2


def test_similarity_measurement_is_stable():
    """把模块文档里那张实测表固化成回归（阈值决策的依据）。"""
    assert mf.similarity("用户偏好中文回复", "用户偏好英文回复") < mf.CONFLICT_SIM + 0.02
    a = mf.similarity("用户偏好中文回复", "用户偏好英文回复")
    b = mf.similarity("项目A用PostgreSQL", "项目B用PostgreSQL")
    # 关键结论：应作废的相似度（a）不高于绝不能作废的（b）——相似度不可作判据
    assert a <= b + 0.05
    assert mf.similarity("项目采用纯静态架构", "项目改用 Next.js 架构") < mf.CONFLICT_SIM


# ── 向后兼容 ─────────────────────────────────────────────────────────────

def test_legacy_facts_are_normalized_without_rewrite(mem):
    m, p = mem
    legacy = {"enabled": True, "facts": [
        {"key": "备份", "value": "每周五备份数据库", "type": "事实",
         "ts": "2026-09-01T10:00:00", "entities": [], "relations": []}]}
    p.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    items = m.all_facts()
    assert len(items) == 1
    f = items[0]
    assert f["origin"] == "unknown" and f["status"] == "active" and f["id"]
    # 读不改写：磁盘上仍是原样（无新增字段）
    assert "origin" not in _raw(p)["facts"][0]


def test_stats_counts_by_origin(mem):
    m, p = mem
    m.remember("a", origin="user")
    m.remember("b", origin="agent")
    m.remember("c", origin="web")
    s = m.stats()
    assert s["total"] == 3 and s["active"] == 3
    assert s["by_origin"]["user"] == 1 and s["by_origin"]["web"] == 1


# ── 注入渲染 ─────────────────────────────────────────────────────────────

def test_render_marks_non_user_origin(mem):
    """关键：模型必须能区分「用户明说」与「我自己推断的」。"""
    m, p = mem
    m.remember("用户偏好中文回复", origin="user")
    m.remember("用户下周要交报价单", origin="agent")
    m.remember("某网页称该库已弃用", origin="web")
    text = m.render_for_context()
    assert "[长期记忆]" in text
    assert "\n- 用户偏好中文回复" in text                 # user 不标注
    assert "〔推断〕用户下周要交报价单" in text
    assert "〔来自外部内容〕某网页称该库已弃用" in text
    assert "不得当用户前提" in text


def test_render_excludes_superseded(mem):
    m, p = mem
    m.remember("用户偏好中文回复", origin="user", key="语言偏好")
    m.remember("用户偏好英文回复", origin="user", key="语言偏好",
               supersede_key="语言偏好")
    text = m.render_for_context()
    assert "英文回复" in text and "中文回复" not in text


def test_render_none_when_empty(mem):
    m, p = mem
    assert m.render_for_context() is None
