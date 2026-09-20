"""GPU 算子层：分离高斯 / 仿射 warp / 缩放 / 转场（device 常驻）。

这些算子对应 CPU 侧最大的热点：
  · cv2.GaussianBlur 大核  —— 占全片约 17–24%
  · cv2.warpAffine（相机）  —— 每帧一次全帧重映射
  · torch/复制的全帧 pass   —— 合成

约定：输入/输出均为「扁平 float32 device buffer」，由调用方管理生命周期。
高斯的 pad 用 BORDER_REPLICATE，与 cv2.warpAffine(borderMode=REPLICATE) 语义一致。
"""
from __future__ import annotations

import numpy as np

from .runtime import Runtime


def gauss_blur1(rt: Runtime, b_src, b_dst, sigma, w, h, tmp: str = "gauss_tmp"):
    """单通道分离高斯（REPLICATE 边界）。"""
    kb, rad = rt.gauss_kernel(sigma)
    b_tmp = rt.buf(tmp, w * h * 4)
    rt.run("k_gauss_h", w * h, b_src, b_tmp, kb, w, h, rad)
    rt.run("k_gauss_v", w * h, b_tmp, b_dst, kb, w, h, rad)
    return b_dst


def gauss_blur3(rt: Runtime, b_src, b_dst, sigma, w, h, tmp: str = "gauss_tmp3"):
    """三通道（HxWx3 扁平）分离高斯。"""
    kb, rad = rt.gauss_kernel(sigma)
    b_tmp = rt.buf(tmp, w * h * 3 * 4)
    rt.run("k_gauss3_h", w * h * 3, b_src, b_tmp, kb, w, h, rad)
    rt.run("k_gauss3_v", w * h * 3, b_tmp, b_dst, kb, w, h, rad)
    return b_dst


def invert_affine(M):
    """求 2x3 仿射矩阵的逆（A 为 2x2，t 为平移）。"""
    m = np.asarray(M, np.float32)
    A = m[:, :2]
    t = m[:, 2]
    Ai = np.linalg.inv(A)
    return np.concatenate([Ai, (-Ai @ t).reshape(2, 1)], axis=1).astype(np.float32)


def warp3(rt: Runtime, b_src, b_dst, M, w, h):
    """仿射采样（双线性 + REPLICATE），M 为 2x3，语义与 cv2.warpAffine 一致。

    cv2 的 M 是「源→目标」变换，kernel 需要「目标→源」，故此处先求逆。
    """
    m = invert_affine(M)
    rt.run("k_warp3", w * h * 3, b_src, b_dst,
           float(m[0, 0]), float(m[0, 1]), float(m[0, 2]),
           float(m[1, 0]), float(m[1, 1]), float(m[1, 2]), w, h)
    return b_dst


def resize1(rt: Runtime, b_src, b_dst, sw, sh, dw, dh):
    rt.run("k_resize1", dw * dh, b_src, b_dst, sw, sh, dw, dh)
    return b_dst


def resize3(rt: Runtime, b_src, b_dst, sw, sh, dw, dh):
    rt.run("k_resize3", dw * dh * 3, b_src, b_dst, sw, sh, dw, dh)
    return b_dst


def downsample1(rt: Runtime, b_src, b_dst, sw, sh, dw, dh):
    """盒式区域平均降采样（等价 cv2.INTER_AREA）。"""
    rt.run("k_downsample1", dw * dh, b_src, b_dst, sw, sh, dw, dh)
    return b_dst


def gauss_blur1_ds(rt: Runtime, b_src, b_dst, sigma, w, h, n=None):
    """降采样大核高斯（σ≥16 视觉等价，快数倍；σ<16 走原分辨率）。

    【为什么必须】元素层有 σ=max(8, w*0.35) 这类**数百**的大核（手机发光），
    全分辨率高斯核长 3σ 上千，逐像素采样会卡死（实测 σ=594 全帧 5.4s）。
    降采样后误差 < 0.5/255（实测 σ=220 → 8bit 1.8；σ=594 → 2.1）。
    """
    # 【踩坑】显式给了 n 时必须**强制**降采样：glow_layer(scale=6) 对 σ=10 也走
    # 1/6 分辨率；若此处按 σ<16 退回全分辨率，峰值会明显偏高（实测 mean 差 3.9x）。
    if n is None and (sigma is None or sigma < 16):
        return gauss_blur1(rt, b_src, b_dst, sigma or 0.0, w, h)
    if n is None:
        n = 4 if sigma < 48 else 8
    dw, dh = max(1, w // n), max(1, h // n)
    b_small = rt.buf("ds_small", dw * dh * 4)
    downsample1(rt, b_src, b_small, w, h, dw, dh)
    gauss_blur1(rt, b_small, b_small, max(0.5, float(sigma) / n), dw, dh, tmp="ds_tmp")
    resize1(rt, b_small, b_dst, dw, dh, w, h)
    return b_dst


def post_tonemap(rt: Runtime, b_img, b_vig, b_lut, b_out, gain, flash,
                 cmul=(1.05, 1.0, 1.0)):
    """Renderer.post + to_uint8 融合（输出 uint8）。cmul=(b,g,r) 通道乘子。"""
    b, g, r = cmul
    rt.run("k_post_tonemap", rt.n3, b_img, b_vig, b_lut, b_out,
           np.float32(gain), np.float32(flash),
           np.float32(b), np.float32(g), np.float32(r))
    return b_out


# ── CPU 参照实现（一致性校验用）──────────────────────────────────────
def cpu_gauss1(layer, sigma):
    import cv2
    return cv2.GaussianBlur(layer, (0, 0), float(sigma),
                            borderType=cv2.BORDER_REPLICATE)


def cpu_warp3(img, M):
    import cv2
    return cv2.warpAffine(img, np.asarray(M, np.float32), (img.shape[1], img.shape[0]),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
