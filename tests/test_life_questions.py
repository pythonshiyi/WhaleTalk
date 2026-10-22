"""开放问题账本的行为约束。

核心不变量：
1. id 必须唯一（实测踩过：同一毫秒连续登记生成相同 id，mark() 反复只改第一条）
2. 同一 claim 只登记一次（幂等）
3. 核销后统计立刻反映，超期未核会进 stale
4. 账本损坏时回落空列表，而不是让调用方崩溃
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from life import questions as Q


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "open_questions.json"
    monkeypatch.setattr(Q, "path", lambda: p)
    return p


def test_add_and_mark(ledger):
    a = Q.add("A 未知", "cmdA", "1s")
    Q.add("B 未知", "cmdB", "2s")
    assert a["status"] == "unverified"
    assert len(Q.load()) == 2

    Q.mark(a["id"], "verified", "证据在此")
    st = Q.stats()
    assert (st["verified"], st["unverified"]) == (1, 1)
    assert [q for q in Q.load() if q["id"] == a["id"]][0]["evidence"] == "证据在此"


def test_ids_unique_in_same_millisecond(ledger):
    ids = [Q.add(f"断言 {i}", status="unverified")["id"] for i in range(6)]
    assert len(set(ids)) == 6


def test_duplicate_claim_is_idempotent(ledger):
    first = Q.add("同一条未知")
    second = Q.add("同一条未知")
    assert first["id"] == second["id"]
    assert len(Q.load()) == 1


def test_stale_after_seven_days(ledger):
    Q.add("久未核的未知")
    items = Q.load()
    items[0]["last_checked"] = (datetime.now().astimezone() - timedelta(days=9)).isoformat()
    Q.save(items)
    st = Q.stats()
    assert st["stale"] == 1 and st["unverified"] == 1


def test_refuted_counted_separately(ledger):
    q = Q.add("会被推翻的断言")
    Q.mark(q["id"], "refuted", "证据表明不成立")
    st = Q.stats()
    assert st["refuted"] == 1 and st["verified"] == 0


def test_missing_ledger_returns_empty(ledger):
    assert Q.load() == []
    assert Q.stats()["total"] == 0


def test_broken_ledger_does_not_raise(ledger):
    ledger.write_text("{ not json", encoding="utf-8")
    assert Q.load() == []


def test_due_prefers_oldest(ledger):
    a = Q.add("新未知")
    b = Q.add("旧未知")
    items = Q.load()
    for q in items:
        if q["id"] == b["id"]:
            q["last_checked"] = (datetime.now().astimezone() - timedelta(days=5)).isoformat()
    Q.save(items)
    assert Q.due(1)[0]["id"] == b["id"]
