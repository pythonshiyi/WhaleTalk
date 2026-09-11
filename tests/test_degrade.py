# -*- coding: utf-8 -*-
"""退化日志测试：静默降级要变成可见事实，且记录自身绝不能反噬主流程。"""
import os

import pytest

import degrade


@pytest.fixture(autouse=True)
def _clean():
    degrade.init(path=None, reset=True)
    yield
    degrade.init(path=None, reset=True)


def test_records_component_impact_and_count():
    degrade.degrade("context.memory", ValueError("bad json"), "长期记忆未注入")
    e = degrade.degrade("context.memory", ValueError("bad json"), "长期记忆未注入")
    assert e["count"] == 2
    assert e["component"] == "context.memory"
    assert e["last_impact"] == "长期记忆未注入"
    assert "ValueError" in e["sample"]
    assert e["first_ts"] and e["last_ts"]


def test_dedup_by_key_but_separate_keys_stay_apart():
    degrade.degrade("a", ValueError("x"))
    degrade.degrade("a", ValueError("y"))
    degrade.degrade("b", ValueError("x"))
    assert len(degrade.snapshot()) == 2  # a:ValueError 与 b:ValueError


def test_never_raises_on_broken_input():
    """记录降级绝不能抛出——它是旁路。"""
    class Boom:
        def __str__(self):
            raise RuntimeError("no str for you")

    assert degrade.degrade("weird", Boom(), "描述", detail="d") is not None
    assert degrade.degrade(None, None, None) is not None
    assert degrade.degrade("x", object()) is not None


def test_critical_is_sticky_and_drives_notice():
    assert degrade.critical_notice() == ""
    degrade.degrade("context.brain", RuntimeError("timeout"), "大脑上下文未注入", critical=True)
    degrade.degrade("context.brain", RuntimeError("timeout"), "", critical=False)
    note = degrade.critical_notice()
    assert "运行状态" in note and "context.brain" in note
    assert "大脑上下文未注入" in note
    # critical 只升不降：后续同类按关键处理
    assert degrade.snapshot()[0]["critical"] is True


def test_non_critical_never_enters_notice():
    degrade.degrade("ui.theme", ValueError("x"), "主题回退默认", critical=False)
    assert degrade.critical_notice() == ""
    assert degrade.summary()["count"] == 1
    assert degrade.summary()["critical"] == 0


def test_entry_cap_drops_oldest():
    for i in range(degrade.MAX_ENTRIES + 25):
        degrade.degrade(f"c{i}", ValueError("x"))
    assert len(degrade.snapshot(limit=10_000)) == degrade.MAX_ENTRIES


def test_summary_shape_and_sorting():
    degrade.degrade("old", ValueError("x"))
    degrade.degrade("new", ValueError("x"))
    snap = degrade.snapshot()
    assert set(snap[0]) >= {"key", "component", "count", "critical", "last_ts"}
    s = degrade.summary()
    assert s["count"] == 2 and isinstance(s["components"], list)


def test_persists_to_disk_when_path_injected(tmp_path):
    p = tmp_path / "degradations.json"
    degrade.init(path=str(p))
    degrade.degrade("sched", RuntimeError("boom"), "定时任务未启动", critical=True)
    assert degrade._maybe_flush(force=True) is True
    assert p.is_file()
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["entries"][0]["component"] == "sched"


def test_flush_failure_is_swallowed(tmp_path):
    """落盘失败（路径非法）也不能抛出。"""
    degrade.init(path=os.path.join(str(tmp_path), "nope", "\0bad", "x.json"))
    degrade.degrade("x", ValueError("y"), "z")
    assert degrade.snapshot()  # 内存里仍然有记录
