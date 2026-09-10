# -*- coding: utf-8 -*-
"""后台进程契约回归（G20）。

覆盖三处同根源缺陷与两处契约易错点：
  1. stop_process / cleanup_idle_processes 曾「杀失败仍摘除条目」→ 进程孤儿化
     （端口占着、stop/list 都看不见、再也停不掉，实测复现）；
  2. stop_process 参数名 target 与 start_process 的 name 错位 → 模型高频猜错
     （本轮自检中连错 3 次）；
  3. start_process 缺 cwd、_stop_process/_start_process 的 ok 恒为 True（不诚实）。
"""
import collections
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import deepseek_client as dc  # noqa: E402  必须先导入，构建工具注册表
import api_server  # noqa: E402
from agent_tools import tool_files  # noqa: E402


class _FakeProc:
    def __init__(self, pid=99999):
        self.pid = pid

    def poll(self):
        return None

    def wait(self, timeout=None):
        return None


@pytest.fixture(autouse=True)
def _clean_proc(monkeypatch):
    monkeypatch.setattr(tool_files, "_emit_process", lambda *a, **k: None)
    monkeypatch.setattr(dc, "_emit_process", lambda *a, **k: None)
    yield
    with dc._PROCESSES_LOCK:
        dc.PROCESSES.clear()


def _add_fake(name="fake", pid=99999):
    with dc._PROCESSES_LOCK:
        dc.PROCESSES[name] = {
            "proc": _FakeProc(pid), "pid": pid, "name": name,
            "started": "00:00:00", "started_ts": 0.0,
            "exited": False, "code": None, "lines": collections.deque(maxlen=10),
        }


def test_stop_process_accepts_aliases(monkeypatch):
    """name/process_id/pid 三个别名都应能命中同一进程。"""
    for alias_kw in ({"name": "fake"}, {"process_id": "99999"}, {"pid": "99999"}, {"target": "fake"}):
        _add_fake()
        monkeypatch.setattr(tool_files, "_kill_tree", lambda p: True)
        out = tool_files.stop_process(**alias_kw)
        assert out.startswith("已停止进程「fake」"), f"{alias_kw} → {out}"
        assert "fake" not in dc.PROCESSES


def test_stop_process_keeps_entry_when_kill_fails(monkeypatch):
    """杀失败必须保留条目并如实报错，绝不能摘除（否则进程孤儿化）。"""
    _add_fake()
    monkeypatch.setattr(tool_files, "_kill_tree", lambda p: False)
    out = tool_files.stop_process(name="fake")
    assert out.startswith("错误：进程「fake」"), out
    assert "taskkill /F /T /PID" in out, "应给出强制终止的下一步"
    assert "fake" in dc.PROCESSES, "杀失败时条目必须保留，可重试"


def test_stop_process_missing_arg():
    assert tool_files.stop_process().startswith("错误：需要进程名或 pid")
    assert tool_files.stop_process(target="").startswith("错误：需要进程名或 pid")


def test_stop_process_not_found():
    out = tool_files.stop_process(name="nope_999")
    assert out.startswith("错误：未找到进程：nope_999"), out


def test_cleanup_idle_keeps_entry_when_kill_fails(monkeypatch):
    _add_fake()
    monkeypatch.setattr(dc, "_kill_tree", lambda p: False)
    killed = dc.cleanup_idle_processes(force_all=True)
    assert killed == [], "杀失败的进程不得计为已清理"
    assert "fake" in dc.PROCESSES, "杀失败时条目必须保留"


def test_cleanup_idle_pops_when_killed(monkeypatch):
    _add_fake()
    monkeypatch.setattr(dc, "_kill_tree", lambda p: True)
    killed = dc.cleanup_idle_processes(force_all=True)
    assert killed == ["fake"]
    assert "fake" not in dc.PROCESSES


def test_start_process_rejects_missing_cwd():
    out = tool_files.start_process("echo hi", cwd="Z:/__no_such_dir__/xyz")
    assert out.startswith("错误：工作目录不存在")


def test_start_process_accepts_cwd(tmp_path, monkeypatch):
    """cwd 是模型常用参数（此前缺它导致契约错误），应能显式指定并启动成功。"""
    monkeypatch.setattr(tool_files, "_kill_tree", lambda p: True)
    code = "import time; time.sleep(30)"
    out = tool_files.start_process(f"{sys.executable} -c \"{code}\"",
                                   name="cwd_test", cwd=str(tmp_path))
    assert "已启动后台进程「cwd_test」" in out, out
    try:
        assert any(k == "cwd_test" for k in dc.PROCESSES)
    finally:
        tool_files.stop_process(name="cwd_test")


def test_api_stop_process_honest_ok(monkeypatch):
    monkeypatch.setattr(dc, "stop_process", lambda t: "错误：未找到进程：x（运行中：无）")
    out, err = api_server._stop_process({"name": "x"})
    assert err is None
    assert out["ok"] is False and out["error"], "未找到进程时 ok 不得为 True"

    monkeypatch.setattr(dc, "stop_process", lambda t: "已停止进程「x」（pid=1）")
    out, err = api_server._stop_process({"target": "x"})
    assert out["ok"] is True


def test_api_stop_process_accepts_aliases():
    out, err = api_server._stop_process({})
    assert out is None and "name/target/pid" in err


def test_api_start_process_honest_ok(monkeypatch):
    monkeypatch.setattr(dc, "start_process", lambda command, name="", cwd="": "错误：命令为空")
    out, err = api_server._start_process({"command": "x"})
    assert err is None and out["ok"] is False and out["error"]
