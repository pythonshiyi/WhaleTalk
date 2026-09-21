"""瓶颈诊断 —— 把低分辨档的单帧耗时拆到各阶段，验证「缓存不变量」。

用法：
    python -m mvrender.lowres.diag_bottleneck
    python -m mvrender.lowres.diag_bottleneck --frames 200

结论预期（实测量级）：
  · 首帧（底色缓存 miss）里 ink 的宣纸底会明显偏重；
  · 之后同镜帧（缓存 hit）背景阶段几乎为 0；
  · draw / style 才是稳态耗时主体。
若背景阶段在稳态仍占大头，说明「镜头内不变量」没被缓存住 —— 就是漏了缓存。
"""
from __future__ import annotations

import sys

import numpy as np

from .renderer_lowres import LowResRenderer
from .synth import SyntheticCtx, synthetic_shots


def _profile(r, ts):
    r.collect_profile = True
    tot = {k: 0.0 for k in ("draw", "transition", "post", "background", "style", "upscale")}
    for t in ts:
        before = dict(r.prof)
        r.render_u8(t)
        for k in tot:
            tot[k] += r.prof.get(k, 0.0) - before.get(k, 0.0)
    return tot


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    style = "ink"
    n = 200
    i = 0
    while i < len(argv):
        if argv[i] == "--frames":
            n = int(argv[i + 1])
            i += 2
        elif argv[i] == "--style":
            style = argv[i + 1]
            i += 2
        else:
            i += 1

    ctx = SyntheticCtx(duration=12.0)
    shots = synthetic_shots(ctx)
    r = LowResRenderer(shots, ctx, w=1920, h=1080, style=style)
    print(r.describe())

    # 首帧：底色缓存 miss
    r.prof.clear()
    r.collect_profile = True
    r.render_u8(0.5)
    miss = dict(r.prof)
    print("\n── 首帧（缓存 miss）──")
    msum = sum(miss.values()) or 1.0
    for k, v in sorted(miss.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<12}{v * 1000:8.2f} ms{100 * v / msum:8.1f}%")

    # 稳态：同镜若干帧（缓存 hit）
    r.prof.clear()
    ts = np.linspace(0.35, 3.6, n)
    tot = _profile(r, ts)
    print(f"\n── 稳态（同镜 {n} 帧，缓存 hit）──")
    tsum = sum(tot.values()) or 1.0
    for k, v in sorted(tot.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<12}{v / n * 1000:8.3f} ms/帧{100 * v / tsum:8.1f}%")

    bg_ms = tot["background"] / n * 1000
    print(f"\n背景（不变量）稳态 {bg_ms:.3f} ms/帧；缓存条目 {len(r._bg_cache)}")
    print("OK：不变量已缓存" if bg_ms < 0.2 else "警告：背景仍在重算，检查缓存键")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
