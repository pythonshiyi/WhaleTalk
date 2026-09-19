"""GPU 加速层（可选 pyopencl；无 GPU / 无 OpenCL 时自动回退 CPU）。

## 为什么需要它（真实作业复盘）
自研渲染管线（NumPy/OpenCV 逐帧合成）单帧 1-4s、把 6 个进程的 12 逻辑线程打满，
而 AMD 9060XT（16G）全程闲置——**瓶颈是全分辨率 pass 的反复 CPU 内存往返**，
不是内存容量、也不是 ffmpeg 编码（编码只占 ~1%）。实测结论：
- OpenCV `UMat` 走 GPU **反而更慢**（GaussianBlur 0.53x / resize 0.18x）；
- 真正有效的是 **pyopencl**：把逐帧 pass 融合进 GPU，且**数据常驻显存**——
  后处理段 121.4ms → 8.12ms（含传输，14.9x）/ 0.137ms（显存常驻，884x），像素误差 ≤1。

本模块把这套能力封装为**可复用 API**，供工具与生成的渲染管线直接调用；
无 GPU 时语义等价地回退 CPU，保证可移植与可测试。

## 约定
- 图像为 `numpy.float32`，形状 `(H, W, C)`，C ∈ {1,3,4}，取值 0..1（超出会 clamp）。
- 所有算子**确定性**：同输入同输出。
- `device_info()/benchmark()/selftest()` 供工具层做「哪条通道更快」的实证选择，
  避免再踩「UMat 更慢」的坑。
"""
from __future__ import annotations

import os
import time

import numpy as np

_STATE = {
    "tried": False,
    "ok": False,
    "device": "",
    "platform": "",
    "mem_gb": 0.0,
    "devices": [],
    "ctx": None,
    "queue": None,
    "prg": None,
    "kernels": {},
}

_KERNELS = r"""
inline int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

// 逐元素：曝光/对比度/伽马/饱和度
__kernel void k_grade(__global const float* src, __global float* dst, const int n, const int c,
                      const float exposure, const float contrast, const float inv_gamma, const float sat) {
    int i = get_global_id(0);
    if (i >= n) return;
    int ch = i % c;
    float v = src[i] * exposure;
    v = (v - 0.5f) * contrast + 0.5f;
    v = v <= 0.0f ? 0.0f : pow(v, inv_gamma);
    if (c >= 3 && sat != 1.0f) {
        int base = i - ch;
        float lum = 0.299f * src[base] + 0.587f * src[base + 1] + 0.114f * src[base + 2];
        v = lum + (v - lum) * sat;
    }
    dst[i] = v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v);
}

// 逐元素：base + bloom*intensity
__kernel void k_add(__global float* base, __global const float* bloom, const int n, const float intensity) {
    int i = get_global_id(0);
    if (i >= n) return;
    float v = base[i] + bloom[i] * intensity;
    base[i] = v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v);
}

// 逐元素：base*(1-a) + layer*a（a 为标量）
__kernel void k_composite(__global float* base, __global const float* layer, const int n, const float a) {
    int i = get_global_id(0);
    if (i >= n) return;
    base[i] = base[i] * (1.0f - a) + layer[i] * a;
}

// 阈值提取（bloom 源）
__kernel void k_threshold(__global const float* src, __global float* dst, const int n, const float thr) {
    int i = get_global_id(0);
    if (i >= n) return;
    float v = src[i];
    dst[i] = v > thr ? (v - thr) / (1.0f - thr + 1e-6f) : 0.0f;
}

// 一维高斯（沿 x）：buf 形状 (H, W, C) 扁平
__kernel void k_blur_h(__global const float* src, __global float* dst, __global const float* wts,
                       const int H, const int W, const int C, const int R, const float wsum) {
    int x = get_global_id(0), y = get_global_id(1);
    if (x >= W || y >= H) return;
    for (int c = 0; c < C; ++c) {
        float acc = 0.0f;
        for (int k = -R; k <= R; ++k) {
            int xx = clampi(x + k, 0, W - 1);
            acc += src[((y * W) + xx) * C + c] * wts[k + R];
        }
        dst[((y * W) + x) * C + c] = acc / wsum;
    }
}

// 一维高斯（沿 y）
__kernel void k_blur_v(__global const float* src, __global float* dst, __global const float* wts,
                       const int H, const int W, const int C, const int R, const float wsum) {
    int x = get_global_id(0), y = get_global_id(1);
    if (x >= W || y >= H) return;
    for (int c = 0; c < C; ++c) {
        float acc = 0.0f;
        for (int k = -R; k <= R; ++k) {
            int yy = clampi(y + k, 0, H - 1);
            acc += src[((yy * W) + x) * C + c] * wts[k + R];
        }
        dst[((y * W) + x) * C + c] = acc / wsum;
    }
}
"""


