"""mv_qc —— 程序化影像的客观质量自检（不靠主观评价，用数字判定）。

沉淀自《山河砚》MV 的三类判据：

1. **动感自检**（证明"不是幻灯片"）：三重判据 —— 帧差 / 结构相关 / 颗粒比。
   · 采样点**必须落在镜头内部**（避开交叉转场），否则会把换镜跳变误判为动态异常。
2. **画面风格 QC**（客观指纹）：霓虹饱和度 / 冷暗底 / 结构边缘密度 / 对比度 / 死白占比。
3. **音画同步**：逐样本相关 + 包络相关 + 偏移搜索（避免被音乐动态掩盖）。

设计：纯函数 + 可选读盘；只依赖 numpy（cv2 可选加速），可在 CI 无 GPU 环境跑。
"""
from __future__ import annotations

import os
import subprocess

import numpy as np

import mv_tex as T


def _gray01(img: np.ndarray) -> np.ndarray:
    """RGB(0..1) → 灰度(0..1)。"""
    a = np.clip(np.asarray(img, np.float32), 0, 1)
    if a.ndim == 2:
        return a
    return 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]


def frame_diff(a: np.ndarray, b: np.ndarray) -> float:
    """平均像素帧差（**归一到 0..255 量纲**，阈值 >1.0 表示画面确实在变）。"""
    return float(np.mean(np.abs(np.asarray(a, np.float32) - np.asarray(b, np.float32))) * 255.0)


