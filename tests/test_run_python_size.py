"""run_python 去上限回归：超过旧 8000 字符的代码应一次执行成功（临时文件执行）。"""
import api_server  # noqa: F401  # 先触发完整工具注册，避免后 import agent_tools 重复注册


def _tc():
    import agent_tools.tool_code as tc
    return tc


def test_run_python_accepts_over_8000_chars():
    code = "# " + "x" * 9000 + "\nprint('LARGE_OK', 6 * 7)\n"
    assert len(code) > 8000
    out = _tc().run_python(code)
    assert "LARGE_OK" in out and "42" in out


def test_run_python_empty_code():
    assert "代码为空" in _tc().run_python("")
