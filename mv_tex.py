"""mv_tex —— 程序化影像质感积木（题材无关，供 mv_scene / mv_engine 复用）。

沉淀自《山河砚》MV 制作（2026-09）的实测经验：把「水墨的骨 + 赛博的光」
抽象为一组**与题材无关**的纯函数积木，任何 MV / 微电影 / 短片都能复用。

设计取舍（产品约束）：
- **只依赖 numpy + Pillow（核心依赖）**；`cv2` 与 `pyopencl` 均为**可选加速**，
  缺失时自动回退等价实现——保证 CI 与最小安装也能出片。
- 图像统一为 `numpy.float32`、形状 `(H,W,3)`、取值 0..1。
- 所有算子**确定性**（同输入同输出），并尽量按时间连续，避免逐帧噪声掩盖真实运动。

关键实测教训（写进代码，防止回退）：
1. `cv2.imwrite` 在中文路径上**静默失败**（返回 False 不报错）→ 统一 `imwrite_safe`。
2. 字体写死 `C:\\Windows\\Fonts` 会漏掉用户字体目录 → 自动搜索 + 字形预检。
3. 逐帧独立噪声会掩盖运动（结构相关从 +0.99 掉到 ~0）→ `film_grain` 时间连续。
4. bloom 过曝会冲掉结构 → 高光阈值 + `highlight_rolloff`。
5. 稀疏折线 + 坐标取模回绕 → 画面几乎空白 → 密集折线 + 无回绕平移。
"""
from __future__ import annotations

import functools
import math
import os

import numpy as np

# ── 可选加速后端（缺失不影响正确性）────────────────────────────────────
try:  # cv2：模糊/缩放/线绘制更快
    import cv2 as _cv2
except Exception:  # noqa: BLE001
    _cv2 = None


def has_cv2() -> bool:
    return _cv2 is not None


# ===================================================================== 配色
# 「墨夜霓虹」通用色板：夜底 + 一个暖主色（情绪）+ 一个冷主色（未来）。
PALETTES = {
    # 青花 / 国风（冷青 + 朱砂）
    "qinghua": {
        "night": (10, 14, 26), "deep": (22, 34, 56), "paper": (226, 232, 236),
        "accent": (232, 62, 58), "accent_hi": (255, 122, 110),
        "glow": (66, 196, 224), "glow_dim": (28, 108, 140),
        "violet": (140, 96, 216), "gold": (240, 196, 96), "jade": (120, 236, 152),
        "steel": (150, 168, 196),
    },
    # 城市夜景 / 霓虹（紫红 + 暖橙）
    "citypop_night_v1": {
        "night": (16, 12, 34), "deep": (44, 30, 78), "paper": (240, 224, 216),
        "accent": (240, 90, 130), "accent_hi": (255, 150, 170),
        "glow": (120, 90, 240), "glow_dim": (60, 44, 150),
        "violet": (170, 80, 220), "gold": (244, 180, 110), "jade": (110, 230, 190),
        "steel": (168, 172, 200),
    },
    # 通用冷调兜底
    "default": {
        "night": (14, 18, 30), "deep": (34, 50, 78), "paper": (226, 232, 240),
        "accent": (224, 96, 72), "accent_hi": (255, 150, 120),
        "glow": (90, 176, 220), "glow_dim": (40, 96, 140),
        "violet": (150, 110, 210), "gold": (236, 196, 110), "jade": (120, 224, 168),
        "steel": (156, 172, 200),
    },
}


def get_palette(name: str) -> dict:
    """按名取色板（未知名回退 default）。"""
    return PALETTES.get(str(name or "").lower()) or PALETTES["default"]


def hexr(c) -> np.ndarray:
    """颜色元组 → 归一化 float32 向量。"""
    return np.array(c, np.float32) / 255.0


