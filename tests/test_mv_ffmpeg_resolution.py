"""`mv_engine` ffmpeg 解析回归：必须能找到**随包携带**的 ffmpeg。

背景（真实缺陷，本机实测）：
本项目**不要求 ffmpeg 在 PATH 上**——它经 `requirements.txt` 的 `imageio-ffmpeg`
随包携带，`deepseek_client.py` 早已用 `imageio_ffmpeg.get_ffmpeg_exe()` 解析。
但 `mv_engine.py` 有两处写的是裸 `"ffmpeg"`：

  1. `_ffmpeg_decode_wav` —— 静默失败 → `analyze()` 返回 `duration=0`
     → `build_shots` 无分镜 → 表现为「音频时长异常」，**音频分析整条链失效**；
  2. `encode_video` —— 抛 `[WinError 2] 系统找不到指定的文件`
     → **成片永远导不出来**（MV 的核心产物）。

修复前实测：`encode_video` 返回 `(False, "[WinError 2]...")`；
修复后实测：真出 mp4（60/60 帧，138KB）。本文件把「解析到可用 ffmpeg」和
「真能编码」两件事都钉死在回归里。
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import mv_engine as me  # noqa: E402


def _has_imageio():
    """本机是否有 imageio-ffmpeg 携带的可用二进制。"""
    try:
        import imageio_ffmpeg
        return bool(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:  # noqa: BLE001
        return False


def test_ffmpeg_bin_resolves_to_existing_executable():
    """`_ffmpeg_bin()` 必须解析到真实存在的可执行文件。

    最低要求：解析结果要么能在 PATH 上找到，要么是 imageio-ffmpeg 携带的二进制
    且文件真实存在。绝不允许直接返回裸 "ffmpeg" 而本机又没有它（那正是缺陷态）。
    """
    bin_path = me._ffmpeg_bin()
    assert bin_path, "未解析到 ffmpeg"

    if os.path.sep in bin_path or (os.path.altsep and os.path.altsep in bin_path):
        assert os.path.isfile(bin_path), f"解析到的路径不存在：{bin_path}"
    else:
        assert shutil.which(bin_path), (
            f"解析结果为裸 {bin_path!r}，但 PATH 上找不到它——"
            "这正是 encode_video 抛 WinError 2 的根因")


def test_ffmpeg_bin_is_cached_and_stable():
    """重复调用返回同一结果（避免每次渲染都重新探测）。"""
    assert me._ffmpeg_bin() == me._ffmpeg_bin()


def test_ffmpeg_bin_prefers_path_when_available(monkeypatch):
    """PATH 上有 ffmpeg 时优先用它（用户自装版本优先于内置）。"""
    monkeypatch.setattr(me, "_FFMPEG_BIN_CACHE", None)
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\fake\ffmpeg.exe")
    try:
        assert me._ffmpeg_bin() == r"C:\fake\ffmpeg.exe"
    finally:
        monkeypatch.setattr(me, "_FFMPEG_BIN_CACHE", None)


def test_ffmpeg_bin_falls_back_to_imageio_when_path_empty(monkeypatch):
    """PATH 上没有时回退 imageio-ffmpeg（本项目随包携带的那份）。"""
    monkeypatch.setattr(me, "_FFMPEG_BIN_CACHE", None)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    try:
        got = me._ffmpeg_bin()
        assert got, "PATH 无 ffmpeg 且回退也失败"
        if os.path.isfile(got):
            assert "ffmpeg" in os.path.basename(got).lower()
    finally:
        monkeypatch.setattr(me, "_FFMPEG_BIN_CACHE", None)


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") or _has_imageio()),
    reason="本机无任何可用 ffmpeg（PATH 与 imageio-ffmpeg 都没有）",
)
def test_encode_video_actually_produces_mp4(tmp_path):
    """端到端：给出真实帧序列，`encode_video` 必须真的产出非空 mp4。

    这是「ffmpeg 未解析」缺陷的直接验收项——修复前这里返回 False + WinError 2。
    """
    pytest.importorskip("PIL")
    from PIL import Image
    import numpy as np

    fd = tmp_path / "frames"
    fd.mkdir()
    for i in range(6):
        arr = np.full((48, 64, 3), fill_value=(i * 40) % 256, dtype=np.uint8)
        Image.fromarray(arr).save(fd / f"{i:06d}.jpg")

    out = str(tmp_path / "out.mp4")
    ok, detail = me.encode_video(str(fd), out, fps=5, audio="", encoder="libx264", crf=30)

    assert ok is True, f"encode_video 失败：{detail}"
    assert os.path.isfile(out), "未产出 mp4"
    assert os.path.getsize(out) > 0, "mp4 为空文件"
