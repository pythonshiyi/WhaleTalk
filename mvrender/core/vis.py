"""绘图公共算子（从 vis_core 提炼，供 core/transition/props 复用）。

只保留引擎需要的部分：颜色 / 径向光晕 / 噪声 / 变形 / 缓动 / 文字遮罩。
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def C(x, scale=1.0):
    """颜色归一化：'#rrggbb' | (b,g,r) | [b,g,r] | 'b,g,r' -> (b,g,r) float"""
    if isinstance(x, str):
        s = x.strip()
        if s.startswith("#"):
            hh = s.lstrip("#")
            r, g, b = int(hh[0:2], 16), int(hh[2:4], 16), int(hh[4:6], 16)
            return (b * scale, g * scale, r * scale)
        parts = [float(v) for v in s.replace("，", ",").split(",")]
        return (parts[0] * scale, parts[1] * scale, parts[2] * scale)
    if isinstance(x, (tuple, list)) and len(x) >= 3:
        return (float(x[0]) * scale, float(x[1]) * scale, float(x[2]) * scale)
    raise ValueError(f"无法解析颜色: {x!r}")


# ── 径向光晕（CPU；GPU 版在 gpu.canvas_gpu）─────────────────────────
_GLOW_CACHE = {}


def radial_glow(h, w, cx, cy, r, color, gain=1.0, power=2.2, feather=0.42, edge=None):
    """无大核高斯的径向光晕（夜景灯光的底色）。cx/cy/r 为归一化坐标。"""
    key = (h, w, round(cx, 4), round(cy, 4), round(r, 4), round(power, 3),
           round(feather, 3), None if edge is None else round(edge, 3))
    m = _GLOW_CACHE.get(key)
    if m is None:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt(((xx / w - cx) * (w / min(w, h))) ** 2 + ((yy / h - cy) * (h / min(w, h))) ** 2)
        rr = max(1e-4, r)
        m = 1.0 / (1.0 + (d / rr) ** power * (1.0 / max(1e-3, feather)))
        m /= (m.max() + 1e-9)
        if edge is not None:
            e0 = max(1e-3, float(edge))
            f = np.clip(1.0 - (d - e0) / (e0 * 0.45), 0.0, 1.0)
            m = m * (f * f * (3.0 - 2.0 * f))
        if len(_GLOW_CACHE) < 96:
            _GLOW_CACHE[key] = m
    return m[:, :, None] * np.array(color, np.float32).reshape(1, 1, 3) * gain


# ── 噪声 ────────────────────────────────────────────────────────────
_NOISE_CACHE = {}


def value_noise(h, w, seed=0, octaves=5, base=6, gain=0.55, blur=1.2):
    key = (h, w, seed, octaves, base, round(gain, 3))
    if key in _NOISE_CACHE:
        return _NOISE_CACHE[key]
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        res = int(base * (2 ** o))
        g = rng.random((res + 1, res + 1)).astype(np.float32)
        acc += cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC) * amp
        tot += amp
        amp *= gain
    acc /= max(tot, 1e-6)
    if blur > 0:
        acc = cv2.GaussianBlur(acc, (0, 0), blur)
    if len(_NOISE_CACHE) < 80:
        _NOISE_CACHE[key] = acc
    return acc


def voronoi_cells(h, w, n=24, seed=1, step=8):
    rng = np.random.default_rng(seed)
    pts = np.stack([rng.random(n) * w, rng.random(n) * h], 1).astype(np.float32)
    yy, xx = np.mgrid[0:h:step, 0:w:step].astype(np.float32)
    d = np.sqrt((xx[..., None] - pts[None, None, :, 0]) ** 2
                + (yy[..., None] - pts[None, None, :, 1]) ** 2)
    idx = np.argsort(d, axis=2)[:, :, :2]
    d1 = np.take_along_axis(d, idx[:, :, 0:1], axis=2)[:, :, 0]
    d2 = np.take_along_axis(d, idx[:, :, 1:2], axis=2)[:, :, 0]
    edge = np.clip((d2 - d1) / 6.0, 0, 1)
    return 1.0 - cv2.resize(edge, (w, h), interpolation=cv2.INTER_LINEAR), pts


# ── 文字 ────────────────────────────────────────────────────────────
_FONT_CACHE = {}
FONT_SONG = r"C:\Windows\Fonts\simsun.ttc"
FONT_UI = r"C:\Windows\Fonts\msyh.ttc"
FONT_HEI = r"C:\Windows\Fonts\simhei.ttf"
FONT_KAI = r"C:\Windows\Fonts\simkai.ttf"


def get_font(size, path=FONT_SONG, index=0):
    key = (int(size), path, int(index))
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(path, int(size), index=int(index))
    return _FONT_CACHE[key]


def render_text_mask(text, size, w, h, font_path=FONT_SONG, index=0, xy=(0, 0),
                     anchor="lt", tracking=0):
    img = Image.new("L", (int(w), int(h)), 0)
    d = ImageDraw.Draw(img)
    font = get_font(size, font_path, index)
    if tracking:
        x, y = xy
        for ch in text:
            d.text((x, y), ch, font=font, fill=255, anchor=anchor)
            x += d.textlength(ch, font=font) + tracking
    else:
        d.text(xy, text, font=font, fill=255, anchor=anchor)
    return np.asarray(img, np.float32) / 255.0


# ── 缓动 ────────────────────────────────────────────────────────────
def smoothstep(x):
    x = float(np.clip(x, 0, 1))
    return x * x * (3 - 2 * x)


def ease_out(x, p=2.0):
    return 1.0 - (1.0 - float(np.clip(x, 0, 1))) ** p


def ease_in(x, p=2.0):
    return float(np.clip(x, 0, 1)) ** p


def pulse_decay(dt, decay=6.0):
    return math.exp(-max(0.0, dt) * decay)
