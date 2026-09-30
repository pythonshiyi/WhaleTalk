"""相机运动：每种镜头都有真实运动（zoom/dx/dy/rot）与仿射重映射。

与旧 engine.camera/warp_cam 数值一致，但 warp 支持 GPU（在 GpuRenderer 中）。
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def camera(shot, tl, t, ctx, kind="float", amp=1.0, w=1080, h=1920):
    """返回 (zoom, dx, dy, rot)。与旧 engine.camera 逐行等价。"""
    u = float(np.clip(tl / max(shot.dur, 1e-6), 0, 1))
    p = ctx.pulse(t)
    e = ctx.energy(t)
    zoom = 1.06
    dx = dy = rot = 0.0
    if kind == "push":
        zoom = 1.05 + 0.10 * u + 0.012 * p
    elif kind == "pull":
        zoom = 1.18 - 0.11 * u + 0.012 * p
    elif kind == "drift":
        zoom = 1.09 + 0.03 * u
        dx = (-1 + 2 * u) * 22 * amp + 3 * np.sin(t * 0.7) + 5 * p
        dy = 8 * np.sin(t * 0.45 + 1.1)
    elif kind == "float":
        zoom = 1.08 + 0.02 * np.sin(t * 0.31) + 0.014 * p
        dx = 9 * np.sin(t * 0.52) + 2.5 * np.sin(t * 2.1) + 6 * p
        dy = 7 * np.cos(t * 0.41 + 0.7) + 2.0 * np.cos(t * 1.7)
        rot = 0.25 * np.sin(t * 0.33)
    elif kind == "beat":
        zoom = 1.07 + 0.055 * u + 0.045 * p * amp
        dx = 4 * np.sin(t * 0.9)
        dy = -3 * p * amp
    elif kind == "slow":
        zoom = 1.04 + 0.03 * u + 0.006 * p
        dx = 4 * np.sin(t * 0.23)
        dy = 5 * np.cos(t * 0.19)
    elif kind == "hand":
        zoom = 1.12 + 0.02 * u
        dx = 12 * np.sin(t * 0.61) + 3 * np.sin(t * 2.7) + 8 * p
        dy = 9 * np.cos(t * 0.53) + 3 * np.cos(t * 3.1) + 5 * p
        rot = 0.5 * np.sin(t * 0.37)
    elif kind == "still":
        zoom = 1.05 + 0.02 * p
    elif kind == "rise":
        zoom = 1.06 + 0.05 * u
        dy = 26 * u + 6 * p
    elif kind == "sink":
        zoom = 1.10 - 0.04 * u
        dy = -22 * u - 4 * p
    zoom += 0.006 * e
    zoom *= (1.0 + 0.010 * p * amp)
    return zoom, dx, dy, rot


def affine_matrix(zoom, dx, dy, rot, w, h):
    """构造 cv2 风格的 2x3 仿射矩阵（源→目标语义）。"""
    if cv2 is not None:
        M = cv2.getRotationMatrix2D((w / 2, h / 2), rot, zoom)
    else:  # pragma: no cover
        # 与 cv2.getRotationMatrix2D 逐位等价（见 _get_rotation_matrix_2d）
        M = _get_rotation_matrix_2d((w / 2, h / 2), rot, zoom)
    M = np.asarray(M, np.float32).copy()
    M[0, 2] += dx
    M[1, 2] += dy
    return M


def _get_rotation_matrix_2d(center, angle_deg, scale):
    """`cv2.getRotationMatrix2D` 的 numpy 等价实现（无 cv2 时使用）。

    cv2 的语义：alpha = scale*cos(angle), beta = scale*sin(angle)，
        M = [[alpha, beta, (1-alpha)*cx - beta*cy],
             [-beta, alpha, beta*cx + (1-alpha)*cy]]
    """
    cx, cy = float(center[0]), float(center[1])
    a = np.deg2rad(float(angle_deg))
    alpha = float(scale) * np.cos(a)
    beta = float(scale) * np.sin(a)
    return np.array([[alpha, beta, (1 - alpha) * cx - beta * cy],
                     [-beta, alpha, beta * cx + (1 - alpha) * cy]], np.float32)


def _warp_affine_linear(img, M, w, h):
    """`cv2.warpAffine(INTER_LINEAR, BORDER_REPLICATE)` 的 numpy 等价实现。

    逆映射 + 双线性采样；越界坐标按 BORDER_REPLICATE 钳到边缘。
    坐标约定与 cv2 一致：像素中心在整数坐标处。
    """
    src = np.asarray(img, np.float32)
    sh, sw = src.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # 逆矩阵：目标 → 源
    Minv = np.linalg.inv(np.vstack([M, [0.0, 0.0, 1.0]]).astype(np.float64))[:2]
    sx = Minv[0, 0] * xx + Minv[0, 1] * yy + Minv[0, 2]
    sy = Minv[1, 0] * xx + Minv[1, 1] * yy + Minv[1, 2]
    # BORDER_REPLICATE：钳到 [0, dim-1]
    sx = np.clip(sx, 0.0, sw - 1.0)
    sy = np.clip(sy, 0.0, sh - 1.0)
    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    x1 = np.minimum(x0 + 1, sw - 1)
    y1 = np.minimum(y0 + 1, sh - 1)
    fx = (sx - x0)[..., None] if src.ndim == 3 else (sx - x0)
    fy = (sy - y0)[..., None] if src.ndim == 3 else (sy - y0)
    if src.ndim == 3:
        fx = fx.astype(np.float32)
        fy = fy.astype(np.float32)
    top = src[y0, x0] * (1 - fx) + src[y0, x1] * fx
    bot = src[y1, x0] * (1 - fx) + src[y1, x1] * fx
    out = top * (1 - fy) + bot * fy
    return np.ascontiguousarray(out, src.dtype)


def warp_cam(img, zoom, dx, dy, rot=0.0, w=1080, h=1920):
    """CPU 仿射重映射（BORDER_REPLICATE），与旧 engine.warp_cam 一致。

    无 cv2 时回退 `_warp_affine_linear`（纯 numpy 双线性 + REPLICATE 边界），
    保证低分辨率档在最小安装 / CI 环境下仍可渲染。
    """
    M = affine_matrix(zoom, dx, dy, rot, w, h)
    if cv2 is None:
        return _warp_affine_linear(img, M, w, h)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)
