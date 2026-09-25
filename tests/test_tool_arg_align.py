"""工具参数对齐（容错）回归。

现象（有据可查的真实失败，见 DATA_DIR/failures.json）：模型常按「通用直觉」传参，
与工具签名不一致，旧的 `fn(**args)` 直接抛 TypeError → 整次调用硬失败、浪费一整轮。

覆盖：
- 别名映射（offset→start_line、old_string→old、packages→package、query→keyword…）
- 丢弃签名不接受的多余键（run_command 的 cwd/background）
- 缺必填的检测与报错
- 精确命中优先、已给的键不被别名覆盖
- 不误伤：接受 **kwargs 的函数原样返回；签名取不到时原样返回
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import deepseek_client as dc  # noqa: E402


def _fn(name):
    return dc.TOOL_CALL_MAP[name]


def test_alias_offset_limit_to_read_file():
    a, ignored, notes = dc._align_tool_args(_fn("read_file"), {"path": "x", "offset": 1, "limit": 2})
    assert a == {"path": "x", "start_line": 1, "max_lines": 2}
    assert ignored == []
    assert "offset→start_line" in notes and "limit→max_lines" in notes


def test_alias_old_new_string_to_edit_file():
    a, ignored, notes = dc._align_tool_args(
        _fn("edit_file"), {"path": "x", "old_string": "a", "new_string": "b"})
    assert a == {"path": "x", "old": "a", "new": "b"}
    assert ignored == []
    assert "old_string→old" in notes


def test_alias_packages_to_package():
    a, _ig, notes = dc._align_tool_args(_fn("pip_install"), {"packages": ["numpy"]})
    assert a == {"package": ["numpy"]}
    assert "packages→package" in notes


def test_alias_query_to_keyword():
    a, _ig, notes = dc._align_tool_args(_fn("read_memory"), {"query": "abc"})
    assert a == {"keyword": "abc"}
    assert "query→keyword" in notes


def test_drop_extra_keys_run_command():
    a, ignored, _notes = dc._align_tool_args(
        _fn("run_command"), {"command": "echo hi", "cwd": "/tmp", "background": True})
    assert a == {"command": "echo hi"}
    assert set(ignored) == {"cwd", "background"}


def test_exact_match_wins_over_alias():
    # 同时给了 offset 与 start_line：精确的 start_line 优先，offset 不再覆盖
    a, ignored, _notes = dc._align_tool_args(
        _fn("read_file"), {"path": "x", "start_line": 5, "offset": 1})
    assert a["start_line"] == 5
    assert "offset" in ignored  # 目标已被占，offset 无处可去 → 丢弃


def test_missing_required_detected():
    a, _ig, _n = dc._align_tool_args(_fn("run_command"), {"cwd": "/tmp"})
    assert a == {}
    assert dc._missing_required(_fn("run_command"), a) == ["command"]


def test_no_missing_when_provided():
    a, _ig, _n = dc._align_tool_args(_fn("run_command"), {"command": "ls"})
    assert dc._missing_required(_fn("run_command"), a) == []


def test_non_dict_and_none_safe():
    a, ignored, notes = dc._align_tool_args(_fn("read_file"), None)
    assert a == {} and ignored == [] and notes == ""


def test_kwargs_function_passthrough():
    def f(**kwargs):
        return kwargs
    a, ignored, notes = dc._align_tool_args(f, {"anything": 1, "x": 2})
    assert a == {"anything": 1, "x": 2} and ignored == [] and notes == ""


def test_uninspectable_callable_passthrough():
    class C:
        @property
        def __signature__(self):
            raise ValueError("no sig")
        def __call__(self, **k):
            return k
    a, _ig, _n = dc._align_tool_args(C(), {"a": 1})
    assert a == {"a": 1}


def test_real_execution_no_typeerror(tmp_path):
    """端到端：read_file 用 offset/limit（错名）经对齐后可正常执行，不再 TypeError。"""
    p = tmp_path / "t.txt"
    p.write_text("a\nb\nc\n", encoding="utf-8")
    fn = _fn("read_file")
    aligned, _ig, _n = dc._align_tool_args(fn, {"path": str(p), "offset": 1, "limit": 2})
    dc._missing_required(fn, aligned)  # 不应有缺参
    out = fn(**aligned)  # 不应抛 TypeError（权限未初始化时返回提示文本也算通过）
    assert isinstance(out, str)
