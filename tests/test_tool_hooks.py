# -*- coding: utf-8 -*-
"""工具钩子管线测试。

核心不变量：
- 包装对调用方**透明**（__name__ / inspect.signature / 异常语义都不变）；
- 钩子按 priority 执行，且任何一个钩子炸了都不能影响工具结果；
- 工具自身的异常必须原样上抛（钩子不得吞）；
- 内核文件改动经钩子自动登记，非内核文件零副作用。
"""
import inspect
import json
import os

import pytest

import tool_hooks
import degrade


@pytest.fixture(autouse=True)
def _clean():
    degrade.init(path=None, reset=True)
    tool_hooks.reset_for_tests()
    yield
    tool_hooks.reset_for_tests()
    degrade.init(path=None, reset=True)


# ── 透明性 ───────────────────────────────────────────────────────────────

def _sample(path, content="", flag=False):
    """样例工具：位置 + 关键字 + 默认值。"""
    return f"{path}|{content}|{flag}"


def test_wrapper_is_transparent():
    w = tool_hooks.wrap("sample", _sample)
    # 透明 = 忠实保留原名/文档/签名（不是改成工具名）
    assert w.__name__ == _sample.__name__ == "_sample"
    assert w.__doc__ == _sample.__doc__
    # inspect.signature 必须仍然是原签名（functools.wraps 的 __wrapped__ 生效）
    assert str(inspect.signature(w)) == str(inspect.signature(_sample))


def test_positional_and_keyword_args_are_bound():
    seen = {}

    @tool_hooks.pre_hook("peek", 1)
    def _peek(ctx):
        seen.update(ctx.args)

    w = tool_hooks.wrap("sample", _sample)
    w("a.txt", content="hi", flag=True)          # 全关键字
    assert seen == {"path": "a.txt", "content": "hi", "flag": True}
    seen.clear()
    w("b.txt", "yo")                              # 位置参数
    assert seen["path"] == "b.txt" and seen["content"] == "yo"


def test_shorthand_arguments_are_normalized_by_toolkit_style_call():
    """位置参数经签名绑定后，钩子拿到的仍是具名 dict（PATH_KEYS 才认得出来）。"""
    got = {}
    tool_hooks.hook("pre", "capture", lambda ctx: got.update(ctx.args), 1)
    tool_hooks.wrap("write_file", lambda path, content: "ok")("x/y.txt", "body")
    assert got == {"path": "x/y.txt", "content": "body"}


def test_non_callable_executor_passes_through():
    assert tool_hooks.wrap("x", None) is None
    assert tool_hooks.wrap("x", "some_symbol") == "some_symbol"


# ── 顺序与失败隔离 ───────────────────────────────────────────────────────

def test_hooks_run_in_priority_order():
    order = []
    tool_hooks.hook("pre", "p2", lambda ctx: order.append("p2"), 20)
    tool_hooks.hook("pre", "p1", lambda ctx: order.append("p1"), 10)
    tool_hooks.hook("post", "q1", lambda ctx: order.append("q1"), 10)
    tool_hooks.wrap("t", lambda: "r")()
    assert order == ["p1", "p2", "q1"]


def test_hook_failure_does_not_break_tool_and_is_recorded():
    def boom(ctx):
        raise RuntimeError("hook down")

    tool_hooks.hook("pre", "boom", boom, 1)
    assert tool_hooks.wrap("t", lambda: "fine")() == "fine"
    snap = degrade.snapshot()
    assert any("toolhook.pre.boom" == e["component"] for e in snap)


def test_tool_exception_propagates_and_post_hook_still_runs():
    ran = []
    tool_hooks.hook("post", "after", lambda ctx: ran.append(ctx.ok), 1)

    def bad():
        raise ValueError("tool boom")

    with pytest.raises(ValueError):
        tool_hooks.wrap("t", bad)()
    assert ran == [False]          # post 拿到 ok=False，且异常原样上抛


def test_post_hook_can_enrich_result():
    """post 能改返回值（信任声明回执就是这么挂上去的）——所以 return 必须在 post 之后。"""
    tool_hooks.hook("post", "suffix", lambda ctx: setattr(
        ctx, "result", (ctx.result or "") + " +note"), 1)
    assert tool_hooks.wrap("t", lambda: "base")() == "base +note"


