# -*- coding: utf-8 -*-
"""失败记忆生命周期的 API / 工具 / 记账链路回归（G18 接线层）。

覆盖：_record_failure 落盘（带指纹）/ _auto_resolve_failure /
_failures_action 三种动作 / failure_memory 工具四种动作 /
_tool_bookkeeping 成功即自动消解（修复验证闭环）。
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402
import deepseek_client as dc  # noqa: E402
import stores  # noqa: E402


@pytest.fixture()
def fail_paths(tmp_path, monkeypatch):
    active = str(tmp_path / "failures.json")
    archive = str(tmp_path / "failures_archive.json")
    monkeypatch.setattr(api_server, "FAILURES_PATH", active)
    monkeypatch.setattr(api_server, "FAILURES_ARCHIVE_PATH", archive)
    # 记账链路会顺带写成功模式/最近产物，一并隔离，避免污染真实数据目录
    monkeypatch.setattr(api_server, "PATTERNS_PATH", str(tmp_path / "patterns.json"))
    monkeypatch.setattr(api_server, "RECENT_PATH", str(tmp_path / "recent.json"))
    monkeypatch.setattr(dc, "FAILURES_FILE", active)
    monkeypatch.setattr(dc, "FAILURES_ARCHIVE_FILE", archive)
    return {"active": active, "archive": archive}


def _items(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_record_failure_writes_fingerprint(fail_paths):
    api_server._record_failure("list_dir", r"错误：目录不存在：d:\x\evolutions")
    items = _items(fail_paths["active"])
    assert len(items) == 1
    assert items[0]["fingerprint"] and items[0]["hits"] == 1
    assert items[0]["resolved"] is False


def test_bookkeeping_auto_resolves_on_success(fail_paths):
    """修复验证闭环：失败被记录 → 同工具连续成功 → 自动消解且不再注入。"""
    api_server._record_failure("list_dir", r"错误：目录不存在：d:\x\evolutions")
    assert stores.failure_patterns_text(fail_paths["active"]) != ""

    api_server._tool_bookkeeping("list_dir", {}, "DIR  .git\nFILE a.py 1KB")
    assert _items(fail_paths["active"])[0]["resolved"] is False, "单次成功不消解（防偶发）"
    api_server._tool_bookkeeping("list_dir", {}, "DIR  .git\nFILE a.py 1KB")

    items = _items(fail_paths["active"])
    assert items[0]["resolved"] is True
    assert items[0]["resolved_by"] == "auto:success"
    assert stores.failure_patterns_text(fail_paths["active"]) == ""


def test_bookkeeping_keeps_recording_new_failures(fail_paths):
    api_server._tool_bookkeeping("image_generate", {}, "错误：图片生成失败: 404 Not Found")
    items = _items(fail_paths["active"])
    assert items[0]["tool"] == "image_generate" and items[0]["resolved"] is False


def test_failure_action_resolve_by_tool_and_fingerprint(fail_paths):
    api_server._record_failure("list_dir", r"错误：目录不存在：d:\x\evolutions")
    api_server._record_failure("call_api", "Client error 500")

    out, err = api_server._failures_action({"tool": "list_dir", "note": "已补建目录"}, "resolve")
    assert err is None and out["affected"] == 1
    assert out["stats"]["unresolved"] == 1

    fp = [it for it in out["failures"] if it["tool"] == "call_api"][0]["fingerprint"]
    out, err = api_server._failures_action({"fingerprint": fp}, "resolve")
    assert err is None and out["affected"] == 1
    assert out["stats"]["unresolved"] == 0


def test_failure_action_reopen_and_forget(fail_paths):
    api_server._record_failure("list_dir", "错误：x")
    api_server._failures_action({"tool": "list_dir"}, "resolve")
    out, err = api_server._failures_action({"tool": "list_dir"}, "reopen")
    assert err is None and out["affected"] == 1 and out["stats"]["unresolved"] == 1

    out, err = api_server._failures_action({"tool": "list_dir"}, "forget")
    assert err is None and out["affected"] == 1 and out["stats"]["total"] == 0


def test_failure_action_requires_selector(fail_paths):
    out, err = api_server._failures_action({}, "resolve")
    assert out is None and "fingerprint 或 tool" in err
    out, err = api_server._failures_action({"tool": "x"}, "bogus")
    assert out is None and "未知动作" in err


def test_failure_memory_tool_roundtrip(fail_paths):
    from agent_tools.tool_brain import failure_memory

    empty = failure_memory("list")
    assert "没有" in empty

    api_server._record_failure("list_dir", r"错误：目录不存在：d:\x\evolutions")
    api_server._record_failure("list_dir", r"错误：目录不存在：d:\y\evolutions")
    listed = failure_memory("list")
    assert "list_dir" in listed and "× 2 次" in listed and "fingerprint:" in listed

    assert "活跃 1 条" in failure_memory("stats")
    resolved = failure_memory("resolve", tool="list_dir", note="目录已补建")
    assert "1 条" in resolved
    assert failure_memory("list") != "" and "没有未消解的" in failure_memory("list")
    assert "未消解 0" in failure_memory("stats")

    assert "1 条" in failure_memory("reopen", tool="list_dir")
    assert "1 条" in failure_memory("forget", tool="list_dir")
    assert "活跃 0 条" in failure_memory("stats")


def test_failure_memory_tool_requires_injected_path(monkeypatch):
    from agent_tools.tool_brain import failure_memory

    monkeypatch.setattr(dc, "FAILURES_FILE", None)
    assert "路径未注入" in failure_memory("list")


def test_failure_list_endpoint_helper_filters(monkeypatch, tmp_path, fail_paths):
    """_g_v1_failures 的数据准备口径：未消解过滤 + 按最近排序 + 统计。"""
    api_server._record_failure("list_dir", "错误：A")
    api_server._record_failure("call_api", "Client error 500")
    api_server._failures_action({"tool": "call_api"}, "resolve")

    all_items = [x for x in (stores.normalize_failure(i) for i in stores.load_failures(fail_paths["active"])) if x]
    live = [it for it in all_items if not it.get("resolved")]
    assert len(all_items) == 2 and len(live) == 1
    assert live[0]["tool"] == "list_dir"
    assert stores.failure_stats(fail_paths["active"])["resolved"] == 1
