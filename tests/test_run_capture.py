# -*- coding: utf-8 -*-
"""A6 回归：_run_capture 公共执行辅助 + 五执行工具统一路径的持久化测试。

覆盖：成功/失败/超时 kill/截断/cwd/错误合并 六条 _run_capture 路径；
run_python / run_command / run_tests / run_lint / pip_install 的参数校验与
统一执行路径（此前仅临时冒烟脚本覆盖，本次固化进 pytest 防止回归）。
"""
import os
import sys
import tempfile
import time

# 必须先 import deepseek_client（其顶层会完整构建六层注册表并加载 agent_tools）；
# 直接 import agent_tools.tool_code 会触发 __init__ 循环导入导致 TOOLS 未注册报错
import deepseek_client as dsc  # noqa: F401
import permissions

from agent_tools.tool_code import (
    _run_capture,
    run_command,
    run_lint,
    run_python,
    run_tests,
    pip_install,
)

PY = sys.executable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── _run_capture 辅助 ──────────────────────────────────────────────

def test_capture_success():
    rc, out = _run_capture([PY, "-c", "print(42)"], timeout=10, max_output=1000)
    assert rc == 0
    assert "42" in out


def test_capture_merges_stderr():
    rc, out = _run_capture(
        [PY, "-c", "import sys; sys.stderr.write('boom')"], timeout=10, max_output=1000
    )
    assert rc == 0
    assert "boom" in out


def test_capture_nonzero_exit():
    rc, out = _run_capture([PY, "-c", "import sys; sys.exit(3)"], timeout=10, max_output=1000)
    assert rc == 3


def test_capture_timeout_kills_process_tree():
    t0 = time.time()
    try:
        _run_capture([PY, "-c", "import time; time.sleep(30)"], timeout=1, max_output=1000)
        raise AssertionError("应抛 TimeoutError")
    except TimeoutError as e:
        assert e.args[0] == 1
    # 进程树必须被 kill：不能真等 30s
    assert time.time() - t0 < 10


def test_capture_truncates_output():
    rc, out = _run_capture([PY, "-c", "print('x' * 3000)"], timeout=10, max_output=500)
    assert rc == 0
    assert "[输出已截断]" in out
    assert len(out) < 1000


def test_capture_respects_cwd():
    with tempfile.TemporaryDirectory() as td:
        rc, out = _run_capture(
            [PY, "-c", "import os; print(os.getcwd())"], timeout=10, max_output=1000, cwd=td
        )
        assert rc == 0
        assert os.path.normpath(out.strip()) == os.path.normpath(td)


# ── run_python ─────────────────────────────────────────────────────

def test_run_python_success():
    r = run_python("print(1+1)", with_site=False)
    assert r.startswith("2")


def test_run_python_no_output():
    r = run_python("x = 1", with_site=False)
    assert "无输出" in r


def test_run_python_error_traceback():
    r = run_python("raise ValueError('boom')", with_site=False)
    assert "boom" in r


def test_run_python_timeout_text():
    r = run_python("import time; time.sleep(30)", with_site=False)
    assert "超时" in r


def test_run_python_oversize_blocked():
    r = run_python("x" * 100000, with_site=False)
    assert "错误" in r


def test_run_python_empty():
    r = run_python("")
    assert "错误" in r


# ── run_command ────────────────────────────────────────────────────

def test_run_command_success():
    r = run_command("python --version")
    assert "Python" in r


def test_run_command_error_exit_code():
    r = run_command("python -S -c 1/0")
    assert "退出码 1" in r
    assert "ZeroDivisionError" in r


def test_run_command_ignores_blocklist():
    # 无限制模式（v3.9+）：run_command 不再做命令黑白名单拦截——即使配置了
    # shell.blocklist，命令也照常执行（注入后恢复）
    old = list(permissions._data["shell"].get("blocklist") or [])
    try:
        permissions._data["shell"]["blocklist"] = ["powershell"]
        r = run_command("python --version")
        assert "Python" in r, r[:200]
    finally:
        permissions._data["shell"]["blocklist"] = old


def test_run_command_supports_pipe():
    # shell 模式：管道/重定向等原生语法必须可用
    r = run_command('echo 42 | python -c "import sys; print(int(sys.stdin.read().strip())+1)"')
    assert "43" in r, r[:200]


# ── run_tests ──────────────────────────────────────────────────────

def test_run_tests_pytest_ok():
    # 用仓库内真实 pytest 用例文件（test_registry.py 无外部依赖）
    r = run_tests(os.path.join(PROJECT_ROOT, "tests", "test_registry.py"), framework="pytest")
    assert r.startswith("退出码 0"), r


def test_run_tests_missing_path():
    r = run_tests("/nonexistent/xyz.py", framework="pytest")
    assert "错误" in r


# ── run_lint ───────────────────────────────────────────────────────

def test_run_lint_invalid_dir():
    r = run_lint("/nonexistent_dir_xyz")
    assert "错误" in r or "不存在" in r


def test_run_lint_works_or_graceful():
    # 本机有 ruff：应返回"无问题/发现问题"；无 ruff：优雅提示安装
    r = run_lint(os.path.join(PROJECT_ROOT, "agent_tools"))
    assert r.startswith(("无问题", "ruff")) or "未安装 ruff" in r


# ── pip_install ────────────────────────────────────────────────────

def test_pip_install_option_injection_blocked():
    r = pip_install("-r evil.txt")
    assert "非法" in r or "拒绝" in r


def test_pip_install_bad_chars():
    r = pip_install("foo$bar")
    assert "非法" in r or "拒绝" in r


def test_pip_install_empty():
    r = pip_install("")
    assert "错误" in r
