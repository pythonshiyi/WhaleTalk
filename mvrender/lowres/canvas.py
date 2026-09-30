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

import numpy as np

# ── 可选加速后端（缺失不影响正确性）────────────────────────────────────
# cv2 是**可选**依赖（见 requirements.txt 之外的 mv_tex 注释与 `has_cv2()`）：
# 绘图原语在无 cv2 时回退到 numpy 等价实现，保证 CI/最小安装可跑。
# 注意：本模块此前是 `import cv2` 硬导入，导致 `import mvrender.lowres`
# 在未装 opencv 的环境直接 ImportError（tests/test_lowres.py 收集期就炸，
# 整个 pytest 套件被中断）。改为 try/except + numpy 回退。
try:  # pragma: no cover - 取决于环境是否装 cv2
    import cv2 as _cv2
except Exception:  # noqa: BLE001
    _cv2 = None

from ..core.vis import radial_glow


def has_cv2() -> bool:
    """cv2 加速后端是否可用（与 mv_tex.has_cv2 同义）。"""
    return _cv2 is not None


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """盒式模糊（低分辨率下的廉价近似高斯）。2D 输入。"""
    r = int(r)
    if r <= 0:
        return a
    k = 2 * r + 1
    a = np.asarray(a, np.float32)
    if _cv2 is not None:
        return _cv2.blur(a, (k, k))
    # numpy 回退：可分离均值滤波（积分图实现）
    return _box_blur_np(a, r)


def _box_blur_np(a: np.ndarray, r: int) -> np.ndarray:
    """可分离盒式模糊（numpy 回退）。

    边界用 **reflect-101**——与 `cv2.blur` 的默认 `borderType` 一致（实测
    `cv2.blur` 默认 == `BORDER_REFLECT_101`，不是 edge 复制）。两者在
    图像内部逐像素完全相同，仅边界一圈因 border 语义不同而有差异。
    """
    k = 2 * r + 1
    pad = np.pad(a, ((r, r), (r, r)), mode="reflect")
    csum = pad.cumsum(axis=0, dtype=np.float64)
    csum = np.vstack([np.zeros((1, csum.shape[1]), np.float64), csum])
    out_v = (csum[k:, :] - csum[:-k, :])                   # 垂直窗口和
    csum2 = out_v.cumsum(axis=1, dtype=np.float64)
    csum2 = np.hstack([np.zeros((csum2.shape[0], 1), np.float64), csum2])
    out = (csum2[:, k:] - csum2[:, :-k]) / float(k * k)
    return np.ascontiguousarray(out[:a.shape[0], :a.shape[1]], np.float32)


def upscale(img: np.ndarray, scale: int) -> np.ndarray:
    """整数倍最近邻放大 —— 放大后方块感即风格本身（保持 dtype）。

    用 cv2.resize(INTER_NEAREST)：与 np.repeat 逐像素等价（整数倍时），
    但快 ~3x（实测 480×270 float32 → 1920×1080：11ms → 4ms；uint8 仅 1ms）。
    无 cv2 时回退 np.repeat（数值完全一致）。
    """
    scale = max(1, int(scale))
    if scale == 1:
        return img
    h, w = img.shape[:2]
    if _cv2 is not None:
        return _cv2.resize(img, (w * scale, h * scale), interpolation=_cv2.INTER_NEAREST)
    return np.repeat(np.repeat(img, scale, axis=0), scale, axis=1)


# ── 矢量原语的 numpy 回退（无 cv2 时使用；语义对齐 cv2 对应函数）──────
def _rect_fill(m: np.ndarray, x0: int, y0: int, x1: int, y1: int, al: float) -> None:
    """闭区间填充矩形（对齐 cv2.rectangle(...,-1)：含两端点）。"""
    xa, xb = max(0, min(x0, x1)), min(m.shape[1] - 1, max(x0, x1))
    ya, yb = max(0, min(y0, y1)), min(m.shape[0] - 1, max(y0, y1))
    if xb >= xa and yb >= ya:
        np.maximum(m[ya:yb + 1, xa:xb + 1], al, out=m[ya:yb + 1, xa:xb + 1])


def _circle_fill(m: np.ndarray, cx: int, cy: int, r: int, al: float) -> None:
    """实心圆（对齐 cv2.circle(...,-1)）。"""
    if r <= 0:
        _rect_fill(m, cx, cy, cx, cy, al)
        return
    h, w = m.shape
    y0, y1 = max(0, cy - r), min(h - 1, cy + r)
    x0, x1 = max(0, cx - r), min(w - 1, cx + r)
    if x1 < x0 or y1 < y0:
        return
    yy = np.arange(y0, y1 + 1, dtype=np.float32)[:, None] - cy
    xx = np.arange(x0, x1 + 1, dtype=np.float32)[None, :] - cx
    mask = (xx * xx + yy * yy) <= (r * r + 0.5)
    sub = m[y0:y1 + 1, x0:x1 + 1]
    np.maximum(sub, np.where(mask, al, 0.0).astype(np.float32), out=sub)