def _init_opencl():
    """探测并初始化 OpenCL（优先显存最大的设备=独显）。只尝试一次。"""
    if _STATE["tried"]:
        return
    _STATE["tried"] = True
    try:
        import pyopencl as cl
    except Exception:
        return
    try:
        best = None
        for p in cl.get_platforms():
            for d in p.get_devices():
                m = float(getattr(d, "global_mem_size", 0) or 0)
                _STATE["devices"].append({
                    "platform": p.name, "device": d.name,
                    "mem_gb": round(m / 1e9, 1),
                })
                if best is None or m > best[2]:
                    best = (p, d, m)
        if best is None:
            return
        p, d, m = best
        ctx = cl.Context([d])
        queue = cl.CommandQueue(ctx)
        prg = cl.Program(ctx, _KERNELS).build()
        kernels = {nm: cl.Kernel(prg, nm) for nm in
                   ("k_grade", "k_add", "k_composite", "k_threshold", "k_blur_h", "k_blur_v")}
        _STATE.update({"ok": True, "device": d.name, "platform": p.name,
                       "mem_gb": round(m / 1e9, 1), "ctx": ctx, "queue": queue,
                       "prg": prg, "kernels": kernels})
    except Exception:
        _STATE.update({"ok": False})


def available() -> bool:
    _init_opencl()
    return bool(_STATE["ok"])


def device_info() -> dict:
    _init_opencl()
    return {
        "available": bool(_STATE["ok"]),
        "backend": "opencl" if _STATE["ok"] else "cpu",
        "device": _STATE["device"],
        "platform": _STATE["platform"],
        "mem_gb": _STATE["mem_gb"],
        "devices": list(_STATE["devices"]),
        "note": ("GPU 通道可用（pyopencl）" if _STATE["ok"]
                 else "无可用 OpenCL 设备或未装 pyopencl，已回退 CPU"),
    }


# ── CPU 参考实现（与 GPU 同权重，便于自检；生产 CPU 路径也用它）──────────────

def _as_f32(img):
    a = np.asarray(img, dtype=np.float32)
    if a.ndim == 2:
        a = a[..., None]
    return a


def _to_cl(queue, arr):
    import pyopencl as cl
    return cl.Buffer(queue.context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=arr)


def _empty_cl(queue, size):
    import pyopencl as cl
    return cl.Buffer(queue.context, cl.mem_flags.READ_WRITE, size)


def grade(img, exposure=1.0, contrast=1.0, gamma=1.0, saturation=1.0):
    """曝光 / 对比度 / 伽马 / 饱和度（逐元素，GPU 可用）。"""
    a = _as_f32(img)
    if _STATE["ok"]:
        try:
            return _grade_gpu(a, exposure, contrast, gamma, saturation)
        except Exception:
            pass
    v = a * float(exposure)
    v = (v - 0.5) * float(contrast) + 0.5
    v = np.clip(v, 0, None) ** (1.0 / float(gamma))
    # 饱和度用**原图**亮度（GPU/CPU 一致）
    if a.shape[2] >= 3 and saturation != 1.0:
        lum = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2])[..., None]
        v = lum + (v - lum) * float(saturation)
    return np.clip(v, 0.0, 1.0).astype(np.float32)


