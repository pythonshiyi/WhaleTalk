"""单次任务的「工具轮数预算」透传。

前端意图面板允许**单次**设预算（「这次最多 12 步」），它是单次意图，不该改写
全局配置。这里锁定它的解析规则：显式优先、非法回退、负数视为不限。
"""
import pytest


@pytest.fixture(scope="module")
def budget():
    import api_server
    return api_server._Handler._round_budget


def test_explicit_rounds_win_over_config(budget):
    assert budget({"max_tool_rounds": 12}, {"max_tool_rounds": 99}) == 12


def test_missing_falls_back_to_config(budget):
    assert budget({}, {"max_tool_rounds": 99}) == 99
    assert budget({}, {}) == 0


def test_invalid_value_falls_back_instead_of_erroring(budget):
    # 一个乱传的数字不该挡住用户的提问：退回配置值，绝不抛错
    assert budget({"max_tool_rounds": "abc"}, {"max_tool_rounds": 7}) == 7
    assert budget({"max_tool_rounds": None}, {"max_tool_rounds": 7}) == 7


def test_negative_means_unlimited(budget):
    assert budget({"max_tool_rounds": -3}, {}) == 0


def test_zero_passes_through_as_unlimited(budget):
    # 0 在 deepseek_client 里语义是「不限」，不能被当成「没设」而回退到配置
    assert budget({"max_tool_rounds": 0}, {"max_tool_rounds": 7}) == 0
