"""mv_scene —— 题材无关的场景基元库（动画意象）。

每个基元是一个**纯函数**：`fn(H, W, t, ctx) -> RGB float32(H,W,3)`。

统一契约（沉淀自《山河砚》MV）：
- 用**全局 t**（秒）驱动动画，保证跨镜头连续（换镜时山脊漂移/星轨旋转不跳变）。
- 无状态、无副作用、可多进程；同一 t 必然同一帧（确定性）。
- `ctx` 携带 `palette`（色板名）、`lyric`（该镜歌词）、`variant`（副歌再现差异化）等。

这些基元**不针对某一首歌**：它们是把"山河/星空/城市/江河/雨/眼/人/桥/字"等
通用意象抽象成可动画的场。具体镜头如何与歌词绑定由 `choose_scene` + 分镜表决定。
"""
from __future__ import annotations

import math

import numpy as np

import mv_tex as T


def _pal(ctx) -> dict:
    p = (ctx or {}).get("pal")
    if isinstance(p, dict):
        return p
    return T.get_palette((ctx or {}).get("palette") or "default")


def _base(H: int, W: int, t: float, pal: dict, seed: int = 11) -> np.ndarray:
    """夜石底色：石材 + 冷暖环境光（允许上面生长任何东西）。"""
    tex = T.inkstone(H, W, seed=seed)
    base = T.hexr(pal["night"])[None, None, :] * (1.0 - tex[:, :, None] * 0.62) \
        + T.hexr(pal["deep"])[None, None, :] * tex[:, :, None] * 0.62
    base = base * 1.75 + 0.028
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    base = base + T.hexr(pal["glow"])[None, None, :] * (1 - yy)[:, :, None] * 0.045
    base = base + T.hexr(pal["accent"])[None, None, :] * yy[:, :, None] * 0.030
    return np.clip(base, 0, 1)


def _finish(out: np.ndarray, H: int, W: int, t: float, pal: dict,
            glitch: bool = True, grain: bool = True) -> np.ndarray:
    """统一收尾（顺序有物理含义）：暗角 → 扫描线 → 故障 → 颗粒。"""
    out = out * T.vignette(H, W, 0.48)
    out = out + T.scanlines(H, W, frame=int(t * 30), period=3, strength=0.045)[:, :, None] \
        * T.hexr(pal["glow"])[None, None, :] * 0.35
    if glitch:
        rs = T.glitch_offset(t, H, W, seed=3)
        if np.any(rs):
            out = T.apply_glitch(out, rs)
    if grain:
        out = out + T.film_grain(H, W, int(t * 30))
    return np.clip(out, 0, 1)


