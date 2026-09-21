"""低分辨率极速档 + 风格包 回归测试。

全部题材无关：用合成镜头 / 合成 Ctx，不依赖任何外部工程与音频。
"""
import numpy as np
import pytest

from mvrender.lowres import LowResCanvas, LowResRenderer, get_style, plan, style
from mvrender.lowres.synth import SyntheticCtx, synthetic_shots


def test_plan_auto_picks_largest_logical_canvas_within_budget():
    p = plan(1920, 1080)
    assert (p.lw, p.lh, p.scale) == (480, 270, 4)
    assert (p.out_w, p.out_h) == (1920, 1080)
    assert p.pixels <= 480 * 270
    assert p.speedup > 15.0


def test_plan_explicit_scale_and_full_cover():
    p = plan(1920, 1080, scale=8)
    assert (p.lw, p.lh) == (240, 135)
    assert p.out_w >= 1920 and p.out_h >= 1080
    p2 = plan(1000, 1000, scale=3)
    assert p2.out_w >= 1000 and p2.out_h >= 1000
    p3 = plan(1920, 1080, pixel_budget=1000)   # 预算过小 → 退到最快档
    assert p3.scale == 8


def test_canvas_primitives_and_integer_upscale():
    c = LowResCanvas(64, 48)
    c.fill((0, 0, 0))
    assert c.buf.dtype == np.float32 and c.buf.shape == (48, 64, 3)
    c.fill_rects([(4, 4, 20, 20, 1.0)], color=(255, 255, 255))
    c.draw_lines([(0, 0, 63, 47, 1, 1.0)], color=(0, 255, 0))
    c.fill_circles([(40, 30, 6, 1.0)], color=(0, 0, 255))
    assert c.buf[10, 10].tolist() == [255, 255, 255]
    full = c.to_full(4)
    assert full.shape == (48 * 4, 64 * 4, 3) and full.dtype == np.uint8
    assert (full[2 * 4:3 * 4, 30 * 4:31 * 4] == 0).all()   # 空白逻辑像素=4×4 方块


def test_style_registry_and_unknown_style_raises():
    ids = style.names()
    assert "pixel" in ids and "ink" in ids
    assert get_style("ink").id == "ink"
    with pytest.raises(KeyError):
        get_style("no_such_style")


def test_renderer_contract_shape_and_stateless_determinism():
    ctx = SyntheticCtx()
    shots = synthetic_shots(ctx)
    r = LowResRenderer(shots, ctx, style="pixel", w=1920, h=1080)
    u = r.render_u8(10 / 30.0)
    assert u.dtype == np.uint8 and u.shape == (1080, 1920, 3)
    assert r.render(10 / 30.0).dtype == np.float32
    a = r.render_u8(2.0)
    b = r.render_u8(2.0)
    assert np.array_equal(a, b)                  # 无状态：同帧同结果


def test_swapping_style_changes_frame_without_touching_shots():
    ctx = SyntheticCtx()
    shots = synthetic_shots(ctx)
    pix = LowResRenderer(shots, ctx, style="pixel", w=960, h=540).render_u8(1.0)
    ink = LowResRenderer(shots, ctx, style="ink", w=960, h=540).render_u8(1.0)
    assert pix.shape == ink.shape
    assert not np.array_equal(pix, ink)          # 同镜头、换风格 → 画面不同


def test_ink_paper_is_shot_invariant_and_cached():
    ctx = SyntheticCtx()
    shots = synthetic_shots(ctx)
    r = LowResRenderer(shots, ctx, style="ink", w=1920, h=1080)
    r.render_u8(0.5)
    n = len(r._bg_cache)
    r.render_u8(0.6)                             # 同镜 → 底色复用
    assert len(r._bg_cache) == n
    s = shots[0]
    b1 = r.style.background(r.lw, r.lh, ctx, s)
    b2 = r.style.background(r.lw, r.lh, ctx, s)
    assert np.array_equal(b1, b2)                # 镜头内不变量：可复现
