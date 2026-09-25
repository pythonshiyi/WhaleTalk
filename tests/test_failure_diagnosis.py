"""失败诊断（观测层）回归。

目的：让失败记录从「失败了什么」升级为「模型错在哪、怎么错的」——
为持续观测与可靠性优化提供数据。设计原则：**只加信息，不改指纹与生命周期语义**。

覆盖：分类判定 / 误传参数提取 / 旧数据兼容（读时补默认）/ 汇总统计。
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import stores  # noqa: E402


def test_diagnose_arg_mismatch_extracts_misused_arg():
    d = stores.diagnose_failure(
        "工具参数错误: read_file() got an unexpected keyword argument 'offset'")
    assert d["kind"] == "arg_mismatch"
    assert d["misused_args"] == ["offset"]
    assert "参数名" in d["label"]


def test_diagnose_missing_required():
    d = stores.diagnose_failure(
        "工具参数错误: run_command() missing 1 required positional argument: 'command'")
    assert d["kind"] == "arg_mismatch"
    assert any("command" in a for a in d["misused_args"])


def test_diagnose_kinds():
    assert stores.diagnose_failure("错误：执行超时（超过同步上限 60s）")["kind"] == "timeout"
    assert stores.diagnose_failure("错误：内存超限（峰值约 2100MB）")["kind"] == "memory"
    assert stores.diagnose_failure("'tail' 不是内部或外部命令")["kind"] == "not_found"
    assert stores.diagnose_failure("ConnectError: [WinError 10061]")["kind"] == "network"
    assert stores.diagnose_failure("SyntaxError: invalid syntax")["kind"] == "syntax"
    assert stores.diagnose_failure("脚本以退出码 1 结束")["kind"] == "exit_nonzero"
    assert stores.diagnose_failure("")["kind"] == "unknown"


def test_diagnosis_survives_garbage():
    assert stores.diagnose_failure(None)["kind"] == "unknown"
    assert stores.diagnose_failure("", {"ignored_args": ["a"], "aliased_args": ["b"]})["ignored_args"] == ["a"]


def test_normalize_failure_backfills_diagnosis():
    """旧数据（无 diagnosis）读时补默认，不改写文件。"""
    legacy = {"tool": "read_file", "error": "工具参数错误: got an unexpected keyword argument 'limit'", "ts": "2026-01-01"}
    out = stores.normalize_failure(legacy)
    assert isinstance(out["diagnosis"], dict)
    assert out["diagnosis"]["kind"] == "arg_mismatch"
    assert "limit" in out["diagnosis"]["misused_args"]


def test_record_failures_stores_diagnosis(tmp_path):
    p = str(tmp_path / "f.json")
    ap = str(tmp_path / "fa.json")
    stores.record_failures(p, [{"tool": "read_file",
                                "error": "工具参数错误: read_file() got an unexpected keyword argument 'offset'"}],
                           archive_path=ap)
    items = stores.load_failures(p)
    assert len(items) == 1
    d = items[0].get("diagnosis")
    assert d and d["kind"] == "arg_mismatch" and d["misused_args"] == ["offset"]
    # 复现：hits 增加，诊断刷新
    stores.record_failures(p, [{"tool": "read_file",
                                "error": "工具参数错误: read_file() got an unexpected keyword argument 'offset'"}],
                           archive_path=ap)
    items = stores.load_failures(p)
    assert items[0]["hits"] == 2 and items[0]["diagnosis"]["kind"] == "arg_mismatch"


def test_fingerprint_unchanged_by_diagnosis(tmp_path):
    """关键：新增 diagnosis 不得改变指纹（否则历史失败无法归并）。"""
    tool, err = "edit_file", "工具参数错误: got an unexpected keyword argument 'old_string'"
    fp1 = stores.failure_fingerprint(tool, err)
    # 记录后再读，指纹应与纯函数算出的一致
    p = str(tmp_path / "f.json")
    stores.record_failures(p, [{"tool": tool, "error": err}], archive_path=str(tmp_path / "a.json"))
    assert stores.load_failures(p)[0]["fingerprint"] == fp1


def test_diagnosis_summary(tmp_path):
    p = str(tmp_path / "f.json")
    ap = str(tmp_path / "a.json")
    stores.record_failures(p, [
        {"tool": "run_python", "error": "错误：执行超时（超过同步上限 60s）"},
        {"tool": "run_python", "error": "错误：内存超限（峰值约 2100MB）"},
        {"tool": "read_file", "error": "工具参数错误: got an unexpected keyword argument 'offset'"},
    ], archive_path=ap)
    s = stores.failure_diagnosis_summary(p)
    assert s["total_failures"] == 3
    kinds = dict(s["by_kind"])
    assert kinds.get("timeout") == 1 and kinds.get("memory") == 1 and kinds.get("arg_mismatch") == 1
    tools = dict(s["by_tool"])
    assert tools.get("run_python") == 2
    assert any(a == "offset" for a, _ in s["misused_args"])
