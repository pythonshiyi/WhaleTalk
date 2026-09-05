# -*- coding: utf-8 -*-
"""tool_trace 异步留痕回归：后台聚合写盘、顺序保持、flush 排空、目录跟随 init。

对应 P2：工具留痕从「同步 open/append/close」改为「有界队列 + 工作线程批量写」，
保证高频工具循环下调用线程不被磁盘 I/O 拖累。
"""
import os
import time

import permissions


def _init_tmp(tmp_path, tag="logs"):
    logs = tmp_path / tag
    permissions.init(
        str(tmp_path / "permissions.json"),
        str(tmp_path / "workspace"),
        audit_dir=str(logs),
    )
    permissions.set_audit_enabled(True)
    return logs


def test_tool_trace_async_batch_and_flush(tmp_path):
    logs = _init_tmp(tmp_path)
    try:
        # 多次调用后 flush：行必须全部落盘且保持入队顺序
        for i in range(50):
            permissions.tool_trace(f"tool_{i}", {"k": i}, f"result-{i}", 0.01)
        permissions.tool_trace_flush(timeout=5.0)

        path = logs / "tools.log"
        assert path.exists(), "flush 后 tools.log 应已落盘"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 50, f"应写入 50 行，实际 {len(lines)}"
        assert "[tool:tool_0]" in lines[0] and "result-0" in lines[0]
        assert "[tool:tool_49]" in lines[-1] and "result-49" in lines[-1]
        # 顺序性：id 单调递增
        ids = []
        for ln in lines:
            start = ln.find("[tool:tool_")
            if start >= 0:
                ids.append(int(ln[start + len("[tool:tool_"):].split("]")[0]))
        assert ids == list(range(50)), "留痕行序应与调用顺序一致"
        print("[PASS] 批量落盘 + 顺序保持")
    finally:
        permissions.set_audit_enabled(False)


def test_tool_trace_worker_follows_reinit_dir(tmp_path):
    """init 换目录后，worker 应写到新目录（不固化启动时目录）。"""
    logs_a = _init_tmp(tmp_path, "logs_a")
    logs_b = tmp_path / "logs_b"
    try:
        permissions.tool_trace("first", {}, "in-a", 0.0)
        permissions.tool_trace_flush(timeout=5.0)
        assert (logs_a / "tools.log").exists()

        # 重新 init 到新目录，再留痕：应写入 logs_b 而非 logs_a
        permissions.init(
            str(tmp_path / "permissions.json"),
            str(tmp_path / "workspace"),
            audit_dir=str(logs_b),
        )
        permissions.tool_trace("second", {}, "in-b", 0.0)
        permissions.tool_trace_flush(timeout=5.0)
        assert (logs_b / "tools.log").exists(), "re-init 后留痕应写入新目录"
        assert "in-b" in (logs_b / "tools.log").read_text(encoding="utf-8")
        # 旧目录不再被追加（保持只有 first 的 1 行）
        old = (logs_a / "tools.log").read_text(encoding="utf-8")
        assert old.count("in-a") == 1 and "in-b" not in old
        print("[PASS] worker 跟随 init 目录切换")
    finally:
        permissions.set_audit_enabled(False)


def test_tool_trace_disabled_is_noop(tmp_path):
    """审计关闭（AUDIT_ENABLED=False）时留痕为 no-op，不创建文件。"""
    logs = _init_tmp(tmp_path)
    permissions.set_audit_enabled(False)
    try:
        permissions.tool_trace("x", {}, "y", 0.0)
        permissions.tool_trace_flush(timeout=2.0)
        time.sleep(0.3)
        assert not (logs / "tools.log").exists(), "审计关闭时不应产生日志文件"
        print("[PASS] 审计关闭为 no-op")
    finally:
        permissions.set_audit_enabled(False)
