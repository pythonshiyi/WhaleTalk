"""mv_tex（题材无关质感积木）回归：噪声/模糊/霓虹/颗粒/安全 IO/字体。

不依赖 cv2（缺失自动回退 PIL/numpy），保证 CI 可跑。
"""
import numpy as np
import pytest

import mv_tex as T


def test_fbm_shape_and_deterministic():
    a = T.fbm(48, 64, octaves=4, seed=3)
    b = T.fbm(48, 64, octaves=4, seed=3)
    assert a.shape == (48, 64)
    assert np.allclose(a, b)
    assert a.min() >= 0.0 and a.max() <= 1.0 + 1e-6


def test_blur_2d_and_3d():
    a = np.zeros((32, 40), np.float32)
    a[16, 20] = 1.0
    b = T.blur(a, 2.0)
    assert b.shape == a.shape and b.max() < 1.0 and b.sum() > 0
    rgb = np.zeros((32, 40, 3), np.float32)
    rgb[16, 20] = 1.0
    assert T.blur(rgb, 2.0).shape == rgb.shape


def test_resize():
    a = np.zeros((10, 20), np.float32)
    assert T.resize(a, 40, 80).shape == (40, 80)


def test_neon_glow_bounded():
    m = np.zeros((40, 60), np.float32)
    m[20, 30] = 1.0
    g = T.neon_glow(m, sigma=6.0)
    assert g.shape == m.shape and g.max() > 0


def test_film_grain_time_continuous():
    """相邻帧颗粒必须高度相关，长间隔才明显不同（否则掩盖运动）。"""
    a0 = T.film_grain(80, 120, 30)[:, :, 0]
    a1 = T.film_grain(80, 120, 31)[:, :, 0]
    far = T.film_grain(80, 120, 300)[:, :, 0]
    d_near = float(np.abs(a0 - a1).mean())
    d_far = float(np.abs(a0 - far).mean())
    assert d_near < d_far


def test_highlight_rolloff_keeps_range():
    a = np.linspace(0, 1.5, 200, dtype=np.float32)
    y = T.highlight_rolloff(a, 0.8)
    assert y.max() <= 1.0 + 1e-6
    assert y[0] == pytest.approx(0.0)


def test_get_palette_fallback():
    assert "night" in T.get_palette("qinghua")
    assert T.get_palette("nope") == T.PALETTES["default"]


def test_imwrite_imread_roundtrip_unicode(tmp_path):
    d = tmp_path / "中文目录"
    d.mkdir()
    p = str(d / "帧_001.jpg")
    img = np.zeros((24, 32, 3), np.float32)
    img[:, :, 0] = 0.8
    assert T.imwrite_safe(p, img, 90)
    back = T.imread_safe(p)
    assert back is not None and back.shape == (24, 32, 3)
    assert back[:, :, 0].mean() > 0.5


def test_gauss_spot_peaks_at_center():
    g = T.gauss_spot(50, 60, 30, 25, 5, 5)
    assert g.shape == (50, 60)
    assert g[25, 30] == pytest.approx(1.0, abs=1e-4)
    assert g[0, 0] < 0.1


def test_preflight_fonts_returns_list():
    assert isinstance(T.preflight_fonts(["测试", "ABC"]), list)


def test_mountain_wire_nonempty():
    m = T.mountain_wire(120, 200, 1.0, layers=3)
    assert m.shape == (120, 200) and m.max() > 0