def structure_corr(a: np.ndarray, b: np.ndarray, blocks: int = 8) -> float:
    """降采样块均值后的相关系数：对逐像素噪声不敏感，只看结构是否连续。

    连续运动 → 接近 +1；每帧完全独立 → 接近 0。
    """
    ga, gb = _gray01(a), _gray01(b)
    h, w = ga.shape
    bh, bw = max(1, h // blocks), max(1, w // blocks)
    hh, ww = (h // bh) * bh, (w // bw) * bw
    va = ga[:hh, :ww].reshape(h // bh, bh, w // bw, bw).mean(axis=(1, 3)).ravel()
    vb = gb[:hh, :ww].reshape(h // bh, bh, w // bw, bw).mean(axis=(1, 3)).ravel()
    va = va - va.mean()
    vb = vb - vb.mean()
    den = float(np.sqrt((va * va).sum()) * np.sqrt((vb * vb).sum()))
    if den < 1e-9:
        return 1.0
    return float(np.clip((va * vb).sum() / den, -1.0, 1.0))


def grain_ratio(frames: list, lag: int = 10) -> float:
    """相邻帧差 / 长间隔帧差。≈1 说明噪声主导，<0.85 才健康（帧差来自真实运动）。

    lag 会按实际帧数自适应收窄（采样帧少时仍能给出有效判据）。
    """
    n = len(frames or [])
    if n < 4:
        return 1.0
    lag = int(min(lag, max(1, (n - 1) // 2)))
    near = float(np.mean([frame_diff(frames[i], frames[i + 1]) for i in range(n - 1)]))
    far = float(np.mean([frame_diff(frames[i], frames[i + lag]) for i in range(n - lag)]))
    if far < 1e-6:
        return 1.0
    return float(np.clip(near / far, 0.0, 5.0))


def assert_dynamic(frames: list, min_diff: float = 1.0, max_grain: float = 0.85) -> dict:
    """动感三重判据。frames 为连续帧（须落在**同一镜头内部**）。返回判定字典。"""
    if not frames or len(frames) < 2:
        return {"ok": False, "avg_diff": 0.0, "corr": 0.0, "grain_ratio": 1.0,
                "reason": "帧数不足（至少 2 帧）"}
    avg = float(np.mean([frame_diff(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]))
    corr = float(np.mean([structure_corr(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]))
    gr = grain_ratio(frames)
    reasons = []
    if avg <= min_diff:
        reasons.append(f"帧差 {avg:.3f} ≤ {min_diff}（疑似静态）")
    if corr <= 0.9:
        reasons.append(f"结构相关 {corr:+.3f} ≤ 0.9（变化不连续，疑似跳变/换镜采样）")
    # 颗粒比只在结构不连续时才作判据：corr≈1 说明是连续运动（快速运动会让
    # near≈far，属正常），此时颗粒比不具噪声含义，避免误报。
    if gr >= max_grain and corr < 0.98:
        reasons.append(f"颗粒比 {gr:.3f} ≥ {max_grain}（帧差被噪声主导）")
    return {"ok": not reasons, "avg_diff": round(avg, 3), "corr": round(corr, 3),
            "grain_ratio": round(gr, 3), "reason": "；".join(reasons)}


# ── 风格 QC ──────────────────────────────────────────────────────────────
def neon_ratio(img: np.ndarray) -> float:
    """高饱和霓虹像素占比（赛博证据）。"""
    rgb = np.clip(np.asarray(img, np.float32), 0, 1)
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    sat = (mx - mn) / (mx + 1e-6)
    return float(((sat > 0.42) & (mx > 0.35)).mean())


def cold_dark(img: np.ndarray) -> tuple:
    """冷暗底：返回 (平均亮度, 冷偏置=蓝-红)。"""
    rgb = np.clip(np.asarray(img, np.float32), 0, 1)
    return float(rgb.mean()), float(rgb[:, :, 2].mean() - rgb[:, :, 0].mean())


def edge_density(img: np.ndarray) -> float:
    """边缘密度（笔画/线框/文字密度 —— 结构可辨的量化指纹）。"""
    g = _gray01(img)
    gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
    gy = np.abs(np.diff(g, axis=0, prepend=g[:1, :]))
    mag = np.sqrt(gx * gx + gy * gy)
    return float((mag > 0.08).mean())


def dead_white_ratio(img: np.ndarray, thr: float = 0.995) -> float:
    """死白像素占比（过曝判据，健康 < 1%）。"""
    rgb = np.clip(np.asarray(img, np.float32), 0, 1)
    return float((rgb.min(axis=2) > thr).mean())


def qc_frames(images: list) -> dict:
    """对一组帧算风格 QC 指标 + 通过标记。images 为 RGB(0..1) 列表。"""
    imgs = [np.asarray(x, np.float32) for x in images if x is not None]
    if not imgs:
        return {"ok": False, "error": "无可用帧"}
    res = {
        "neon_ratio": round(float(np.mean([neon_ratio(x) for x in imgs])), 4),
        "mean_lum": round(float(np.mean([cold_dark(x)[0] for x in imgs])), 4),
        "cold_bias": round(float(np.mean([cold_dark(x)[1] for x in imgs])), 4),
        "edge_density": round(float(np.mean([edge_density(x) for x in imgs])), 4),
        "std": round(float(np.mean([np.std(x) for x in imgs])), 4),
        "dead_white": round(float(np.mean([dead_white_ratio(x) for x in imgs])), 5),
        "n": len(imgs),
    }
    res["PASS_neon"] = res["neon_ratio"] > 0.02
    res["PASS_lum"] = res["mean_lum"] < 0.45
    res["PASS_edge"] = res["edge_density"] > 0.002
    res["PASS_std"] = res["std"] > 0.04
    res["PASS_dead_white"] = res["dead_white"] < 0.01
    res["ok"] = all(res[k] for k in ("PASS_neon", "PASS_edge", "PASS_std", "PASS_dead_white"))
    return res


def qc_frame_files(frames_dir: str, n_sample: int = 40) -> dict:
    """对帧目录抽样做风格 QC（Unicode 路径安全）。"""
    try:
        files = sorted(f for f in os.listdir(frames_dir) if f.lower().endswith((".jpg", ".png")))
    except OSError as e:
        return {"ok": False, "error": f"帧目录不可读: {e}"}
    if not files:
        return {"ok": False, "error": "帧目录为空"}
    idx = np.linspace(0, len(files) - 1, min(n_sample, len(files))).astype(int)
    imgs = []
    for i in idx:
        a = T.imread_safe(os.path.join(frames_dir, files[i]))
        if a is not None:
            imgs.append(a)
    if not imgs:
        return {"ok": False, "error": "帧读取失败（路径或编码问题）"}
    out = qc_frames(imgs)
    out["total_frames"] = len(files)
    return out


# ── 音画同步 ─────────────────────────────────────────────────────────────
def _decode_mono(path: str, sr: int = 8000):
    """用 ffmpeg 解码为单声道 float32（失败返回 None）。"""
    import tempfile
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-i", str(path), "-ac", "1", "-ar", str(sr), wav],
                           capture_output=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not os.path.isfile(wav) or os.path.getsize(wav) <= 44:
            return None
        import wave
        with wave.open(wav, "rb") as w:
            n = w.getnframes()
            data = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
        return data
    except Exception:  # noqa: BLE001
        return None
    finally:
        import contextlib
        with contextlib.suppress(OSError):
            os.remove(wav)


def _envelope(x: np.ndarray, hop: int = 80) -> np.ndarray:
    n = len(x) // hop
    if n < 2:
        return np.zeros(0, np.float32)
    return np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1))


def audio_sync(audio_a: str, audio_b: str, max_offset_s: float = 0.5) -> dict:
    """两段音频的同步核对：逐样本相关 + 包络相关 + 最佳偏移搜索。

    实测教训：只做"逐秒 RMS 相关"会被音乐动态掩盖（0.49 误判不同步）；
    正解是逐样本相关 + 包络相关 + 偏移搜索三者结合。
    """
    sr = 8000
    a = _decode_mono(audio_a, sr)
    b = _decode_mono(audio_b, sr)
    if a is None or b is None or len(a) < sr or len(b) < sr:
        return {"ok": False, "error": "音频解码失败或无 ffmpeg"}
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]

    def _corr(x, y):
        x = x - x.mean()
        y = y - y.mean()
        den = float(np.sqrt((x * x).sum()) * np.sqrt((y * y).sum()))
        return float((x * y).sum() / den) if den > 1e-9 else 0.0

    sample_corr = _corr(a, b)
    ea, eb = _envelope(a), _envelope(b)
    m = min(len(ea), len(eb))
    env_corr = _corr(ea[:m], eb[:m]) if m > 2 else 0.0
    # 偏移搜索（±max_offset_s）
    best_off, best_c = 0, sample_corr
    max_lag = int(max_offset_s * sr)
    step = max(1, sr // 200)
    for lag in range(-max_lag, max_lag + 1, step):
        c = _corr(a[lag:], b[:n - lag]) if lag >= 0 else _corr(a[:n + lag], b[-lag:])
        if c > best_c:
            best_c, best_off = c, lag
    dur_a, dur_b = len(a) / sr, len(b) / sr
    ok = sample_corr > 0.9 and abs(dur_a - dur_b) <= 0.5
    return {"ok": ok, "sample_corr": round(sample_corr, 4), "env_corr": round(env_corr, 4),
            "best_offset_s": round(best_off / sr, 4), "dur_a": round(dur_a, 3),
            "dur_b": round(dur_b, 3), "error": "" if ok else "相关偏低或时长不一致"}
