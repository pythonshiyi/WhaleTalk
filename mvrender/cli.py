"""FastMV CLI：framewise 渲染 / 对比 / 出片。

用法（在曲目工程目录，或指定 --root）：
    python -m mvrender.cli probe
    python -m mvrender.cli compare <root> <t> [...]      # CPU vs GPU 逐像素
    python -m mvrender.cli timing <root> [n]
    python -m mvrender.cli preview <root> <outdir> <t> [...]
    python -m mvrender.cli worker <root> <a> <b> <out.mp4>

镜头级增量缓存（迭代速度核心）：
    python -m mvrender.cli cache <root> status
    python -m mvrender.cli cache <root> render <shot> [end_shot]
    python -m mvrender.cli cache <root> invalidate <shot[.dep]>
    python -m mvrender.cli cache <root> clear
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
import time

import numpy as np


def load_project(root):
    """加载曲目工程：root/scripts 下有 engine/shots 即按旧契约接入。"""
    root = os.path.abspath(root)
    scripts = os.path.join(root, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("_proj_shots", os.path.join(scripts, "shots.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return root, scripts, mod


# 镜头依赖标签（引擎侧推断，不改创意代码）：
# 用于 `cache invalidate "A8.rain"` 这类细粒度失效——只重渲引用该元素的镜头。
# 键为镜头名前缀或函数名片段，值为该镜头依赖的元素标签。
SHOT_DEPS_HINTS = {
    "rain": ("a1", "rain_window", "p1", "window_rain", "f2"),
    "lamps": ("street_lamp", "c1_online", "c2_unfinished", "c5", "c6", "d1"),
    "moon": ("b3", "moon", "c4"),
    "stars": ("b4", "stars"),
    "phone": ("phone", "a2", "b2", "p3", "chat_screen"),
    "leaf": ("leaf", "d3", "spring"),
    "mist": ("mist",),
}


def infer_deps(shot_name, fn_name):
    """按镜头名 / 函数名推断依赖标签（供细粒度失效）。"""
    hay = f"{shot_name}|{fn_name}".lower()
    deps = []
    for tag, keys in SHOT_DEPS_HINTS.items():
        if any(k.lower() in hay for k in keys):
            deps.append(tag)
    return tuple(deps)


def build_renderers(root, use_gpu=True, accelerate=True):
    """构建 CPU/GPU 渲染器。

    accelerate=True 时启用元素层 GPU 接管（glow_layer / mist / 大核高斯降采样），
    这是「元素绘制占 100%」瓶颈的主要解法；出问题可用 MV_ACCEL=0 回退。
    """
    root, scripts, SH = load_project(root)
    if accelerate and os.environ.get("MV_ACCEL", "1") != "0":
        try:
            from .gpu import element_ops as _eo
            _eo.install()
        except Exception as e:  # noqa: BLE001
            print("元素层 GPU 接管不可用，退回 CPU：", e)
    import engine as G

    from .core.renderer import Renderer as CpuRenderer
    from .core.renderer_gpu import GpuRenderer
    tl, bt = G.load_data()
    # 用 mvrender 自己的 Ctx，而不是宿主工程的 G.Ctx：
    # 宿主 G.Ctx 的构造函数把分辨率硬编码（三更帖 engine 里 W,H=1080,1920 写死），
    # 且各曲实现细节不同（旧城慢无 who 字段）。mvrender.Ctx 已做字段兼容，
    # 并对 line_at 等接口做了兜底。
    from .core.project import Ctx as MvCtx
    w, h = (tl.get("resolution") or [1080, 1920])[:2]
    w, h = int(w), int(h)
    ctx = MvCtx(tl, bt, w=w, h=h)
    defs = SH.build_shots(tl)
    from .core.project import Shot as MvShot  # noqa: E402
    shots = []
    for d in defs:
        if isinstance(d, MvShot):
            shots.append(d)
            continue
        if hasattr(d, "fn") and hasattr(d, "t0"):
            # 宿主工程的 engine.Shot（旧契约）：直接搬字段，避免按位置解包
            shots.append(MvShot(d.t0, d.t1, d.name, d.fn, cam=d.cam,
                                tin=d.tin, tout=d.tout,
                                tin_kind=d.tin_kind, tout_kind=d.tout_kind,
                                day=d.day,
                                deps=getattr(d, "deps", None) or infer_deps(
                                    d.name, getattr(d.fn, "__name__", ""))))
            continue
        # 元组定义：兼容 9 元（旧：无 day）与 10/11 元（新：带 day / deps）
        # 【踩坑】旧工程釉下青/旧城慢是 9 元组（无 day 位），硬按 d[:10] 解包会
        # ValueError: not enough values to unpack (expected 10, got 9)。
        vals = list(d) + [None] * (11 - len(d))
        t0, t1, name, fn, cam, tin, tout, tik, tok, day = vals[:10]
        deps = vals[10] if len(d) > 10 else None
        if deps is None:
            deps = infer_deps(name, getattr(fn, "__name__", ""))
        shots.append(MvShot(t0, t1, name, fn, cam=cam, tin=tin, tout=tout,
                            tin_kind=tik, tout_kind=tok,
                            day=bool(day) if day is not None else False, deps=deps))
    # 歌词渲染器：优先用 mvrender 自带实现（尺寸参数化、支持任意分辨率），
    # 宿主工程的 G.LyricRenderer 是 1080x1920 写死的旧版：
    #   · 其 render() 未必接受 day= 关键字 → TypeError（釉下青实测）
    #   · 硬编码尺寸会在非 1080x1920 工程上错位
    from .core.lyrics import LyricRenderer as MvLyrics
    _lines = ctx.lines
    lyr_c = MvLyrics(_lines, w=w, h=h)
    lyr_g = MvLyrics(_lines, w=w, h=h)
    cpu = CpuRenderer(shots, ctx, lyr_c, w=w, h=h, use_gpu=False)
    gpu = GpuRenderer(shots, ctx, lyr_g, w=w, h=h) if use_gpu else None
    return tl, ctx, shots, cpu, gpu


def cmd_compare(root, times):
    tl, ctx, shots, cpu, gpu = build_renderers(root)
    print(f"{'t':>9} {'shot':<18} {'CPU mean':>9} {'GPU mean':>9} {'MAE':>8} {'max':>5} {'>2%':>7}")
    worst = 0
    for t in times:
        a = cpu.to_uint8(cpu.render(t)).astype(np.int16)
        b = gpu.render_u8(t).astype(np.int16)
        d = np.abs(a - b)
        worst = max(worst, int(d.max()))
        sh = [s for s in shots if s.t0 <= t < s.t1]
        print(f"{t:>9.2f} {(sh[0].name if sh else '-'):<18} {a.mean():>9.2f} "
              f"{b.mean():>9.2f} {d.mean():>8.3f} {d.max():>5} {100 * (d > 2).mean():>6.3f}%")
    print(f"\n最差 max|diff| = {worst}")
    return worst


def cmd_timing(root, n=12):
    tl, ctx, shots, cpu, gpu = build_renderers(root)
    idx = np.linspace(0, len(shots) - 1, min(n, len(shots))).astype(int)
    ts = [shots[i].t0 + shots[i].dur * 0.5 for i in idx]
    cpu.to_uint8(cpu.render(ts[0]))
    gpu.render_u8(ts[0])
    for tag, fn in (("CPU", lambda t: cpu.to_uint8(cpu.render(t))),
                    ("GPU", lambda t: gpu.render_u8(t))):
        t0 = time.time()
        for t in ts:
            fn(t)
        el = (time.time() - t0) / len(ts) * 1000
        print(f"  {tag}  {el:7.1f} ms/帧  ({len(ts)} 帧，全管线)")


def cmd_preview(root, outdir, times):
    import cv2
    tl, ctx, shots, cpu, gpu = build_renderers(root)
    os.makedirs(outdir, exist_ok=True)
    for t in times:
        t0 = time.time()
        img = gpu.render_u8(t)
        p = os.path.join(outdir, f"gpu_{t:07.2f}.png")
        ok, buf = cv2.imencode(".png", img)
        buf.tofile(p)
        print(f"{t:7.2f}s -> {os.path.basename(p)}  {time.time()-t0:.3f}s  mean {img.mean():.1f}")


def cmd_cache(root, action, arg=None, arg2=None):
    """镜头级缓存操作：status / render <shot> [end] / invalidate <key> / clear"""
    from .core.shotcache import ShotCache
    tl, ctx, shots, cpu, gpu = build_renderers(root)
    fps, res = int(tl["fps"]), tuple(tl["resolution"])
    cache = ShotCache(root)
    if action == "status":
        st = cache.status()
        print(f"缓存：{st['shots']} 个镜头 · {st['bytes'] / 1048576:.1f} MB")
        for s in shots:
            print(f"  {'✓' if cache.has(s, fps, res) else '·'} {s.name}")
        return
    if action == "clear":
        cache.invalidate_all()
        print("已清空缓存")
        return
    if action == "invalidate":
        hit = cache.invalidate(arg or "", shots)
        print("已失效：", hit or "（无匹配）")
        return
    if action == "render":
        if not arg:
            print("用法：cache render <shot> [end_shot]")
            return
        t0 = time.time()
        out = cache.render_range(gpu, arg, arg2, fps, res)
        hit = sum(1 for _f, c in out.values() if c)
        print(f"渲染 {len(out)} 镜（缓存命中 {hit}）· {time.time() - t0:.1f}s")
        return
    print(__doc__)


def cmd_worker(root, a, b, out):
    tl, ctx, shots, cpu, gpu = build_renderers(root)
    fps = int(tl["fps"])
    w, h = tl["resolution"]
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
         "-pix_fmt", "yuv420p", out],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    a, b = int(a), int(b)
    t0 = time.time()
    try:
        for i in range(a, b):
            proc.stdin.write(gpu.render_u8(i / fps).tobytes())
            if (i - a) % 60 == 0:
                el = time.time() - t0
                d = i - a + 1
                print(f"[{i}/{b}] {el/d*1000:.0f}ms/f ETA {el/d*(b-i-1)/60:.1f}min", flush=True)
    except BrokenPipeError:
        print("pipe broken", flush=True)
    finally:
        with contextlib.suppress(Exception):
            proc.stdin.close()
        proc.wait()
    print(f"worker done {a}-{b} rc={proc.returncode} {time.time()-t0:.1f}s", flush=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 0
    m = argv[0]
    if m == "probe":
        from .gpu import Runtime
        Runtime.probe()
        return 0
    root = argv[1]
    if m == "compare":
        cmd_compare(root, [float(x) for x in argv[2:]] or [8.0, 55.0, 105.0])
    elif m == "timing":
        cmd_timing(root, int(argv[2]) if len(argv) > 2 else 12)
    elif m == "preview":
        cmd_preview(root, argv[2], [float(x) for x in argv[3:]])
    elif m == "worker":
        cmd_worker(root, argv[2], argv[3], argv[4])
    elif m == "cache":
        cmd_cache(root, argv[2] if len(argv) > 2 else "status",
                  argv[3] if len(argv) > 3 else None,
                  argv[4] if len(argv) > 4 else None)
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