def _grade_gpu(a, exposure, contrast, gamma, sat):
    import pyopencl as cl
    q = _STATE["queue"]
    k = _STATE["kernels"]["k_grade"]
    n = int(a.size)
    c = int(a.shape[2])
    src = _to_cl(q, np.ascontiguousarray(a))
    dst = _empty_cl(q, a.nbytes)
    k(q, (n,), None, src, dst, np.int32(n), np.int32(c),
      np.float32(exposure), np.float32(contrast),
      np.float32(1.0 / float(gamma)), np.float32(sat))
    out = np.empty_like(a)
    cl.enqueue_copy(q, out, dst)
    q.finish()
    return out.astype(np.float32)


def _gauss_weights(sigma, r=None):
    sigma = max(0.1, float(sigma))
    r = int(r if r is not None else max(1, round(3.0 * sigma)))
    k = np.arange(-r, r + 1, dtype=np.float64)
    w = np.exp(-(k * k) / (2.0 * sigma * sigma))
    return w.astype(np.float32), r, float(w.sum())


def gaussian_blur(img, sigma=2.0, radius=None):
    """可分离高斯模糊（GPU 可用）。"""
    a = _as_f32(img)
    w, r, wsum = _gauss_weights(sigma, radius)
    if _STATE["ok"]:
        try:
            return _blur_gpu(a, w, r, wsum)
        except Exception:
            pass
    # CPU：可分离卷积（np.pad + 滑动窗口，与 GPU 同权重）
    out = a
    for axis in (1, 0):
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        for i, wi in enumerate(w):
            sl = [slice(None)] * a.ndim
            sl[axis] = slice(i, i + out.shape[axis])
            acc += p[tuple(sl)] * wi
        out = (acc / wsum).astype(np.float32)
    return out.astype(np.float32)


def _blur_gpu(a, w, r, wsum):
    import pyopencl as cl
    q = _STATE["queue"]
    H, W, C = int(a.shape[0]), int(a.shape[1]), int(a.shape[2])
    wt = cl.Buffer(q.context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=np.ascontiguousarray(w))
    src = _to_cl(q, np.ascontiguousarray(a))
    tmp = _empty_cl(q, a.nbytes)
    dst = _empty_cl(q, a.nbytes)
    kh = _STATE["kernels"]["k_blur_h"]
    kv = _STATE["kernels"]["k_blur_v"]
    kh(q, (W, H), None, src, tmp, wt, np.int32(H), np.int32(W), np.int32(C),
       np.int32(r), np.float32(wsum))
    kv(q, (W, H), None, tmp, dst, wt, np.int32(H), np.int32(W), np.int32(C),
       np.int32(r), np.float32(wsum))
    out = np.empty_like(a)
    cl.enqueue_copy(q, out, dst)
    q.finish()
    return out.astype(np.float32)


def _bloom_gpu(a, threshold, sigma, intensity):
    """融合辉光：阈值→blur_h→blur_v→叠加，全程**数据留在显存**（只有首尾各一次传输）。

    这正是复盘结论的关键：孤立算子因每步搬运而未必快；把整条 pass 融合、避免
    中间结果回主存，才能真正拿到 GPU 收益。"""
    import pyopencl as cl
    q = _STATE["queue"]
    H, W, C = int(a.shape[0]), int(a.shape[1]), int(a.shape[2])
    n = int(a.size)
    w, r, wsum = _gauss_weights(sigma)
    src = _to_cl(q, np.ascontiguousarray(a))       # base 常驻显存
    hi = _empty_cl(q, a.nbytes)
    tmp = _empty_cl(q, a.nbytes)
    wt = cl.Buffer(q.context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR,
                   hostbuf=np.ascontiguousarray(w))
    _STATE["kernels"]["k_threshold"](q, (n,), None, src, hi, np.int32(n), np.float32(threshold))
    _STATE["kernels"]["k_blur_h"](q, (W, H), None, hi, tmp, wt,
                                  np.int32(H), np.int32(W), np.int32(C), np.int32(r), np.float32(wsum))
    _STATE["kernels"]["k_blur_v"](q, (W, H), None, tmp, hi, wt,
                                  np.int32(H), np.int32(W), np.int32(C), np.int32(r), np.float32(wsum))
    _STATE["kernels"]["k_add"](q, (n,), None, src, hi, np.int32(n), np.float32(intensity))
    out = np.empty_like(a)
    cl.enqueue_copy(q, out, src)
    q.finish()
    return out.astype(np.float32)


