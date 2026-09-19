"""OpenCL 运行时（FastMV GPU 后端）。

相对旧实现的修复与改进
----------------------
1. **修复 `AttributeError: 'GPU' object has no attribute 'pools'`**：
   旧 `gpu_core.py` 在 `__init__` 里写 `self.bufs = {}`，但 `_buf()` 访问 `self.pools`
   → 首次分配即崩，导致 GPU 路径从未真正跑起来（一直退回 CPU 单进程）。
2. **命名缓冲池（按用途复用，只增不减）**：不能按 nbytes 建 key（同用途不同尺寸会
   在几十帧内把显存耗尽，实测 MEM_OBJECT_ALLOCATION_FAILURE）。
3. **常驻显存**：画布 / LUT / 暗角表 / 噪声 / 掩膜表上传一次后复用，只在出帧回读 u8。
4. **kernel 实例缓存**：避免 RepeatedKernelRetrieval 开销。
5. **设备优先 gfx1200（RX 9060 XT）**，回退任意 GPU。
"""
from __future__ import annotations

import threading

import numpy as np

from .kernels import KERNEL_SRC

try:
    import pyopencl as cl
except ImportError:  # pragma: no cover
    cl = None

_KERNEL_NAMES = (
    "k_add", "k_add_mask", "k_blend_scalar", "k_blend_mask",
    "k_mul_scalar", "k_mul_mask", "k_canvas_out", "k_post_tonemap", "k_post_float",
    "k_transition", "k_radial_glow", "k_glow_mask", "k_glow_premul",
    "k_gauss_h", "k_gauss_v", "k_gauss3_h", "k_gauss3_v",
    "k_warp3", "k_resize1", "k_resize3", "k_layer_over",
    "k_scale1", "k_downsample1", "k_box_h", "k_box_v", "k_add_field", "k_mist",
    "k_add_region", "k_region_mode",
)


class GPUUnavailable(RuntimeError):
    """无可用 OpenCL 设备 / pyopencl 缺失。"""


