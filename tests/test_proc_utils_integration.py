"""`proc_utils.kill_tree` 真实集成回归（非 mock）。

背景：`kill_tree` 是「终止类操作」的底座——`run_python` / `run_command` /
`start_process` / `stop_process` / 服务退出清理全部经它收敛。历史上它吞掉全部
异常且无返回值，导致调用方「杀失败却照样摘除进程条目」→ 进程孤儿化（端口占着、
list 看不见、再也停不掉，G20 事故）。

但既有回归 `test_process_contract.py` 把 `_kill_tree` **全部 mock 掉了**，于是
真实实现（`taskkill /T /F`、psutil 递归、`wait`+`poll` 的诚实契约）**从未被任何
用例覆盖**——这段代码坏了不会有测试红。本文件补上真实进程树集成用例。

设计取舍：不 mock 任何东西，真的起进程、真的杀、真的检查孙进程是否存活。
代价是慢（约 1-2 秒/例），但这是唯一能验证 taskkill /T 语义的办法。
"""
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from proc_utils import kill_tree  # noqa: E402

# 起一个「父 → 孙」两层进程树：父进程把自己的孙进程 pid 打到 stdout 方便断言。
_TREE_CODE = (
    "import subprocess,sys,time;"
    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
    "print(p.pid,flush=True);"
    "time.sleep(120)"
)


def _spawn_tree():
    """起一个两层进程树，返回 (proc, grandchild_pid)。"""
    proc = subprocess.Popen([sys.executable, "-c", _TREE_CODE],
                            stdout=subprocess.PIPE, text=True)
    line = proc.stdout.readline().strip()
    assert line.isdigit(), f"未能读到孙进程 pid：{line!r}"
    return proc, int(line)


def _pid_alive(pid):
    """进程是否仍存活（psutil 优先；缺失时回退到 tasklist/kill -0）。"""
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    except ImportError:
        pass
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    import os
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _reap(pid):
    """清理用例残留的孙进程（避免污染后续测试）。"""
    try:
        import psutil
        psutil.Process(pid).kill()
    except Exception:
        pass


def test_kill_tree_kills_grandchild_and_reports_true():
    """核心：`kill_tree` 必须真的杀掉**孙**进程，并诚实返回 True。

    这正是 `proc.kill()` 做不到的事（只杀直接子进程，孙进程残留占端口）。
    """
    proc, gpid = _spawn_tree()
    try:
        assert proc.poll() is None
        assert _pid_alive(gpid), "孙进程未起来，用例前置条件不成立"

        ok = kill_tree(proc)

        assert ok is True, "kill_tree 未回报成功"
        assert proc.poll() is not None, "父进程仍在运行"
        # 给 OS 一点回收时间再断言孙进程已死
        for _ in range(20):
            if not _pid_alive(gpid):
                break
            time.sleep(0.1)
        assert not _pid_alive(gpid), "孙进程残留（端口/CPU 会一直被占）"
    finally:
        _reap(gpid)
        if proc.poll() is None:
            proc.kill()


def test_kill_tree_returns_true_for_already_exited_process():
    """已退出的进程：应如实返回 True（没有可杀的东西 = 已达成目标）。

    调用方据此判断「进程确实不在了」，可以安全摘除进程条目。
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    assert proc.poll() is not None
    assert kill_tree(proc) is True


def test_kill_tree_handles_none_without_raising():
    """`proc=None` 必须安全返回 False，绝不抛异常。

    `stop_process` 等调用方会在「没找到进程」时传 None，抛异常会冒泡成 500。
    """
    assert kill_tree(None) is False


def test_kill_tree_is_honest_when_kill_cannot_succeed(monkeypatch):
    """诚实性契约：杀不掉时必须返回 False，不得谎报 True。

    回归的是 G20 根因——`kill_tree` 曾吞异常且无返回值，调用方无从判断，
    于是「杀失败仍摘条目」把进程变成孤儿。这里注入一个永远杀不掉的假进程：
    taskkill/kill 都无效、`poll()` 恒 None，期望 False。
    """
    class _ImmortalProc:
        pid = 999999  # 必然不存在，taskkill 会失败

        def poll(self):
            return None          # 永远「还活着」

        def wait(self, timeout=None):
            return None

        def kill(self):
            return None

    assert kill_tree(_ImmortalProc(), wait_seconds=1) is False, \
        "杀不掉的进程被谎报为 True（会导致条目被摘除、进程孤儿化）"
