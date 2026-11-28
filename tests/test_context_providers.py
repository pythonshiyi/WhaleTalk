"""上下文装配测试：顺序/门控/预算/失败隔离/回执。

核心不变量：
- 注入顺序由 priority 决定（顺序变了 = 前缀缓存失效 + 行为漂移）；
- 单个 Provider 失败不影响其余 Provider，且必须留下可查痕迹（degrade + 回执）；
- 回执只有元信息（名字/字符数/原因），不得回灌注入内容本身。

历史说明：v3.29 起「长期记忆 / 核心自我状态 / 大脑 / 生命」四个 Provider 随对应
模块整体移除，本套件相应收敛到剩余来源。
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
    "trust_notice": "",
    "degrade_notice": "",
    "egress_notice": "",
    "failures_text": "",
    "patterns_load": lambda: [],
}


def _ctx(query="帮我看下项目结构", **over):
    deps = dict(FIXED_DEPS)
    deps.update(over.pop("deps", {}))
    kw = {"messages": [{"role": "user", "content": query}], "cfg": {}, "deps": deps}
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
    assert names == ["base.task_quality_guide", "context.workspace"]
    # 文本拼接顺序与 priority 一致
    text = out["text"]
    assert text.index("[任务质量指南]") < text.index("[工作区目录]")


def test_removed_providers_are_gone():
    """记忆 / 自我状态 / 大脑 / 生命 四个注入源必须已整体移除。"""
    names = {p.name for p in cp.providers()}
    for gone in ("context.memory", "context.self_profile", "context.brain", "context.life"):
        assert gone not in names


def test_prompt_selection_task_vs_dialog():
    assert cp.assemble(_ctx())["prompt"] == "DEFAULT"
    assert cp.assemble(_ctx(pure_chat=True))["prompt"] == "DIALOG"


def test_pure_chat_skips_task_only_providers_with_reason():
    out = cp.assemble(_ctx(pure_chat=True))
    skipped = {s["name"]: s["reason"] for s in out["receipt"]["skipped"]}
    assert "base.task_quality_guide" in skipped
    assert out["text"] is None or "[工作区目录]" not in out["text"]


def test_empty_provider_recorded_as_no_content():
    out = cp.assemble(_ctx())
    reasons = {s["name"]: s["reason"] for s in out["receipt"]["skipped"]}
    assert reasons.get("context.trust_integrity") == "本次无内容"
    assert reasons.get("context.degrade_status") == "本次无内容"


def test_non_critical_failure_does_not_enter_notice():
    def boom():
        raise RuntimeError("x")

    cp.assemble(_ctx(deps={"patterns_load": boom}))
    assert degrade.critical_notice() == ""
    assert degrade.summary()["count"] == 1
