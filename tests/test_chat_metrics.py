"""chat.metrics.TurnMetrics 的纯函数测试（不启动模型/服务）。"""

from __future__ import annotations

from chat.metrics import TurnMetrics


def test_turn_metrics_accumulates_rounds_and_usage():
    clock = {"now": 100.0}
    m = TurnMetrics(clock=lambda: clock["now"])

    m.add_round(
        round_t0=99.5,
        first_ts=99.6,
        last_ts=100.0,
        usage={"prompt": 100, "completion": 10, "cache_hit": 3, "cache_miss": 7},
        round_end=100.2,
    )
    m.add_round(
        round_t0=100.2,
        first_ts=None,
        last_ts=None,
        usage={"prompt": 50, "completion": 5},
        round_end=100.4,
    )

    snap = m.snapshot()
    assert snap["rounds"] == 2
    assert snap["prompt"] == 150
    assert snap["completion"] == 15
    assert snap["cache_hit"] == 3
    assert snap["cache_miss"] == 7
    assert snap["ttft_ms"] == 100.0  # 仅第一轮计入
    assert snap["gen_ms"] == 400.0   # 100.0 - 99.6 = 0.4s
    assert snap["total_ms"] == 400.0
    assert snap["tps"] == 37.5       # 15 / 0.4s
    assert snap["interrupted"] is False


def test_turn_metrics_ignores_bad_usage_values():
    m = TurnMetrics(clock=lambda: 0.0)
    m.add_usage({"prompt": "not-a-number", "completion": None})
    snap = m.snapshot(interrupted=True)
    assert snap["prompt"] == 0
    assert snap["completion"] == 0
    assert snap["interrupted"] is True
