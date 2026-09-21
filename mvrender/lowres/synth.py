"""合成镜头 / 合成 Ctx —— 题材无关的基准与自检素材。

不开任何外部工程、不依赖音频：用一个极简 Ctx 桩 + 纯抽象几何镜头，
就能测「低分辨档 + 风格包」的渲染契约、确定性与性能。
"""
from __future__ import annotations

import math

import numpy as np

from ..core.project import Shot


class SyntheticCtx:
    """实现底座渲染器用到的 Ctx 信号（energy/pulse/line_at/...）。"""

    def __init__(self, w: int = 480, h: int = 270, duration: float = 12.0,
                 fps: int = 30, period: float = 0.5):
        self.w, self.h = int(w), int(h)
        self.duration = float(duration)
        self.fps = int(fps)
        self.period = float(period)
        self.lines = [{"text": "测试歌词", "t0": 1.5, "t1": 3.0, "sec": "主歌", "who": "合"},
                      {"text": "第二句", "t0": 6.0, "t1": 8.0, "sec": "副歌", "who": "合"}]

    def energy(self, t: float) -> float:
        return 0.45 + 0.30 * math.sin(t * 0.8)

    def pulse(self, t: float) -> float:
        dt = t % self.period
        return math.exp(-dt * 7.0)

    def line_at(self, t: float, lead: float = 1.2, tail: float = 1.6):
        for ln in self.lines:
            if ln["t0"] - lead <= t <= ln["t1"] + tail:
                return ln
        return None

    def progress(self, t: float) -> float:
        return float(np.clip(t / max(1e-6, self.duration), 0.0, 1.0))


def _draw_stage(c, tl, shot, ctx, cache):
    ph = tl * 1.7
    c.fill_gradient((28, 20, 14), (9, 8, 12))
    x = c.w * (0.15 + 0.5 * (0.5 + 0.5 * math.sin(ph)))
    c.fill_rects([(x - 22, c.h * 0.35, x + 22, c.h * 0.9, 1.0)],
                 color=(196, 120, 64), gain=1.1)
    c.fill_circles([(c.w * 0.72, c.h * 0.30, 8 + 4 * math.sin(ph), 1.0)],
                   color=(70, 200, 255))
    c.draw_lines([(0, c.h * 0.82, c.w, c.h * 0.82 + 6 * math.sin(ph), 2, 1.0)],
                 color=(90, 170, 210))


def _draw_bars(c, tl, shot, ctx, cache):
    ph = tl * 2.3
    c.fill_gradient((14, 12, 26), (6, 5, 10))
    for i in range(6):
        hh = (0.25 + 0.6 * abs(math.sin(ph + i * 0.7))) * c.h
        x0 = c.w * (0.08 + i * 0.15)
        c.fill_rects([(x0, c.h - hh, x0 + c.w * 0.09, c.h, 1.0)],
                     color=(120, 90 + i * 20, 200 - i * 20), gain=1.0)


def _draw_rings(c, tl, shot, ctx, cache):
    ph = tl * 1.1
    c.fill_gradient((22, 16, 30), (8, 6, 12))
    cx, cy = c.w * 0.5, c.h * 0.55
    for i in range(5):
        r = 10 + i * 12 + 6 * math.sin(ph + i)
        c.fill_circles([(cx, cy, r, 0.35)], color=(60, 220, 240))


def synthetic_shots(ctx=None, duration: float | None = None) -> list[Shot]:
    """三段式抽象镜头表，覆盖全片无空隙。"""
    dur = float(duration if duration is not None else (ctx.duration if ctx else 12.0))
    third = dur / 3.0
    return [
        Shot(0.0, third, "A", _draw_stage, cam="float", tin=0.0, tout=0.6,
             tin_kind="dissolve", tout_kind="dissolve", day=False, deps=("a",)),
        Shot(third, 2 * third, "B", _draw_bars, cam="push", tin=0.6, tout=0.6,
             tin_kind="dissolve", tout_kind="ink", day=False, deps=("b",)),
        Shot(2 * third, dur, "C", _draw_rings, cam="drift", tin=0.6, tout=0.0,
             tin_kind="flow", tout_kind="dissolve", day=True, deps=("c",)),
    ]
