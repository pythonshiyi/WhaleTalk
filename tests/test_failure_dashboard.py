"""③d 失败模式看板回归：failure_dashboard 聚合口径。"""
import stores


def _seed(path):
    stores.save_failures(path, [
        {"tool": "read_file", "error": "not found", "hits": 3, "last_ts": "2026-01-02", "resolved": False},
        {"tool": "read_file", "error": "permission", "hits": 1, "last_ts": "2026-01-01", "resolved": False},
        {"tool": "run_python", "error": "syntax", "hits": 5, "last_ts": "2026-01-03", "resolved": False},
        {"tool": "fetch_url", "error": "timeout", "hits": 2, "last_ts": "2025-12-01",
         "resolved": True, "resolved_by": "auto:success"},
    ])


def test_failure_dashboard_aggregates(tmp_path):
    p = str(tmp_path / "failures.json")
    _seed(p)
    d = stores.failure_dashboard(p)
    assert d["total"] == 4
    assert d["unresolved"] == 3
    assert d["resolved"] == 1
    assert d["resolution_rate"] == 0.25
    assert d["auto_resolved"] == 1
    assert d["recurring"] == 2
    assert d["unresolved_hits"] == 9
    assert d["by_tool"][0] == {"tool": "read_file", "count": 2}
    assert d["top"][0]["tool"] == "run_python"   # hits 5 最高
    assert d["top"][0]["hits"] == 5


def test_failure_dashboard_empty(tmp_path):
    d = stores.failure_dashboard(str(tmp_path / "none.json"))
    assert d["total"] == 0 and d["resolution_rate"] == 0.0
    assert d["by_tool"] == [] and d["top"] == []
