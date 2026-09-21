"""题材无关性验证 —— 证明风格包只知道「像素能量」，不知道镜头画的是什么。

用法：
    python -m mvrender.lowres.verify_agnostic

验证项：
  1. 两个风格包都能渲染「题材 A（合成几何）」且输出有效；
  2. 同镜头换风格画面确实不同（风格生效）；
  3. 风格包源码里不含任何题材关键词（楼 / 雨 / 月 / 灯 / 曲名…）；
  4. 换一个完全不同的「题材 B」渲染，仍不报错、输出依然有效；
  5. 风格包注册表可枚举，未知 id 抛 KeyError。
"""
from __future__ import annotations

import math
import os
import re

import numpy as np

from ..core.project import Shot
from .renderer_lowres import LowResRenderer
from .style import names
from .synth import SyntheticCtx, synthetic_shots

# 题材关键词（出现在风格包里即为「风格耦合了题材」，判失败）
_TOPIC_WORDS = [
    "rain", "moon", "star", "lamp", "city", "window", "leaf", "flower",
    "墨不入骨", "三更", "釉下", "旧城", "师士", "雨", "月", "灯", "伞", "楼", "窗",
]


def _ok_frame(u):
    return (isinstance(u, np.ndarray) and u.dtype == np.uint8 and u.ndim == 3
            and u.shape[2] == 3 and u.size > 0 and np.isfinite(u).all()
            and float(u.std()) > 0.0)


def _draw_topic_b(c, tl, shot, ctx, cache):
    """题材 B：与合成几何无关的另一组抽象元素（竖条 + 三角）。"""
    c.fill_gradient((10, 18, 12), (4, 6, 4))
    for k in range(9):
        x = c.w * k / 9.0
        c.fill_rects([(x, 0, x + c.w * 0.02, c.h, 0.6)],
                     color=(40, 200, 120), gain=1.0)
    ph = tl * 1.3
    c.fill_polys([([(c.w * 0.2, c.h * 0.7),
                    (c.w * 0.5, c.h * 0.2 + 12 * math.sin(ph)),
                    (c.w * 0.8, c.h * 0.7)], 1.0)], color=(210, 180, 60))


def _scan_topic_words() -> list[tuple[str, str]]:
    hits = []
    here = os.path.dirname(os.path.abspath(__file__))
    styles_dir = os.path.join(here, "styles")
    for fn in sorted(os.listdir(styles_dir)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(styles_dir, fn), encoding="utf-8") as fh:
            text = fh.read().lower()
        for w in _TOPIC_WORDS:
            wl = w.lower()
            # 词边界，避免 grain 命中 rain
            pat = r"(?<![a-z])" + re.escape(wl) + r"(?![a-z])" if wl.isascii() else re.escape(wl)
            if re.search(pat, text):
                hits.append((fn, w))
    return hits


def main(argv=None):  # noqa: ARG001
    checks: list[tuple[str, bool, str]] = []

    theme_a_ok = True
    diff_ok = True
    frames = {}
    for st in names():
        ctx = SyntheticCtx()
        shots = synthetic_shots(ctx)
        r = LowResRenderer(shots, ctx, style=st, w=960, h=540)
        u = r.render_u8(1.0)
        frames[st] = u
        theme_a_ok = theme_a_ok and _ok_frame(u)
    checks.append(("题材A：两风格均输出有效帧", theme_a_ok, ""))
    if len(frames) >= 2:
        a, b = list(frames.values())[:2]
        diff_ok = a.shape == b.shape and not np.array_equal(a, b)
    checks.append(("换风格画面不同", diff_ok, ""))

    hits = _scan_topic_words()
    checks.append(("风格包源码无题材关键词", not hits, str(hits)))

    theme_b_ok = True
    for st in names():
        ctx = SyntheticCtx()
        shot = Shot(0.0, 4.0, "topicB", _draw_topic_b, cam="drift",
                    tin=0.0, tout=0.0, day=False)
        r = LowResRenderer([shot], ctx, style=st, w=960, h=540)
        theme_b_ok = theme_b_ok and _ok_frame(r.render_u8(1.0))
    checks.append(("题材B：两风格均输出有效帧", theme_b_ok, ""))

    try:
        from .style import get_style
        get_style("__no_such__")
        reg_ok = False
    except KeyError:
        reg_ok = True
    checks.append(("注册表：未知 id 抛 KeyError", reg_ok, f"styles={names()}"))

    print("── 题材无关性验证 ──")
    for label, ok, info in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {info}" if info else ""))
    passed = all(ok for _l, ok, _i in checks)
    print("\n" + ("AGNOSTIC PASS" if passed else "AGNOSTIC FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
