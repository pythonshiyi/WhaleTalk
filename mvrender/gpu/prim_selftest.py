"""底座绘制原语 vs cv2 一致性门禁。

这是**底座质量的核心保证**：项目作者用这些原语画元素，必须与 cv2 等价，
否则换项目时画面会跑偏。跑法：

    python -m mvrender.gpu.prim_selftest
"""
from __future__ import annotations

import sys

import cv2
import numpy as np

from .canvas_gpu import GPUCanvas
from .runtime import Runtime

W, H = 1080, 1920


def _cov_ratio(ref, got):
    return got.sum() / max(1e-9, float(ref.sum()))


def main(argv=None):
    Runtime.probe()
    rt = Runtime.get(w=W, h=H, verbose=True)
    # 原语名, 参考(cv2)图层, GPU 图层, 覆盖率容差
    cases = []

    # 线段
    lines = [(200, 300, 200, 900, 8, 1.0), (300, 500, 900, 520, 3, 1.0),
             (400, 200, 800, 1000, 20, 1.0)]
    ref = np.zeros((H, W), np.float32)
    for (x0, y0, x1, y1, th, _a) in lines:
        cv2.line(ref, (x0, y0), (x1, y1), 1.0, th, cv2.LINE_AA)
    c = GPUCanvas(W, H, rt=rt)
    c.draw_lines(lines, color=(1, 1, 1), gain=1.0)
    cases.append(("draw_lines", ref, c.rt.down("elems_layer", (H, W), np.float32), 0.12))

    # 矩形（填充）
    rects = [(100, 200, 500, 700, 1.0), (600, 300, 900, 500, 0.5)]
    ref = np.zeros((H, W), np.float32)
    for (x0, y0, x1, y1, a) in rects:
        cv2.rectangle(ref, (x0, y0), (x1, y1), float(a), -1)
    c = GPUCanvas(W, H, rt=rt)
    c.fill_rects(rects, color=(1, 1, 1), gain=1.0)
    cases.append(("fill_rects", ref, c.rt.down("elems_layer", (H, W), np.float32), 0.01))

    # 圆（填充）
    circ = [(400, 600, 80, 1.0), (800, 400, 40, 0.7)]
    ref = np.zeros((H, W), np.float32)
    for (cx, cy, r, a) in circ:
        cv2.circle(ref, (int(cx), int(cy)), int(r), float(a), -1, cv2.LINE_AA)
    c = GPUCanvas(W, H, rt=rt)
    c.fill_circles(circ, color=(1, 1, 1), gain=1.0)
    cases.append(("fill_circles", ref, c.rt.down("elems_layer", (H, W), np.float32), 0.03))

    # 多边形（填充）
    pts = [(200, 200), (600, 250), (580, 700), (220, 680)]
    ref = np.zeros((H, W), np.float32)
    cv2.fillPoly(ref, [np.array(pts, np.int32)], 1.0)
    c = GPUCanvas(W, H, rt=rt)
    c.rasterize(polys=[(pts, 1.0)], color=(1, 1, 1), gain=1.0)
    cases.append(("fill_polys", ref, c.rt.down("elems_layer", (H, W), np.float32), 0.03))

    # 圆角矩形
    rr = [(200, 300, 700, 900, 60, 1.0)]
    ref = np.zeros((H, W), np.float32)
    x0, y0, x1, y1, rad, _a = rr[0]
    cv2.rectangle(ref, (x0 + rad, y0), (x1 - rad, y1), 1.0, -1)
    cv2.rectangle(ref, (x0, y0 + rad), (x1, y1 - rad), 1.0, -1)
    for (cx, cy) in ((x0 + rad, y0 + rad), (x1 - rad, y0 + rad),
                     (x0 + rad, y1 - rad), (x1 - rad, y1 - rad)):
        cv2.circle(ref, (cx, cy), rad, 1.0, -1)
    c = GPUCanvas(W, H, rt=rt)
    c.rasterize(round_rects=rr, color=(1, 1, 1), gain=1.0)
    cases.append(("round_rects", ref, c.rt.down("elems_layer", (H, W), np.float32), 0.03))

    ok = True
    print("\n── 绘制原语一致性（GPU vs cv2）──")
    for name, ref, got, tol in cases:
        d = np.abs(ref - got)
        r = _cov_ratio(ref, got)
        bad = abs(r - 1.0) > tol
        ok = ok and not bad
        print(f"  {'OK ' if not bad else 'BAD'} {name:14s} max {float(d.max()):6.3f} "
              f"mean {float(d.mean()):.6f}  覆盖率 {r:.3f}")
    print("\n" + ("PRIM SELFTEST PASS" if ok else "PRIM SELFTEST FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
