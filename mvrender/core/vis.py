"""绘图公共算子（从 vis_core 提炼，供 core/transition/props 复用）。

只保留引擎需要的部分：颜色 / 径向光晕 / 噪声 / 变形 / 缓动 / 文字遮罩。
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── 可选加速后端（缺失不影响正确性）────────────────────────────────────
# cv2 是**可选**依赖：`resize` / `GaussianBlur` 在缺失时回退到 numpy / PIL。
# 此前是 `import cv2` 硬导入，导致 `import mvrender.core.vis`（经
# `core.transition`、`core.lyrics` 被 lowres 渲染链间接依赖）在未装 opencv
# 的环境直接 ImportError。与 `core.camera` / `core.sparse` 保持同一约定。
try:  # pragma: no cover - 取决于环境是否装 cv2
    import cv2 as _cv2
except Exception:  # noqa: BLE001
    _cv2 = None


def has_cv2() -> bool:
    """cv2 加速后端是否可用（与 mv_tex.has_cv2 同义）。"""
    return _cv2 is not None


def _resize(a: np.ndarray, w: int, h: int, interp: str = "cubic") -> np.ndarray:
    """缩放（cv2 → PIL 逐级回退）。interp: 'cubic' | 'linear' | 'nearest'。"""
    a = np.asarray(a, np.float32)
    if _cv2 is not None:
        flag = {"cubic": _cv2.INTER_CUBIC, "linear": _cv2.INTER_LINEAR,
                "nearest": _cv2.INTER_NEAREST}[interp]
        return _cv2.resize(a, (int(w), int(h)), interpolation=flag)
    from PIL import Image as _I
    resample = {"cubic": _I.BICUBIC, "linear": _I.BILINEAR,
                "nearest": _I.NEAREST}[interp]
    if a.ndim == 2:
        return np.asarray(_I.fromarray(a, mode="F").resize((int(w), int(h)), resample),
                          np.float32)
    chans = [_I.fromarray(a[:, :, c], mode="F").resize((int(w), int(h)), resample)
             for c in range(a.shape[2])]
    return np.stack([np.asarray(c, np.float32) for c in chans], axis=2)


def _gaussian_kernel(sigma: float) -> np.ndarray:
    """一维高斯核（半径 3σ，与 cv2 的 ksize=0 自动推导一致）。"""
    r = max(1, int(round(3.0 * float(sigma))))
    x = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-(x * x) / (2.0 * float(sigma) ** 2))
    return (k / k.sum()).astype(np.float32)


def _gaussian_blur_np(a: np.ndarray, sigma: float) -> np.ndarray:
    """可分离高斯模糊（numpy 回退，边界 reflect-101，对齐 cv2 默认行为）。"""
    k = _gaussian_kernel(sigma)
    r = len(k) // 2
    single = a.ndim == 2
    x = a[:, :, None] if single else a
    # 水平
    pad = np.pad(x, ((0, 0), (r, r), (0, 0)), mode="reflect")
    acc = np.zeros_like(x, np.float32)
    for i, kv in enumerate(k):
        acc += pad[:, i:i + x.shape[1], :] * kv
    # 垂直
    pad = np.pad(acc, ((r, r), (0, 0), (0, 0)), mode="reflect")
    out = np.zeros_like(acc, np.float32)
    for i, kv in enumerate(k):
        out += pad[i:i + acc.shape[0], :, :] * kv
    return out[:, :, 0] if single else out


def _gaussian_blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """高斯模糊（cv2 → numpy 可分离回退）。

    注意：**不能**用 `PIL ImageFilter.GaussianBlur` 回退——PIL 拒绝 mode "F"
    （float32）图像，抛 `ValueError: image has wrong mode`（实测）。故回退走
    numpy 可分离卷积，数值与 cv2 的 reflect-101 边界一致。
    """
    if sigma <= 0:
        return np.asarray(a, np.float32)
    a = np.asarray(a, np.float32)
    if _cv2 is not None:
        return _cv2.GaussianBlur(a, (0, 0), float(sigma))
    return _gaussian_blur_np(a, float(sigma))


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
        acc += _resize(g, w, h, "cubic") * amp
        tot += amp
        amp *= gain
    acc /= max(tot, 1e-6)
    if blur > 0:
        acc = _gaussian_blur(acc, blur)
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
    return 1.0 - _resize(edge, w, h, "linear"), pts


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
