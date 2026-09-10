# -*- coding: utf-8 -*-
"""失败模式生命周期回归（G18）。

覆盖：指纹归并 / 状态码不误并 / 自动消解（修复验证）/ 复现重开 /
人工消解 / 撤销消解 / 彻底移除 / 溢出归档 / 旧格式兼容 / 注入只含未消解项。
"""
import json
import os

import pytest

import stores


@pytest.fixture()
def paths(tmp_path):
    return {
        "active": str(tmp_path / "failures.json"),
        "archive": str(tmp_path / "failures_archive.json"),
    }


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


WIN_A = r"错误：目录不存在：d:\jingyu\whaletalk\evolutions"
WIN_B = r"错误：目录不存在：d:\other\project\evolutions"


def test_fingerprint_merges_same_error_across_paths(paths):
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_A}],
                           archive_path=paths["archive"], now="2026-09-10 13:36:42")
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_B}],
                           archive_path=paths["archive"], now="2026-09-10 14:00:00")
    items = _load(paths["active"])
    assert len(items) == 1, "同一类错误换路径后应归并为一条"
    assert items[0]["hits"] == 2, "复现次数应累加"
    assert items[0]["first_ts"] == "2026-09-10 13:36:42"
    assert items[0]["last_ts"] == "2026-09-10 14:00:00"
    assert items[0]["fingerprint"]


def test_different_status_codes_are_not_merged(paths):
    stores.record_failures(paths["active"], [{"tool": "call_api", "error": "Client error 404 Not Found"}],
                           archive_path=paths["archive"])
    stores.record_failures(paths["active"], [{"tool": "call_api", "error": "Client error 500 Server Error"}],
                           archive_path=paths["archive"])
    assert len(_load(paths["active"])) == 2, "404 与 500 是不同失败，不得被数字归一误并"


def test_different_tools_are_not_merged(paths):
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": "错误：目录不存在"}],
                           archive_path=paths["archive"])
    stores.record_failures(paths["active"], [{"tool": "read_file", "error": "错误：目录不存在"}],
                           archive_path=paths["archive"])
    assert len(_load(paths["active"])) == 2


def test_inject_text_excludes_resolved_and_shows_hits(paths):
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_A}],
                           archive_path=paths["archive"], now="2026-09-10 13:36:42")
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_B}],
                           archive_path=paths["archive"], now="2026-09-10 13:40:00")
    text = stores.failure_patterns_text(paths["active"])
    assert "list_dir" in text and "复现 2 次" in text

    stores.auto_resolve_on_success(paths["active"], "list_dir",
                                   archive_path=paths["archive"], now="2026-09-10 15:00:00", streak=1)
    assert stores.failure_patterns_text(paths["active"]) == "", "已消解的失败不得再注入上下文"


def test_auto_resolve_requires_consecutive_successes(paths):
    """连续成功才消解（默认 2 次）：一次偶发成功不应掩盖真故障。"""
    stores.record_failures(paths["active"], [{"tool": "image_generate", "error": "404 Not Found"}],
                           archive_path=paths["archive"])
    assert stores.auto_resolve_on_success(paths["active"], "image_generate",
                                          archive_path=paths["archive"]) == 0
    assert stores.failure_stats(paths["active"])["unresolved"] == 1
    assert _load(paths["active"])[0]["ok_streak"] == 1

    assert stores.auto_resolve_on_success(paths["active"], "image_generate",
                                          archive_path=paths["archive"]) == 1
    it = _load(paths["active"])[0]
    assert it["resolved"] is True
    assert it["resolved_by"] == "auto:success"
    assert it["resolved_ts"]
    assert stores.failure_stats(paths["active"]) == {
        "total": 1, "unresolved": 0, "resolved": 1, "recurring": 0,
    }


def test_failure_resets_success_streak(paths):
    """成功计数期间任一次失败 → 计数清零，重新起算。"""
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_A}],
                           archive_path=paths["archive"])
    stores.auto_resolve_on_success(paths["active"], "list_dir", archive_path=paths["archive"])
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_B}],
                           archive_path=paths["archive"])
    assert _load(paths["active"])[0]["ok_streak"] == 0
    assert stores.auto_resolve_on_success(paths["active"], "list_dir",
                                          archive_path=paths["archive"]) == 0


