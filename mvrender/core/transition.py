"""转场 mask 生成与合成。

不变量：每个镜界全局只定义一次转场；mask 由镜头入场进度 p 与类型决定。
与旧 engine.trans_mask/apply_transition 数值一致（w/h 参数化，不再硬编码）。
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

_NOISE_TR = {}
_W, _H = 1080, 1920


def _noise(seed=1, size=(120, 68), w=None, h=None):
    w = w or _W
    h = h or _H
    key = (seed, size, w, h)
    if key not in _NOISE_TR:
        from .vis import value_noise
        n = value_noise(size[0], size[1], seed=seed, octaves=5, base=4)
        _NOISE_TR[key] = cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
    return _NOISE_TR[key]


def trans_mask(kind, p, seed=7, origin=(0.5, 0.42), w=None, h=None):
    """生成转场 mask（1=新镜头）+ ring（发光环，可为 None）。

    与旧 engine.trans_mask 逐行等价；w/h 参数化以支持任意分辨率。
    """
    w = w or _W
    h = h or _H
    p = float(np.clip(p, 0, 1))
    if kind == "ink":
        n = _noise(seed, w=w, h=h)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt(((xx / w - origin[0]) * 1.0) ** 2 + ((yy / h - origin[1]) * 0.56) ** 2)
        d = d / (d.max() + 1e-9)
        edge = d - 0.55 * (n - 0.5)
        r = p * 1.45
        m = np.clip((r - edge) * 6.0, 0, 1)
        ring = np.exp(-((d - r) ** 2) / 0.0016) * (1 - p) * 1.5
        return m, ring
    if kind == "crack":
        from .vis import voronoi_cells
        e, pts = voronoi_cells(h, w, n=34, seed=seed)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dd = np.sqrt(((xx / w - origin[0])) ** 2 + ((yy / h - origin[1]) * 0.56) ** 2)
        dd = dd / (dd.max() + 1e-9)
        order = dd + 0.22 * (_noise(seed + 3, w=w, h=h) - 0.5)
        m = np.clip((p * 1.3 - order) * 7.0, 0, 1)
        ring = np.exp(-((order - p * 1.3) ** 2) / 0.002) * (1 - p) * 1.6
        return m, ring
    if kind == "dissolve":
        n = _noise(seed + 11, w=w, h=h)
        m = np.clip(((n * 0.65 + 0.35) - (1 - p)) * 7.0, 0, 1)
        return m, None
    if kind == "flow":
        n = _noise(seed + 21, w=w, h=h)
        yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        thr = (1 - p) * 1.12 - 0.12
        m = np.clip((thr + 0.10 * (n - 0.5) - yy) * 9.0, 0, 1)
        return m, None
    if kind == "shatter":
        bs = 60
        rng = np.random.default_rng(seed + 5)
        gh, gw = h // bs + 1, w // bs + 1
        r = rng.random((gh, gw)).astype(np.float32)
        order = cv2.resize(r, (w, h), interpolation=cv2.INTER_NEAREST)
        order = cv2.GaussianBlur(order, (0, 0), 12)
        m = np.clip((p * 1.35 - order) * 8.0, 0, 1)
        return m, None
    if kind == "fade":
        return np.full((h, w), p, np.float32), None
    return np.full((h, w), p, np.float32), None


def apply_transition(old, new, mask, ring=None, glow_color=(196, 168, 132)):
    """CPU 转场合成：old*(1-m) + new*m (+ ring*glow)。与旧 engine 一致。"""
    m = mask[:, :, None]
    out = old * (1 - m) + new * m
    if ring is not None:
        rg = np.clip(ring, 0, 1)[:, :, None] * np.array(glow_color, np.float32) * 1.4
        out = out + rg
    return out
