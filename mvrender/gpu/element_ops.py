"""元素层重算子的 GPU 接管（monkey-patch 注入，不改元素代码）。

策略
----
元素层的**绘制**（cv2.line/circle/文字 mask）是稀疏写入，适合留在 CPU；
但其中的**逐像素重算子**是真实热点：实测 `glow_layer` 每镜产出 ~20MB、
`mist` 单次 129ms。这些用 GPU 计算更划算。

为不改元素代码、也便于一键回退，这里用 patch 注入：
    import mvrender.gpu.element_ops as eo
    eo.install()      # 接管 vis_core.glow_layer / elements.mist
    eo.uninstall()

每个 patch：输入 CPU numpy（绘制产物）→ 上传 → GPU 计算 → 回读 CPU numpy，
保持调用方契约不变。净收益 = 一次上传/回读 换掉 CPU 的多趟大核运算。
"""
from __future__ import annotations

import numpy as np

from . import ops
from .runtime import Runtime

_ORIG = {}
_INSTALLED = False
_RT = None


def _rt(w, h):
    """取（或重建）与当前画布尺寸匹配的运行时。"""
    global _RT
    if _RT is None or _RT.w != w or _RT.h != h:
        _RT = Runtime.get(w=w, h=h)
    return _RT


# ── glow_layer：降采样 → 分离高斯 → 上采样 → ×gain ────────────────
def gpu_glow_layer(layer, sigma, gain, scale=6):
    """等价 vis_core.glow_layer（1/N 分辨率计算），在 GPU 上完成。

    CPU：resize(AREA) → GaussianBlur → resize(LINEAR) → ×gain×GLOW_MASTER
    GPU：k_resize1 降采样 → 分离高斯 → k_scale1 升采样并乘增益
    差异：INTER_AREA 用双线性近似（H 级缩放误差 < 1/255，视觉不可辨）。
    """
    import pyopencl as cl
    import vis_core as V

    h, w = layer.shape[:2]
    # 返回形状必须与输入一致：2D 进 2D 出；(H,W,1) 进 (H,W,1) 出；(H,W,C) 进 C 出。
    # 【踩坑】把 (H,W,1) 一律按 2D 返回会让 `glow_layer(x[:,:,None],...)[:,:,0]`
    # 这类调用抛 IndexError（实测 scenes.street_lamp）。
    in_ndim = layer.ndim
    in_c = layer.shape[2] if in_ndim == 3 else 1
    rt = _rt(w, h)
    dw, dh = max(1, w // scale), max(1, h // scale)
    gg = float(gain) * float(getattr(V, "GLOW_MASTER", 0.60))
    sg = max(0.5, float(sigma) / scale)

    def _chan(ch):
        src = np.ascontiguousarray(ch, np.float32)
        b_src = rt.up(src.reshape(-1), "el_glow_src")
        b_small = rt.buf("el_glow_small", max(1, dw * dh) * 4)
        rt.run("k_downsample1", dw * dh, b_src, b_small, w, h, dw, dh)  # 盒式平均=INTER_AREA
        b_blur = rt.buf("el_glow_blur2", max(1, dw * dh) * 4)
        ops.gauss_blur1(rt, b_small, b_blur, sg, dw, dh, tmp="el_glow_tmp")
        b_out = rt.buf("el_glow_out", w * h * 4)
        rt.run("k_scale1", w * h, b_blur, b_out, dw, dh, w, h, np.float32(gg))
        res = np.empty((h, w), np.float32)
        cl.enqueue_copy(rt.q, res, b_out)
        rt.finish()
        return res

    if in_ndim == 2:
        return _chan(layer)
    if in_c == 1:
        return _chan(layer[:, :, 0])[:, :, None]
    # 多通道：逐通道（CPU 版对 (H,W,C) 走同一路径）
    return np.stack([_chan(layer[:, :, k]) for k in range(in_c)], axis=2)


# ── mist：GPU 直接合成（sin/cos/广播搬上 GPU）────────────────────
def gpu_mist(canvas, t, seed=5, op=0.10, drift=(0.04, 0.012), color=(120, 150, 180)):
    """等价 elements.mist，但时空调制与整帧合成在 GPU 上完成。"""
    import cv2
    import elements as E

    # 尺寸取画布，不读 elements 模块属性：
    # 【踩坑】并非所有宿主工程的 elements.py 都定义 H/W（釉下青、旧城慢都没有），
    # `E.H` 会 AttributeError；而此处在同一 except 链里，会让 CPU 回退也一起失败。
    try:
        H = int(getattr(E, "H", getattr(canvas, "h", 1920)))
        W = int(getattr(E, "W", getattr(canvas, "w", 1080)))
    except Exception:  # noqa: BLE001
        H, W = 1920, 1080
    n_small = _ORIG["value_noise"](H // 4, W // 4, seed=seed, octaves=5, base=4)
    n = cv2.resize(n_small, (W, H), interpolation=cv2.INTER_CUBIC)
    c = canvas
    # GPU 画布：全程 device 合成；CPU 画布：退回原实现（避免为一次 mist 做搬运）
    if not hasattr(c, "_bname"):
        return _ORIG["mist"](canvas, t, seed=seed, op=op, drift=drift, color=color)
    c._flush_host()
    rt = c.rt if hasattr(c, "rt") else _rt(W, H)
    b_n = rt.up(np.ascontiguousarray(n, np.float32).reshape(-1), "el_mist_noise")
    b_buf = rt.buf(c._bname, c.n3 * 4)
    rt.run("k_mist", c.n3, b_buf, b_n, W, H, np.float32(t),
           np.float32(drift[0]), np.float32(drift[1]), np.float32(op),
           np.float32(color[0]), np.float32(color[1]), np.float32(color[2]))
    c._mark_device_dirty()


# ── 雨幕：纹理常驻显存 + kernel 循环位移 ─────────────────────────
_RAIN_SPECS = [(560.0, 1150, 0.11, 0.045, 1, 0.0, -2.0),   # 远：细密如雾
               (1080.0, 520, 0.22, 0.085, 1, 0.6, 0.0),    # 中
               (1950.0, 190, 0.40, 0.150, 2, 2.2, 3.0)]    # 近：粗长、虚焦
_RAIN_TINT = [(196, 176, 148), (232, 214, 186), (250, 240, 222)]


def gpu_rain(canvas, t, op=1.0, seed=1, layers=3, near=0.0, gust=0.0, slant=11.0):
    """等价 elements.rain，但三层雨幕改为「纹理常驻 + kernel 位移合成」。

    原实现每层：np.roll(tex)（全帧拷贝）+ tex[:,:,None]*tint*w（全帧广播）
    + 全帧上传 → 三层约 75MB/帧。GPU 版纹理只上传一次，每层一个 kernel。
    near 层保持原实现（局部圆点，量小）。
    """
    import math

    import cv2
    import elements as E
    if not hasattr(canvas, "add_rolled"):
        return _ORIG["rain"](canvas, t, op=op, seed=seed, layers=layers,
                             near=near, gust=gust, slant=slant)
    for li in range(min(layers, 3)):
        spd, dens, al, lk, th, bl, da = _RAIN_SPECS[li]
        tex = E._rain_tex(seed + li * 7, dens=dens, ang=slant + da, lk=lk,
                          thickness=th, blur=bl)
        spd = spd * (1.0 + 0.55 * gust)
        off = int((t * spd + li * 391) % E.H)
        wob = int(6 * math.sin(t * 0.7 + li)) if gust > 0 else 0
        w = al * op * (0.86 + 0.30 * math.sin(t * (1.0 + 0.35 * li) + li))
        canvas.add_rolled(tex, off, wob, _RAIN_TINT[li], w)
    if near > 0.0:
        if "near" not in E._rain_cache:
            rng = np.random.default_rng(77)
            E._rain_cache["near"] = [(rng.uniform(0, E.W), rng.uniform(0, E.H),
                                      rng.uniform(0.4, 1.0), rng.uniform(24, 66))
                                     for _ in range(18)]
        lay = np.zeros((E.H, E.W), np.float32)
        for (x, y, s, r) in E._rain_cache["near"]:
            yy = (y + t * (22 * s)) % (E.H + 220) - 110
            cv2.circle(lay, (int(x), int(yy)), int(r * 0.42), float(0.30 * s * near),
                       -1, cv2.LINE_AA)
        lay = cv2.GaussianBlur(lay, (0, 0), 5.5)
        canvas.add(lay[:, :, None] * np.array((245, 228, 205), np.float32) * 0.42)


# ── 行人剪影：局部绘制 + 局部 Canny/发光 + 区域合成 ───────────────
def gpu_walker(canvas, t, x, y_base, scale=1.0, going=1, umbrella=False, op=1.0,
               color=(10, 13, 18), rim=(150, 182, 212), step=None, dust=0.0, lean=0.0):
    """等价 props.walker，但全程在**局部包围盒**内运算。

    原实现三次全帧操作（body 广播、Canny 图层广播、glow_layer 广播），
    而人物只占画面 ~4%。改为局部绘制 + 局部处理 + add_region 合成。
    """
    import math

    import cv2
    import vis_core as V
    if not hasattr(canvas, "add_region"):
        return _ORIG["walker"](canvas, t, x, y_base, scale=scale, going=going,
                               umbrella=umbrella, op=op, color=color, rim=rim,
                               step=step, dust=dust, lean=lean)
    s = float(scale)
    y_base = int(y_base)
    ph = (t * 2.5 if step is None else step) + (0.0 if going > 0 else 1.6)
    legs = math.sin(ph)
    hip = y_base - int(150 * s)
    sh = y_base - int(250 * s)
    head = y_base - int(292 * s)
    xx = int(x + going * 9 * s * math.sin(ph * 0.5) + lean * 40 * s)
    pad = int(95 * s) + 10
    top = head - int(21 * s)                       # 头顶
    if umbrella:
        top = min(top, sh - int(58 * s) - int(31 * s))   # 伞顶（比头顶更高）
    x0 = max(0, xx - pad)
    y0 = max(0, top - 10)
    x1 = min(canvas.w, xx + pad)
    y1 = min(canvas.h, y_base + int(26 * s) + 10)
    if x1 <= x0 or y1 <= y0:
        return
    bw, bh = x1 - x0, y1 - y0
    body = np.zeros((bh, bw), np.float32)
    X, YB, HIP, SH, HEAD = xx - x0, y_base - y0, hip - y0, sh - y0, head - y0
    for sgn in (-1, 1):
        kx = X + sgn * int(28 * s * legs * going)
        cv2.line(body, (X, HIP), (kx, YB), 1.0, int(max(3, 15 * s)), cv2.LINE_AA)
    cv2.line(body, (X, HIP), (X, SH), 1.0, int(max(4, 25 * s)), cv2.LINE_AA)
    cv2.circle(body, (X, HEAD), int(21 * s), 1.0, -1, cv2.LINE_AA)
    cv2.line(body, (X, SH + int(18 * s)), (X + going * int(27 * s), SH + int(80 * s)),
             1.0, int(max(3, 12 * s)), cv2.LINE_AA)
    if umbrella:
        uy = SH - int(58 * s)
        cv2.line(body, (X + going * int(27 * s), SH + int(62 * s)), (X, uy), 1.0,
                 int(max(2, 7 * s)), cv2.LINE_AA)
        cv2.ellipse(body, (X, uy), (int(76 * s), int(31 * s)), 0, 180, 360, 1.0, -1, cv2.LINE_AA)
    canvas.add_region(body[:, :, None] * np.array(color, np.float32) * op, x0, y0, "add")
    e = cv2.Canny((np.clip(body, 0, 1) * 255).astype(np.uint8), 40, 120).astype(np.float32) / 255.0
    e = cv2.GaussianBlur(e, (0, 0), 1.2)
    canvas.add_region(e[:, :, None] * np.array(rim, np.float32) * (0.8 * op), x0, y0, "add")
    gl = V.glow_layer(e[:, :, None], 9, 0.5 * op, scale=6)[:, :, 0]
    canvas.add_region(gl[:, :, None] * np.array(rim, np.float32), x0, y0, "add")
    if dust > 0:
        d = np.zeros((bh, bw), np.float32)
        cv2.ellipse(d, (X, YB + 4), (int(34 * s), int(8 * s)), 0, 0, 360, 1.0, -1)
        canvas.add_region(cv2.GaussianBlur(d, (0, 0), 7)[:, :, None]
                          * np.array((120, 150, 175), np.float32) * dust, x0, y0, "add")


# ── 路灯：局部绘制（灯杆 + 光锥 + 灯泡 + 发光）────────────────────
def gpu_street_lamp(canvas, t, x, y_base, h, color=None, op=1.0, arm=1.0, seed=3,
                    cone=0.16, soft=14.0, ground=-1):
    """等价 scenes.street_lamp，但四块全帧操作全部局部化。

    原实现：灯杆层广播、光锥层广播、灯杆 glow_layer 广播、灯泡层广播 ——
    而路灯只占画面一小条。改为局部绘制 + 局部模糊 + add_region 合成。
    """
    import math

    import cv2
    import scenes as SC

    from mvrender.core.sparse import add_circle_blurred, add_fillpoly_blurred, draw_sparse
    if not hasattr(canvas, "add_region"):
        return _ORIG["street_lamp"](canvas, t, x, y_base, h, color=color, op=op,
                                    arm=arm, seed=seed, cone=cone, soft=soft,
                                    ground=ground)
    H, W = SC.H, SC.W
    if color is None:
        color = SC.WARM
    x, y_base, h = int(x), int(y_base), int(h)
    if ground < 0:
        ground = y_base
    ax = int(x + h * 0.20 * arm)
    bulb_y = y_base - h + int(h * 0.045)
    lw1 = max(3, int(h * 0.020))
    lw2 = max(3, int(h * 0.016))
    xs = [x, ax - int(h * cone), ax + int(h * cone), ax - int(h * 0.06), ax + int(h * 0.06)]
    x0 = max(0, min(xs) - 32)
    x1 = min(canvas.w, max(xs) + 32)
    y0 = max(0, y_base - h - int(h * 0.07) - 32)
    y1 = min(canvas.h, ground + 32)
    box = (x0, y0, x1, y1)

    def _pole(sub, ox, oy):
        cv2.line(sub, (x - ox, y_base - oy), (x - ox, y_base - h - oy), 1.0, lw1, cv2.LINE_AA)
        cv2.line(sub, (x - ox, y_base - h - oy), (ax - ox, y_base - h + int(h * 0.03) - oy),
                 1.0, lw2, cv2.LINE_AA)
        cv2.ellipse(sub, (ax - ox, bulb_y - oy), (int(h * 0.060), int(h * 0.024)),
                    0, 0, 360, 1.0, -1, cv2.LINE_AA)
    draw_sparse(canvas, box, _pole, blur=0, color=(22, 26, 32), gain=op)
    flick = 0.88 + 0.12 * (0.5 + 0.5 * math.sin(t * 5.3)) * (0.7 + 0.3 * math.sin(t * 0.7 + seed))
    pts = np.array([(ax - int(h * 0.048), y_base - h + int(h * 0.05)),
                    (ax + int(h * 0.048), y_base - h + int(h * 0.05)),
                    (ax + int(h * cone), ground), (ax - int(h * cone), ground)], np.int32)
    add_fillpoly_blurred(canvas, pts, blur=soft, color=color, gain=0.22 * flick * op, aa=False)
    canvas.glow_add(ax / W, bulb_y / H, 0.20, color, gain=0.50 * flick * op, edge=0.62)
    # 灯杆发光：等价 glow_layer(lay, 8, 0.30*op, scale=6)（降采样高斯 ≈ blur 8）
    draw_sparse(canvas, box, _pole, blur=8.0, color=color, gain=0.30 * op * 0.60)
    add_circle_blurred(canvas, (ax, bulb_y), max(4, int(h * 0.022)),
                       blur=max(6.0, h * 0.030), color=color, gain=1.35 * flick * op, aa=False)


# ── 城市天际线：局部绘制（楼 + 窗 + 灯）──────────────────────────
def gpu_city_skyline(canvas, t, horizon=0.55, seed=4, op=0.95, color=(62, 42, 26),
                     lamps=True, kind="mixed", window_op=0.42):
    """等价 elements.city_skyline，但楼/窗/灯在**局部包围盒**内完成。

    原实现三块全帧操作（楼广播、窗广播、灯直接改 buf）。楼最高也就 860px，
    整块内容集中在 [base-1200, base] 一带，局部化后上传量大幅下降。
    """
    import math

    import cv2
    import elements as E
    if not hasattr(canvas, "add_region"):
        return _ORIG["city_skyline"](canvas, t, horizon=horizon, seed=seed, op=op,
                                     color=color, lamps=lamps, kind=kind,
                                     window_op=window_op)
    H, W = E.H, E.W
    rng = np.random.default_rng(seed)
    base = int(H * horizon)
    blds = []
    x = -70
    min_top = base
    while x < W + 70:
        old = (kind == "old") or (kind == "mixed" and rng.random() < 0.45)
        bw = int(rng.uniform(96, 240))
        bh = int(rng.uniform(150, 300) if old else rng.uniform(380, 860))
        top = base - bh
        ant = False
        if old:
            roof = int(bh * 0.26)
        else:
            ant = rng.random() < 0.28
            roof = int(bh * 0.10) if ant else 0
        blds.append((x, bw, bh, old, top, ant))
        min_top = min(min_top, top - roof)
        x += bw + int(rng.uniform(0, 12))
    wh = E._win_tex(seed + 3).shape[0] if window_op > 0 else 0
    wy0 = max(0, base - wh) if window_op > 0 else base
    box_y0 = max(0, min(min_top, wy0) - 14)
    box_y1 = min(H, base + 2)
    hh = box_y1 - box_y0
    if hh <= 0:
        return
    col = np.array(color, np.float32)
    # 楼（含坡顶）
    lay = np.zeros((hh, W), np.float32)
    for (x, bw, bh, old, top, ant) in blds:
        cv2.rectangle(lay, (x, top - box_y0), (x + bw, base - box_y0), 1.0, -1)
        if old:
            ry = top - int(bh * 0.26) - box_y0
            cv2.line(lay, (x - 8, top - box_y0), (x + bw // 2, ry), 1.0, 6, cv2.LINE_AA)
            cv2.line(lay, (x + bw + 8, top - box_y0), (x + bw // 2, ry), 1.0, 6, cv2.LINE_AA)
        elif ant:
            cv2.line(lay, (x + bw // 2, top - box_y0),
                     (x + bw // 2, top - int(bh * 0.10) - box_y0), 1.0, 4, cv2.LINE_AA)
    lay = cv2.GaussianBlur(lay, (0, 0), 0.7)
    canvas.add_region(lay[:, :, None] * col * op, 0, box_y0, "add")
    # 窗灯
    if window_op > 0:
        wtex = E._win_tex(seed + 3)
        tile = cv2.resize(wtex, (W, wh))
        win = np.zeros((hh, W), np.float32)
        gy0, gy1 = wy0, base
        cy0, cy1 = gy0 - box_y0, gy1 - box_y0
        seg = tile[0:gy1 - gy0][:cy1 - cy0]
        win[cy0:cy0 + seg.shape[0]] = seg
        tw = 0.66 + 0.34 * np.sin(t * 0.8 + np.arange(W)[None, :] * 0.011)
        canvas.add_region((win * tw)[:, :, None] * np.array((205, 185, 150), np.float32)
                          * window_op, 0, box_y0, "add")
    # 灯（原实现直接覆盖 buf 的零星像素；这里在 host 镜像上覆盖后统一提交）
    if lamps:
        buf = canvas.buf
        for i in range(30):
            xx = int((i * 39 + 13) % W)
            yy = base - int(rng.uniform(0, 9)) - 4
            a = 0.22 + 0.20 * abs(math.sin(t * 1.5 + i))
            cv2.circle(buf, (xx, yy), 1, (a * 150, a * 200, a * 255), -1, cv2.LINE_AA)
        canvas._host_dirty = True


def _cpu_glow(layer, sigma, gain, scale=6):
    return _ORIG["glow_layer"](layer, sigma, gain, scale)


def _cpu_mist(canvas, t, **kw):
    return _ORIG["mist"](canvas, t, **kw)


# ── 大核高斯：降采样等价（覆盖「全帧 + 超大 σ」反模式）────────────
# 实测：σ=594 全帧 5370ms → 降采样 N=8 仅 6.1ms（876x），8bit 误差 0.09。
# 这是「优化后反而更慢」的主因——元素层大量 max(8, w*0.35) / max(10, w*0.55)
# 会产出 σ 数百的全帧模糊。
def cpu_blur_downsample(layer, sigma, n=None):
    """降采样等价大 σ 高斯（与全帧视觉等价）。"""
    import cv2
    if sigma is None or sigma <= 0:
        return layer
    sg = float(sigma)
    if sg < 8.0:
        return cv2.GaussianBlur(layer, (0, 0), sg)
    if n is None:
        n = 4 if sg < 48 else 8
    h, w = layer.shape[:2]
    small = cv2.resize(layer, (max(1, w // n), max(1, h // n)),
                       interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), max(0.5, sg / n))
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def gpu_blur_full(layer, sigma, n=None):
    """GPU 降采样版大核高斯（单/三通道）。"""
    import cv2
    import pyopencl as cl
    h, w = layer.shape[:2]
    if sigma is None or float(sigma) < 8.0:
        return cv2.GaussianBlur(layer, (0, 0), float(sigma or 0))
    sg = float(sigma)
    if n is None:
        n = 4 if sg < 48 else 8
    dw, dh = max(1, w // n), max(1, h // n)
    rt = _rt(w, h)
    ndim = layer.ndim
    chans = layer.shape[2] if ndim == 3 else 1
    outs = []
    for k in range(chans):
        src = np.ascontiguousarray(layer if ndim == 2 else layer[:, :, k], np.float32)
        b_src = rt.up(src.reshape(-1), "el_bd_src")
        b_small = rt.buf("el_bd_small", max(1, dw * dh) * 4)
        rt.run("k_downsample1", dw * dh, b_src, b_small, w, h, dw, dh)
        ops.gauss_blur1(rt, b_small, b_small, max(0.5, sg / n), dw, dh, tmp="el_bd_tmp")
        b_out = rt.buf("el_bd_out", w * h * 4)
        ops.resize1(rt, b_small, b_out, dw, dh, w, h)
        res = np.empty((h, w), np.float32)
        cl.enqueue_copy(rt.q, res, b_out)
        rt.finish()
        outs.append(res)
    if ndim == 2:
        return outs[0]
    return np.stack(outs, axis=2)


def patched_gaussian_blur(src, ksize, sigmaX, dst=None, sigmaY=0,
                          borderType=None):
    """替换 cv2.GaussianBlur：大 σ 全帧自动降采样。

    保持 cv2 参数签名兼容（ksize=(0,0)/0 表示按 σ 自动；sigmaY/borderType 忽略）。
    """
    import cv2
    if (borderType is None):
        borderType = cv2.BORDER_DEFAULT
    if (ksize not in ((0, 0), 0)) or src.dtype != np.float32 or src.ndim > 3:
        return _ORIG["gblur"](src, ksize, sigmaX, dst, sigmaY, borderType)
    sg = float(sigmaX or 0)
    # 阈值 16：σ<16 时降采样收益有限而误差可见（实测 σ=9 降 N=4 误差 13.5/255）。
    # σ≥16 才启用降采样，误差迅速回到 1/255 级（σ=60 4.5、σ=220 1.8、σ=594 2.1）。
    if sg < 16.0:
        return _ORIG["gblur"](src, ksize, sigmaX, dst, sigmaY, borderType)
    out = cpu_blur_downsample(src, sg)
    if dst is not None:
        dst[...] = out
        return dst
    return out


# ── 安装 / 卸载 ──────────────────────────────────────────────────
def install(enable=("glow_layer", "mist", "gblur", "rain", "walker")):
    """接管元素层重算子；enable 可指定子集（便于逐项验证与回退）。

    enable:
      glow_layer  高斯发光（GPU 降采样+分离高斯）
      mist        雾（GPU 时空调制合成）
      gblur       大 σ 全帧 GaussianBlur → 降采样等价（**最大收益**）
      rain        雨幕（纹理常驻显存 + kernel 循环位移）
    """
    global _INSTALLED
    if _INSTALLED:
        return False
    import cv2
    import vis_core as V
    _ORIG["glow_layer"] = V.glow_layer
    _ORIG["value_noise"] = V.value_noise
    _ORIG["gblur"] = cv2.GaussianBlur

    if "glow_layer" in enable:
        V.glow_layer = gpu_glow_layer
    try:
        import elements as E
        if "mist" in enable:
            _ORIG["mist"] = E.mist
            E.mist = gpu_mist
        if "rain" in enable:
            _ORIG["rain"] = E.rain
            E.rain = gpu_rain
        if "city_skyline" in enable:
            # 实验性：局部化的窗口贴图与全帧实现有 ~8-12/255 差异（收益仅 1.4x），
            # 默认不启用；仅在确认画质可接受时显式开启。
            _ORIG["city_skyline"] = E.city_skyline
            E.city_skyline = gpu_city_skyline
    except Exception:
        pass
    try:
        import props as P
        if "walker" in enable:
            _ORIG["walker"] = P.walker
            P.walker = gpu_walker
    except Exception:
        pass
    try:
        import scenes as SC
        if "street_lamp" in enable:
            _ORIG["street_lamp"] = SC.street_lamp
            SC.street_lamp = gpu_street_lamp
    except Exception:
        pass
    if "gblur" in enable:
        cv2.GaussianBlur = patched_gaussian_blur
    _INSTALLED = True
    return True


def uninstall():
    global _INSTALLED
    if not _INSTALLED:
        return False
    import cv2
    import vis_core as V
    if "glow_layer" in _ORIG:
        V.glow_layer = _ORIG["glow_layer"]
    try:
        import elements as E
        if "mist" in _ORIG:
            E.mist = _ORIG["mist"]
        if "rain" in _ORIG:
            E.rain = _ORIG["rain"]
        if "city_skyline" in _ORIG:
            E.city_skyline = _ORIG["city_skyline"]
    except Exception:
        pass
    try:
        import props as P
        if "walker" in _ORIG:
            P.walker = _ORIG["walker"]
    except Exception:
        pass
    try:
        import scenes as SC
        if "street_lamp" in _ORIG:
            SC.street_lamp = _ORIG["street_lamp"]
    except Exception:
        pass
    if "gblur" in _ORIG:
        cv2.GaussianBlur = _ORIG["gblur"]
    _INSTALLED = False
    return True


def stats():
    return {"installed": _INSTALLED,
            "patched": sorted(k for k in _ORIG if k != "value_noise")}
