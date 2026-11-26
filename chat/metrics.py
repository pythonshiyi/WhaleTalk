"""本轮对话的指标聚合（从 DeepSeekClient.chat 内联 agg 抽出）。

只负责「算」，不负责「发」：回调仍由 chat 持有，避免把 UI 语义带进指标层。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


class TurnMetrics:
    """一次用户可见回复（可跨多个工具轮次）的累计指标。"""

    USAGE_KEYS = ("prompt", "completion", "cache_hit", "cache_miss")

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._clock = clock or time.perf_counter
        self.t0 = self._clock()
        self.rounds = 0
        self.prompt = 0
        self.completion = 0
        self.cache_hit = 0
        self.cache_miss = 0
        self.gen_ms = 0.0
        self.ttft_ms: Optional[float] = None
        self.total_ms = 0.0
        self.tps = 0.0

    def add_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        if not usage:
            return
        for k in self.USAGE_KEYS:
            try:
                setattr(self, k, getattr(self, k) + int(usage.get(k, 0) or 0))
            except (TypeError, ValueError):
                continue

    def add_round(
        self,
        round_t0: float,
        first_ts: Optional[float],
        last_ts: Optional[float],
        usage: Optional[Dict[str, Any]] = None,
        round_end: Optional[float] = None,
    ) -> None:
        """累加一个 LLM 轮次：首字等待、生成时长、token、墙钟与 tps。"""
        if first_ts is not None:
            if self.ttft_ms is None:
                self.ttft_ms = round((first_ts - round_t0) * 1000.0, 1)
            if last_ts is not None:
                self.gen_ms += (last_ts - first_ts) * 1000.0
        self.rounds += 1
        self.add_usage(usage)
        end = self._clock() if round_end is None else round_end
        self.total_ms = round((end - self.t0) * 1000.0, 1)
        gen_s = self.gen_ms / 1000.0
        self.tps = round(self.completion / gen_s, 1) if gen_s > 0.05 else 0.0

    def snapshot(self, interrupted: bool = False) -> Dict[str, Any]:
        """给前端/分数卡的稳定结构；不包含任何回调对象。"""
        return {
            "rounds": self.rounds,
            "prompt": self.prompt,
            "completion": self.completion,
            "cache_hit": self.cache_hit,
            "cache_miss": self.cache_miss,
            "gen_ms": round(self.gen_ms, 1),
            "ttft_ms": self.ttft_ms,
            "total_ms": self.total_ms,
            "tps": self.tps,
            "interrupted": bool(interrupted),
        }
