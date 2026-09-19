"""GPU 算子一致性自检：逐算子与 CPU 参照实现对比。

用法：
    python -m mvrender.gpu.selftest            # 全部算子
    python -m mvrender.gpu.selftest bench      # 附带性能对比
"""
from __future__ import annotations

import sys
import time

import numpy as np

from . import ops
from .canvas_gpu import GPUCanvas
from .runtime import Runtime

W, H = 1080, 1920


def _mk_lut():
    i = np.arange(256, dtype=np.float32) / 255.0
    x = i ** 0.92
    x = x * x * (3 - 2 * x) * 0.44 + x * 0.56
    x = x / (1.0 + 0.10 * x)
    x = x / (x.max() + 1e-9)
    return np.clip(x * 255.0, 0, 255).astype(np.uint8)


def test_canvas_ops(rt, rng):
    base = (rng.random((H, W, 3), np.float32) * 120).astype(np.float32)
    lay = (rng.random((H, W, 3), np.float32) * 90).astype(np.float32)
    mask = rng.random((H, W), np.float32)
    ok = True

    c = GPUCanvas(W, H, rt=rt)
    c.buf = base.copy()
    c.add(lay)
    d = np.abs(c.buf - (base + lay)).max()
    print(f"  [add]        max|Δ|={d:.6f}")
    ok &= d < 1e-3

    c.buf = base.copy()
    c.add(lay, mask)
    d = np.abs(c.buf - (base + lay * mask[:, :, None])).max()
    print(f"  [add_mask]   max|Δ|={d:.6f}")
    ok &= d < 1e-3

    a = 0.42
    c.buf = base.copy()
    c.blend(lay, a)
    d = np.abs(c.buf - (base * (1 - a) + lay * a)).max()
    print(f"  [blend_scal] max|Δ|={d:.6f}")
    ok &= d < 1e-3

    c.buf = base.copy()
    c.blend(lay, mask)
    ref = base * (1 - mask[:, :, None]) + lay * mask[:, :, None]
    d = np.abs(c.buf - ref).max()
    print(f"  [blend_mask] max|Δ|={d:.6f}")
    ok &= d < 1e-3

    c.buf = base.copy()
    c.mul(mask)
    d = np.abs(c.buf - base * mask[:, :, None]).max()
    print(f"  [mul_mask]   max|Δ|={d:.6f}")
    ok &= d < 1e-3
    return ok


def test_canvas_out(rt, rng):
    base = (rng.random((H, W, 3), np.float32) * 200).astype(np.float32)
    c = GPUCanvas(W, H, rt=rt)
    c.buf = base.copy()
    got = c.out(1.0).astype(np.int16)

    def cpu_out(buf, gain=1.0):
        x = np.clip(buf * gain, 0, 255.0)
        x = 255.0 * (x / 255.0) / (1.0 + 0.25 * (x / 255.0)) * 1.25
        return np.clip(x, 0, 255).astype(np.uint8)

    ref = cpu_out(base, 1.0).astype(np.int16)
    d = np.abs(got - ref)
    print(f"  [canvas.out] max|Δ|={d.max()}  |>1| {100 * (d > 1).mean():.4f}%")
    return d.max() <= 2


def test_glow(rt, rng):
    """径向光晕：GPU 掩膜 vs CPU radial_glow 公式。"""
    cx, cy, r, power, feather, edge = 0.42, 0.36, 0.72, 2.2, 0.42, 0.34
    color = (52, 82, 112)
    gain = 1.0

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx / W - cx) * (W / min(W, H))) ** 2 + ((yy / H - cy) * (H / min(W, H))) ** 2)
    rr = max(1e-4, r)
    m = 1.0 / (1.0 + (d / rr) ** power * (1.0 / max(1e-3, feather)))
    m /= (m.max() + 1e-9)
    if edge is not None:
        e0 = max(1e-3, float(edge))
        f = np.clip(1.0 - (d - e0) / (e0 * 0.45), 0.0, 1.0)
        m = m * (f * f * (3.0 - 2.0 * f))
    ref = m[:, :, None] * np.array(color, np.float32).reshape(1, 1, 3) * gain

    c = GPUCanvas(W, H, rt=rt)
    c.buf = np.zeros((H, W, 3), np.float32)
    c.glow_add(cx, cy, r, color, gain, edge=edge)
    from pyopencl import enqueue_copy  # noqa
    got = c.buf
    dd = np.abs(got - ref)
    rel = dd.max() / max(1e-9, ref.max())
    print(f"  [radial_glow] max|Δ|={dd.max():.4f}  ref_max={ref.max():.1f}  rel={rel:.4%}")
    return rel < 0.01