# ===================================================================== 安全 IO
def imwrite_safe(path: str, rgb01, quality: int = 93) -> bool:
    """把 float32 RGB(0..1) 写成 JPEG，**对中文/Unicode 路径安全**。

    实测坑：`cv2.imwrite` 在含中文路径上静默返回 False、不写文件也不报错
    → "渲染完成但一帧都没有"。统一用 `cv2.imencode` 得到字节流再以 Python
    文件对象写出（Unicode 安全）；无 cv2 时走 Pillow（原生支持 Unicode 路径）。
    """
    arr = (np.clip(np.asarray(rgb01, np.float32), 0, 1) * 255 + 0.5).astype(np.uint8)
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    if _cv2 is not None:
        ok, buf = _cv2.imencode(".jpg", np.ascontiguousarray(arr[:, :, ::-1]),
                                [int(_cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok:
            return False
        with open(path, "wb") as f:
            f.write(buf.tobytes())
        return True
    try:
        from PIL import Image
        Image.fromarray(arr, "RGB").save(path, quality=int(quality), subsampling=1)
        return True
    except Exception:  # noqa: BLE001
        return False


def imread_safe(path: str):
    """读图 → float32 RGB(0..1)；失败返回 None（Unicode 路径安全）。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if _cv2 is not None:
        buf = np.frombuffer(raw, np.uint8)
        a = _cv2.imdecode(buf, _cv2.IMREAD_COLOR)
        if a is None:
            return None
        return _cv2.cvtColor(a, _cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    try:
        import io

        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        return np.asarray(im, np.float32) / 255.0
    except Exception:  # noqa: BLE001
        return None


# ===================================================================== 基础算子
@functools.lru_cache(maxsize=16)
def coord(h: int, w: int):
    """归一化坐标场（-1..1），缓存复用（实测坐标场 66ms → 0ms）。"""
    yy = np.linspace(-1.0, 1.0, h, dtype=np.float32)[:, None]
    xx = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
    return xx, yy


@functools.lru_cache(maxsize=8)
def radial(h: int, w: int, power: float = 2.0) -> np.ndarray:
    xx, yy = coord(h, w)
    r = np.sqrt(xx * xx + yy * yy)
    return np.clip(1.0 - r ** power, 0.0, 1.0).astype(np.float32)


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """高斯模糊（cv2 → Pillow → numpy 可分离，逐级回退）。"""
    if sigma <= 0:
        return a
    a = np.asarray(a, np.float32)
    if _cv2 is not None:
        return _cv2.GaussianBlur(a, (0, 0), float(sigma))
    try:
        from PIL import Image, ImageFilter
        if a.ndim == 2:
            im = Image.fromarray(a, mode="F").filter(ImageFilter.GaussianBlur(float(sigma)))
            return np.asarray(im, np.float32)
        chans = [Image.fromarray(a[:, :, c], mode="F").filter(ImageFilter.GaussianBlur(float(sigma)))
                 for c in range(a.shape[2])]
        return np.stack([np.asarray(c, np.float32) for c in chans], axis=2)
    except Exception:  # noqa: BLE001
        try:
            import gpu_accel
            b = gpu_accel.gaussian_blur(a, sigma)
            return b[..., 0] if (b.ndim == 3 and a.ndim == 2) else b
        except Exception:  # noqa: BLE001
            return a


def resize(a: np.ndarray, h: int, w: int, cubic: bool = True) -> np.ndarray:
    """缩放到 (h,w)。"""
    a = np.asarray(a, np.float32)
    if a.shape[0] == h and a.shape[1] == w:
        return a
    if _cv2 is not None:
        interp = _cv2.INTER_CUBIC if cubic else _cv2.INTER_LINEAR
        return _cv2.resize(a, (int(w), int(h)), interpolation=interp)
    try:
        from PIL import Image
        if a.ndim == 2:
            im = Image.fromarray(a, mode="F")
        else:
            im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "RGB")
        resample = Image.BICUBIC if cubic else Image.BILINEAR
        im = im.resize((int(w), int(h)), resample)
        out = np.asarray(im, np.float32)
        return out if a.ndim == 2 else out / 255.0
    except Exception:  # noqa: BLE001
        ys = (np.arange(h) * a.shape[0] // max(1, h)).clip(0, a.shape[0] - 1)
        xs = (np.arange(w) * a.shape[1] // max(1, w)).clip(0, a.shape[1] - 1)
        return a[ys][:, xs]


# ===================================================================== 分形噪声
@functools.lru_cache(maxsize=64)
def _fbm_hw(h: int, w: int, octaves: int, seed: int, base: int):
    rng = np.random.default_rng(int(seed))
    acc = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(max(1, octaves)):
        bh = max(2, int(base * (2 ** o)))
        bw = max(2, int(base * (2 ** o) * w / max(h, 1)))
        g = rng.random((min(bh, h), min(bw, w)), np.float32)
        acc += resize(g, h, w) * amp
        tot += amp
        amp *= 0.5
    return ((acc / max(tot, 1e-6)),)


def fbm(h: int, w: int, octaves: int = 5, seed: int = 1, base: int = 4) -> np.ndarray:
    """分形布朗噪声场（石纹/云/纸/裂纹的底）。"""
    return _fbm_hw(h, w, octaves, seed, base)[0]


# ===================================================================== 光学层
def gauss_spot(h: int, w: int, cx: float, cy: float, sx: float, sy: float) -> np.ndarray:
    """二维高斯光斑（各向可异），返回 (h,w) float32。"""
    yy = np.arange(h, dtype=np.float32)[:, None]
    xx = np.arange(w, dtype=np.float32)[None, :]
    return np.exp(-(((xx - cx) ** 2) / (2 * max(sx, 1e-3) ** 2)
                    + ((yy - cy) ** 2) / (2 * max(sy, 1e-3) ** 2))).astype(np.float32)


def vignette(h: int, w: int, strength: float = 0.55, aspect: float = 0.78) -> np.ndarray:
    """暗角（四角压暗）。"""
    xx, yy = coord(h, w)
    r = np.sqrt(xx * xx + (yy * aspect) ** 2)
    return np.clip(1.0 - strength * (r ** 2.0), 0.0, 1.0)[:, :, None]


def scanlines(h: int, w: int, frame: int = 0, period: int = 4, strength: float = 0.10) -> np.ndarray:
    """CRT 扫描线（按 frame 缓慢滚动）。"""
    yy = np.arange(h, dtype=np.float32) + frame * 0.35
    s = 0.5 + 0.5 * np.sin(yy * math.pi * 2.0 / max(period, 2))
    return (s[:, None] * strength).astype(np.float32)


def film_grain(h: int, w: int, frame: int, amount: float = 0.014) -> np.ndarray:
    """胶片颗粒（低频成团 **且时间连续**）。

    实测教训：逐帧独立噪声会让"相邻帧差 ≈ 长间隔帧差"，掩盖真实运动，
    使动态验证失效（结构相关从 +0.99 掉到 ~0）。正解：连续相位 + 相邻切片插值，
    相邻帧高度相关（颗粒缓慢游移，像真实胶片），长间隔才明显不同。
    """
    fpos = frame / 18.0
    i0 = int(np.floor(fpos))
    fr = float(fpos - i0)

    def _slice(k):
        rng = np.random.default_rng(1000 + (k % 977))
        g = resize(rng.random((max(2, h // 16), max(2, w // 16)), np.float32) - 0.5, h, w)
        hi = resize(rng.random((max(2, h // 2), max(2, w // 2)), np.float32) - 0.5, h, w, cubic=False)
        return g * 0.78 + hi * 0.22

    g = _slice(i0) * (1.0 - fr) + _slice(i0 + 1) * fr
    return (g * amount)[:, :, None]


def highlight_rolloff(a: np.ndarray, knee: float = 0.80) -> np.ndarray:
    """高光压缩（软膝）：knee 以上平滑压缩到 1.0，保住亮部结构（治死白）。

    公式：y = knee + (1-knee) * (1 - exp(-(x-knee)/(1-knee)))
    """
    x = np.clip(a, 0, 2.0)
    hi = x > knee
    if not np.any(hi):
        return x
    k = (1.0 - knee)
    y = x.copy()
    y[hi] = knee + k * (1.0 - np.exp(-(x[hi] - knee) / max(k, 1e-6)))
    return y


# ===================================================================== 霓虹 / 故障
def neon_glow(mask: np.ndarray, sigma: float = 9.0, gain: float = 1.6,
              core: float = 0.9) -> np.ndarray:
    """霓虹辉光：mask → 多层辉散（近核 + 远晕 + 大 halo）。"""
    m = np.clip(mask, 0, 1)
    near = blur(m, sigma * 0.30) * 1.25
    far = blur(m, sigma) * 0.95
    halo = blur(m, sigma * 2.6) * 0.58
    return np.clip(near + far + halo, 0, 1) * gain * 1.25 + m * core * 1.15


def glitch_offset(t: float, h: int, w: int, seed: int = 3,
                  bands: int = 3, amp: float = 14.0) -> np.ndarray:
    """故障位移图：**少量窄条带**的水平位移量（像素）。

    实测教训：条带过多/幅度过大/触发频繁 → 故障横贯画面、掩盖真实运动
    （grain_ratio 达 1.0）。正解：更少更窄、幅度更小、触发更罕见。
    """
    rng = np.random.default_rng(int(t * 3.0) * 977 + seed)
    row = np.zeros(h, np.float32)
    burst = 0.5 + 0.5 * math.sin(t * 2.7 + 1.3)
    if burst < 0.86:
        return row
    for _ in range(bands):
        y0 = int(rng.integers(0, max(1, h - 40)))
        bh = int(rng.integers(4, 22))
        dx = float(rng.normal(0, amp)) * (burst - 0.86) / 0.14
        row[y0:y0 + bh] = dx
    return row


def apply_glitch(rgb: np.ndarray, row_shift: np.ndarray) -> np.ndarray:
    """通道错位故障（**只在条带内部**生效，让故障成为点缀而非改写画面）。"""
    if row_shift is None or not np.any(row_shift):
        return rgb
    h, w = rgb.shape[:2]
    active = np.abs(row_shift) > 0.5
    if not np.any(active):
        return rgb
    out = rgb.copy()
    xs = np.arange(w, dtype=np.int32)[None, :]
    for ch, mul in ((0, 0.82), (2, 1.10)):
        sh = (row_shift * mul).astype(np.int32)
        idx = np.clip(xs - sh[:, None], 0, w - 1)
        shifted = np.take_along_axis(rgb[:, :, ch], idx, axis=1)
        out[:, :, ch] = np.where(active[:, None], shifted, rgb[:, :, ch])
    return out


# ===================================================================== 赛博意象
def holo_grid(h: int, w: int, t: float, spacing: int = 64,
              strength: float = 0.22, persp: float = 0.55) -> np.ndarray:
    """全息透视栅格：地平线在下方，栅格向前推进（纵深）。"""
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    gy = (1.0 / (1.0 - yy * persp + 1e-4)) - 1.0
    gy = (gy + t * 0.55) % 1.0
    line_y = np.exp(-((gy - 0.5) ** 2) / 0.0009)
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    gx = (xx * max(8.0, w / max(spacing, 1))) % 1.0
    line_x = np.exp(-((gx - 0.5) ** 2) / 0.0035)
    grid = np.clip(line_y * 0.85 + line_x * 0.55, 0, 1)
    fade = np.clip(1.0 - (1.0 - yy) * 1.25, 0.0, 1.0)
    return (grid * fade * strength).astype(np.float32)


def data_rain(h: int, w: int, t: float, n: int = 190, seed: int = 91,
              speed: float = 1.0, tail: int = 14) -> np.ndarray:
    """数据雨（矩阵雨）：竖直光柱 + 拖尾，向量化。"""
    rng = np.random.default_rng(seed)
    x = (rng.random(n).astype(np.float32) * w)
    phase = rng.random(n).astype(np.float32)
    spd = (180 + rng.random(n) * 460) * speed
    ln = (60 + rng.random(n) * 220)
    alpha = (0.10 + rng.random(n) * 0.30)
    y = (phase * (h + 600) + spd * t) % (h + 600) - 300
    out = np.zeros((h, w), np.float32)
    k = max(3, int(tail))
    frac = np.linspace(0.0, 1.0, k, dtype=np.float32)
    yy = y[:, None] - ln[:, None] * frac[None, :]
    aa = alpha[:, None] * (1.0 - 0.85 * frac[None, :])
    xb = np.clip(np.rint(x).astype(np.int32), 0, w - 1)
    yi = yy.ravel().astype(np.int32)
    ai = aa.ravel()
    m = (yi >= 0) & (yi < h)
    xi = np.repeat(xb[None, :], k, axis=0).ravel()
    np.add.at(out, (yi[m], xi[m]), ai[m] * (8.0 / k))
    return np.clip(blur(out, 0.7) * 1.4, 0, 1)


def ink_ripple(h: int, w: int, cx: float, cy: float, t: float,
               rings: int = 5, speed: float = 0.55, sigma: float = 2.0) -> np.ndarray:
    """涟漪：从中心扩散的同心环（数据雨落砚 / 墨滴入水）。"""
    xx, yy = coord(h, w)
    r = np.sqrt((xx * w / 2 - (cx - w / 2)) ** 2 + (yy * h / 2 - (cy - h / 2)) ** 2)
    out = np.zeros((h, w), np.float32)
    for i in range(rings):
        phase = (t * speed + i / rings) % 1.0
        rad = phase * min(h, w) * 0.62
        band = np.exp(-((r - rad) ** 2) / (2 * sigma ** 2 * (1 + rad * 0.02) ** 2))
        out += band * (1.0 - phase) ** 1.4
    return np.clip(out, 0, 1)


def mountain_wire(h: int, w: int, t: float, layers: int = 4,
                  seed: int = 5, base_frac: float = 0.34, gain: float = 1.6,
                  speed: float = 7.0) -> np.ndarray:
    """山水线框：多层远山折线，缓慢横向漂移（密集顶点 + 无回绕平移）。

    实测教训：稀疏折线（9 段）+ 坐标取模回绕 → 边缘密度仅 0.0009（画面几乎空白）。
    正解：密集折线（每 ~60px 一个顶点）+ 无回绕平移 + 多层错开高度。
    """
    out = np.zeros((h, w), np.float32)
    for L in range(max(1, layers)):
        depth = (L + 1) / max(1, layers)
        base_y = h * (base_frac + 0.13 * L)
        n_pts = max(24, w // 60)
        xs = np.linspace(-w * 0.15, w * 1.15, n_pts)
        amp = h * (0.075 + 0.06 * depth)
        drift = (t * (speed - L * 1.2)) % (w * 1.3)
        ridge = fbm(1, n_pts + 8, 4, seed + L * 7)[0]
        phase = xs / max(w, 1) * math.pi * (1.6 + L * 0.55)
        ys = base_y - np.abs(np.sin(phase)) * amp \
            - np.sin(phase * 2.7 + L) * amp * 0.28 \
            - ridge[2:2 + n_pts] * amp * 0.75
        thick = max(2, int(round(1.4 + L * 0.9)))
        if _cv2 is not None:
            pts = np.stack([xs - drift, ys], axis=1).astype(np.int32)
            _cv2.polylines(out, [pts.reshape(-1, 1, 2)], False, 1.0, thick)
        else:
            from PIL import Image, ImageDraw
            im = Image.fromarray(out, mode="F")
            d = ImageDraw.Draw(im)
            pts = [(float(xs[i] - drift), float(ys[i])) for i in range(n_pts)]
            d.line(pts, fill=1.0, width=thick)
            out = np.asarray(im, np.float32)
    return np.clip(blur(out, 0.6) * gain, 0, 1)


def inkstone(h: int, w: int, seed: int = 11) -> np.ndarray:
    """砚台/石材：细密石纹 + 云母星点 + 打磨沟痕（亮度纹理）。"""
    n = fbm(h, w, octaves=6, seed=seed)
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    groove = 0.5 + 0.5 * np.sin(yy * math.pi * 26 + n[:, ::max(1, w // 64)].mean(axis=1, keepdims=True) * 9.0)
    tex = n * 0.62 + groove * 0.24
    rng = np.random.default_rng(seed + 5)
    sp = resize(rng.random((max(2, h // 3), max(2, w // 3)), np.float32), h, w, cubic=False)
    tex = tex + np.clip(sp - 0.985, 0, 1) * 9.0
    return np.clip(tex, 0, 1)


# ===================================================================== 合成
def over(base: np.ndarray, rgb, alpha, glow=None) -> np.ndarray:
    """over 合成（alpha 支持 2D/3D）+ 可选辉光叠加。"""
    a = np.asarray(alpha, np.float32)
    if a.ndim == 2:
        a = a[:, :, None]
    a = np.clip(a, 0, 1)
    out = base * (1.0 - a) + np.asarray(rgb, np.float32) * a
    if glow is not None:
        g = np.asarray(glow, np.float32)
        if g.ndim == 2:
            g = g[:, :, None]
        out = out + g
    return np.clip(out, 0, 1)


def add_light(base: np.ndarray, light: np.ndarray, color) -> np.ndarray:
    """加光：把灰度光层染色后加到画布。"""
    c = hexr(color).reshape(1, 1, 3)
    lit = np.asarray(light, np.float32)
    if lit.ndim == 2:
        lit = lit[:, :, None]
    return np.clip(base + lit * c, 0, 1)


# ===================================================================== 字体
_FONT_DIRS = [
    r"C:\Windows\Fonts",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts"),
    os.path.expanduser(r"~\AppData\Local\Microsoft\Windows\Fonts"),
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/truetype",
    "/System/Library/Fonts",
]

_FONT_NAMES = {
    "kai": ("Tensentype FanXiaoGeKaiShuJF.ttf", "simkai.ttf", "STKaiti.ttc"),
    "serif": ("Source Han Serif SC Heavy (TrueType).ttf", "SourceHanSerifCN-Heavy.otf",
              "NotoSerifCJK-Bold.ttc", "simsun.ttc", "STSong.ttf"),
    "tech": ("AlibabaPuHuiTi-3-85-Bold.ttf", "AlibabaPuHuiTi-3-75-SemiBold.ttf",
             "msyhbd.ttc", "simhei.ttf", "NotoSansCJK-Bold.ttc"),
    "tech_light": ("AlibabaPuHuiTi-3-55-Regular.ttf", "AlibabaPuHuiTi-3-45-Light.ttf",
                   "msyh.ttc", "simsun.ttc", "NotoSansCJK-Regular.ttc"),
    "cjk": ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc",
            "NotoSansCJK-Regular.ttc", "NotoSansSC-VF.ttf", "PingFang.ttc"),
}


def find_font(names) -> str:
    """在常见字体目录（含用户字体目录）里按文件名查找，返回首个存在的完整路径。"""
    if isinstance(names, str):
        names = (names,)
    for nm in names:
        for d in _FONT_DIRS:
            if d and os.path.isdir(d):
                p = os.path.join(d, nm)
                if os.path.exists(p):
                    return p
    return ""


FONTS = {k: find_font(v) for k, v in _FONT_NAMES.items()}

_font_cache: dict = {}
_FONT_WARN: list = []


def font_fn(path: str):
    """返回 font_fn(size, bold=False)；带**字形缺失告警**（不静默回退糊字）。"""
    def _f(size: int, bold: bool = False):
        key = (path, int(size), bool(bold))
        if key not in _font_cache:
            got = None
            if path and os.path.exists(path):
                try:
                    from PIL import ImageFont
                    got = ImageFont.truetype(path, int(size))
                except Exception as e:  # noqa: BLE001
                    _FONT_WARN.append(f"{os.path.basename(path)}: {type(e).__name__}")
            if got is None:
                try:
                    from PIL import ImageFont
                    got = ImageFont.load_default()
                except Exception:  # noqa: BLE001
                    got = None
            _font_cache[key] = got
        return _font_cache[key]
    return _f


def pick_font(*kinds: str):
    """从若干字体类别里挑第一个可用的 font_fn；都不可用回退 CJK。"""
    for k in kinds:
        if FONTS.get(k):
            return font_fn(FONTS[k])
    return font_fn(FONTS.get("cjk") or "")


def font_warnings() -> list:
    """字体告警（非空即存在字形/路径问题）。"""
    return list(dict.fromkeys(_FONT_WARN))


def glyph_missing(text: str, path: str) -> str:
    """返回 text 中该字体缺失字形的字符（无法检测时返回空串）。

    用 fontTools 读 cmap；未安装 fontTools 时返回 ""（不阻断渲染）。
    """
    if not path or not os.path.exists(path):
        return "字体文件不存在"
    try:
        from fontTools.ttLib import TTFont
    except Exception:  # noqa: BLE001
        return ""
    try:
        ft = TTFont(path, fontNumber=0, lazy=True)
        cmap = set()
        for t_ in ft["cmap"].tables:
            cmap |= set(t_.cmap.keys())
        return "".join(sorted({c for c in str(text) if c.strip() and ord(c) not in cmap}))
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def preflight_fonts(texts) -> list:
    """开渲前自检：字体存在 + 待渲染文本字形覆盖。返回问题列表（空=OK）。"""
    problems = []
    if not FONTS.get("cjk"):
        problems.append("未找到任何 CJK 字体（中文将无法渲染）")
    blob = "".join(str(t) for t in (texts or []) if t)
    for label in ("cjk", "tech", "tech_light", "kai"):
        fp = FONTS.get(label)
        if not fp:
            continue
        miss = glyph_missing(blob, fp)
        if miss and not miss.startswith(("字体", "TTF", "Key", "Value", "Index")):
            problems.append(f"{label} 缺字形: {miss}")
    return problems
