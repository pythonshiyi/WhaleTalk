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
        a = np.deg2rad(rot)
        M = np.array([[zoom * np.cos(a), zoom * np.sin(a), 0.0],
                      [-zoom * np.sin(a), zoom * np.cos(a), 0.0]], np.float32)
    M = np.asarray(M, np.float32).copy()
    M[0, 2] += dx
    M[1, 2] += dy
    return M


def warp_cam(img, zoom, dx, dy, rot=0.0, w=1080, h=1920):
    """CPU 仿射重映射（BORDER_REPLICATE），与旧 engine.warp_cam 一致。"""
    if cv2 is None:  # pragma: no cover
        raise RuntimeError("需要 opencv-python")
    M = affine_matrix(zoom, dx, dy, rot, w, h)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)
