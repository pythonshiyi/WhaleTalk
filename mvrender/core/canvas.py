"""CPU 画布（与 vis_core.Canvas 契约一致，作为 GPU 后端的一致性参照与回退）。

保持与旧实现逐像素等价的关键点：
· `add` 按通道 in-place（避免整帧临时数组）；
· `blend` 用差分形式 `buf += (layer-buf)*m`；
· `out` 用一次曲线 + uint8。
"""
from __future__ import annotations

import numpy as np

from .vis import radial_glow


class Canvas:
    """float32 BGR 累加画布（线性空间，0..255+，允许过曝，后处理再压）。"""

    def __init__(self, w=1080, h=1920, bg=None):
        self.w, self.h = int(w), int(h)
        self.buf = np.zeros((self.h, self.w, 3), np.float32)
        if bg is not None:
            self.fill(tuple(bg))

    def fill(self, color):
        self.buf[:] = np.array(color, np.float32).reshape(1, 1, 3)

    def fill_gradient(self, top, bottom, gamma=1.0):
        t = (np.linspace(0, 1, self.h, dtype=np.float32) ** gamma).reshape(-1, 1, 1)
        c0 = np.array(top, np.float32).reshape(1, 1, 3)
        c1 = np.array(bottom, np.float32).reshape(1, 1, 3)
        self.buf[:] = c0 * (1 - t) + c1 * t

    def add(self, layer, mask=None):
        if mask is None:
            np.add(self.buf, layer, out=self.buf)
        else:
            m = mask[:, :, None] if mask.ndim == 2 else mask
            if m.shape[2] == 1:
                m0 = m[:, :, 0]
                for c in range(self.buf.shape[2]):
                    self.buf[:, :, c] += layer[:, :, c] * m0
            else:
                self.buf += layer * m

    def blend(self, layer, alpha):
        a = alpha
        if np.isscalar(a):
            self.buf *= (1 - a)
            self.buf += layer * a
        else:
            m = a[:, :, None] if a.ndim == 2 else a
            if m.shape[2] == 1:
                m0 = m[:, :, 0]
                for c in range(self.buf.shape[2]):
                    tmp = layer[:, :, c] - self.buf[:, :, c]
                    tmp *= m0
                    self.buf[:, :, c] += tmp
            else:
                tmp = layer - self.buf
                tmp *= m
                self.buf += tmp

    def mul(self, m):
        if np.isscalar(m):
            self.buf *= m
        else:
            self.buf *= (m[:, :, None] if m.ndim == 2 else m)

    def out(self, gain=1.0):
        x = np.clip(self.buf * gain, 0, 255.0)
        x = 255.0 * (x / 255.0) / (1.0 + 0.25 * (x / 255.0)) * 1.25
        return np.clip(x, 0, 255).astype(np.uint8)

    def glow_add(self, cx, cy, r, color, gain=1.0, edge=None):
        self.buf += radial_glow(self.h, self.w, cx, cy, r, color, gain, edge=edge)

    def vignette(self, strength=0.34, power=1.9, ry=1.7):
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        d = np.sqrt((xx / self.w - 0.5) ** 2 + ((yy / self.h - 0.5) * ry) ** 2)
        m = np.clip(1.0 - strength * (d / 0.66) ** power, 0.42, 1.0)
        self.buf *= m[:, :, None]

    # GPU 后端同名方法的空实现（渲染器统一处理）
    def sync_down(self):
        return self.buf