def test_recurrence_reopens_resolved(paths):
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_A}],
                           archive_path=paths["archive"])
    stores.auto_resolve_on_success(paths["active"], "list_dir", archive_path=paths["archive"], streak=1)
    assert _load(paths["active"])[0]["resolved"] is True
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_B}],
                           archive_path=paths["archive"], now="2026-09-10 16:00:00")
    it = _load(paths["active"])[0]
    assert it["resolved"] is False, "复现即视为未修复"
    assert it["resolved_ts"] is None
    assert it["hits"] == 2


def test_resolve_by_fingerprint_and_tool(paths):
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_A}],
                           archive_path=paths["archive"])
    fp = _load(paths["active"])[0]["fingerprint"]
    hit, items = stores.resolve_failures(paths["active"], fingerprint=fp, note="已补建目录")
    assert hit == 1 and items[0]["resolved"] is True and items[0]["note"] == "已补建目录"

    # 已消解项不会被重复消解
    assert stores.resolve_failures(paths["active"], fingerprint=fp)[0] == 0

    stores.record_failures(paths["active"], [{"tool": "call_api", "error": "500"}],
                           archive_path=paths["archive"])
    assert stores.resolve_failures(paths["active"], tool="call_api")[0] == 1


def test_reopen_and_forget(paths):
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_A}],
                           archive_path=paths["archive"])
    stores.resolve_failures(paths["active"], tool="list_dir")
    assert stores.reopen_failures(paths["active"], tool="list_dir")[0] == 1
    assert _load(paths["active"])[0]["resolved"] is False
    removed, keep = stores.forget_failures(paths["active"], tool="list_dir")
    assert removed == 1 and keep == []


def test_overflow_archives_resolved_first(paths):
    """超上限时优先归档已消解项，未消解项尽量保留。"""
    for i in range(3):
        stores.record_failures(paths["active"], [{"tool": f"t{i}", "error": f"错误 {i}"}],
                               archive_path=paths["archive"])
    stores.resolve_failures(paths["active"], tool="t0")
    # 4 条（3 未消解 + 1 已消解）压到上限 3 → 只该归档已消解的 t0
    stores.record_failures(paths["active"], [{"tool": "t9", "error": "错误 9"}],
                           archive_path=paths["archive"], max_failures=3)
    active = _load(paths["active"])
    assert sorted(it["tool"] for it in active) == ["t1", "t2", "t9"]
    assert [it["tool"] for it in _load(paths["archive"])] == ["t0"], "已消解项应优先归档"


def test_overflow_keeps_most_recent_when_all_unresolved(paths):
    """连未消解项都超上限时：保留最近出现的，最旧的归档。"""
    for i in range(4):
        stores.record_failures(paths["active"], [{"tool": f"t{i}", "error": f"错误 {i}"}],
                               archive_path=paths["archive"],
                               now=f"2026-09-10 10:0{i}:00")
    stores.record_failures(paths["active"], [{"tool": "t9", "error": "错误 9"}],
                           archive_path=paths["archive"], max_failures=2,
                           now="2026-09-10 11:00:00")
    active = _load(paths["active"])
    assert len(active) == 2
    assert sorted(it["tool"] for it in active) == ["t3", "t9"], "应保留最近出现的未消解项"
    assert sorted(it["tool"] for it in _load(paths["archive"])) == ["t0", "t1", "t2"]


def test_legacy_format_is_normalized(paths):
    legacy = [{"tool": "list_dir", "error": WIN_A, "ts": "2026-09-10 13:36:42"}]
    with open(paths["active"], "w", encoding="utf-8") as f:
        json.dump(legacy, f, ensure_ascii=False)
    stores.record_failures(paths["active"], [{"tool": "list_dir", "error": WIN_B}],
                           archive_path=paths["archive"], now="2026-09-10 14:00:00")
    items = _load(paths["active"])
    assert len(items) == 1, "旧格式条目应能参与指纹归并"
    assert items[0]["fingerprint"] and items[0]["hits"] == 2
    assert items[0]["first_ts"] == "2026-09-10 13:36:42"


def test_empty_and_broken_input_is_safe(paths):
    assert stores.record_failures(paths["active"], []) == []
    assert stores.record_failures(paths["active"], [None, "x"]) == []
    with open(paths["active"], "w", encoding="utf-8") as f:
        f.write("{不是合法 JSON")
    assert stores.load_failures(paths["active"]) == []
    assert stores.failure_patterns_text(paths["active"]) == ""
    assert stores.resolve_failures(paths["active"])[0] == 0
    assert stores.forget_failures(paths["active"])[0] == 0
    assert os.path.exists(paths["active"])