# ── 内置钩子：信任声明与快照 ─────────────────────────────────────────────

@pytest.fixture
def kernel_sandbox(tmp_path, monkeypatch):
    """把 trust_kernel 指向 tmp，并把 tmp 里一个文件纳入内核清单。"""
    import trust_kernel as tk
    root = tmp_path / "proj"
    root.mkdir()
    for name in tk.PROTECTED:
        (root / name).write_text(f"# v1 {name}\n", encoding="utf-8")
    monkeypatch.setattr(tk, "PROJECT_DIR", str(root))
    monkeypatch.setattr(tk, "TRUST_DIR", str(root / "trust"))
    tk.init()
    (root / "trust" / "config.json").write_text(
        json.dumps({"mode": "report", "extra_protected": ["guard_x.py"]},
                   ensure_ascii=False), encoding="utf-8")
    (root / "guard_x.py").write_text("# v1\n", encoding="utf-8")
    tk.init()
    tk.accept("guard_x.py")
    tool_hooks.clear_kernel_cache()
    yield tk, root
    tool_hooks.clear_kernel_cache()


def test_trust_hook_declares_kernel_write(kernel_sandbox):
    tk_, root = kernel_sandbox

    def writer(path, content=""):
        (root / path).write_text(content, encoding="utf-8")
        return f"已写入 {path}"

    out = tool_hooks.wrap("write_file", writer)("guard_x.py", "# v2\n")
    assert "已登记为信任内核声明改动" in out and "guard_x.py" in out
    assert tk_.verify()["ok"] is True          # 基线已推进 → 不算未声明改动


def test_trust_hook_ignores_ordinary_files(kernel_sandbox):
    tk_, root = kernel_sandbox

    def writer(path, content=""):
        (root / path).write_text(content, encoding="utf-8")
        return f"已写入 {path}"

    out = tool_hooks.wrap("write_file", writer)("ordinary.txt", "hi")
    assert "信任内核" not in out
    assert tk_.verify()["ok"] is True


def test_trust_hook_marks_failed_write_as_aborted(kernel_sandbox):
    """工具抛错时不得推进基线（否则真实改动会被误认为已登记）。"""
    tk_, root = kernel_sandbox

    def failing(path):
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        tool_hooks.wrap("write_file", failing)("guard_x.py")
    assert tk_.verify()["ok"] is True
    events = [r.get("event") for r in tk_.ledger_tail(6)]
    assert "abort" in events


def test_snapshot_hook_only_for_declared_tools(tmp_path, monkeypatch):
    import snapshot as snapshot_mod
    monkeypatch.setattr(snapshot_mod, "UNDO_DIR", str(tmp_path / "undo"),
                        raising=False)
    os.makedirs(snapshot_mod.UNDO_DIR, exist_ok=True)
    target = tmp_path / "data.txt"
    target.write_text("orig", encoding="utf-8")

    # 未声明 snapshot 的工具：不应产生快照
    tool_hooks.wrap("other_tool", lambda path: "ok")(str(target))
    assert snapshot_mod.list_snapshots() == []

    # 声明了 hooks=("snapshot",) 的工具：写前留快照
    tool_hooks.wrap("write_file", lambda path: "ok", ("snapshot",))(str(target))
    snaps = snapshot_mod.list_snapshots()
    assert snaps and snaps[0]["path"].endswith("data.txt")


# ── 与 toolkit 的真实接线 ────────────────────────────────────────────────

def test_toolkit_wraps_registered_tools():
    """注册处包装必须真的生效：TOOL_CALL_MAP 与模块级名字都是包装体。"""
    import deepseek_client as dc
    assert getattr(dc.write_file, "__wt_tool__", None) == "write_file"
    assert getattr(dc.TOOL_CALL_MAP["write_file"], "__wt_tool__", None) == "write_file"
    # 声明式快照标记可从包装体读回
    assert "snapshot" in tool_hooks.declared_names(dc.write_file)
    assert "snapshot" in tool_hooks.declared_names(dc.edit_file)
    # 特殊回调工具（ask_user）无执行体 → 不包装，仍是 None
    assert dc.TOOL_CALL_MAP["ask_user"] is None