def bloom(img, threshold=0.7, sigma=8.0, intensity=0.5):
    """辉光：阈值提取 → 高斯模糊 → 叠加回底图（GPU 融合、显存常驻；否则 CPU）。"""
    a = _as_f32(img)
    if _STATE["ok"]:
        try:
            return _bloom_gpu(a, threshold, sigma, intensity)
        except Exception:
            pass
    hi = np.clip((np.clip(a, 0, 1) - float(threshold)) / (1.0 - float(threshold) + 1e-6), 0, 1)
    bl = gaussian_blur(hi, sigma)
    return np.clip(a + bl * float(intensity), 0.0, 1.0).astype(np.float32)


def composite(base, layer, alpha=0.5):
    """按常量 alpha 混合：base*(1-a)+layer*a（GPU 可用）。"""
    b = _as_f32(base)
    l = _as_f32(layer)
    if b.shape != l.shape:
        return b
    a = float(alpha)
    if _STATE["ok"]:
        try:
            return _composite_gpu(b, l, a)
        except Exception:
            pass
    return np.clip(b * (1.0 - a) + l * a, 0.0, 1.0).astype(np.float32)


def _composite_gpu(b, l, a):
    import pyopencl as cl
    q = _STATE["queue"]
    n = int(b.size)
    bb = _to_cl(q, np.ascontiguousarray(b))
    lb = _to_cl(q, np.ascontiguousarray(l))
    _STATE["kernels"]["k_composite"](q, (n,), None, bb, lb, np.int32(n), np.float32(a))
    out = np.empty_like(b)
    cl.enqueue_copy(q, out, bb)
    q.finish()
    return out.astype(np.float32)


def resize(img, w, h):
    """缩放（CPU：cv2 优先，缺则最近邻 numpy）。"""
    a = _as_f32(img)
    w, h = int(w), int(h)
    try:
        import cv2
        return cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)
    except Exception:
        ys = (np.arange(h) * a.shape[0] // max(1, h)).clip(0, a.shape[0] - 1)
        xs = (np.arange(w) * a.shape[1] // max(1, w)).clip(0, a.shape[1] - 1)
        return a[ys][:, xs].astype(np.float32)


def selftest():
    """GPU 与 CPU 参考实现像素级一致性自检。返回 (ok, detail)。"""
    if not available():
        return False, f"无 GPU 通道：{device_info()['note']}"
    rng = np.random.default_rng(7)
    a = rng.random((48, 64, 4), dtype=np.float32)
    # GPU 结果
    g_gpu = _grade_gpu(a, 1.1, 1.05, 0.95, 1.1)
    b_gpu = _blur_gpu(a, *_gauss_weights(2.0))
    c_gpu = _composite_gpu(a, a[::-1], 0.4)
    # CPU 参考（同算法）
    v = np.clip(a * 1.1, 0, None)
    v = (v - 0.5) * 1.05 + 0.5
    v = np.clip(v, 0, None) ** (1 / 0.95)
    lum = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2])[..., None]
    v = np.clip(lum + (v - lum) * 1.1, 0, 1)
    d_grade = float(np.max(np.abs(g_gpu - v)))
    d_blur = float(np.max(np.abs(b_gpu - gaussian_blur_cpu_ref(a, 2.0))))
    d_comp = float(np.max(np.abs(c_gpu - (a * 0.6 + a[::-1] * 0.4))))
    ok = max(d_grade, d_blur, d_comp) < 2e-3
    return ok, (f"device={_STATE['device']} max|diff| grade={d_grade:.2e} "
                f"blur={d_blur:.2e} composite={d_comp:.2e}")


