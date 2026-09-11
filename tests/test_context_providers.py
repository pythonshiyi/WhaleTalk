# -*- coding: utf-8 -*-
"""上下文装配测试：顺序/门控/预算/失败隔离/回执。

核心不变量：
- 注入顺序由 priority 决定（顺序变了 = 前缀缓存失效 + 行为漂移）；
- 单个 Provider 失败不影响其余 Provider，且必须留下可查痕迹（degrade + 回执）；
- 回执只有元信息（名字/字符数/原因），不得回灌注入内容本身。
"""
import pytest

import context_providers as cp
import degrade


FIXED_DEPS = {
    "task_quality_guide": "[任务质量指南]",
    "default_prompt": "DEFAULT",
    "dialog_prompt": "DIALOG",
    "data_dir": ".",
    "active_dir": ".",
    "memory_full": lambda: {"facts": [{"text": "用户偏好中文"}]},
    "self_profile": lambda: "[核心自我状态] 我是鲸语",
    "brain_api": lambda: type("B", (), {"brain_context": staticmethod(lambda **k: "[大脑上下文] X")}),
    "trust_notice": lambda: "",
    "degrade_notice": lambda: "",
    "failures_text": lambda: "",
    "patterns_load": lambda: [],
    "plugins_hint": lambda: "",
}


def _ctx(**over):
    deps = dict(FIXED_DEPS)
    deps.update(over.pop("deps", {}))
    kw = {"messages": [{"role": "user", "content": "你好"}], "cfg": {}, "deps": deps}
    kw.update(over)
    return cp.Context(**kw)


@pytest.fixture(autouse=True)
def _clean():
    degrade.init(path=None, reset=True)
    cp.reset_receipt()
    yield
    degrade.init(path=None, reset=True)


def test_injection_order_follows_priority():
    out = cp.assemble(_ctx())
    names = [i["name"] for i in out["receipt"]["injected"]]
    assert names == ["base.task_quality_guide", "context.memory",
                     "context.self_profile", "context.brain", "context.workspace"]
    # 文本拼接顺序与 priority 一致
    text = out["text"]
    assert text.index("[任务质量指南]") < text.index("[长期记忆]") \
        < text.index("[核心自我状态]") < text.index("[大脑上下文]")


def test_prompt_selection_task_vs_dialog():
    assert cp.assemble(_ctx())["prompt"] == "DEFAULT"
    assert cp.assemble(_ctx(pure_chat=True))["prompt"] == "DIALOG"


def test_pure_chat_skips_task_only_providers_with_reason():
    out = cp.assemble(_ctx(pure_chat=True))
    skipped = {s["name"]: s["reason"] for s in out["receipt"]["skipped"]}
    assert "base.task_quality_guide" in skipped
    assert out["text"] is None or "[工作区目录]" not in out["text"]


def test_quiet_mode_skips_personal_context_only():
    out = cp.assemble(_ctx(quiet_mode=True))
    names = [i["name"] for i in out["receipt"]["injected"]]
    assert "context.memory" not in names
    assert "context.self_profile" not in names
    assert "context.brain" not in names
    # 与个性无关的来源仍然注入
    assert "base.task_quality_guide" in names


def test_memory_disabled_by_config():
    out = cp.assemble(_ctx(cfg={"memory_enabled": False}))
    assert "context.memory" not in [i["name"] for i in out["receipt"]["injected"]]


def test_empty_provider_recorded_as_no_content():
    out = cp.assemble(_ctx())
    reasons = {s["name"]: s["reason"] for s in out["receipt"]["skipped"]}
    assert reasons.get("context.trust_integrity") == "本次无内容"
    assert reasons.get("context.degrade_status") == "本次无内容"


def test_provider_failure_is_isolated_and_recorded():
    """一个来源炸了，其余来源照常注入；同时回执与 degrade 都要留痕。"""
    def boom():
        raise RuntimeError("brain down")

    out = cp.assemble(_ctx(deps={"brain_api": boom}))
    injected = [i["name"] for i in out["receipt"]["injected"]]
    assert "context.brain" not in injected
    assert "context.memory" in injected and "context.self_profile" in injected
    failed = {f["name"]: f for f in out["receipt"]["failed"]}
    assert "context.brain" in failed
    assert failed["context.brain"]["critical"] is True
    assert "RuntimeError" in failed["context.brain"]["error"]
    # 关键降级必须能进入自我状态提示（AI 应知道自己的大脑上下文没进来）
    note = degrade.critical_notice()
    assert "context.brain" in note


def test_non_critical_failure_does_not_enter_notice():
    def boom():
        raise RuntimeError("x")

    cp.assemble(_ctx(deps={"plugins_hint": boom}))
    assert degrade.critical_notice() == ""
    assert degrade.summary()["count"] == 1


def test_budget_truncates_and_marks():
    out = cp.assemble(_ctx(deps={"memory_full": lambda: {"facts": [{"text": "x" * 9000}]}}))
    item = next(i for i in out["receipt"]["injected"] if i["name"] == "context.memory")
    assert item["truncated"] is True
    assert item["chars"] <= 4000 + 20
    assert "已按预算截断" in out["text"]


def test_receipt_has_metadata_not_payload():
    """回执只记元信息，不把注入内容本身带出去（避免又一处泄露面）。"""
    out = cp.assemble(_ctx())
    blob = repr(out["receipt"])
    assert "用户偏好中文" not in blob
    assert "我是鲸语" not in blob
    assert "total_chars" in out["receipt"] and "prompt_chars" in out["receipt"]


def test_last_receipt_matches_assemble_output():
    out = cp.assemble(_ctx())
    assert cp.last_receipt() == out["receipt"]
