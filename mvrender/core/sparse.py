"""稀疏绘制 API —— 把「全帧 zeros + 局部绘制 + 全帧模糊 + 全帧广播」压成局部运算。

为什么需要
----------
创意代码里最常见的反模式（实测 C2 镜头重复 20+ 次，占该镜 638ms/1075ms）：

    lay = np.zeros((H, W), np.float32)          # 8.3MB 全帧分配
    cv2.rectangle(lay, (x0, y0), (x1, y1), 1.0, -1)
    lay = cv2.GaussianBlur(lay, (0, 0), 2)      # 全帧卷积（只有 2% 有内容）
    c.add(lay[:, :, None] * col * 0.42)         # 全帧广播 → 24.9MB 临时

这类元素在画布上只占几个百分点，却按整幅计算。改成「局部绘制 + 局部模糊 +
仅有效区域合成」后实测 **34x**（内容面积 2.25% 时）。

API 语义
--------
`draw_sparse(canvas, box, draw_fn, blur=0, color=None, gain=1.0, mask_mode=...)`：

    box      : (x0, y0, x1, y1) 内容包围盒（像素，自动钳到画布内）
    draw_fn  : 在**局部子图**上作画，签名 draw_fn(sub, ox, oy) -> None
               （sub 为 (h,w) float32 局部画布，ox/oy 为局部原点在全局的偏移）
    blur     : 高斯 σ；0 表示不模糊。会为子图外扩 3σ 边界，避免边缘截断
    color    : 染色 (b,g,r)；None 则视为已着色图层
    gain     : 全局增益
    mode     : "add" 加色（默认）/ "over" 覆盖 / "mul" 乘

返回实际合成区域 (x0,y0,x1,y1)。
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def pad_box(box, pad, w, h):
    """把包围盒外扩 pad 并钳到画布内。"""
    x0, y0, x1, y1 = box
    x0 = max(0, int(np.floor(x0)) - int(pad))
    y0 = max(0, int(np.floor(y0)) - int(pad))
    x1 = min(w, int(np.ceil(x1)) + int(pad))
    y1 = min(h, int(np.ceil(y1)) + int(pad))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def draw_sparse(canvas, box, draw_fn, blur=0.0, color=None, gain=1.0, mode="add"):
    """局部绘制 + 局部模糊 + 区域合成（语义等价于全帧版本，见模块 docstring）。"""
    if cv2 is None:  # pragma: no cover
        raise RuntimeError("需要 opencv-python")
    H, W = canvas.h, canvas.w
    # 外扩 4σ：高斯在 3σ 处仍有约 0.3% 权重，3σ 截断会让矩形边缘留 ~1/255 偏差
    # （实测 max 1.18）；4σ 后残差降到背景噪声级。
    pad = 0 if blur <= 0 else int(np.ceil(4.0 * float(blur)))
    b = pad_box(box, pad, W, H)
    if b is None:
        return None
    x0, y0, x1, y1 = b
    sub = np.zeros((y1 - y0, x1 - x0), np.float32)
    draw_fn(sub, x0, y0)
    if blur > 0:
        # BORDER_REFLECT_101：仅在子图已贴到画布边缘时才与全帧不同（此时全帧用
        # 同一模式），其余情况子图外围是零，与全帧一致。
        bt = cv2.BORDER_REFLECT_101 if (x0 == 0 or y0 == 0 or x1 == W or y1 == H) \
            else cv2.BORDER_CONSTANT
        sub = cv2.GaussianBlur(sub, (0, 0), float(blur), borderType=bt)
    if color is not None:
        layer = np.ascontiguousarray(sub)[:, :, None] * np.array(color, np.float32).reshape(1, 1, 3)
    else:
        layer = np.ascontiguousarray(sub)
    if gain != 1.0:
        layer = layer * float(gain)
    _compose_region(canvas, layer, x0, y0, mode)
    return b


def _compose_region(canvas, layer, x0, y0, mode):
    """把局部图层合成到画布（CPU 直接切片；GPUCanvas 上传区域后由 kernel 合成）。"""
    if hasattr(canvas, "add_region"):
        canvas.add_region(layer, x0, y0, mode)
        return
    buf = canvas.buf
    h, w = layer.shape[:2]
    if layer.ndim == 2:
        layer = layer[:, :, None]
    dst = buf[y0:y0 + h, x0:x0 + w]
    sub = layer[:dst.shape[0], :dst.shape[1]]
    if mode == "add":
        dst += sub
    elif mode == "mul":
        dst *= sub
    else:                                   # over
        dst *= (1.0 - sub)


# ── 常用形状的便捷封装（覆盖元素层最常见写法）────────────────────
def add_rect_blurred(canvas, box, blur=2.0, color=None, gain=1.0, mode="add", fill=True):
    """矩形（可选模糊）+ 染色 + 合成。替代「zeros+rectangle+Blur+广播」四连。"""
    def _d(sub, ox, oy):
        x0, y0, x1, y1 = box
        a = (int(x0) - ox, int(y0) - oy)
        b = (int(x1) - ox, int(y1) - oy)
        if fill:
            cv2.rectangle(sub, a, b, 1.0, -1)
        else:
            cv2.rectangle(sub, a, b, 1.0, 1)
    return draw_sparse(canvas, box, _d, blur=blur, color=color, gain=gain, mode=mode)


def add_poly_blurred(canvas, pts, blur=2.0, color=None, gain=1.0, mode="add",
                     closed=True, thickness=1, line_aa=True):
    """多边形/折线（可选模糊）+ 染色 + 合成。"""
    pts = np.asarray(pts, np.float32)
    lw = max(1, int(round(thickness)))
    pad = lw                                      # 粗线要外扩线宽，避免被裁
    box = (pts[:, 0].min() - pad, pts[:, 1].min() - pad,
           pts[:, 0].max() + pad, pts[:, 1].max() + pad)
    flags = cv2.LINE_AA if line_aa else 0

    def _d(sub, ox, oy):
        q = np.round(pts - np.array([ox, oy], np.float32)).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(sub, [q], closed, 1.0, lw, flags)
    return draw_sparse(canvas, box, _d, blur=blur, color=color, gain=gain, mode=mode)


def add_circle_blurred(canvas, center, radius, blur=2.0, color=None, gain=1.0,
                       mode="add", fill=True):
    """圆（可选模糊）+ 染色 + 合成。"""
    cx, cy = center
    r = float(radius)
    box = (cx - r, cy - r, cx + r, cy + r)

    def _d(sub, ox, oy):
        cv2.circle(sub, (int(cx) - ox, int(cy) - oy), int(max(1, r)), 1.0,
                   -1 if fill else 1, cv2.LINE_AA)
    return draw_sparse(canvas, box, _d, blur=blur, color=color, gain=gain, mode=mode)
