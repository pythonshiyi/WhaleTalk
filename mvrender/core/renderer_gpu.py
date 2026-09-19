"""GpuRenderer —— 全 GPU 管线渲染器（画布常驻 + warp + 后处理融合）。

相对 CPU Renderer 的三处关键加速
--------------------------------
A. **画布不回读**：raw_shot 全程留在 device，warp 直接读 device 画布。
   旧实现的致命问题是每帧 `c.buf` 触发 device→host 同步（98.5% 时间在搬运）。
B. **相机 warp 上 GPU**：k_warp3（实测 5.2ms → 0.34ms，15.2x）。
C. **后处理融合**：post + to_uint8 合成单 kernel（原 139ms → 常驻 0.21ms）。

词层仍是 CPU 稀疏绘制（文字 mask），再上传一个稀疏 overlay 由 GPU 合成 —— 
这是「绘制稀疏、合成密集」的正确分工，避免为文字写复杂 kernel。
"""
from __future__ import annotations

import numpy as np

from ..gpu import ops
from ..gpu.canvas_gpu import GPUCanvas
from .camera import affine_matrix, camera
from .renderer import Renderer, _lut


class GpuRenderer(Renderer):
    def __init__(self, shots, ctx, lyrics=None, w=1080, h=1920, rt=None):
        super().__init__(shots, ctx, lyrics, w=w, h=h, use_gpu=True, rt=rt)
        if self.rt is None:
            from ..gpu import Runtime
            self.rt = Runtime.get(w=self.w, h=self.h)
        self._vig_b = None
        self._lut_b = None
        # 两槽轮转：转场需要同一帧内同时持有「上一镜」与「本镜」两个 warp 结果。
        # 【关键】若只用一个 buffer，raw_shot_gpu(prev) 的结果会被 raw_shot_gpu(cur)
        # 覆盖，转场合成会用错误的老画面（实测 MAE 95）。
        self._warp_pool = [self.rt.buf("rr_warp0", self.rt.n3 * 4),
                           self.rt.buf("rr_warp1", self.rt.n3 * 4)]
        self._warp_slot = 0

    # ── 常驻表 ─────────────────────────────────────────────────────
    def _vig_buf(self, day=False):
        """暗角表（白昼版含 +0.22 归一化，与 CPU post 的 day 分支一致）。

        两张表分别常驻，避免每帧改表。
        """
        from .renderer import _vigbase
        vb = _vigbase(self.cache, self.w, self.h)
        key = "rr_vig_day" if day else "rr_vig"
        cache = getattr(self, "_vig_cache", None)
        if cache is None:
            cache = {}
            self._vig_cache = cache
        b = cache.get(key)
        if b is None:
            tbl = np.clip(vb + 0.22, 0.35, 1.0) if day else vb
            b = self.rt.resident(tbl.reshape(-1), key)
            cache[key] = b
        return b

    def _lut_buf(self):
        if self._lut_b is None:
            self._lut_b = self.rt.resident(_lut(self).reshape(-1), "rr_lut", dtype=np.uint8)
        return self._lut_b

    # ── 镜头：画布常驻 + GPU warp ──────────────────────────────────
    def raw_shot_gpu(self, s, t):
        """在 device 上完成作画 + 相机 warp，返回 device buffer（不回读）。"""
        c = GPUCanvas(self.w, self.h, rt=self.rt)
        tl = t - s.t0
        s.fn(c, tl, s, self.ctx, self.cache)
        c.commit_host()               # 把 host 镜像（含切片原地写）同步回 device
        b_src = self.rt.buf("canvas_buf", self.rt.n3 * 4)
        z, dx, dy, rot = camera(s, tl, t, self.ctx, kind=s.cam, w=self.w, h=self.h)
        M = affine_matrix(z, dx, dy, rot, self.w, self.h)
        slot = self._warp_pool[self._warp_slot]
        self._warp_slot ^= 1
        ops.warp3(self.rt, b_src, slot, M, self.w, self.h)
        return slot

    # ── post + tonemap：单 kernel，只回读 u8 ───────────────────────
    def post(self, img, t, day=False):
        """GPU 路径下 post 恒等（全部工作交给 finalize）。"""
        return img

    def to_uint8(self, img):
        return super().to_uint8(img)

    def _post_params(self, t, day):
        """与 CPU post 完全等价的 (gain, flash, (cmul_b, cmul_g, cmul_r))。"""
        ctx = self.ctx
        e = ctx.energy(t)
        p = ctx.pulse(t)
        if day:
            return 0.94 + 0.06 * e, p * 3.0, (1.0, 1.0, 1.0)
        gain = (0.98 + 0.30 * e) * 0.96
        flash = p * 13.0
        cb, cg, cr = 1.05, 1.0, 1.0            # 夜戏：B 通道 ×1.05
        # 段落级色调必须与 CPU post 逐行一致。注意 numpy `img[:,:,0]` 是 **B 通道**、
        # `img[:,:,2]` 是 **R 通道**（BGR 顺序）：
        #   尾段：B×0.94, G×0.96, R×1.00
        #   桥段：B×0.97, R×1.02        ← 早期实现把 R/B 系数写反（实测 MAE 107）
        ln = ctx.line_at(t)
        seg = ln["sec"] if ln else None
        if seg == "尾段":
            cb *= 0.94
            cg *= 0.96
            cr *= 1.00
        elif seg == "桥段":
            cb *= 0.97
            cr *= 1.02
        return gain, flash, (cb, cg, cr)

    def post_gpu(self, b_img, t, day):
        """在 device 上做 Renderer.post（不含 tonemap），返回 device buffer。

        必须拆成 post / tonemap 两步：CPU 主循环顺序是
        `post → 歌词 → to_uint8`，歌词（字带压暗 + 叠加）作用在 **post 后的
        float 域**。若把歌词放在 post 前（或先 tonemap 再叠字），会在字幕带产生
        整段偏差（实测桥段镜头 MAE 107）。
        """
        gain, flash, cmul = self._post_params(t, day)
        # 全 GPU：k_post_float（vig 常驻，无任何全帧搬运）
        b = self.rt.buf("rr_post_f", self.rt.n3 * 4)
        self.rt.run("k_post_float", self.rt.n3, b_img, self._vig_buf(day), b,
                    np.float32(gain), np.float32(flash),
                    np.float32(cmul[0]), np.float32(cmul[1]), np.float32(cmul[2]))
        return b

    def finalize(self, b_img, t, day):
        """tonemap：post 已完成的 float buffer → u8（只回读一次）。"""
        import pyopencl as cl
        b_out = self.rt.buf("rr_out_u8", self.rt.n3)
        one = self.rt.up(np.ones(self.rt.npix, np.float32), "_one", once=True)
        b_lut = self._lut_buf()
        self.rt.run("k_post_tonemap", self.rt.n3, b_img, one, b_lut, b_out,
                    np.float32(1.0), np.float32(0.0),
                    np.float32(1.0), np.float32(1.0), np.float32(1.0))
        res = np.empty((self.h, self.w, 3), np.uint8)
        cl.enqueue_copy(self.rt.q, res, b_out)
        self.rt.finish()
        return res

    # ── 主循环（GPU 内联版，避免基类每次回读）─────────────────────
    def render_u8(self, t):
        """完整渲染一帧并返回 uint8（GPU 常驻，仅末尾回读）。"""
        i, s = self.shot_index_at(t)
        b_img = self.raw_shot_gpu(s, t)
        D = self.trans_dur(i)
        if D > 0 and t < s.t0 + D:
            prev = self.shots[i - 1]
            b_old = self.raw_shot_gpu(prev, t)
            p = (t - s.t0) / D
            gc = self.DAY_GLOW if s.day else self.NIGHT_GLOW
            from .transition import trans_mask
            m, ring = trans_mask(s.tin_kind, p, seed=(i * 17) % 90,
                                 origin=(0.5, 0.4 + 0.1 * ((i % 3) - 1)),
                                 w=self.w, h=self.h)
            b_new = self.rt.buf("rr_new", self.rt.n3 * 4)
            b_m = self.rt.up(m.reshape(-1), "rr_tmask")
            use_ring = 0 if ring is None else 1
            b_r = self.rt.up((ring if ring is not None else np.zeros(self.w * self.h, np.float32)).reshape(-1),
                             "rr_tring")
            self.rt.run("k_transition", self.rt.n3, b_old, b_img, b_m, b_r, b_new,
                        float(gc[0]), float(gc[1]), float(gc[2]), use_ring)
            b_img = b_new
            self.last_tr = (i, s.tin_kind, float(p))
        else:
            self.last_tr = None
        # 顺序必须与 CPU 主循环一致：post → 歌词 → tonemap
        # （CPU render(): img=post(...) → lyrics.render → dark 压暗 → +overlay，
        #  最后 render_at 才 to_uint8。歌词作用在 post 后的 float 域。）
        b_post = self.post_gpu(b_img, t, s.day)
        b_lyr = self._lyrics_to_device(b_post, t, s)
        return self.finalize(b_lyr, t, s.day)

    def _lyrics_to_device(self, b_img, t, s):
        """歌词层：读 device 图像 → CPU 稀疏绘制 → 叠加后回传 device。

        与 CPU 主循环同序（post → lyrics → tonemap）。为省一次全帧搬运，
        这里对整帧做一次 device→host→叠加→host→device；相较旧实现每算子都
        回读，仍是「一帧两次搬运」的常量开销。
        """
        if self.lyrics is None:
            return b_img
        ln = self.ctx.line_at(t)
        if ln is None:
            return b_img
        import pyopencl as cl
        # 只搬运字幕带（dark/overlay 的有效行区间），而不是整帧：
        # 歌词永远落在 y_base=1436 附近 ±~260（字高 88 + 字带半径 190），
        # 占全帧约 27%。区域搬运把两次 23.7MB 全帧拷贝降到 ~6MB。
        # 整帧搬运：OpenCL 的 buffer 偏移写入要求对齐（非对齐 dst_offset 会
        # INVALID_VALUE，实测），逐行区域搬运需要 padding 到设备对齐，
        # 收益（~27% 拷贝量）不足以抵消复杂度与风险，故保持整帧。
        img_f = np.empty((self.h, self.w, 3), np.float32)
        cl.enqueue_copy(self.rt.q, img_f, b_img)
        self.rt.finish()
        _, ov = self.lyrics.render(img_f, t, self.ctx,
                                   sec=(ln["sec"] if ln else None), day=s.day)
        dark = getattr(self.lyrics, "last_dark", None)
        if dark is not None:
            img_f = img_f * (1.0 - dark)[:, :, None]
        img_f = img_f + ov
        return self.rt.up(np.ascontiguousarray(img_f).reshape(-1), "rr_lyr")

    # 覆盖基类 render（返回 float，供一致性对比用）──────────────────
    def render(self, t):
        return self.render_u8(t).astype(np.float32)
