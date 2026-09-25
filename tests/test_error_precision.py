"""数据驱动收口回归（来自失败诊断观测层）。

数据来源：DATA_DIR/failures.json 的结构化诊断。本轮针对真实高频问题收口：
  1. dev_plan 在目标目录不存在时抛 FileNotFoundError 硬崩（[Errno 2]）；
  2. search_local / list_dir 把「路径是文件」笼统报成「目录不存在」，
     模型据此反复重试同一个错——精确错误才能自我纠正。
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import deepseek_client as dc  # noqa: E402  （导入顺序契约）
import permissions  # noqa: E402


def _init_ws(tmp_path):
    ws = str(tmp_path)
    permissions.init(os.path.join(ws, "perm.json"), ws, ws)
    return ws


# ── dev_plan 目录不存在时不再硬崩 ──────────────────────
def test_dev_plan_init_creates_missing_dir(tmp_path):
    ws = _init_ws(tmp_path)
    bogus = os.path.join(ws, "nonexistent", "proj")  # 目录不存在
    assert not os.path.isdir(bogus)
    out = dc.dev_plan("init", title="t", steps=["a", "b"], path=bogus)
    assert "已初始化开发计划" in out
    assert os.path.isfile(os.path.join(bogus, ".whaletalk_plan.json")), "应自动建目录并落盘"


def test_dev_plan_show_after_init(tmp_path):
    ws = _init_ws(tmp_path)
    bogus = os.path.join(ws, "deep", "gone")
    dc.dev_plan("init", title="计划X", steps=["一", "二", "三"], path=bogus)
    out = dc.dev_plan("show", path=bogus)
    assert "计划X" in out and "三" in out


def test_dev_plan_step_done_persists(tmp_path):
    ws = _init_ws(tmp_path)
    d = os.path.join(ws, "p")
    dc.dev_plan("init", title="t", steps=["a", "b"], path=d)
    out = dc.dev_plan("step_done", step_index=0, path=d)
    assert "[x]" in out or "1/2" in out


def test_dev_plan_never_raises_on_bad_path(tmp_path):
    """异常路径也不应抛异常（工具约定：失败返回错误字符串）。"""
    ws = _init_ws(tmp_path)
    out = dc.dev_plan("init", title="t", steps=["a"], path=os.path.join(ws, "x", "y", "z"))
    assert isinstance(out, str)


# ── search_local / list_dir 精确错误（文件 vs 目录）──
def test_list_dir_file_path_precise_error(tmp_path):
    ws = _init_ws(tmp_path)
    f = os.path.join(ws, "a.py")
    open(f, "w", encoding="utf-8").write("x=1")
    out = dc.list_dir(f)
    assert "是文件，不是目录" in out
    assert "read_file" in out
    assert "目录不存在" not in out


def test_list_dir_missing_still_generic(tmp_path):
    ws = _init_ws(tmp_path)
    out = dc.list_dir(os.path.join(ws, "nope"))
    assert "目录不存在" in out


def test_search_local_file_path_precise_error(tmp_path):
    ws = _init_ws(tmp_path)
    f = os.path.join(ws, "b.py")
    open(f, "w", encoding="utf-8").write("y=2")
    out = dc.search_local(f, "y")
    assert "不是目录" in out and "文件" in out
    assert "目录不存在" not in out


def test_search_local_missing_still_generic(tmp_path):
    ws = _init_ws(tmp_path)
    out = dc.search_local(os.path.join(ws, "nope"), "y")
    assert "目录不存在" in out
