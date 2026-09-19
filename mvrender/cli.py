"""FastMV CLI：framewise 渲染 / 对比 / 出片。

用法（在曲目工程目录，或指定 --root）：
    python -m mvrender.cli probe
    python -m mvrender.cli compare <root> <t> [...]      # CPU vs GPU 逐像素
    python -m mvrender.cli timing <root> [n]
    python -m mvrender.cli preview <root> <outdir> <t> [...]
    python -m mvrender.cli worker <root> <a> <b> <out.mp4>
"""
from __future__ import annotations

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
    ctx = G.Ctx(tl, bt)
    defs = SH.build_shots(tl)
    shots = []
    for d in defs:
        t0, t1, name, fn, cam, tin, tout, tik, tok, day = d[:10]
        deps = d[10] if len(d) > 10 else None
        shots.append(G.Shot(t0, t1, name, fn, cam=cam, tin=tin, tout=tout,
                            tin_kind=tik, tout_kind=tok, day=day))
    lyr_c = G.LyricRenderer(tl["lines"])
    lyr_g = G.LyricRenderer(tl["lines"])
    w, h = tl["resolution"]
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
        try:
            proc.stdin.close()
        except Exception:
            pass
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
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