# ===================================================================== 1. 砚台 / 大地
def inkstone_land(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【砚台 · 大地】一方砚台 = 一片土地；墨池 = 江河；漩涡 = 岁月。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=11)
    cx, cy = W * 0.5, H * 0.56
    rx, ry = W * 0.30, W * 0.30 * 0.44
    yy = np.arange(H, dtype=np.float32)[:, None]
    xx = np.arange(W, dtype=np.float32)[None, :]
    dx = (xx - cx) / rx
    dy = (yy - cy) / ry
    r = np.sqrt(dx * dx + dy * dy)
    ang = np.arctan2(dy, dx)
    bowl = np.clip(1.0 - r, 0, 1) ** 0.7
    out = out + bowl[:, :, None] * T.hexr(pal["deep"])[None, None, :] * 0.35
    spin = t * 0.62
    twist = ang * 2.0 + np.log(np.clip(r, 1e-3, 9)) * 3.4 + spin
    swirl = (0.5 + 0.5 * np.sin(twist * 2.2)) * bowl * np.clip(1.0 - r * 0.22, 0, 1)
    out = out * (1.0 - swirl[:, :, None] * 0.72)
    edge = np.clip(swirl - 0.62, 0, 1) * 2.6
    out = T.add_light(out, T.neon_glow(edge, sigma=7.0, gain=1.5, core=0.5) * bowl, pal["glow"])
    rim = np.exp(-((r - 1.0) ** 2) / 0.006)
    out = T.add_light(out, rim * 1.5, pal["glow"])
    out = T.add_light(out, T.ink_ripple(H, W, cx, cy, t, rings=4, speed=0.35) * 0.5, pal["violet"])
    out = T.add_light(out, T.holo_grid(H, W, t, strength=0.10), pal["glow"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 2. 山脊
def mountains(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【山河为砚】多层霓虹山脊错开高度、缓慢漂移（青→紫→朱空气透视）。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=23)
    ridge = T.mountain_wire(H, W, t, layers=4, seed=5, base_frac=0.30, gain=1.5)
    colors = (pal["glow"], pal["violet"], pal["accent"], pal["gold"])
    for i, c in enumerate(colors):
        layer = np.clip(ridge - i * 0.18, 0, 1)
        out = T.add_light(out, layer * (0.9 - i * 0.12), c)
    # 朱砂大印（山河为砚的落款）
    if ctx.get("stamp", True):
        cy, cx = int(H * 0.66), int(W * 0.5)
        s = int(min(H, W) * 0.075)
        plate = np.zeros((H, W), np.float32)
        plate[cy - s:cy + s, cx - s:cx + s] = 1.0
        plate = T.blur(plate, 1.2) * (0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 1.1)))
        out = T.add_light(out, plate * 0.5, pal["accent"])
    out = T.add_light(out, T.holo_grid(H, W, t, strength=0.08), pal["glow"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 3. 星空
def starry_sky(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【星辰为灯】旋转星轨 + 灯阵（缓慢自转，非逐帧跳变）。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=31) * 0.7
    cx, cy = W * 0.5, H * 0.42
    rng = np.random.default_rng(7)
    n = 220
    rad = rng.random(n) ** 0.7 * min(H, W) * 0.62
    ang0 = rng.random(n) * 2 * math.pi
    size = (rng.random(n) * 2.2 + 0.6).astype(np.float32)
    stars = np.zeros((H, W), np.float32)
    ang = ang0 + t * (0.06 + 0.02 * (rng.random(n)))
    px = (cx + np.cos(ang) * rad).astype(np.int32)
    py = (cy + np.sin(ang) * rad * 0.85).astype(np.int32)
    ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
    np.add.at(stars, (py[ok], px[ok]), size[ok])
    out = T.add_light(out, T.blur(stars, 1.0) * 0.9, pal["paper"])
    out = T.add_light(out, T.neon_glow(stars, sigma=6.0, gain=1.0, core=0.3) * 0.5, pal["glow"])
    # 灯阵（地平线上一排呼吸灯）
    for k in range(9):
        x = W * (0.12 + 0.76 * k / 8.0)
        breath = 0.5 + 0.5 * math.sin(t * 1.3 + k * 0.7)
        out = T.add_light(out, T.gauss_spot(H, W, x, H * 0.78, 40.0, 30.0) * breath, pal["gold"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 4. 城市
def neon_city(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【城市霓虹】全息栅格 + 楼体线框 + 数据雨（赛博纵深）。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=41)
    out = T.add_light(out, T.holo_grid(H, W, t, spacing=52, strength=0.22), pal["glow"])
    rng = np.random.default_rng(13)
    horizon = H * 0.72
    for _ in range(16):
        bw = rng.integers(int(W * 0.03), int(W * 0.10))
        bh = rng.integers(int(H * 0.12), int(H * 0.42))
        bx = int(rng.integers(0, W - bw))
        by = int(horizon - bh)
        col = T.hexr(pal["glow"] if rng.random() < 0.6 else pal["violet"])
        # 楼体线框
        frame = np.zeros((H, W), np.float32)
        frame[by:by + bh, bx:bx + 2] = 1.0
        frame[by:by + bh, bx + bw - 2:bx + bw] = 1.0
        frame[by:by + 2, bx:bx + bw] = 1.0
        out = T.add_light(out, frame * 0.6, pal["glow"])
        # 窗格（随机亮窗，按时间缓慢闪烁）
        wy = np.arange(by + 6, by + bh - 4, 14)
        wx = np.arange(bx + 6, bx + bw - 4, 12)
        for yv in wy:
            for xv in wx:
                if rng.random() < 0.28:
                    tw = 0.5 + 0.5 * math.sin(t * (1.0 + rng.random()) + xv * 0.01)
                    out[yv:yv + 5, xv:xv + 5] = np.clip(col * tw * 0.9, 0, 1)
    out = T.add_light(out, T.data_rain(H, W, t, n=140, speed=0.9) * 0.7, pal["glow"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 5. 江河
def river(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【不改道的江】流线型水脉：直江 vs 弯曲支流，持续流动。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=53)
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    for k in range(3):
        phase = xx * (5.0 + k * 3) + yy * 4.0 + t * (0.8 + k * 0.4)
        band = np.exp(-((np.sin(phase * math.pi) - (yy * 2 - 1) * (0.4 + k * 0.3)) ** 2) / 0.0025)
        out = T.add_light(out, band * (0.5 - k * 0.12), pal["glow"] if k % 2 == 0 else pal["violet"])
    # 主干直江（不被弯曲改道）
    straight = np.exp(-((xx - (0.5 + 0.04 * np.sin(t * 0.5))) ** 2) / 0.0008)
    out = T.add_light(out, straight * 0.6, pal["paper"])
    out = T.add_light(out, T.ink_ripple(H, W, W * 0.5, H * 0.5, t, rings=6, speed=0.4) * 0.4, pal["jade"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 6. 雨
def rainfall(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【眼泪变河口】数据雨/泪雨落下，在下方化成涟漪。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=61)
    out = T.add_light(out, T.data_rain(H, W, t, n=220, speed=1.3, tail=18) * 1.1, pal["glow"])
    out = T.add_light(out, T.ink_ripple(H, W, W * 0.5, H * 0.8, t, rings=7, speed=0.7, sigma=2.4) * 0.7,
                      pal["violet"])
    out = T.add_light(out, T.holo_grid(H, W, t, strength=0.10), pal["glow"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 7. 眼里有光
def eye_light(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【眼里有光】结构可辨的杏仁眼：眼睑开孔 + 虹膜等高线 + 瞳孔天际线。

    实测教训：过度抽象会"看不出是眼睛"→ 必须加可辨结构（眼睑/虹膜/瞳孔/高光）。
    """
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=71) * 0.8
    cx, cy = W * 0.5, H * 0.48
    rx, ry = W * 0.34, H * 0.16
    yy = np.arange(H, dtype=np.float32)[:, None]
    xx = np.arange(W, dtype=np.float32)[None, :]
    # 目光缓慢游移（让眼神"活"起来，避免静态）
    gx = 0.05 * math.sin(t * 0.6)
    gy = 0.03 * math.cos(t * 0.8)
    dx = (xx - cx) / rx - gx
    dy = (yy - cy) / ry - gy
    # 杏仁眼睑：上下两条弧围成的开孔
    lid = np.clip(1.0 - (dx ** 2 + (dy * 1.15) ** 2), 0, 1)
    eye = (lid > 0.06).astype(np.float32)
    eye = T.blur(eye, 2.0)
    iris = np.clip(1.0 - ((dx / 0.42) ** 2 + (dy / 0.55) ** 2), 0, 1)
    iris = np.clip(iris * eye, 0, 1)
    pupil = np.clip(1.0 - ((dx / 0.16) ** 2 + (dy / 0.22) ** 2), 0, 1)
    # 虹膜等高线：半径随时间轻微脉动
    rr = 0.30 + 0.015 * math.sin(t * 1.1)
    ring = np.abs(np.sqrt((dx / rr) ** 2 + (dy / (rr * 1.33)) ** 2) - 1.0)
    ring = np.exp(-(ring ** 2) / 0.02) * eye
    out = T.add_light(out, eye * 0.10, pal["steel"])
    out = T.add_light(out, iris * 0.5, pal["glow"])
    out = T.add_light(out, ring * 0.9, pal["violet"])
    out = T.add_light(out, pupil * 0.7, pal["night"])
    # 呼吸高光（含缓慢游移）
    hl = np.exp(-(((dx - 0.10) ** 2 + (dy + 0.14) ** 2) / 0.012))
    breath = 0.55 + 0.45 * math.sin(t * 1.6)
    out = T.add_light(out, hl * breath, pal["paper"])
    # 瞳孔里的城市天际线（装着山河）
    sky = np.zeros((H, W), np.float32)
    base_y = int(cy + ry * 0.10)
    for k in range(7):
        bx = int(cx - W * 0.03 + k * W * 0.010)
        bh = int((0.02 + 0.03 * ((k * 37) % 5) / 5.0) * H)
        sky[max(0, base_y - bh):base_y, bx:bx + max(2, int(W * 0.006))] = 1.0
    out = T.add_light(out, T.blur(sky, 0.8) * pupil * 0.9, pal["accent"])
    # 睫毛（电路）
    lash = np.zeros((H, W), np.float32)
    for k in range(11):
        a = -math.pi * 0.9 + k / 10.0 * math.pi * 1.8
        x0 = cx + math.cos(a) * rx * 1.02
        y0 = cy + math.sin(a) * ry * 1.05
        x1 = x0 + math.cos(a) * W * 0.03
        y1 = y0 + math.sin(a) * H * 0.02
        _draw_line(lash, x0, y0, x1, y1, 0.5)
    out = T.add_light(out, lash * 0.6, pal["glow"])
    return _finish(out, H, W, t, pal)


def _draw_line(canvas: np.ndarray, x0, y0, x1, y1, val) -> None:
    """在**灰度** canvas 上画线（原地累加，无返回值）。"""
    h, w = canvas.shape
    n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    ts = np.linspace(0, 1, max(2, n))
    xs = np.clip((x0 + (x1 - x0) * ts).astype(np.int32), 0, w - 1)
    ys = np.clip((y0 + (y1 - y0) * ts).astype(np.int32), 0, h - 1)
    np.add.at(canvas, (ys, xs), val)


# ===================================================================== 8. 群像
def crowd(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【沉默的人】群像剪影，缓慢起伏呼吸，个别轮廓有霓虹描边。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=83) * 0.9
    rng = np.random.default_rng(19)
    n = 13
    base_y = H * 0.86
    for i in range(n):
        cx = W * (0.06 + 0.88 * i / (n - 1)) + math.sin(t * 0.4 + i) * W * 0.006
        hh = H * (0.20 + 0.14 * rng.random())
        bw = W * (0.030 + 0.02 * rng.random())
        sway = math.sin(t * 0.5 + i * 1.1) * W * 0.004
        head_r = bw * 0.42
        head_y = base_y - hh - head_r
        sil = np.zeros((H, W), np.float32)
        yy = np.arange(H, dtype=np.float32)[:, None]
        xx = np.arange(W, dtype=np.float32)[None, :]
        body = ((np.abs(xx - (cx + sway)) < bw) & (yy > base_y - hh) & (yy < base_y)).astype(np.float32)
        head = (((xx - (cx + sway)) ** 2 + (yy - head_y) ** 2) < head_r ** 2).astype(np.float32)
        sil = np.clip(body + head, 0, 1)
        out = out * (1.0 - sil[:, :, None] * 0.85)
        if i % 3 == 0:
            edge = np.clip(T.blur(sil, 1.2) - T.blur(sil, 3.0), 0, 1) * 2.5
            out = T.add_light(out, edge * 0.5, pal["glow"])
    out = T.add_light(out, T.holo_grid(H, W, t, strength=0.08), pal["glow"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 9. 桥
def bridge(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【无名者成桥】骨桥接力：一串逐渐点亮的桥墩 + 流动光脉冲。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=97)
    n = 9
    deck_y = H * 0.60
    xs = np.linspace(W * 0.06, W * 0.94, n)
    piers = np.zeros((H, W), np.float32)
    for i, x in enumerate(xs):
        bh = H * (0.16 + 0.05 * math.sin(i * 1.3))
        _draw_line(piers, x, deck_y, x, deck_y + bh, 0.9)
        lit = 0.5 + 0.5 * math.sin(t * 1.2 - i * 0.6)
        out = T.add_light(out, T.gauss_spot(H, W, x, deck_y, 18.0, 10.0) * lit * 0.6, pal["accent"])
    out = T.add_light(out, piers, pal["glow"])
    # 桥面弧线
    deck = np.zeros((H, W), np.float32)
    for k in range(2):
        pts_x = np.linspace(W * 0.06, W * 0.94, 80)
        pts_y = deck_y - k * 8 - np.sin((pts_x / W) * math.pi) * H * 0.04
        for j in range(len(pts_x) - 1):
            _draw_line(deck, pts_x[j], pts_y[j], pts_x[j + 1], pts_y[j + 1], 0.7)
    out = T.add_light(out, deck * 0.7, pal["paper"])
    # 流动光脉冲（缓慢，避免帧间结构跳变）
    p = (t * 0.14) % 1.0
    px = W * 0.06 + (W * 0.88) * p
    out = T.add_light(out, T.gauss_spot(H, W, px, deck_y, 30.0, 24.0), pal["jade"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 10. 文字墙
def text_wall(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【天地识字】天幕文字墙：漂浮汉字缓慢升腾 + 闪烁（字是灯，灯是字）。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=101) * 0.75
    chars = str(ctx.get("wall_chars") or "山河砚墨岁月故乡中国风天地人")
    f = T.pick_font("tech", "cjk")(max(24, int(W * 0.06)))
    if f is not None:
        from PIL import Image, ImageDraw
        canvas = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(canvas)
        rng = np.random.default_rng(5)
        for i, ch in enumerate(chars):
            bx = rng.random()
            by = rng.random()
            px = bx * (W - W * 0.08)
            py = (by + (-t * (0.012 + 0.02 * (i % 4) / 4.0))) % 1.0 * (H - H * 0.10)
            tw = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * (0.7 + i * 0.13) + i))
            d.text((px, py), ch, font=f, fill=int(255 * 0.5 * tw))
        mask = np.asarray(canvas, np.float32) / 255.0
        out = T.add_light(out, T.blur(mask, 0.6) * 0.7, pal["paper"])
        out = T.add_light(out, T.neon_glow(mask, sigma=8.0, gain=0.9, core=0.3) * 0.5, pal["glow"])
    out = T.add_light(out, T.holo_grid(H, W, t, strength=0.10), pal["violet"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 11. 抽象流光（兜底）
def flow(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【抽象流光】无明确意象时的通用动画底：漂移的霓虹光带。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=7)
    xx, yy = T.coord(H, W)
    for k in range(5):
        ph = xx * (1.2 + k * 0.4) + yy * (0.8 + k * 0.3) + t * (0.16 + k * 0.08) + k
        band = np.exp(-(np.sin(ph * math.pi) ** 2) / 0.06)
        c = (pal["glow"], pal["violet"], pal["accent"], pal["jade"], pal["gold"])[k]
        out = T.add_light(out, band * (0.30 - k * 0.03), c)
    out = T.add_light(out, T.holo_grid(H, W, t, strength=0.10), pal["glow"])
    return _finish(out, H, W, t, pal)


# ===================================================================== 12. 空镜
def empty(H: int, W: int, t: float, ctx=None) -> np.ndarray:
    """【空砚 · 一滴未落】极简呼吸空镜（首尾与间奏用）。"""
    ctx = ctx or {}
    pal = _pal(ctx)
    out = _base(H, W, t, pal, seed=3) * 0.85
    breath = 0.5 + 0.5 * math.sin(t * 0.5)
    out = T.add_light(out, T.radial(H, W, 2.0) * (0.10 + 0.06 * breath), pal["glow"])
    out = T.add_light(out, T.ink_ripple(H, W, W * 0.5, H * 0.55, t, rings=3, speed=0.25) * 0.3,
                      pal["violet"])
    return _finish(out, H, W, t, pal, glitch=False, grain=True)


# ===================================================================== 注册表 + 选择
SCENES = {
    "inkstone": inkstone_land,
    "mountains": mountains,
    "sky": starry_sky,
    "city": neon_city,
    "river": river,
    "rain": rainfall,
    "eye": eye_light,
    "crowd": crowd,
    "bridge": bridge,
    "text_wall": text_wall,
    "flow": flow,
    "empty": empty,
}

# 关键词 → 场景（按顺序命中；用于无显式 scene 时按歌词自动选景）
_KEYWORD_SCENES = (
    (("山河", "土地", "大地", "泥土", "碑", "砚", "墨", "石"), "inkstone"),
    (("山", "岭", "峰", "崖", "峰", "岭"), "mountains"),
    (("星", "月", "夜", "天", "空", "梦", "灯"), "sky"),
    (("城", "街", "霓虹", "楼", "都会", "都市"), "city"),
    (("雨", "泪", "落", "滴", "哭"), "rain"),
    (("江", "河", "海", "浪", "川", "渡", "流", "水"), "river"),
    (("眼", "看", "望", "光", "眸"), "eye"),
    (("人", "群", "沉默", "众", "谁", "我们"), "crowd"),
    (("桥", "路", "行", "走", "接力", "过"), "bridge"),
    (("字", "书", "写", "名", "姓", "史", "卷", "识字"), "text_wall"),
)

_SCENE_CYCLE = ("inkstone", "mountains", "sky", "river", "city", "flow", "eye", "rain")


def choose_scene(lyric: str, index: int = 0) -> str:
    """按歌词关键词选景；无命中时按镜头序号确定性轮转（同输入同结果）。"""
    text = str(lyric or "")
    for words, scene in _KEYWORD_SCENES:
        if any(w in text for w in words):
            return scene
    return _SCENE_CYCLE[int(index) % len(_SCENE_CYCLE)]


def scene_names() -> list:
    return list(SCENES.keys())
