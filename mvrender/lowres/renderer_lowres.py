"""低分辨率渲染器 —— 实现底座 `Renderer` 的 render 契约。

它不是第四套引擎，而是底座在「低分辨率逻辑画布」档位上的一个实现：

    draw（镜头 fn 在逻辑画布作画）
      → 相机 warp（偏移按放大倍数折算）
      → 镜间转场（逻辑分辨率下合成）
      → 底座 post（暗角 / 脉动 / 段落色调，逐行复用 core.renderer._cpu_post）
      → 风格包 apply（底色为镜头不变量，缓存复用）
      → 整数倍最近邻放大到目标尺寸
      → 歌词（在全分辨率上叠，避免低分辨率字形糊）

镜头代码（`Shot.fn`）**无需改动**：仍按 `ctx.w / ctx.h` 与画布作画。
"""
from __future__ import annotations

import time

import numpy as np

from ..core.camera import camera, warp_cam
from ..core.renderer import _cpu_post
from ..core.transition import apply_transition, trans_mask
from .canvas import LowResCanvas, upscale
from .plan import DEFAULT_PIXEL_BUDGET, Plan
from .plan import plan as make_plan
from .style import StylePack, get_style


class LowResRenderer:
    """低分辨率极速档渲染器。"""

    NIGHT_GLOW = (196, 168, 132)
    DAY_GLOW = (232, 214, 196)

    def __init__(self, shots, ctx, lyrics=None, *, w: int = 1080, h: int = 1920,
                 scale: int | None = None, pixel_budget: int = DEFAULT_PIXEL_BUDGET,
                 style: str | StylePack = "pixel", plan: Plan | None = None,
                 cache=None, min_scale: int = 2, max_scale: int = 8):
        self.shots = list(shots)
        self.ctx = ctx
        self.lyrics = lyrics
        self.plan = plan or make_plan(int(w), int(h), scale=scale,
                                      pixel_budget=pixel_budget,
                                      min_scale=min_scale, max_scale=max_scale)
        self.lw, self.lh, self.scale = self.plan.lw, self.plan.lh, self.plan.scale
        # 逻辑分辨率下与底座 Renderer 同义（camera / transition / post 都用它）
        self.w, self.h = self.lw, self.lh
        self.target_w, self.target_h = self.plan.target_w, self.plan.target_h
        self.out_w, self.out_h = self.plan.out_w, self.plan.out_h
        self.style = get_style(style) if isinstance(style, str) else style
        self.cache = cache if cache is not None else {}
        self._bg_cache: dict = {}
        self.last_tr = None
        # 诊断用：置 True 后 prof 累积各阶段耗时（见 diag_bottleneck.py）
        self.collect_profile = False
        self.prof: dict = {}

    # ── 契约：镜头索引 / 转场时长 ──────────────────────────────────
    def shot_index_at(self, t: float):
        for i, s in enumerate(self.shots):
            if s.t0 <= t < s.t1:
                return i, s
        return len(self.shots) - 1, self.shots[-1]

    def trans_dur(self, i: int) -> float:
        if i <= 0:
            return 0.0
        return max(float(self.shots[i].tin), float(self.shots[i - 1].tout))

    def _tick(self, key: str, d0: float) -> None:
        if self.collect_profile:
            self.prof[key] = self.prof.get(key, 0.0) + (time.perf_counter() - d0)

    # ── 画布 / 不变量底色 ──────────────────────────────────────────
    def make_canvas(self) -> LowResCanvas:
        return LowResCanvas(self.lw, self.lh)

    def background(self, shot) -> np.ndarray | None:
        """镜头内不变的风格底色（按 镜头名 + 逻辑尺寸 缓存）。"""
        key = (getattr(shot, "name", ""), self.lw, self.lh)
        bg = self._bg_cache.get(key)
        if bg is None:
            bg = self.style.background(self.lw, self.lh, self.ctx, shot)
            self._bg_cache[key] = bg
        return bg

    # ── 单镜作画 + 相机 warp（逻辑分辨率）──────────────────────────
    def raw_shot(self, s, t: float) -> np.ndarray:
        c = self.make_canvas()
        tl = t - s.t0
        s.fn(c, tl, s, self.ctx, self.cache)
        img = c.buf
        z, dx, dy, rot = camera(s, tl, t, self.ctx, kind=s.cam,
                                w=self.out_w, h=self.out_h)
        return warp_cam(img, z, dx / self.scale, dy / self.scale, rot,
                        w=self.lw, h=self.lh)

    # ── 单帧（逻辑分辨率，风格化后）────────────────────────────────
    def render_lowres(self, t: float) -> np.ndarray:
        d0 = time.perf_counter()
        i, s = self.shot_index_at(t)
        img = self.raw_shot(s, t)
        self._tick("draw", d0)

        D = self.trans_dur(i)
        if D > 0 and t < s.t0 + D:
            d0 = time.perf_counter()
            prev = self.shots[i - 1]
            old = self.raw_shot(prev, t)
            p = (t - s.t0) / D
            gc = self.DAY_GLOW if s.day else self.NIGHT_GLOW
            m, ring = trans_mask(s.tin_kind, p, seed=(i * 17) % 90,
                                 origin=(0.5, 0.4 + 0.1 * ((i % 3) - 1)),
                                 w=self.lw, h=self.lh)
            img = apply_transition(old, img, m, ring, glow_color=gc)
            self.last_tr = (i, s.tin_kind, float(p))
            self._tick("transition", d0)
        else:
            self.last_tr = None

        d0 = time.perf_counter()
        img = _cpu_post(self, img, t, s.day)
        self._tick("post", d0)

        d0 = time.perf_counter()
        bg = self.background(s)
        self._tick("background", d0)

        d0 = time.perf_counter()
        img = self.style.apply(img, t, self.ctx, s, bg)
        self._tick("style", d0)
        return img

    # ── 契约：render 返回全分辨率 float；render_u8 返回 uint8 ──────
    def render(self, t: float) -> np.ndarray:
        low = self.render_lowres(t)
        d0 = time.perf_counter()
        big = upscale(low, self.scale)
        self._tick("upscale", d0)
        return big[:self.target_h, :self.target_w].astype(np.float32)

    def render_u8(self, t: float) -> np.ndarray:
        i, s = self.shot_index_at(t)
        low = self.render_lowres(t)
        d0 = time.perf_counter()
        big = upscale(np.clip(low, 0, 255).astype(np.uint8), self.scale)   # uint8 放大比 float 快 ~4x
        self._tick("upscale", d0)
        out = big[:self.target_h, :self.target_w]
        if self.lyrics is not None:
            ln = self.ctx.line_at(t)
            out, ov = self.lyrics.render(out, t, self.ctx,
                                         sec=(ln["sec"] if ln else None), day=s.day)
            dark = getattr(self.lyrics, "last_dark", None)
            if dark is not None:
                out = out * (1.0 - dark)[:, :, None]
            out = out + ov
        return np.clip(out, 0, 255).astype(np.uint8)

    def to_uint8(self, img: np.ndarray) -> np.ndarray:
        return np.clip(img, 0, 255).astype(np.uint8)

    # ── 便捷 ───────────────────────────────────────────────────────
    def describe(self) -> str:
        return f"LowResRenderer(style={self.style.id}) {self.plan.describe()}"
