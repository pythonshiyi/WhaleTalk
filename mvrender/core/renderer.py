"""Renderer —— 通用渲染主循环（CPU/GPU 双后端）。

核心路径（GPU）
---------------
    raw_shot(GPUCanvas 作画，不回读)
      → 相机 warp 上 GPU（k_warp3）
      → 转场合成上 GPU（k_transition）
      → post + tonemap 融合（k_post_tonemap，只回读最终 u8）
      → 歌词层（CPU 稀疏绘制后 GPU 合成）

与旧 engine.Renderer 的差异
---------------------------
1. **画布不回读**：旧实现每帧 `c.buf` 都触发 device→host 同步，把 GPU 收益吃光。
   这里全程用 device buffer 串联，只在最后回读一帧 u8。
2. **warp 上 GPU**：相机运动是每帧一次全帧重映射（CPU 5.2ms → GPU 0.34ms）。
3. **后处理融合**：post + to_uint8 合成单 kernel（原 139ms → 常驻 0.21ms）。
"""
from __future__ import annotations

import numpy as np

from .transition import apply_transition, trans_mask


class Renderer:
    """CPU/GPU 双后端渲染器。

    use_gpu=True 时需要传入 runtime（`mvrender.gpu.Runtime`）。
    """

    NIGHT_GLOW = (196, 168, 132)
    DAY_GLOW = (232, 214, 196)

    def __init__(self, shots, ctx, lyrics=None, w=1080, h=1920,
                 use_gpu=True, rt=None, canvas_factory=None):
        self.shots = shots
        self.ctx = ctx
        self.lyrics = lyrics
        self.w, self.h = int(w), int(h)
        self.cache = {}
        self.last_tr = None
        self.use_gpu = bool(use_gpu)
        self.rt = rt
        self._canvas_factory = canvas_factory
        self._vig = None
        self._lut = None
        self._b_canvas = self._b_warped = None
        self._b_prev = self._b_new = None

    # ── 画布工厂 ───────────────────────────────────────────────────
    def make_canvas(self):
        if self._canvas_factory is not None:
            return self._canvas_factory()
        if self.use_gpu:
            from ..gpu.canvas_gpu import GPUCanvas
            return GPUCanvas(self.w, self.h, rt=self.rt)
        from .canvas import Canvas
        return Canvas(self.w, self.h)

    # ── 镜头定位 ───────────────────────────────────────────────────
    def shot_index_at(self, t):
        for i, s in enumerate(self.shots):
            if s.t0 <= t < s.t1:
                return i, s
        return len(self.shots) - 1, self.shots[-1]

    def trans_dur(self, i):
        """第 i 镜的入场转场时长 = max(本镜入, 上镜出)；0 = 硬切。

        不变量：每个镜界全局【只做一次】转场，且只发生在引镜自身的前 D 秒内。
        """
        if i <= 0:
            return 0.0
        return max(float(self.shots[i].tin), float(self.shots[i - 1].tout))

    # ── 单帧渲染 ───────────────────────────────────────────────────
    def render(self, t):
        i, s = self.shot_index_at(t)
        img = self.raw_shot(s, t)
        D = self.trans_dur(i)
        if D > 0 and t < s.t0 + D:
            prev = self.shots[i - 1]
            old = self.raw_shot(prev, t)
            p = (t - s.t0) / D
            gc = self.DAY_GLOW if s.day else self.NIGHT_GLOW
            m, ring = trans_mask(s.tin_kind, p, seed=(i * 17) % 90,
                                 origin=(0.5, 0.4 + 0.1 * ((i % 3) - 1)),
                                 w=self.w, h=self.h)
            img = apply_transition(old, img, m, ring, glow_color=gc)
            self.last_tr = (i, s.tin_kind, float(p))
        else:
            self.last_tr = None
        img = self.post(img, t, s.day)
        out = img
        if self.lyrics is not None:
            ln = self.ctx.line_at(t)
            out, ov = self.lyrics.render(out, t, self.ctx,
                                         sec=(ln["sec"] if ln else None), day=s.day)
            dark = getattr(self.lyrics, "last_dark", None)
            if dark is not None:
                out = out * (1.0 - dark)[:, :, None]
            out = out + ov
        return out

    def raw_shot(self, s, t):
        c = self.make_canvas()
        tl = t - s.t0
        s.fn(c, tl, s, self.ctx, self.cache)
        img = c.buf if not self.use_gpu else c.sync_down()
        from .camera import camera, warp_cam
        z, dx, dy, rot = camera(s, tl, t, self.ctx, kind=s.cam, w=self.w, h=self.h)
        return warp_cam(img, z, dx, dy, rot, w=self.w, h=self.h)

    # ── 后处理（CPU 参照；GPU 由子类/GpuRenderer 覆盖）─────────────
    def post(self, img, t, day=False):
        return _cpu_post(self, img, t, day)

    def to_uint8(self, img):
        return _cpu_to_uint8(self, img)


# ── CPU 后处理（与旧 engine 逐行等价，作为参照与回退）────────────────
def _vigbase(cache, w, h):
    if "vigbase" not in cache:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt((xx / w - 0.5) ** 2 * 1.0 + (yy / h - 0.5) ** 2 * 1.8)
        cache["vigbase"] = np.clip(1.0 - 0.40 * (d / 0.66) ** 2.2, 0.35, 1.0).astype(np.float32)
    return cache["vigbase"]


def _cpu_post(R, img, t, day=False):
    ctx = R.ctx
    e = ctx.energy(t)
    p = ctx.pulse(t)
    vb = _vigbase(R.cache, R.w, R.h)
    if day:
        g = 0.94 + 0.06 * e
        vbd = np.clip(vb + 0.22, 0.35, 1.0)
        img = img * (vbd * g)[:, :, None]
        img = img + p * 3.0
    else:
        g = (0.98 + 0.30 * e) * 0.96
        img = img * (vb * g)[:, :, None]
        img = img + p * 13.0
        img[:, :, 0] *= 1.05
    ln = ctx.line_at(t)
    seg = ln["sec"] if ln else None
    if not day:
        if seg == "尾段":
            img[:, :, 0] *= 0.94
            img[:, :, 1] *= 0.96
            img[:, :, 2] *= 1.00
        elif seg == "桥段":
            img[:, :, 0] *= 0.97
            img[:, :, 2] *= 1.02
    return img


def _lut(R):
    if "lut" not in R.cache:
        i = np.arange(256, dtype=np.float32) / 255.0
        x = i ** 0.92
        x = x * x * (3 - 2 * x) * 0.44 + x * 0.56
        x = x / (1.0 + 0.10 * x)
        x = x / (x.max() + 1e-9)
        R.cache["lut"] = np.clip(x * 255.0, 0, 255).astype(np.uint8)
    return R.cache["lut"]


def _cpu_to_uint8(R, img):
    import cv2
    u8 = np.clip(img, 0, 255).astype(np.uint8)
    return cv2.LUT(u8, _lut(R))
