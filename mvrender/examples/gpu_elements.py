"""示例：用**底座原语**写 GPU 友好的元素（教学 + 可直接复制到项目里）。

这是方案 A 的示范：底座不实现任何具体元素，只提供原语；
项目作者按下面的模式写自己的元素即可获得 GPU 加速，且换项目无需改底座。

核心模式（对照）：
    慢（CPU 全帧）：                    快（底座 GPU 原语）：
    lay = np.zeros((H, W))             canvas.rasterize(
    cv2.rectangle(lay, ...)                rects=[...], lines=[...],
    cv2.GaussianBlur(lay, σ)               color=..., gain=..., blur=σ)
    canvas.add(lay[:, :, None] * color)

实测（本机 gfx1200，1080×1920）：
    路灯 165.9ms → 13.2ms（12.6x）
    雨幕 111.1ms →  8.2ms（13.5x）
    窗框 108.2ms → 12.5ms（ 8.6x）
"""
from __future__ import annotations

import math

import numpy as np


# ── 1) 路灯：矩形/线/多边形/圆 + 发光，全部 GPU ──────────────────
def street_lamp(canvas, t, x, y_base, h, color=(196, 168, 132), op=1.0,
                arm=1.0, seed=3, cone=0.16, soft=14.0, ground=-1):
    """路灯：灯杆 + 光锥 + 灯泡（对比 CPU 版 12.6x）。"""
    x, y_base, h = int(x), int(y_base), int(h)
    if ground < 0:
        ground = y_base
    ax = int(x + h * 0.20 * arm)
    bulb_y = y_base - h + int(h * 0.045)
    lw1 = max(3, int(h * 0.020))
    lw2 = max(3, int(h * 0.016))
    # 椭圆用 16 边形近似
    ex, ey = int(h * 0.060), int(h * 0.024)
    ell = [(ax + ex * math.cos(i * math.pi / 8), bulb_y + ey * math.sin(i * math.pi / 8))
           for i in range(16)]
    pole_lines = [(x, y_base, x, y_base - h, lw1, 1.0),
                  (x, y_base - h, ax, y_base - h + int(h * 0.03), lw2, 1.0)]
    # 灯杆实体（线 + 椭圆）
    canvas.rasterize(lines=pole_lines, polys=[(ell, 1.0)],
                     color=(22, 26, 32), gain=op, blur=0.0)
    flick = 0.88 + 0.12 * (0.5 + 0.5 * math.sin(t * 5.3)) * (0.7 + 0.3 * math.sin(t * 0.7 + seed))
    # 光锥（多边形 + 大模糊）
    cone_pts = [(ax - int(h * 0.048), y_base - h + int(h * 0.05)),
                (ax + int(h * 0.048), y_base - h + int(h * 0.05)),
                (ax + int(h * cone), ground), (ax - int(h * cone), ground)]
    canvas.rasterize(polys=[(cone_pts, 1.0)], color=color,
                     gain=0.22 * flick * op, blur=soft)
    # 灯头径向光晕
    canvas.glow_add(ax / canvas.w, bulb_y / canvas.h, 0.20, color,
                    gain=0.50 * flick * op, edge=0.62)
    # 灯杆发光（blur_n=6 对齐 glow_layer(scale=6) 语义）
    canvas.rasterize(lines=pole_lines, polys=[(ell, 1.0)], color=color,
                     gain=0.30 * op * 0.60, blur=8.0, blur_n=6)
    # 灯泡亮点
    canvas.rasterize(circles=[(ax, bulb_y, max(4, int(h * 0.022)), 1.0)],
                     color=color, gain=1.35 * flick * op, blur=max(6.0, h * 0.030))


# ── 2) 雨幕：纹理常驻 + 每帧一个位移 kernel ──────────────────────
def make_rain_texture(w, h, seed=1, dens=520, ang=11.0, lk=0.115, thickness=1, blur=0.0):
    """生成一张雨纹贴图（**一次性**，CPU；之后常驻显存复用）。"""
    import cv2
    rng = np.random.default_rng(seed)
    tex = np.zeros((h, w), np.float32)
    L = int(max(w, h) * lk)
    for _ in range(dens):
        x = rng.uniform(-40, w + 40)
        y = rng.uniform(-40, h + 40)
        ln = rng.uniform(L * 0.45, L)
        dx = ln * math.sin(math.radians(ang))
        dy = ln * math.cos(math.radians(ang))
        cv2.line(tex, (int(x), int(y)), (int(x + dx), int(y + dy)),
                 float(rng.uniform(0.35, 1.0)), thickness, cv2.LINE_AA)
    if blur > 0:
        tex = cv2.GaussianBlur(tex, (0, 0), blur)
    return tex


def rain(canvas, t, texs=None, op=1.0, gust=0.0, tints=None):
    """三层雨幕：每层一个 add_rolled（纹理常驻，无 CPU 全帧运算，13.5x）。

    texs: [(tex, speed, alpha), ...] —— 由 make_rain_texture 生成并缓存。
    """
    if not texs:
        return
    tints = tints or [(196, 176, 148), (232, 214, 186), (250, 240, 222)]
    for li, (tex, spd, al) in enumerate(texs):
        spd = spd * (1.0 + 0.55 * gust)
        off = int((t * spd + li * 391) % canvas.h)
        wob = int(6 * math.sin(t * 0.7 + li)) if gust > 0 else 0
        w = al * op * (0.86 + 0.30 * math.sin(t * (1.0 + 0.35 * li) + li))
        canvas.add_rolled(tex, off, wob, tints[li % len(tints)], w)


# ── 3) 窗框：stroke 描边 + 分格线 + 发光 ─────────────────────────
def window_frame(canvas, t, x0, y0, x1, y1, op=1.0, pane=(0.5, 0.0), n_rail=1,
                 warm=(196, 168, 132)):
    """窗框（对比 CPU 版 8.6x）。"""
    L, T, R, B = int(x0), int(y0), int(x1), int(y1)
    tw = max(3, int((R - L) * 0.012))
    frame = [(L, T, R, B, tw, 0.85)]
    lines = []
    if pane[0] > 0:
        mx = int(L + (R - L) * pane[0])
        lines.append((mx, T, mx, B, max(3, int((R - L) * 0.010)), 0.85))
    if pane[1] > 0:
        my = int(T + (B - T) * pane[1])
        lines.append((L, my, R, my, max(3, int((R - L) * 0.010)), 0.85))
    for k in range(1, n_rail + 1):
        yy = T + int((B - T) * k / (n_rail + 1))
        lines.append((L, yy, R, yy, 2, 0.35))
    canvas.rasterize(stroke_rects=frame, lines=lines, color=(58, 64, 76),
                     gain=op, blur=0.0)
    # 窗框发光（blur_n=6 对齐 glow_layer(scale=6)）
    canvas.rasterize(stroke_rects=frame, lines=lines, color=warm,
                     gain=0.42 * op * 0.60, blur=10.0, blur_n=6)


# ── 4) 残页/板：圆角矩形 + 纹理 + 文字 ───────────────────────────
def panel(canvas, x, y, w, h, color=(196, 208, 220), radius=8, op=1.0, text_mask=None,
          text_color=(30, 40, 55)):
    """一块圆角面板（可选贴文字遮罩）。"""
    canvas.rasterize(round_rects=[(x, y, x + w - 1, y + h - 1, radius, 1.0)],
                     color=color, gain=op, blur=0.0)
    if text_mask is not None:
        canvas.add_text_mask(text_mask, int(x), int(y), color=text_color, gain=op)