def _line_thick(m: np.ndarray, p0, p1, al: float, th: int) -> None:
    """粗线段（对齐 cv2.line：端点方形笔帽，覆盖 [x-th/2, x+th/2]）。"""
    half = th / 2.0
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    h, w = m.shape
    bx0 = max(0, int(np.floor(min(x0, x1) - half)))
    bx1 = min(w - 1, int(np.ceil(max(x0, x1) + half)))
    by0 = max(0, int(np.floor(min(y0, y1) - half)))
    by1 = min(h - 1, int(np.ceil(max(y0, y1) + half)))
    if bx1 < bx0 or by1 < by0:
        return
    yy = np.arange(by0, by1 + 1, dtype=np.float32)[:, None]
    xx = np.arange(bx0, bx1 + 1, dtype=np.float32)[None, :]
    dx, dy = x1 - x0, y1 - y0
    ln2 = dx * dx + dy * dy
    if ln2 <= 1e-12:
        t = np.zeros_like(xx)
    else:
        t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / ln2, 0.0, 1.0)
    px, py = x0 + t * dx, y0 + t * dy
    dist = np.sqrt((xx - px) ** 2 + (yy - py) ** 2)
    mask = dist <= max(0.5, half)
    sub = m[by0:by1 + 1, bx0:bx1 + 1]
    np.maximum(sub, np.where(mask, al, 0.0).astype(np.float32), out=sub)


def _polyline(m: np.ndarray, pts, al: float, th: int, closed: bool = False) -> None:
    """折线/多边形描边（分别调用 _line_thick）。"""
    n = len(pts)
    if n < 2:
        return
    for i in range(n - 1):
        _line_thick(m, pts[i], pts[i + 1], al, th)
    if closed and n > 2:
        _line_thick(m, pts[-1], pts[0], al, th)


def _fill_poly(m: np.ndarray, pts, al: float) -> None:
    """实心多边形（扫描线填充，对齐 cv2.fillPoly 的闭区间语义）。"""
    p = np.asarray(pts, np.float32).reshape(-1, 2)
    if p.shape[0] < 3:
        return
    h, w = m.shape
    y_min = max(0, int(np.floor(p[:, 1].min())))
    y_max = min(h - 1, int(np.ceil(p[:, 1].max())))
    if y_max < y_min:
        return
    xs, ys = p[:, 0], p[:, 1]
    n = p.shape[0]
    for y in range(y_min, y_max + 1):
        yc = y + 0.5
        crossings = []
        for i in range(n):
            j = (i + 1) % n
            y0, y1 = ys[i], ys[j]
            if y0 == y1:
                continue
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                t = (yc - y0) / (y1 - y0)
                crossings.append(xs[i] + t * (xs[j] - xs[i]))
        if len(crossings) < 2:
            continue
        crossings.sort()
        for a, b in zip(crossings[0::2], crossings[1::2]):
            xa = max(0, int(np.floor(a)))
            xb = min(w - 1, int(np.ceil(b)))
            if xb >= xa:
                np.maximum(m[y, xa:xb + 1], al, out=m[y, xa:xb + 1])


def _fill_round_rect(m: np.ndarray, x0: int, y0: int, x1: int, y1: int,
                     rad: int, al: float) -> None:
    """实心圆角矩形（对齐 cv2 的两矩形 + 四角圆组合）。"""
    r = max(0, int(rad))
    _rect_fill(m, x0 + r, y0, x1 - r, y1, al)
    _rect_fill(m, x0, y0 + r, x1, y1 - r, al)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        _circle_fill(m, cx, cy, r, al)


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
        # cv2 存在时走原路径（数值与历史完全一致）；缺失时走 numpy 回退。
        cv = _cv2
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
                if cv is not None:
                    cv.rectangle(m, (int(x0) + r, int(y0)), (int(x1) - r, int(y1)), float(al), -1)
                    cv.rectangle(m, (int(x0), int(y0) + r), (int(x1), int(y1) - r), float(al), -1)
                    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r),
                                   (x0 + r, y1 - r), (x1 - r, y1 - r)):
                        cv.circle(m, (int(cx), int(cy)), r, float(al), -1)
                else:
                    _fill_round_rect(m, int(x0), int(y0), int(x1), int(y1), r, float(al))
        if stroke_rects:
            for x0, y0, x1, y1, th, al in stroke_rects:
                th_i = max(1, int(th))
                if cv is not None:
                    pts = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], np.int32)
                    cv.polylines(m, [pts], True, float(al), th_i)
                else:
                    _polyline(m, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], float(al),
                              th_i, closed=True)
        if lines:
            for x0, y0, x1, y1, th, al in lines:
                th_i = max(1, int(th))
                if cv is not None:
                    cv.line(m, (int(x0), int(y0)), (int(x1), int(y1)), float(al), th_i)
                else:
                    _line_thick(m, (x0, y0), (x1, y1), float(al), th_i)
        if circles:
            for cx, cy, r, al in circles:
                r_i = max(1, int(r))
                if cv is not None:
                    cv.circle(m, (int(cx), int(cy)), r_i, float(al), -1)
                else:
                    _circle_fill(m, int(cx), int(cy), r_i, float(al))
        if polys:
            for pts, al in polys:
                if cv is not None:
                    cv.fillPoly(m, [np.asarray(pts, np.int32)], float(al))
                else:
                    _fill_poly(m, pts, float(al))
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
