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

    H, W = E.H, E.W
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
    import pyopencl as cl
    import cv2
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
def install(enable=("glow_layer", "mist", "gblur")):
    """接管元素层重算子；enable 可指定子集（便于逐项验证与回退）。

    enable:
      glow_layer  高斯发光（GPU 降采样+分离高斯）
      mist        雾（GPU 时空调制合成）
      gblur       大 σ 全帧 GaussianBlur → 降采样等价（**最大收益**）
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
    if "mist" in enable:
        try:
            import elements as E
            _ORIG["mist"] = E.mist
            E.mist = gpu_mist
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
    except Exception:
        pass
    if "gblur" in _ORIG:
        cv2.GaussianBlur = _ORIG["gblur"]
    _INSTALLED = False
    return True


def stats():
    return {"installed": _INSTALLED,
            "patched": sorted(k for k in _ORIG if k != "value_noise")}
