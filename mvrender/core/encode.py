"""视频编码器选择：优先硬件编码（GPU），回退 CPU。

为什么重要（本机实测，1080x1920，随机帧）
----------------------------------------
    libx264 medium     71.6 ms/帧  (14 fps)   ← 纯 CPU，占满核
    libx264 veryfast   21.7 ms/帧  (46 fps)   ← 纯 CPU
    h264_amf            5.9 ms/帧 (169 fps)   ← AMD 硬件编码（GPU）
    hevc_amf            5.4 ms/帧 (186 fps)
    av1_amf             5.3 ms/帧 (187 fps)

「GPU 3% / CPU 90%」的一大来源就是**编码一直在 CPU 上跑**：改用 AMF 后编码
阶段完全交给 GPU（快 12x 且几乎不占 CPU），CPU 得以腾出来做元素绘制。

编码器优先级：AMF（AMD） > NVENC（NVIDIA） > QSV（Intel） > libx264（CPU）。
`quality`/`bitrate` 为 AMF 参数；`crf`/`preset` 为 libx264 参数。
"""
from __future__ import annotations

import functools
import subprocess

_HW_ORDER = ("h264_amf", "h264_nvenc", "h264_qsv")


@functools.lru_cache(maxsize=1)
def available_encoders():
    """列出 ffmpeg 支持的硬件编码器（结果缓存）。"""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=15)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception:  # noqa: BLE001
        return set()
    return {e for e in _HW_ORDER if e in out}


def pick(prefer=None):
    """选一个编码器名：prefer 指定则优先，否则按 AMF>NVENC>QSV>libx264。"""
    avail = available_encoders()
    if prefer and (prefer in avail or prefer == "libx264"):
        return prefer
    for e in _HW_ORDER:
        if e in avail:
            return e
    return "libx264"


def video_args(encoder=None, quality="balanced", bitrate="12M",
               crf=18, preset="veryfast"):
    """返回 ffmpeg 的视频编码参数列表（含 `-c:v`）。

    encoder: None=自动（硬件优先）；"libx264"=强制 CPU；或具体编码器名。
    """
    enc = pick(encoder)
    if enc == "h264_amf":
        # AMF 不认 -preset；用 -quality（speed/balanced/quality）+ 显式码率
        return ["-c:v", enc, "-quality", quality, "-b:v", bitrate,
                "-rc", "cbr", "-pix_fmt", "yuv420p"]
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", "p5", "-b:v", bitrate,
                "-pix_fmt", "yuv420p"]
    if enc == "h264_qsv":
        return ["-c:v", enc, "-global_quality", str(crf), "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p"]
