"""演化账本 P1：同标题提案去重 + 裁剪上限。"""
import brainkit as bk


def test_record_evolution_dedupes_same_proposed_title(tmp_path):
    a = bk.record_evolution("睡眠日结", kind="proposed", note="第一次")
    b = bk.record_evolution("睡眠日结", kind="proposed", note="第二次")
    assert a and b and a["id"] == b["id"]
    evo = bk.load_json(bk.BRAIN_DIR / "evolution.json", {})
    assert len(evo.get("proposals") or []) == 1
    assert evo["proposals"][0]["note"] == "第二次"


def test_prune_evolution_keeps_recent(tmp_path):
    for i in range(60):
        bk.record_evolution(f"提案 {i}", kind="proposed")
    assert bk.prune_evolution(50) == 10
    evo = bk.load_json(bk.BRAIN_DIR / "evolution.json", {})
    proposals = evo.get("proposals") or []
    assert len(proposals) == 50
    assert proposals[-1]["title"] == "提案 59"