class Runtime:
    """OpenCL 运行时的进程级单例（线程安全懒初始化）。

    缓冲池语义：`self.pools[name] = [buffer, capacity_bytes]`，同名只保留一块
    「足够大」的显存，不够时才重建。这既避免别名覆盖，也避免显存堆积。
    """

    _inst = None
    _lock = threading.Lock()

    def __init__(self, w=1080, h=1920, prefer=("gfx1200",), verbose=False):
        if cl is None:
            raise GPUUnavailable("需要 pyopencl：pip install pyopencl")
        self.w, self.h = int(w), int(h)
        self.npix = self.w * self.h
        self.n3 = self.npix * 3
        self.ctx, self.dev = self._pick(prefer)
        self.q = cl.CommandQueue(self.ctx)
        try:
            self.prg = cl.Program(self.ctx, KERNEL_SRC).build()
        except Exception as e:  # pragma: no cover
            raise GPUUnavailable(f"OpenCL 内核编译失败：{e}") from e
        self.K = {n: cl.Kernel(self.prg, n) for n in _KERNEL_NAMES}
        self.pools: dict[str, list] = {}
        self._once: set[str] = set()
        self._gauss_cache: dict[tuple, tuple] = {}
        if verbose:
            print(f"[mvrender.gpu] {self.dev.name} "
                  f"CU={self.dev.max_compute_units} "
                  f"{self.dev.global_mem_size / 2**30:.1f}GB")

    # ── 设备选择 ────────────────────────────────────────────────────
    @staticmethod
    def _pick(prefer):
        cands = []
        for p in cl.get_platforms():
            for d in p.get_devices():
                cands.append((p, d))
        for _p, d in cands:
            if any(k.lower() in d.name.lower() for k in prefer):
                return cl.Context(devices=[d]), d
        for _p, d in cands:
            if d.type & cl.device_type.GPU:
                return cl.Context(devices=[d]), d
        raise GPUUnavailable("找不到可用 GPU 设备")

    @classmethod
    def get(cls, **kw):
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls(**kw)
            return cls._inst

    @classmethod
    def reset(cls):
        """测试用：丢弃单例（缓冲随上下文一起释放）。"""
        with cls._lock:
            cls._inst = None

    @staticmethod
    def probe():
        """列出所有 OpenCL 设备（供诊断）。"""
        if cl is None:
            print("pyopencl 未安装")
            return []
        rows = []
        for p in cl.get_platforms():
            for d in p.get_devices():
                rows.append((p.name, d.name, cl.device_type.to_string(d.type),
                             d.max_compute_units, d.global_mem_size,
                             d.max_clock_frequency))
                print(f"  {p.name:34s} | {d.name:10s} {cl.device_type.to_string(d.type):10s} "
                      f"CU={d.max_compute_units:>3} {d.global_mem_size / 2**30:5.1f}GB "
                      f"{d.max_clock_frequency}MHz")
        return rows

    # ── 缓冲池 ──────────────────────────────────────────────────────
    def buf(self, name: str, nbytes: int, flags=None):
        """取得（必要时创建）命名缓冲，容量只增不减。"""
        cur = self.pools.get(name)
        if cur is None or cur[1] < nbytes:
            cap = max(nbytes, cur[1] if cur else 0)
            cur = [cl.Buffer(self.ctx, flags or cl.mem_flags.READ_WRITE, cap), cap]
            self.pools[name] = cur
        return cur[0]

    def up(self, arr: np.ndarray, name: str, once: bool = False, dtype=np.float32):
        """上传到命名池；once=True 时同名只上传一次（常驻表）。"""
        if once and name in self._once:
            return self.buf(name, arr.nbytes)
        a = np.ascontiguousarray(arr, dtype)
        b = self.buf(name, a.nbytes)
        cl.enqueue_copy(self.q, b, a)
        if once:
            self._once.add(name)
        return b

    def down(self, name: str, shape, dtype, nbytes=None):
        """从命名池回读。"""
        b = self.buf(name, nbytes or (int(np.prod(shape)) * np.dtype(dtype).itemsize))
        out = np.empty(shape, dtype)
        cl.enqueue_copy(self.q, out, b)
        return out

    @staticmethod
    def _coerce(a):
        """把 Python 标量规整成 pyopencl 可接受的最小位宽类型。

        踩坑：直接传 Python int 会被视为 int64，而 kernel 形参是 `const int`（32 位）
        → `Kernel.set_arg failed: INVALID_VALUE`（实测）。此处统一按 32 位传入。
        """
        if isinstance(a, bool):
            return np.int32(a)
        if isinstance(a, int):
            return np.int32(a)
        if isinstance(a, float):
            return np.float32(a)
        return a

    def run(self, kname: str, n: int, *args):
        """启动 kernel（不同步，调用方按需 finish）。"""
        self.K[kname](self.q, (int(n),), None, *[self._coerce(x) for x in args])

    def finish(self):
        self.q.finish()

    # ── 常驻表 ──────────────────────────────────────────────────────
    def resident(self, arr: np.ndarray, name: str, dtype=np.float32):
        return self.up(arr, name, once=True, dtype=dtype)

    # ── 高斯核表（按 sigma/半径缓存）────────────────────────────────
    def gauss_kernel(self, sigma: float):
        """返回 (kernel_buffer, rad)；一维归一化高斯（截断 3σ）。"""
        key = round(float(sigma), 4)
        hit = self._gauss_cache.get(key)
        if hit is not None:
            return hit
        sg = max(1e-3, float(sigma))
        rad = max(1, int(round(3.0 * sg)))
        xs = np.arange(-rad, rad + 1, dtype=np.float32)
        k = np.exp(-0.5 * (xs / sg) ** 2)
        k /= (k.sum() + 1e-9)
        b = self.up(k, f"gk_{key}", dtype=np.float32)
        self._gauss_cache[key] = (b, rad)
        return b, rad
