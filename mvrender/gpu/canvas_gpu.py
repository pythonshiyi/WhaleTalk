"""GPUCanvas —— 常驻显存的画布（drop-in 替换 vis_core.Canvas）。

关键设计
--------
1. **buf 常驻显存**：add/blend/mul/glow 全部在 device 上完成，不回读。
   旧实现的致命问题是「每帧 create buffer + 每算子回读」，把 665x 的 kernel
   优势吃成 9.8x（98.5% 时间在搬运）。
2. **懒同步**：只有外部代码显式读 `.buf` 时才 device→host 回读一次；
   之后的 device 写入会重新标脏。
3. **稀疏层上传**：绘制类（cv2.line/circle）仍在 CPU 上生成局部稀疏图层，
   上传后由 kernel 合成 —— 这是「绘制稀疏、合成密集」的正确分工。
4. **预乘光晕**：radial_glow 掩膜按参数缓存，命中后只做一次 kernel 乘法。
"""
from __future__ import annotations

import numpy as np

from .runtime import Runtime


class GPUCanvas:
    """与 vis_core.Canvas 同接口的 GPU 画布。

    兼容层：`.buf` 属性返回 numpy（懒同步），让尚未 GPU 化的绘制代码可用；
    已 GPU 化的算子直接操作显存。
    """

    def __init__(self, w=1080, h=1920, bg=None, rt: Runtime | None = None):
        self.w, self.h = int(w), int(h)
        self.rt = rt or Runtime.get(w=self.w, h=self.h)
        self.n3 = self.w * self.h * 3
        self._bname = "canvas_buf"
        self._host = None
        # 两个独立的脏标记，缺一不可：
        #   _dirty      : device 比 host 新 → 读 buf 前需回读
        #   _host_dirty : host 被外部改过、尚未上传 → GPU 算子前需回传
        # 旧实现只有一个标记，导致「GPU 算子 → 读 buf 切片改 → GPU 算子」链条中
        # 切片改动被 device 旧值覆盖（实测 raw 画布 32% 像素偏差）。
        self._dirty = True
        self._host_dirty = False
        self._rt_buf = self.rt.buf(self._bname, self.n3 * 4)
        if bg is not None:
            self.fill(bg)
        else:
            self._zero()

    # ── 内部：device 写标记 ─────────────────────────────────────────
    def _flush_host(self):
        """GPU 算子执行前调用：若 host 有未提交改动，先回传 device。

        否则 GPU 算子会基于**过期的 device 数据**计算，丢失 host 切片改动。
        """
        if self._host is not None and self._host_dirty:
            self._upload_host()

    def _mark_device_dirty(self):
        self._dirty = True
        self._host_dirty = False

    def _full_mask(self, m):
        """把可能带广播维度的掩膜（如 (H,1)/(H,1,1)）展开成 (H,W) 或 (H,W,3)。

        【踩坑】元素代码常写 `c.mul(1.0 - 0.30 * cold[:, :, 0])`，其中
        `cold` 形状为 (H,1,1) → 传入的是 (H,1) 二维数组。CPU 的 `buf *= m[:,:,None]`
        依赖 numpy 广播天然正确；但 GPU kernel 按 `mask[i/3]` 线性索引，收到长度 H
        的数组会越界读到错误位置（实测整帧 97.9% 像素不一致）。
        这里显式展开到与画布匹配的形状。
        """
        if np.isscalar(m):
            return m
        a = np.asarray(m, np.float32)
        if a.shape[:2] != (self.h, self.w):
            a = np.broadcast_to(a, (self.h, self.w))
        return np.ascontiguousarray(a)

    def _zero(self):
        z = np.zeros(self.n3, np.float32)
        import pyopencl as cl
        cl.enqueue_copy(self.rt.q, self.rt.buf(self._bname, self.n3 * 4), z)
        self._host = None
        self._dirty = True
        self._host_dirty = False

    # ── host 同步 ───────────────────────────────────────────────────
    def sync_down(self):
        """device → host（仅当外部需要读 buf 时调用）。"""
        import pyopencl as cl
        if self._host is None:
            self._host = np.empty((self.h, self.w, 3), np.float32)
        if self._dirty and not self._host_dirty:
            cl.enqueue_copy(self.rt.q, self._host, self.rt.buf(self._bname, self.n3 * 4))
            self.rt.finish()
            self._dirty = False
        return self._host

    def sync_up(self):
        """host → device（外部直接改过 buf 后调用）。"""
        import pyopencl as cl
        if self._host is not None:
            cl.enqueue_copy(self.rt.q, self.rt.buf(self._bname, self.n3 * 4),
                            np.ascontiguousarray(self._host))
            self._dirty = False

    @property
    def buf(self):
        """返回**常驻 host 镜像**（同一对象），允许调用方直接切片原地改。

        兼容性关键：`elements.py`/`props.py` 普遍写成
            buf = canvas.buf
            buf[hy:] = buf[hy:] * a + refl * b     # 切片原地写
        若每次 `buf` 都返回新回读的数组，切片写会丢在临时对象上（实测报
        broadcast 错误或静默不生效）。因此这里：
          · 保证 host 镜像与 device 同步（仅当 device 更新时回读一次）；
          · 返回**同一数组对象**，切片写留在镜像里；
          · 由 `commit_host()`（渲染器在作画结束后调用一次）整体上传回 device。
        """
        self.sync_down()
        # 外部拿到 buf 后可能直接切片原地改；标记「host 可能已改」，
        # 由 commit_host()（作画结束）或下一个 GPU 算子前统一上传。
        self._host_dirty = True
        return self._host

    @buf.setter
    def buf(self, arr):
        self._host = np.ascontiguousarray(arr, np.float32)
        self._upload_host()

    def commit_host(self):
        """把 host 镜像整体上传回 device —— **仅当 host 确实比 device 新**。

        【关键踩坑】不能在作画结束时无条件上传：GPU 算子（add/glow/mul）直接写
        device 并把 `_dirty=True`，此时 host 镜像是**过期的**（例如 fill_gradient
        的旧内容）。若无条件 `_upload_host()`，会把 device 上刚画好的结果整体
        覆盖回旧值 —— 实测表现为 `night_sky`（fill_gradient + glow_add）画面
        偏暗、地平线光晕整段消失（row1152 CPU 42.9 vs GPU 18.1）。

        正确语义：只有「host 被外部切片改过」才需要上传；GPU 算子路径本就在
        device 上，`_dirty` 说明 device 更新，此时应跳过上传。
        """
        if self._host is None:
            return
        if self._dirty and not self._host_dirty:
            # device 比 host 新且 host 未被外部改：无需回灌
            return
        self._upload_host()

    def _upload_host(self):
        import pyopencl as cl
        h = np.ascontiguousarray(self._host, np.float32)
        self._host = h
        cl.enqueue_copy(self.rt.q, self.rt.buf(self._bname, self.n3 * 4), h.reshape(-1))
        self._dirty = False
        self._host_dirty = False

    # ── 填充 ────────────────────────────────────────────────────────
    def fill(self, color):
        arr = (np.array(color, np.float32).reshape(1, 1, 3)
               * np.ones((self.h, self.w, 1), np.float32))
        self._host = np.ascontiguousarray(arr, np.float32)
        self._upload_host()

    def fill_gradient(self, top, bottom, gamma=1.0):
        """与 CPU 版语义一致：写入**完整 (H,W,3)** 的渐变。

        【踩坑】CPU 版是 `self.buf[:] = c0*(1-t)+c1*t`，赋值到已分配的 (H,W,3)
        数组，广播只填列向一列即可。若直接 `self._host = c0*(1-t)+c1*t`，
        numpy 广播结果是 **(H,1,3)**（只有 1 列），后续 `buf[hy:]` 切片就会变成
        (h,1,3)，导致 `buf[hy:] = ...(h,W,3)` 抛 broadcast ValueError（实测）。
        此处显式广播到完整画布尺寸。
        """
        t = (np.linspace(0, 1, self.h, dtype=np.float32) ** gamma).reshape(-1, 1, 1)
        c0 = np.array(top, np.float32).reshape(1, 1, 3)
        c1 = np.array(bottom, np.float32).reshape(1, 1, 3)
        full = c0 * (1 - t) + c1 * t                       # (H,1,3)
        self._host = np.ascontiguousarray(
            np.broadcast_to(full, (self.h, self.w, 3)), np.float32)
        self._upload_host()

    # ── 合成算子（GPU 路径）────────────────────────────────────────
    def add(self, layer, mask=None):
        self._flush_host()
        b = self.rt.buf(self._bname, self.n3 * 4)
        b_lay = self.rt.up(np.ascontiguousarray(layer, np.float32).reshape(-1), "lay_tmp")
        if mask is None:
            self.rt.run("k_add", self.n3, b, b_lay)
        else:
            m = self._full_mask(mask)
            m = m[:, :, None] if m.ndim == 2 else m
            b_m = self.rt.up(np.ascontiguousarray(m, np.float32).reshape(-1), "mask_tmp")
            self.rt.run("k_add_mask", self.n3, b, b_lay, b_m)
        self._mark_device_dirty()

    def blend(self, layer, alpha):
        self._flush_host()
        b = self.rt.buf(self._bname, self.n3 * 4)
        b_lay = self.rt.up(np.ascontiguousarray(layer, np.float32).reshape(-1), "lay_tmp")
        if np.isscalar(alpha):
            self.rt.run("k_blend_scalar", self.n3, b, b_lay, np.float32(alpha))
        else:
            m = self._full_mask(alpha)
            m = m[:, :, None] if m.ndim == 2 else m
            b_m = self.rt.up(np.ascontiguousarray(m, np.float32).reshape(-1), "mask_tmp")
            self.rt.run("k_blend_mask", self.n3, b, b_lay, b_m)
        self._mark_device_dirty()

    def mul(self, m):
        self._flush_host()
        b = self.rt.buf(self._bname, self.n3 * 4)
        if np.isscalar(m):
            self.rt.run("k_mul_scalar", self.n3, b, np.float32(m))
        else:
            mm = self._full_mask(m)
            mm = mm[:, :, None] if mm.ndim == 2 else mm
            b_m = self.rt.up(np.ascontiguousarray(mm, np.float32).reshape(-1), "mask_tmp")
            self.rt.run("k_mul_mask", self.n3, b, b_m)
        self._mark_device_dirty()

    # ── 光晕（全 GPU 生成，掩膜按参数缓存）─────────────────────────
    def glow_add(self, cx, cy, r, color, gain=1.0, edge=None):
        """径向光晕：等价 vis_core.radial_glow，但掩膜在 device 上生成。

        掩膜按 (cx,cy,r,power,feather,edge) 缓存到显存；命中后只做一次乘法。
        """
        self._flush_host()
        b = self.rt.buf(self._bname, self.n3 * 4)
        col = np.asarray(color, np.float32) * float(gain)
        key = (round(cx, 4), round(cy, 4), round(r, 4), 2.2, 0.42,
               -1.0 if edge is None else round(edge, 3))
        mk = self._mask_cache(key, cx, cy, r, 2.2, 0.42, edge)
        # out = mask * color  → 复用 glow 输出池，再 k_add 到画布
        b_out = self.rt.buf("glow_out", self.n3 * 4)
        self.rt.run("k_glow_premul", self.n3, b_out, mk,
                    float(col[0]), float(col[1]), float(col[2]))
        self.rt.run("k_add", self.n3, b, b_out)
        self._mark_device_dirty()

    def _mask_cache(self, key, cx, cy, r, power, feather, edge):
        cache = getattr(self.rt, "_glow_masks", None)
        if cache is None:
            cache = {}
            self.rt._glow_masks = cache
        b = cache.get(key)
        if b is None:
            b = self.rt.buf(f"glowmask_{abs(hash(key)) % 100000}", self.rt.npix * 4)
            self.rt.run("k_glow_mask", self.rt.npix, b, self.w, self.h,
                        float(cx), float(cy), float(r), float(power), float(feather),
                        -1.0 if edge is None else float(edge))
            if len(cache) < 128:
                cache[key] = b
        return b

    def add_region(self, layer, x0, y0, mode="add"):
        """把**局部**图层（h,w[,3]）合成到画布的 (x0,y0) 区域（纯 GPU，不回读整帧）。

        用于稀疏绘制：只需上传局部小层（几百 KB），由 k_add_region 直接写入
        画布对应区域——避免「先回读整帧 23.7MB → CPU 切片改 → 再传回」的隐性成本
        （实测多次 add_region 交替 GPU 算子时，回读是稀疏化收益的主要漏损）。
        """
        self._flush_host()
        sub = layer[:, :, None] if layer.ndim == 2 else layer
        sub = np.ascontiguousarray(sub, np.float32)
        h, w = sub.shape[:2]
        rw = min(w, self.w - x0)
        rh = min(h, self.h - y0)
        if rw <= 0 or rh <= 0:
            return
        b_lay = self.rt.up(sub[:rh, :rw].reshape(-1), "region_tmp")
        b = self.rt.buf(self._bname, self.n3 * 4)
        if mode == "add":
            self.rt.run("k_add_region", rh * rw * 3, b, b_lay,
                        x0, y0, rw, rh, self.w)
        else:
            self.rt.run("k_region_mode", rh * rw * 3, b, b_lay, b_lay, 0,
                        x0, y0, rw, rh, self.w, 1 if mode == "mul" else 0)
        self._mark_device_dirty()

    def vignette(self, strength=0.34, power=1.9, ry=1.7):
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        d = np.sqrt((xx / self.w - 0.5) ** 2 + ((yy / self.h - 0.5) * ry) ** 2)
        m = np.clip(1.0 - strength * (d / 0.66) ** power, 0.42, 1.0)
        self.mul(m)

    # ── 输出 ────────────────────────────────────────────────────────
    def out(self, gain=1.0):
        """等价 vis_core.Canvas.out()：一次曲线 + uint8（GPU 上完成）。"""
        import pyopencl as cl
        b = self.rt.buf(self._bname, self.n3 * 4)
        b_out = self.rt.buf("canvas_out_u8", self.n3)
        self.rt.run("k_canvas_out", self.n3, b, b_out, np.float32(gain))
        res = np.empty((self.h, self.w, 3), np.uint8)
        cl.enqueue_copy(self.rt.q, res, b_out)
        self.rt.finish()
        return res

    def to_lut(self, lut):
        """用自定义 LUT 出图（vig=1/gain=1/flash=0/bmul=1 即只过 LUT）。"""
        import pyopencl as cl
        lutf = np.ascontiguousarray(lut.reshape(-1), np.uint8)
        b_lut = self.rt.up(lutf, "lut_tbl", once=True, dtype=np.uint8)
        one = np.ones(self.rt.npix, np.float32)
        b_one = self.rt.up(one, "_one", once=True)
        b = self.rt.buf(self._bname, self.n3 * 4)
        b_out = self.rt.buf("canvas_out_u8", self.n3)
        self.rt.run("k_post_tonemap", self.n3, b, b_one, b_lut, b_out,
                    np.float32(1.0), np.float32(0.0), np.float32(1.0))
        res = np.empty((self.h, self.w, 3), np.uint8)
        cl.enqueue_copy(self.rt.q, res, b_out)
        self.rt.finish()
        return res
