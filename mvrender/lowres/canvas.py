"""逻辑画布：低分辨率下的快速绘制原语，对齐底座 / GPU 图元格式。

与 `core.canvas.Canvas` 的关系
------------------------------
同接口（`fill/add/blend/mul/glow_add/vignette/out/sync_down`），因此只用了这些
方法的镜头代码可原样在低分辨率下跑；额外提供 `fill_rects/draw_lines/
fill_circles/fill_polys/rasterize` 等**与 GPUCanvas 同名同格式**的矢量原语，
让「底座 GPU 原语」与「低分辨档」之间无需改写镜头代码。

约定（与底座一致）
------------------
- `buf`：float32 **BGR**，线性累加，0..255+（允许过曝，后处理再压）。
- 图元元组格式与 `API.md` 第 2 节一致：
    rects         [(x0,y0,x1,y1,alpha), ...]          闭区间
    round_rects   [(x0,y0,x1,y1,radius,alpha), ...]
    stroke_rects  [(x0,y0,x1,y1,thickness,alpha), ...]
    lines         [(x0,y0,x1,y1,thickness,alpha), ...]
    circles       [(cx,cy,r,alpha), ...]
    polys         [([(x,y),...], alpha), ...]
"""
from __future__ import annotations

import cv2
import numpy as np

from ..core.vis import radial_glow


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """盒式模糊（低分辨率下的廉价近似高斯）。2D 输入。"""
    r = int(r)
    if r <= 0:
        return a
    k = 2 * r + 1
    return cv2.blur(np.asarray(a, np.float32), (k, k))


def upscale(img: np.ndarray, scale: int) -> np.ndarray:
    """整数倍最近邻放大 —— 放大后方块感即风格本身（保持 dtype）。

    用 cv2.resize(INTER_NEAREST)：与 np.repeat 逐像素等价（整数倍时），
    但快 ~3x（实测 480×270 float32 → 1920×1080：11ms → 4ms；uint8 仅 1ms）。
    """
    scale = max(1, int(scale))
    if scale == 1:
        return img
    h, w = img.shape[:2]
    return cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)


class LowResCanvas:
    """float32 BGR 累加画布（低分辨率）。"""

    def __init__(self, w: int = 480, h: int = 270, bg=None):
        self.w, self.h = int(w), int(h)
        self.buf = np.zeros((self.h, self.w, 3), np.float32)
        if bg is not None:
            self.fill(tuple(bg))

    # ── 底座 Canvas 同接口 ─────────────────────────────────────────
    def fill(self, color):
        self.buf[:] = np.array(color, np.float32).reshape(1, 1, 3)

    def fill_gradient(self, top, bottom, gamma: float = 1.0):
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

    def glow_add(self, cx, cy, r, color, gain: float = 1.0, edge=None):
        self.buf += radial_glow(self.h, self.w, cx, cy, r, color, gain, edge=edge)

    def vignette(self, strength: float = 0.34, power: float = 1.9, ry: float = 1.7):
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        d = np.sqrt((xx / self.w - 0.5) ** 2 + ((yy / self.h - 0.5) * ry) ** 2)
        m = np.clip(1.0 - strength * (d / 0.66) ** power, 0.42, 1.0)
        self.buf *= m[:, :, None]

    def out(self, gain: float = 1.0):
        x = np.clip(self.buf * gain, 0, 255.0)
        x = 255.0 * (x / 255.0) / (1.0 + 0.25 * (x / 255.0)) * 1.25
        return np.clip(x, 0, 255).astype(np.uint8)

    def sync_down(self):
        return self.buf

    def to_full(self, scale: int = 1) -> np.ndarray:
        """裁到显示范围后整数倍最近邻放大为 uint8 全分辨率图。"""
        return np.clip(upscale(self.buf, scale), 0, 255).astype(np.uint8)

    # ── 矢量原语（对齐 GPUCanvas 图元格式）─────────────────────────
    def _coverage(self, rects=None, round_rects=None, stroke_rects=None,
                  lines=None, circles=None, polys=None) -> np.ndarray:
        m = np.zeros((self.h, self.w), np.float32)
        if rects:
            for x0, y0, x1, y1, al in rects:
                xa, ya = max(0, int(round(x0))), max(0, int(round(y0)))
                xb = min(self.w, int(round(x1)) + 1)
                yb = min(self.h, int(round(y1)) + 1)
                if xb > xa and yb > ya:
                    np.maximum(m[ya:yb, xa:xb], float(al), out=m[ya:yb, xa:xb])
        if round_rects:
            for x0, y0, x1, y1, rad, al in round_rects:
                r = max(0, int(rad))
                cv2.rectangle(m, (int(x0) + r, int(y0)), (int(x1) - r, int(y1)), float(al), -1)
                cv2.rectangle(m, (int(x0), int(y0) + r), (int(x1), int(y1) - r), float(al), -1)
                for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r),
                               (x0 + r, y1 - r), (x1 - r, y1 - r)):
                    cv2.circle(m, (int(cx), int(cy)), r, float(al), -1)
        if stroke_rects:
            for x0, y0, x1, y1, th, al in stroke_rects:
                pts = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], np.int32)
                cv2.polylines(m, [pts], True, float(al), max(1, int(th)))
        if lines:
            for x0, y0, x1, y1, th, al in lines:
                cv2.line(m, (int(x0), int(y0)), (int(x1), int(y1)),
                         float(al), max(1, int(th)))
        if circles:
            for cx, cy, r, al in circles:
                cv2.circle(m, (int(cx), int(cy)), max(1, int(r)), float(al), -1)
        if polys:
            for pts, al in polys:
                cv2.fillPoly(m, [np.asarray(pts, np.int32)], float(al))
        return np.clip(m, 0, 1)

    def rasterize(self, rects=None, round_rects=None, stroke_rects=None,
                  lines=None, circles=None, polys=None,
                  color=(255, 255, 255), gain: float = 1.0,
                  blur: float = 0.0, blur_n: int = 1):
        """一次光栅化多类图元到同一图层（对齐 GPUCanvas.rasterize）。"""
        m = self._coverage(rects, round_rects, stroke_rects, lines, circles, polys)
        if blur > 0:
            m = box_blur(m, max(1, int(round(float(blur) / max(1, int(blur_n))))))
        col = np.array(color, np.float32).reshape(1, 1, 3)
        self.buf += m[:, :, None] * col * float(gain)

    def fill_rects(self, rects, color=(255, 255, 255), gain: float = 1.0, blur: float = 0.0):
        self.rasterize(rects=rects, color=color, gain=gain, blur=blur)

    def round_rects(self, rects, color=(255, 255, 255), gain: float = 1.0, blur: float = 0.0):
        self.rasterize(round_rects=rects, color=color, gain=gain, blur=blur)

    def stroke_rects(self, rects, color=(255, 255, 255), gain: float = 1.0, blur: float = 0.0):
        self.rasterize(stroke_rects=rects, color=color, gain=gain, blur=blur)

    def draw_lines(self, lines, color=(255, 255, 255), gain: float = 1.0, blur: float = 0.0):
        self.rasterize(lines=lines, color=color, gain=gain, blur=blur)

    def fill_circles(self, circles, color=(255, 255, 255), gain: float = 1.0, blur: float = 0.0):
        self.rasterize(circles=circles, color=color, gain=gain, blur=blur)

    def fill_polys(self, polys, color=(255, 255, 255), gain: float = 1.0, blur: float = 0.0):
        self.rasterize(polys=polys, color=color, gain=gain, blur=blur)
