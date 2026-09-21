"""极速档真实基准 —— 逐帧耗时 / 全片估算 / 风格对比。

用法（默认用合成镜头，不依赖外部工程）：
    python -m mvrender.lowres.bench_real
    python -m mvrender.lowres.bench_real --root <曲目工程目录> --frames 12
    python -m mvrender.lowres.bench_real --target 1080 1920 --styles pixel ink --scales 2 4 8

「全片」按 duration×fps 估算（合成默认 12s@30fps=360 帧；给 --root 则用真实时长）。
注意：合成基准测的是**低分辨档本身**的开销，不含真实镜头的绘制复杂度；
真实数字请用 --root 指向工程，或对照 workspace 里已出的极速版实测。
"""
from __future__ import annotations

import sys
import time

import numpy as np


def _time_renderer(r, ts, warm=2):
    for t in ts[:warm]:
        r.render_u8(t)
    t0 = time.perf_counter()
    for t in ts:
        r.render_u8(t)
    return (time.perf_counter() - t0) / len(ts) * 1000.0


def load_real(root):
    """加载真实 mvrender 工程（CPU 路径，不依赖 OpenCL）。失败抛异常。"""
    from ..cli import load_project
    root, scripts, SH = load_project(root)
    import engine as G
    tl, bt = G.load_data()
    w, h = (tl.get("resolution") or [1080, 1920])[:2]
    from ..core.project import Ctx as MvCtx
    from ..core.project import Shot as MvShot
    ctx = MvCtx(tl, bt, w=int(w), h=int(h))
    shots = []
    for d in SH.build_shots(tl):
        if isinstance(d, MvShot):
            shots.append(d)
        elif hasattr(d, "fn") and hasattr(d, "t0"):
            shots.append(MvShot(d.t0, d.t1, d.name, d.fn, cam=d.cam, tin=d.tin,
                                tout=d.tout, tin_kind=d.tin_kind,
                                tout_kind=d.tout_kind, day=d.day,
                                deps=getattr(d, "deps", None)))
        else:
            vals = list(d) + [None] * (11 - len(d))
            t0, t1, name, fn, cam, tin, tout, tik, tok, day = vals[:10]
            shots.append(MvShot(t0, t1, name, fn, cam=cam, tin=tin, tout=tout,
                                tin_kind=tik, tout_kind=tok,
                                day=bool(day) if day is not None else False))
    return ctx, shots, int(tl["fps"]), float(tl["duration"])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = None
    target = (1920, 1080)
    styles = ["pixel", "ink"]
    scales = [2, 4, 8]
    n = 12
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--root":
            root = argv[i + 1]
            i += 2
        elif a == "--target":
            target = (int(argv[i + 1]), int(argv[i + 2]))
            i += 3
        elif a == "--styles":
            styles = argv[i + 1].split(",")
            i += 2
        elif a == "--scales":
            scales = [int(x) for x in argv[i + 1].split(",")]
            i += 2
        elif a == "--frames":
            n = int(argv[i + 1])
            i += 2
        else:
            i += 1

    from .renderer_lowres import LowResRenderer
    from .synth import SyntheticCtx, synthetic_shots

    if root:
        try:
            ctx, shots, fps, duration = load_real(root)
            print(f"[真实工程] {root}  shots={len(shots)} fps={fps} dur={duration:.1f}s")
        except Exception as e:  # noqa: BLE001
            print(f"[真实工程不可用，回退合成] {type(e).__name__}: {e}")
            root = None
    if not root:
        ctx = SyntheticCtx(duration=12.0)
        shots = synthetic_shots(ctx)
        fps, duration = ctx.fps, ctx.duration
        print(f"[合成基准] shots={len(shots)} fps={fps} dur={duration:.1f}s")

    total_frames = int(round(duration * fps))
    ts = np.linspace(0.3, duration - 0.3, n)

    print(f"目标 {target[0]}×{target[1]} · 采样 {n} 帧 · 全片 {total_frames} 帧")
    print(f"{'style':<8}{'scale':>6}{'logic':>12}{'ms/帧':>10}{'全片(s)':>12}{'绘制量':>10}")
    for st in styles:
        for sc in scales:
            r = LowResRenderer(shots, ctx, w=target[0], h=target[1], scale=sc, style=st)
            ms = _time_renderer(r, ts)
            print(f"{st:<8}{sc:>6}{f'{r.lw}x{r.lh}':>12}{ms:>10.2f}"
                  f"{ms * total_frames / 1000:>12.1f}{r.plan.speedup:>9.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
