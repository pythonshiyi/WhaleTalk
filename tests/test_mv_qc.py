"""mv_qc（客观质量自检）回归：动感三判据 / 风格指标 / 死白 / 音画同步。"""
import numpy as np

import mv_qc as Q


def _moving(n=14, h=48, w=64, step=0.02):
    """合成一段连续运动的帧（缓慢右移的方块，帧间变化连续）。"""
    frames = []
    for i in range(n):
        a = np.zeros((h, w, 3), np.float32)
        x = int(w * 0.1 + i * w * step)
        a[h // 3:2 * h // 3, x:x + 10, :] = 1.0
        frames.append(a)
    return frames


def test_frame_diff_scaled_255():
    a = np.zeros((10, 10, 3), np.float32)
    b = np.zeros((10, 10, 3), np.float32)
    b[:] = 0.1
    assert Q.frame_diff(a, b) == Q.frame_diff(b, a)
    assert abs(Q.frame_diff(a, b) - 25.5) < 1e-3


def test_structure_corr_identical_and_independent():
    a = np.random.default_rng(1).random((40, 40, 3), dtype=np.float32)
    assert Q.structure_corr(a, a) == 1.0
    b = np.random.default_rng(2).random((40, 40, 3), dtype=np.float32)
    assert abs(Q.structure_corr(a, b)) < 0.6


def test_assert_dynamic_pass_and_fail():
    ok = Q.assert_dynamic(_moving())
    assert ok["ok"], ok
    static = [np.zeros((48, 64, 3), np.float32) for _ in range(8)]
    bad = Q.assert_dynamic(static)
    assert not bad["ok"] and "静态" in bad["reason"]


def test_grain_ratio_adaptive_small_input():
    assert Q.grain_ratio([np.zeros((8, 8, 3), np.float32) for _ in range(3)]) == 1.0
    assert 0.0 <= Q.grain_ratio(_moving(8)) <= 5.0


def test_qc_frames_flags():
    imgs = [np.full((40, 40, 3), 0.15, np.float32) for _ in range(3)]
    imgs[0][10:20, 10:20, 0] = 1.0  # 一点霓虹与结构
    res = Q.qc_frames(imgs)
    assert "PASS_dead_white" in res and "PASS_std" in res


def test_dead_white_ratio():
    a = np.ones((20, 20, 3), np.float32)
    assert Q.dead_white_ratio(a) == 1.0
    b = np.zeros((20, 20, 3), np.float32)
    assert Q.dead_white_ratio(b) == 0.0


def test_audio_sync_missing_files():
    r = Q.audio_sync("__no_such__.wav", "__no_such__.wav")
    assert r["ok"] is False
