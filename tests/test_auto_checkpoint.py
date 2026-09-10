# -*- coding: utf-8 -*-
"""长任务自动打点回归（G15）。

现象：task_checkpoint_save 带 auto 参数、注释也写着"每步工具后写入"，但 Web 版
从未自动调用 → has_checkpoint 恒为 false，长任务崩溃/断电后进度与结论全丢。
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402
import shared  # noqa: E402
import shared  # noqa: E402


@pytest.fixture()
def ck(tmp_path, monkeypatch):
    path = str(tmp_path / "task_checkpoint.json")
    monkeypatch.setattr(api_server, "CHECKPOINT_PATH", path)
    monkeypatch.setattr(api_server, "_audit", lambda *a, **k: None)
    api_server._LAST_TOOL_CHAIN.clear()
    api_server._LAST_AUTO_CHECKPOINT = 0
    api_server._set_current_task("")
    yield path
    api_server._LAST_TOOL_CHAIN.clear()
    api_server._LAST_AUTO_CHECKPOINT = 0
    api_server._set_current_task("")


def _push(*names):
    api_server._LAST_TOOL_CHAIN.extend(names)


def test_no_checkpoint_below_threshold(ck):
    _push(*["t%d" % i for i in range(shared.AUTO_CHECKPOINT_TOOLS - 1)])
    assert api_server._auto_checkpoint("t") == 0
    assert not __import__("os").path.exists(ck)


def test_checkpoint_written_at_threshold_named_by_task(ck):
    api_server._set_current_task("做一份行业调研报告")
    _push(*["t%d" % i for i in range(shared.AUTO_CHECKPOINT_TOOLS)])
    assert api_server._auto_checkpoint("t") == 1
    data = json.load(open(ck, encoding="utf-8"))
    assert data["auto"] is True
    assert data["name"] == "做一份行业调研报告"
    assert data["steps"] == shared.AUTO_CHECKPOINT_TOOLS
    assert data["chain"][0] == "t0"
    assert "已执行" in data["notes"]


def test_fallback_name_without_task_title(ck):
    api_server._set_current_task("")
    _push(*["t%d" % i for i in range(shared.AUTO_CHECKPOINT_TOOLS)])
    assert api_server._auto_checkpoint("t") == 1
    assert "自动断点" in json.load(open(ck, encoding="utf-8"))["name"]


def test_checkpoint_is_throttled(ck):
    _push(*["t%d" % i for i in range(shared.AUTO_CHECKPOINT_TOOLS)])
    assert api_server._auto_checkpoint("t") == 1
    _push("x")  # +1 步：未到补写间隔
    assert api_server._auto_checkpoint("x") == 0
    _push(*["y%d" % i for i in range(shared.AUTO_CHECKPOINT_EVERY - 1)])
    assert api_server._auto_checkpoint("y") == 1, "达到补写间隔应再打一次"
    assert json.load(open(ck, encoding="utf-8"))["steps"] == shared.AUTO_CHECKPOINT_TOOLS + shared.AUTO_CHECKPOINT_EVERY


def test_bookkeeping_triggers_checkpoint(ck):
    """接线校验：_tool_bookkeeping 是工具记账的唯一漏斗，自动打点挂在它上面。"""
    api_server._set_current_task("长任务")
    for i in range(shared.AUTO_CHECKPOINT_TOOLS):
        api_server._tool_bookkeeping(f"tool{i}", {}, "ok")
    assert __import__("os").path.exists(ck)
    assert json.load(open(ck, encoding="utf-8"))["steps"] == shared.AUTO_CHECKPOINT_TOOLS


def test_clear_auto_checkpoint_keeps_manual(ck):
    manual = {"name": "我手动存的", "status": "进行中"}
    with open(ck, "w", encoding="utf-8") as f:
        json.dump(manual, f, ensure_ascii=False)
    assert api_server._clear_auto_checkpoint() is False, "手动断点不得被自动清理"
    assert __import__("os").path.exists(ck)

    with open(ck, "w", encoding="utf-8") as f:
        json.dump({"name": "自动", "auto": True}, f, ensure_ascii=False)
    assert api_server._clear_auto_checkpoint() is True
    assert not __import__("os").path.exists(ck)
    assert api_server._clear_auto_checkpoint() is False  # 幂等


def test_msg_text_handles_multimodal():
    assert api_server._msg_text({"role": "user", "content": " 你好 "}) == "你好"
    assert api_server._msg_text({"content": [{"type": "image_url"}, {"type": "text", "text": "看图"}]}) == "看图"
    assert api_server._msg_text({"content": [{"type": "image_url"}]}) == ""
    assert api_server._msg_text(None) == ""
