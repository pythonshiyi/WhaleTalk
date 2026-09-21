"""水墨风格包 —— 宣纸底 + 墨分五色 + 洇染 / 湿边。

题材无关：它把画布的**亮度**当作「墨量」，叠在宣纸底上。
亮度 → 墨量的非线性映射让边缘更「洇」，低分辨率方块放大后反而像纸纹颗粒。

宣纸底是**镜头内不变量**：分形噪声 + 细颗粒，只算一次，由渲染器按镜头缓存。
这正是「缓存不变量」原则——早期实现每帧重算纸底，占了 94% 的耗时。
"""
from __future__ import annotations

import hashlib

import numpy as np

from ...core.vis import value_noise
from ..canvas import box_blur
from ..style import StylePack, register

# BGR：暖宣纸（RGB 241,236,226 → BGR 226,236,241）
_PAPER = np.array([226, 236, 241], np.float32)
_INK = np.array([22, 19, 18], np.float32)


def _stable_seed(*parts) -> int:
    """跨进程稳定的种子（不能用 hash()：字符串 hash 每进程随机）。"""
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(h[:4], "little") % 100000


@register
class InkStyle(StylePack):
    id = "ink"

    def __init__(self, smear: bool = True, wet_edge: bool = True, gamma: float = 0.85):
        self.smear = bool(smear)
        self.wet_edge = bool(wet_edge)
        self.gamma = float(gamma)

    def background(self, w: int, h: int, ctx, shot) -> np.ndarray:
        seed = _stable_seed("ink-paper", getattr(shot, "name", ""))
        cloud = value_noise(h, w, seed=seed + 1, octaves=3, base=2, blur=0.8)
        grain = value_noise(h, w, seed=seed + 2, octaves=2, base=10, blur=0.4)
        tex = 0.95 + (cloud - 0.5) * 0.10 + (grain - 0.5) * 0.05
        tex = np.clip(tex, 0.60, 1.0)[:, :, None]
        return (tex * _PAPER).astype(np.float32)

    def apply(self, img: np.ndarray, t: float, ctx, shot, bg) -> np.ndarray:
        base = bg if bg is not None else np.broadcast_to(_PAPER, img.shape).astype(np.float32)
        cov = np.clip(img.mean(axis=2) / 255.0, 0.0, 1.0)
        ink = cov ** self.gamma
        if self.smear:
            ink = np.clip(ink + box_blur(ink, 1) * 0.25, 0.0, 1.0)
        if self.wet_edge:
            edge = np.clip(ink - box_blur(ink, 1) * 0.92, 0.0, 1.0)
            ink = np.clip(ink + edge * 0.8, 0.0, 1.0)
        out = base * (1.0 - ink[:, :, None]) + _INK * ink[:, :, None]
        return np.clip(out, 0, 255).astype(np.float32)
