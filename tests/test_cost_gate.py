"""③c 任务级成本预检回归：token 粗估 + 费用预估 + 阈值判定（纯函数）。"""
import api_server


def test_cost_gate_decision():
    assert api_server._cost_gate_decision(0.5, 0.3) is True
    assert api_server._cost_gate_decision(0.1, 0.3) is False
    assert api_server._cost_gate_decision(1.0, 0) is False       # 阈值 0 = 关闭
    assert api_server._cost_gate_decision(0.3, 0.3) is True      # 达到即触发


def test_estimate_text_tokens_empty_and_nonempty():
    assert api_server._estimate_text_tokens("") == 0
    assert api_server._estimate_text_tokens("hello world") > 0
    assert api_server._estimate_text_tokens("你好，世界") > 0


def test_estimate_request_cost_positive():
    msgs = [{"role": "user", "content": "帮我写一份一万字的报告"}]
    cost = api_server._estimate_request_cost(msgs, 4096, "deepseek-flash")
    assert cost > 0
    # 输出上限越大，预估越高
    assert api_server._estimate_request_cost(msgs, 8192, "deepseek-flash") > cost