def test_gauss(rt, rng):
    ok = True
    layer = np.zeros((H, W), np.float32)
    for _ in range(60):
        x, y = rng.integers(0, W), rng.integers(0, H)
        import cv2
        cv2.circle(layer, (int(x), int(y)), int(rng.integers(2, 12)),
                   float(rng.random()), -1, cv2.LINE_AA)
    for sigma in (4.0, 9.0, 22.0, 40.0):
        ref = ops.cpu_gauss1(layer, sigma)
        b_src = rt.up(layer.reshape(-1), "g_src")
        b_dst = rt.buf("g_dst", W * H * 4)
        ops.gauss_blur1(rt, b_src, b_dst, sigma, W, H)
        got = rt.down("g_dst", (H, W), np.float32)
        d = np.abs(ref - got)
        worst8 = d.max() * 255
        print(f"  [gauss1 σ={sigma:>4}] max|Δ|={d.max():.5f}  (8bit {worst8:.1f})  mean={d.mean() * 255:.3f}")
        ok &= worst8 <= 6.0
    return ok


def test_warp(rt, rng):
    import cv2
    img = (rng.random((H, W, 3), np.float32) * 200).astype(np.float32)
    M = cv2.getRotationMatrix2D((W / 2, H / 2), 0.35, 1.09)
    M[0, 2] += 12.0
    M[1, 2] -= 7.0
    ref = ops.cpu_warp3(img, M)
    b_src = rt.up(img.reshape(-1), "w_src")
    b_dst = rt.buf("w_dst", W * H * 3 * 4)
    ops.warp3(rt, b_src, b_dst, M, W, H)
    got = rt.down("w_dst", (H, W, 3), np.float32)
    d = np.abs(ref - got)
    print(f"  [warp3] max|Δ|={d.max():.5f}  (8bit {d.max() * 255:.1f})  mean={d.mean() * 255:.3f}")
    return d.max() * 255 <= 8.0


def bench(rt, rounds=30):
    rng = np.random.default_rng(3)
    img = (rng.random((H, W, 3), np.float32) * 200).astype(np.float32)
    lay = img.copy()
    print("\n── 性能对比 ──")

    c = GPUCanvas(W, H, rt=rt)
    c.buf = img.copy()
    c.add(lay)
    rt.finish()
    t0 = time.time()
    for _ in range(rounds):
        c.add(lay)
    rt.finish()
    t_gpu = (time.time() - t0) / rounds * 1000

    t0 = time.time()
    for _ in range(rounds):
        _ = img + lay
    t_cpu = (time.time() - t0) / rounds * 1000
    print(f"  add           CPU {t_cpu:8.3f}ms   GPU {t_gpu:8.3f}ms   ← {t_cpu / t_gpu:5.1f}x")

    layer1 = np.zeros((H, W), np.float32)
    import cv2
    for _ in range(60):
        cv2.circle(layer1, (int(rng.integers(0, W)), int(rng.integers(0, H))),
                   6, float(rng.random()), -1)
    b_src = rt.up(layer1.reshape(-1), "bg_src")
    b_dst = rt.buf("bg_dst", W * H * 4)
    ops.gauss_blur1(rt, b_src, b_dst, 22.0, W, H)
    rt.finish()
    t0 = time.time()
    for _ in range(rounds):
        ops.gauss_blur1(rt, b_src, b_dst, 22.0, W, H)
    rt.finish()
    t_gb = (time.time() - t0) / rounds * 1000
    t0 = time.time()
    for _ in range(5):
        ops.cpu_gauss1(layer1, 22.0)
    t_cb = (time.time() - t0) / 5 * 1000
    print(f"  gauss σ=22    CPU {t_cb:8.3f}ms   GPU {t_gb:8.3f}ms   ← {t_cb / t_gb:5.1f}x")

    b_img = rt.up(img.reshape(-1), "bw_src")
    M = cv2.getRotationMatrix2D((W / 2, H / 2), 0.35, 1.09)
    b_dst3 = rt.buf("bw_dst", W * H * 3 * 4)
    ops.warp3(rt, b_img, b_dst3, M, W, H)
    rt.finish()
    t0 = time.time()
    for _ in range(rounds):
        ops.warp3(rt, b_img, b_dst3, M, W, H)
    rt.finish()
    t_gw = (time.time() - t0) / rounds * 1000
    t0 = time.time()
    for _ in range(10):
        ops.cpu_warp3(img, M)
    t_cw = (time.time() - t0) / 10 * 1000
    print(f"  warp3         CPU {t_cw:8.3f}ms   GPU {t_gw:8.3f}ms   ← {t_cw / t_gw:5.1f}x")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    Runtime.probe()
    try:
        rt = Runtime.get(w=W, h=H, verbose=True)
    except Exception as e:
        print("GPU 不可用：", e)
        return 1
    rng = np.random.default_rng(7)
    print("\n── 一致性自检 ──")
    results = {
        "canvas ops": test_canvas_ops(rt, rng),
        "canvas.out": test_canvas_out(rt, rng),
        "radial_glow": test_glow(rt, rng),
        "gauss": test_gauss(rt, rng),
        "warp": test_warp(rt, rng),
    }
    ok = all(results.values())
    print("\n" + ("SELFTEST PASS —— 全部算子与 CPU 参照一致" if ok
                  else "SELFTEST FAIL —— " + ", ".join(k for k, v in results.items() if not v)))
    if "bench" in argv:
        bench(rt)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
