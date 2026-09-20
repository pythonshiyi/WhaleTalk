"""绘图内核的 GPU 接管（**通用**，不绑定任何具体项目/元素）。

定位
----
本项目（FastMV）的职责是**底座**，不是某个 MV 的实现。因此这里只接管
「任何项目都会用到的绘图内核级函数」，**不接管任何项目专属的元素函数**
（如某个片子的 street_lamp / window_frame / 人物剪影 —— 那些属于项目，
由项目自己用本底座提供的 Canvas API 实现）。

接管的三类（均为 `vis_core` / `cv2` 级别、跨项目通用）：

1. `GaussianBlur`（大 σ）→ 降采样等价实现
   元素层普遍有 σ 数百的全帧模糊（发光），实测 σ=594 全帧 5.4s → 降采样 6ms。
2. `glow_layer` → GPU 降采样高斯发光
3. `value_noise` → 保留原实现（已带缓存；GPU 化收益不足以抵消传输）

用法
----
    import mvrender.gpu.element_ops as eo
    eo.install()      # 接管（幂等）
    eo.uninstall()    # 一键回退

项目作者应在元素代码里**优先使用底座提供的 Canvas API**（见 mvrender/docs），
那些 API 本身就在 GPU 上执行，无需本模块接管：

    canvas.fill_rects(...) / draw_lines(...) / fill_circles(...) / fill_polys(...)
    canvas.rasterize(...) / over_region(...) / add_region(...) / add_text_mask(...)
    canvas.glow_add(...) / add_rolled(...) / mul_region(...)
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


# ── 1) 大 σ 全帧高斯：降采样等价 ─────────────────────────────────
def cpu_blur_downsample(layer, sigma, n=None):
    """降采样等价大 σ 高斯（CPU）。

    σ≥8 时降采样后模糊再升采样，信息损失可忽略（实测 σ=594 → 8bit 误差 2.1）。
    """
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


def patched_gaussian_blur(src, ksize, sigmaX, dst=None, sigmaY=0, borderType=None):
    """替换 cv2.GaussianBlur：大 σ 全帧自动降采样（保持签名兼容）。"""
    import cv2
    if borderType is None:
        borderType = cv2.BORDER_DEFAULT
    if (ksize not in ((0, 0), 0)) or getattr(src, "dtype", None) is None \
            or src.dtype != np.float32 or src.ndim > 3:
        return _ORIG["gblur"](src, ksize, sigmaX, dst, sigmaY, borderType)
    sg = float(sigmaX or 0)
    # 阈值 16：σ<16 时降采样收益有限而误差可见；σ≥16 误差回到 1/255 级
    if sg < 16.0:
        return _ORIG["gblur"](src, ksize, sigmaX, dst, sigmaY, borderType)
    out = cpu_blur_downsample(src, sg)
    if dst is not None:
        dst[...] = out
        return dst
    return out


# ── 2) glow_layer：GPU 降采样高斯发光 ────────────────────────────
def gpu_glow_layer(layer, sigma, gain, scale=6):
    """等价 vis_core.glow_layer（1/N 分辨率高斯发光），在 GPU 上完成。

    输入/输出形状与 CPU 版一致（2D 进 2D 出；(H,W,1) 进 (H,W,1) 出；
    (H,W,C) 逐通道）。
    """
    import pyopencl as cl
    import vis_core as V

    h, w = layer.shape[:2]
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
        ops.downsample1(rt, b_src, b_small, w, h, dw, dh)      # 盒式=INTER_AREA
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
    return np.stack([_chan(layer[:, :, k]) for k in range(in_c)], axis=2)


# ── 安装 / 卸载 ──────────────────────────────────────────────────
def install(enable=("gblur", "glow_layer")):
    """接管通用绘图内核；enable 可指定子集（便于逐项验证）。

    enable:
      gblur       大 σ 全帧 GaussianBlur → 降采样等价（收益最大）
      glow_layer  高斯发光 → GPU 降采样高斯
    """
    global _INSTALLED
    if _INSTALLED:
        return False
    import cv2
    import vis_core as V
    _ORIG["gblur"] = cv2.GaussianBlur
    _ORIG["glow_layer"] = V.glow_layer

    if "glow_layer" in enable:
        V.glow_layer = gpu_glow_layer
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
    if "gblur" in _ORIG:
        cv2.GaussianBlur = _ORIG["gblur"]
    _INSTALLED = False
    return True


def stats():
    return {"installed": _INSTALLED, "patched": sorted(_ORIG.keys())}
