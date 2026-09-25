"""进程退出码语义化报告回归（run_python / run_command）。

现象（真实失败记录）：run_command 把**任何非零退出**都报成「错误：命令以退出码 N
结束（执行失败）」。但 ruff/pytest/grep/diff/git diff --exit-code 等用非零退出表达
**正常语义**（"发现了问题"），命令本身跑得好好的。一律报「错误：」有两重危害：
  1. 误导模型——以为工具坏了/命令失败，改道重试或放弃；
  2. 因前缀命中 shared.TOOL_RESULT_FAIL_PREFIXES，污染失败记忆与自动消解。

覆盖 shared.format_process_result 的四种情形 + is_tool_failure 判定。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import shared  # noqa: E402


def test_success_with_output():
    out = shared.format_process_result(0, "hello\n")
    assert out.startswith("退出码 0")
    assert "hello" in out
    assert not shared.is_tool_failure(out)


def test_success_no_output():
    out = shared.format_process_result(0, "")
    assert "执行成功" in out and "0" in out
    assert not shared.is_tool_failure(out)


def test_nonzero_with_output_is_not_error():
    """非零 + 有输出：命令已执行，中性报告，不算失败（ruff/pytest 语义）。"""
    out = shared.format_process_result(1, "F401 unused import\n")
    assert "命令退出码 1" in out
    assert "发现了问题" in out
    assert not out.lstrip().startswith("错误")
    assert not shared.is_tool_failure(out), "非零退出但有输出不应被判为失败"


def test_nonzero_empty_output_gives_diagnostic():
    """非零 + 空输出：给可操作诊断，而不是干巴巴的「（无输出）」。"""
    out = shared.format_process_result(1, "", kind="命令")
    assert "无输出" in out
    assert "PATH" in out or "不存在" in out
    assert "（无输出）" not in out
    # 中性前缀，不再一律冠「错误：」
    assert not out.lstrip().startswith("错误")


def test_workspace_suffix():
    out = shared.format_process_result(0, "x", workspace=r"D:\ws")
    assert "工作目录" in out and r"D:\ws" in out


def test_python_kind_wording():
    out = shared.format_process_result(2, "Traceback ...")
    assert "脚本已执行" in out or "命令已执行" in out or "已执行" in out


def test_is_tool_failure_still_catches_real_errors():
    """真正的失败前缀仍应被识别（防过度放松导致失败记忆失效）。"""
    assert shared.is_tool_failure("错误：代码为空")
    assert shared.is_tool_failure("权限拒绝：命中黑名单")
    assert shared.is_tool_failure("工具执行失败: boom")
    assert not shared.is_tool_failure("退出码 0\nok")


def test_tool_result_fail_prefixes_intact():
    assert "错误" in shared.TOOL_RESULT_FAIL_PREFIXES
    assert "未能" in shared.TOOL_RESULT_FAIL_PREFIXES
