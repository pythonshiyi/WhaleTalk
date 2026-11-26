"""P0 立尺的回归测试：评测任务、验收检查、依赖门禁、发布门禁。

这些测试不调用模型、不启动服务，可在 CI 的任意 Python 版本跑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
for p in (str(REPO), str(REPO / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import agent_eval as ev  # noqa: E402
import check_deps  # noqa: E402
import check_release  # noqa: E402

# ---------------------------------------------------------------------------
# 评测任务定义
# ---------------------------------------------------------------------------

def test_golden_tasks_are_valid_and_unique():
    tasks = ev.load_tasks()
    assert len(tasks) >= 30, "P0 验收口径：黄金任务至少 30 个"
    ids = [t.id for t in tasks]
    assert len(ids) == len(set(ids))
    for t in tasks:
        assert t.turns and all(x.strip() for x in t.turns)
        assert t.accept
        assert t.budget.get("max_seconds", 0) > 0
    by_id = {t.id: t for t in tasks}
    assert by_id["memory_update"].fresh_session_at == [3]


def test_unknown_only_task_is_rejected():
    with pytest.raises(ev.EvalError):
        ev.load_tasks(only=["definitely-not-a-task"])


# ---------------------------------------------------------------------------
# 验收检查
# ---------------------------------------------------------------------------

def _task() -> ev.Task:
    return ev.Task(id="unit", category="files", turns=["x"], accept=[])


def _turns(content="完成", tool_name="write_file", result="ok") -> list:
    return [ev.TurnResult(
        index=1, content=content,
        tool_calls=[ev.ToolCall(name=tool_name, result=result)],
    )]


def test_file_and_json_checks(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "report.md").write_text("区域销售报告\n合计：3500\n", encoding="utf-8")
    (tmp_path / "out" / "stats.json").write_text(
        json.dumps({"sum": 36, "mean": 9.0, "max": 15}), encoding="utf-8"
    )
    turns = _turns()
    cases = [
        {"type": "file_exists", "path": "out/report.md"},
        {"type": "file_contains", "path": "out/report.md", "text": "3500"},
        {"type": "file_not_contains", "path": "out/report.md", "text": "编造"},
        {"type": "file_min_bytes", "path": "out/report.md", "bytes": 5},
        {"type": "json_field", "path": "out/stats.json", "field": "sum", "equals": 36},
        {"type": "json_field", "path": "out/stats.json", "field": "mean", "equals": 9},
    ]
    for spec in cases:
        r = ev.run_check(spec, tmp_path, _task(), turns)
        assert r.ok, f"{spec} -> {r.detail}"


def test_csv_checks(tmp_path):
    (tmp_path / "summary.csv").write_text(
        "region,total\n华南,1500\n华东,1200\n华北,800\n", encoding="utf-8"
    )
    turns = _turns()
    for spec in (
        {"type": "csv_columns", "path": "summary.csv", "columns": ["region", "total"]},
        {"type": "csv_row_count", "path": "summary.csv", "rows": 3},
        {"type": "csv_row", "path": "summary.csv", "index": 0, "values": ["华南", "1500"]},
    ):
        r = ev.run_check(spec, tmp_path, _task(), turns)
        assert r.ok, f"{spec} -> {r.detail}"


def test_tool_and_final_checks():
    ok_turns = _turns(content="蓝鲸-7，周三 22:00")
    assert ev.run_check(
        {"type": "tool_called", "name": "write_file"}, Path("."), _task(), ok_turns
    ).ok
    assert ev.run_check(
        {"type": "final_contains", "text": "蓝鲸-7"}, Path("."), _task(), ok_turns
    ).ok
    assert ev.run_check(
        {"type": "final_not_contains", "text": "蓝鲸-8"}, Path("."), _task(), ok_turns
    ).ok
    assert ev.run_check(
        {"type": "no_tool_error"}, Path("."), _task(), ok_turns
    ).ok

    bad_turns = _turns(result="错误：参数不对")
    assert not ev.run_check(
        {"type": "no_tool_error"}, Path("."), _task(), bad_turns
    ).ok
    assert ev.run_check(
        {"type": "no_tool_error", "allow": ["write_file"]}, Path("."), _task(), bad_turns
    ).ok


def test_json_container_checks(tmp_path):
    (tmp_path / "x.json").write_text(
        json.dumps({"a": 1, "b": [10, 20]}, ensure_ascii=False), encoding="utf-8"
    )
    turns = _turns()
    assert ev.run_check(
        {"type": "json_keys", "path": "x.json", "keys": ["a", "b"]}, tmp_path, _task(), turns
    ).ok
    assert ev.run_check(
        {"type": "json_len", "path": "x.json", "length": 2}, tmp_path, _task(), turns
    ).ok
    assert ev.run_check(
        {"type": "json_field", "path": "x.json", "field": "b.1", "equals": 20},
        tmp_path, _task(), turns,
    ).ok


def test_file_number_min(tmp_path):
    (tmp_path / "n.txt").write_text("共 42 个进程\n", encoding="utf-8")
    turns = _turns()
    assert ev.run_check(
        {"type": "file_number_min", "path": "n.txt", "min": 5}, tmp_path, _task(), turns
    ).ok
    assert not ev.run_check(
        {"type": "file_number_min", "path": "n.txt", "min": 100}, tmp_path, _task(), turns
    ).ok


def test_zip_and_command_checks(tmp_path):
    import zipfile

    with zipfile.ZipFile(tmp_path / "a.zip", "w") as zf:
        zf.writestr("docs/one.md", "x")
        zf.writestr("docs/two.md", "y")
    turns = _turns()
    assert ev.run_check(
        {"type": "zip_entries", "path": "a.zip", "entries": ["docs/one.md"]},
        tmp_path, _task(), turns,
    ).ok
    assert ev.run_check(
        {"type": "command", "argv": ["{python}", "-c", "print('ok')"], "timeout": 30},
        tmp_path, _task(), turns,
    ).ok
    assert not ev.run_check(
        {"type": "command", "argv": ["{python}", "-c", "raise SystemExit(3)"]},
        tmp_path, _task(), turns,
    ).ok


def test_image_and_xlsx_checks(tmp_path):
    pytest.importorskip("PIL")
    pytest.importorskip("openpyxl")
    from PIL import Image
    from openpyxl import Workbook

    Image.new("RGB", (320, 240), (1, 2, 3)).save(tmp_path / "pic.png")
    turns = _turns()
    assert ev.run_check(
        {"type": "image_min_size", "path": "pic.png", "width": 300, "height": 200},
        tmp_path, _task(), turns,
    ).ok
    assert not ev.run_check(
        {"type": "image_min_size", "path": "pic.png", "width": 400, "height": 300},
        tmp_path, _task(), turns,
    ).ok

    wb = Workbook()
    ws = wb.active
    ws["A1"], ws["B1"] = "name", 2
    wb.save(tmp_path / "t.xlsx")
    assert ev.run_check(
        {"type": "xlsx_cells", "path": "t.xlsx", "cells": [
            {"cell": "A1", "equals": "name"}, {"cell": "B1", "equals": 2},
        ]},
        tmp_path, _task(), turns,
    ).ok


def test_path_traversal_is_rejected(tmp_path):
    r = ev.run_check(
        {"type": "file_exists", "path": "../outside.txt"}, tmp_path, _task(), _turns()
    )
    assert not r.ok
    assert "越界" in r.detail


# ---------------------------------------------------------------------------
# 依赖 / 发布门禁
# ---------------------------------------------------------------------------

def test_requirements_have_upper_bounds():
    reqs = check_deps.parse_requirements()
    assert len(reqs) >= 30
    assert check_deps.missing_upper_bounds(reqs) == []


def test_lock_covers_direct_dependencies():
    reqs = check_deps.parse_requirements()
    lock = check_deps.load_lock()
    assert lock, "requirements.lock 缺失；运行 python tools/check_deps.py --freeze"
    direct = {name for _raw, _req, name in reqs}
    assert not (direct - set(lock)), f"锁文件缺少：{sorted(direct - set(lock))}"


def test_release_gate_check_passes():
    assert check_release.main(["--check"]) == 0


def test_version_is_semver():
    version = check_release.read_version()
    assert check_release.SEMVER.match(version)