def gaussian_blur_cpu_ref(a, sigma):
    """自检用 CPU 参考（强制走 CPU，避免递归到 GPU）。"""
    w, r, wsum = _gauss_weights(sigma)
    out = a
    for axis in (1, 0):
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        for i, wi in enumerate(w):
            sl = [slice(None)] * a.ndim
            sl[axis] = slice(i, i + out.shape[axis])
            acc += p[tuple(sl)] * wi
        out = (acc / wsum).astype(np.float32)
    return out.astype(np.float32)


def benchmark(size=(1080, 1920)):
    """对关键算子做 GPU/CPU A/B（实测，用于决定哪条通道更快）。"""
    h, w = int(size[0]), int(size[1])
    a = np.random.default_rng(1).random((h, w, 4), dtype=np.float32)
    res = {"device": _STATE["device"], "gpu": available(), "shape": [h, w, 4], "ops": {}}

    def _time(fn, n=3):
        fn()  # warmup
        t = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t) / n * 1000.0

    # grade
    try:
        cg = _time(lambda: grade(a, 1.1, 1.05, 0.95, 1.1))
        res["ops"]["grade"] = {"ms": round(cg, 2), "speedup": 1.0}
    except Exception as e:
        res["ops"]["grade"] = {"error": str(e)}
    # blur（CPU 用 cv2 / GPU 用本模块；孤立算子常因传输而不划算——如实呈现）
    try:
        import cv2
        cb = _time(lambda: cv2.GaussianBlur(a, (0, 0), 2.0))
    except Exception as e:
        cb = None
        res["ops"]["blur_cpu_error"] = str(e)
    if available():
        try:
            gb = _time(lambda: _blur_gpu(a, *_gauss_weights(2.0)), n=2)
            res["ops"]["blur"] = {"cpu_cv2_ms": round(cb, 2) if cb else None,
                                  "gpu_ms": round(gb, 2),
                                  "speedup": round(cb / gb, 2) if (cb and gb) else None}
        except Exception as e:
            res["ops"]["blur"] = {"error": str(e)}
    else:
        res["ops"]["blur"] = {"cpu_cv2_ms": round(cb, 2) if cb else None, "gpu_ms": None}
    # bloom 融合 pass（阈值→双向模糊→叠加，数据常驻显存）：这才是 GPU 的真实收益点
    if available():
        try:
            gb2 = _time(lambda: _bloom_gpu(a, 0.7, 8.0, 0.5), n=2)
            cb2 = _time(lambda: _bloom_cpu(a, 0.7, 8.0, 0.5), n=2)
            res["ops"]["bloom_fused"] = {"cpu_ms": round(cb2, 2), "gpu_ms": round(gb2, 2),
                                         "speedup": round(cb2 / gb2, 2) if gb2 else None}
        except Exception as e:
            res["ops"]["bloom_fused"] = {"error": str(e)}
    else:
        res["ops"]["bloom_fused"] = {"cpu_ms": None, "gpu_ms": None}
    return res


def _bloom_cpu(a, threshold, sigma, intensity):
    """CPU 辉光（生产 CPU 路径口径：优先 cv2 模糊，缺则 numpy 参考）。"""
    hi = np.clip((np.clip(a, 0, 1) - float(threshold)) / (1.0 - float(threshold) + 1e-6), 0, 1)
    try:
        import cv2
        bl = cv2.GaussianBlur(hi, (0, 0), sigma)
    except Exception:
        bl = gaussian_blur_cpu_ref(hi, sigma)
    return np.clip(a + bl * float(intensity), 0.0, 1.0).astype(np.float32)
