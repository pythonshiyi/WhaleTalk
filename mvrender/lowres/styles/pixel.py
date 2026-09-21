"""像素风格包 —— 硬边、有限色阶、纯正像素方块。

题材无关：它只看画布的能量，把它量化成有限色阶叠在夜/日底色上。
低分辨率 + 整数放大天然产生像素方块，因此这是最省的一档。
"""
from __future__ import annotations

import numpy as np

from ..style import StylePack, register

# BGR
_NIGHT_TOP = np.array([36, 26, 18], np.float32)
_NIGHT_BOT = np.array([10, 9, 12], np.float32)
_DAY_TOP = np.array([196, 206, 214], np.float32)
_DAY_BOT = np.array([150, 162, 178], np.float32)


@register
class PixelStyle(StylePack):
    id = "pixel"

    def __init__(self, levels: int = 6, gain: float = 1.15):
        self.levels = max(2, int(levels))
        self.gain = float(gain)

    def background(self, w: int, h: int, ctx, shot) -> np.ndarray:
        day = bool(getattr(shot, "day", False))
        top, bot = (_DAY_TOP, _DAY_BOT) if day else (_NIGHT_TOP, _NIGHT_BOT)
        t = np.linspace(0.0, 1.0, int(h), dtype=np.float32)[:, None, None]
        col = top * (1 - t) + bot * t
        return np.repeat(col, int(w), axis=1).astype(np.float32)

    def apply(self, img: np.ndarray, t: float, ctx, shot, bg) -> np.ndarray:
        base = bg if bg is not None else np.zeros_like(img)
        cov = np.clip(img.mean(axis=2) / 255.0, 0.0, 1.0)
        step = 255.0 / (self.levels - 1)
        q = np.round(np.clip(img, 0, 255) * self.gain / step) * step
        out = base * (1.0 - cov[:, :, None]) + q * cov[:, :, None]
        return np.clip(out, 0, 255).astype(np.float32)
