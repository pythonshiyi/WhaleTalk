"""记忆洞察回归：置信度/时效衰减、排序、溯源、冲突检测（memory_store B3 只读层）。"""
import memory_store as ms


def _entry(**kw):
    base = {"id": "m1", "text": "用户喜欢 Python", "type": "偏好", "importance": 4,
            "tags": ["语言"], "entities": ["Python"], "source": "brain", "ts": "",
            "last_hit": "", "hit_count": 0, "archived": False}
    base.update(kw)
    return base


def test_confidence_archived_is_zero():
    assert ms.confidence(_entry(archived=True)) == 0.0


def test_confidence_recent_beats_old():
    now = 1_700_000_000.0
    recent = ms.confidence(_entry(ts=now), now=now)
    old = ms.confidence(_entry(ts=now - 90 * 86400), now=now)
    assert recent > old


def test_confidence_importance_and_hits():
    now = 1_700_000_000.0
    assert ms.confidence(_entry(importance=5, ts=now), now=now) > \
           ms.confidence(_entry(importance=1, ts=now), now=now)
    assert ms.confidence(_entry(ts=now, hit_count=10), now=now) > \
           ms.confidence(_entry(ts=now, hit_count=0), now=now)


def test_rank_sorts_and_annotates():
    now = 1_700_000_000.0
    low = _entry(id="low", importance=1, ts=now - 200 * 86400)
    high = _entry(id="high", importance=5, ts=now)
    out = ms.rank([low, high], now=now)
    assert [e["id"] for e in out] == ["high", "low"]
    assert all("confidence" in e for e in out)
    assert "confidence" not in low  # 不改入参


def test_parse_ts_formats():
    assert ms._parse_ts(1000) == 1000.0
    assert ms._parse_ts("1000") == 1000.0
    assert ms._parse_ts("2020-01-01T00:00:00") > 0
    assert ms._parse_ts("not-a-date") == 0.0
    assert ms._parse_ts("") == 0.0


def test_provenance_fields():
    p = ms.provenance(_entry(source="memory.json", hit_count=3, ts="2026-01-01T00:00:00"))
    for k in ("id", "source", "ts", "hit_count", "confidence", "supersedes", "version_id"):
        assert k in p
    assert p["source"] == "memory.json" and p["hit_count"] == 3


def test_conflicts_flags_same_topic_low_similarity():
    a = _entry(id="a", text="用户住在北京朝阳区", tags=["居住地"], entities=["北京"])
    b = _entry(id="b", text="用户住在上海浦东新区", tags=["居住地"], entities=["上海"])
    pairs = ms.conflicts([a, b])
    assert len(pairs) == 1
    assert pairs[0]["topic"] == "居住地"
    assert {pairs[0]["a_id"], pairs[0]["b_id"]} == {"a", "b"}


def test_conflicts_ignores_identical_or_unrelated():
    same1 = _entry(id="s1", text="用户喜欢 Python", tags=["语言"])
    same2 = _entry(id="s2", text="用户喜欢 Python", tags=["语言"])
    assert ms.conflicts([same1, same2]) == []
    no_topic1 = _entry(id="n1", text="甲", tags=[], entities=[])
    no_topic2 = _entry(id="n2", text="乙", tags=[], entities=[])
    assert ms.conflicts([no_topic1, no_topic2]) == []


def test_conflicts_skips_archived():
    a = _entry(id="a", text="用户住在北京", tags=["居住地"])
    b = _entry(id="b", text="用户住在上海", tags=["居住地"], archived=True)
    assert ms.conflicts([a, b]) == []
