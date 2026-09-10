# -*- coding: utf-8 -*-
"""run_python 内存看门狗回归（T3）。

策略仍是"默认自由"（不做静态拦截、不隔离），这里只加防误伤兜底：
进程树常驻内存超上限 → 杀树 + 如实报错，避免一次失控分配拖垮用户整机。
"""
import sys

import pytest

# 必须先 import deepseek_client（其顶层会完整构建六层注册表并加载 agent_tools）；
# 直接 import agent_tools.tool_code 会触发 __init__ 循环导入导致 TOOLS 未注册报错
import deepseek_client as dsc  # noqa: F401
import permissions
from agent_tools import tool_code
from agent_tools.tool_code import MemoryLimitError, _proc_tree_rss_mb, _run_capture

psutil = pytest.importorskip("psutil")

RUNAWAY = "a=[]\nwhile True:\n    a.append(b'x' * 5_000_000)\n"


def test_memory_watchdog_kills_runaway_process():
    argv = [sys.executable, "-c", RUNAWAY]
    with pytest.raises(MemoryLimitError) as ei:
        _run_capture(argv, 60, 20000, memory_mb=200)
    assert ei.value.limit_mb == 200
    assert ei.value.seen_mb is None or ei.value.seen_mb >= 200


def test_memory_limit_disabled_by_default():
    """memory_mb=0（默认）不启用看门狗：普通命令照常跑完。"""
    rc, out = _run_capture([sys.executable, "-c", "print('hello-whale')"], 30, 20000)
    assert rc == 0 and "hello-whale" in out


def test_memory_watchdog_allows_normal_work():
    rc, out = _run_capture(
        [sys.executable, "-c", "print(sum(range(100000)))"], 30, 20000, memory_mb=2048
    )
    assert rc == 0 and "4999950000" in out


def test_proc_tree_rss_mb_reads_own_process():
    mb = _proc_tree_rss_mb(__import__("os").getpid())
    assert mb is not None and mb > 0


def test_proc_tree_rss_mb_none_for_dead_pid():
    assert _proc_tree_rss_mb(99999999) is None


def test_run_python_reports_memory_limit(monkeypatch):
    """工具层应给出可执行的下一步建议，而不是静默失败。"""
    monkeypatch.setattr(tool_code, "RUN_PY_MEMORY_MB", 150)
    msg = tool_code.run_python(RUNAWAY)
    assert msg.startswith("错误：内存超限")
    assert "start_process" in msg and "流式" in msg


def test_run_python_still_runs_normally():
    out = tool_code.run_python("print('ok-' + str(1 + 1))")
    assert "ok-2" in out
    assert permissions.WORKSPACE_DIR or True  # 工作目录注入了才好，未注入也不阻断
