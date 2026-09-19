"""FastMV 端到端自检：GPU vs CPU 逐像素一致性 + 性能。

用法：
    python -m mvrender.selftest <曲目工程根目录> [帧数]
"""
from __future__ import annotations

import sys
import time

import numpy as np


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    root = argv[0]
    n = int(argv[1]) if len(argv) > 1 else 12

    from .cli import build_renderers
    tl, ctx, shots, cpu, gpu = build_renderers(root)
    dur = float(tl["duration"])
    ts = np.linspace(2.0, dur - 2.0, n)

    print(f"── 一致性（{n} 帧均匀采样）──")
    maes, worst = [], 0
    for t in ts:
        a = cpu.to_uint8(cpu.render(float(t))).astype(np.int16)
        b = gpu.render_u8(float(t)).astype(np.int16)
        d = np.abs(a - b)
        maes.append(d.mean())
        worst = max(worst, int(d.max()))
    print(f"  MAE 均值 {np.mean(maes):.5f}  最大 {np.max(maes):.5f}  "
          f"最差 max|Δ| {worst}")
    ok = np.mean(maes) < 0.05

    print(f"── 性能（{n} 帧）──")
    gpu.render_u8(ts[0])
    t0 = time.time()
    for t in ts:
        gpu.render_u8(float(t))
    g = (time.time() - t0) / len(ts) * 1000
    t0 = time.time()
    for t in ts:
        cpu.to_uint8(cpu.render(float(t)))
    c = (time.time() - t0) / len(ts) * 1000
    print(f"  CPU {c:8.1f} ms/帧 · GPU {g:8.1f} ms/帧 · 加速 {c / g:.2f}x")

    print("\n" + ("SELFTEST PASS" if ok else "SELFTEST FAIL（一致性超阈值）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
