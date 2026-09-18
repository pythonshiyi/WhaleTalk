"""图像流水线回归：局部重绘 / 控制图 / 精灵表 / 确定性 GIF / 混合渲染。

不触网：图生图与文生图后端以桩替换；控制图与 GIF 为纯本地确定性实现。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PIL import Image  # noqa: E402

import agent_tools.tool_codegen as tg  # noqa: E402
import deepseek_client as dc  # noqa: E402,F401  （先完整初始化工具注册表）
import permissions  # noqa: E402


@pytest.fixture
def ws():
    # 会话级 conftest 已 init 权限；此处取工作区（模块导入时尚为 None）
    return str(permissions.WORKSPACE_DIR)


def _mk(path, color=(200, 30, 30, 255), size=(48, 48)):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    Image.new("RGBA", size, color).save(path)
    return path


def _mk_half(path, size=(64, 64)):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    im = Image.new("RGB", size, (0, 0, 0))
    for x in range(size[0] // 2, size[0]):
        for y in range(size[1]):
            im.putpixel((x, y), (255, 255, 255))
    im.save(path)
    return path


def _png_bytes(color=(10, 120, 240, 255), size=(48, 48)):
    return tg._pix_bytes(Image.new("RGBA", size, color))


# ── 控制图 ────────────────────────────────────────────────────

def test_control_map_kinds(ws):
    src = _mk_half(os.path.join(ws, "ctrl_src.png"))
    for kind in ("edge", "lineart", "gray", "silhouette", "threshold"):
        out = os.path.join(ws, f"ctrl_{kind}.png")
        r = tg.control_map(src=src, kind=kind, output=out)
        assert os.path.exists(out), (kind, r)
        assert Image.open(out).mode == "L"
        assert kind in r


def test_control_map_missing_src(ws):
    assert "不存在" in tg.control_map(src=os.path.join(ws, "nope.png"), kind="edge")


# ── 精灵表 + 确定性 GIF ───────────────────────────────────────

def test_sprite_sheet_grid_and_gif(ws):
    frames = [_mk(os.path.join(ws, f"sf_{i}.png"), color=(i * 40, 60, 200, 255)) for i in range(4)]
    out = os.path.join(ws, "sheet.png")
    gif = os.path.join(ws, "sheet.gif")
    r = tg.sprite_sheet(frames=frames, output=out, layout="grid", columns=2, gif=gif, fps=6)
    assert os.path.exists(out) and os.path.exists(gif)
    assert Image.open(out).size == (96, 96)  # 2x2 单元 48x48
    assert "精灵表" in r and "GIF" in r


def test_make_gif_deterministic(ws):
    frames = [_mk(os.path.join(ws, f"gf_{i}.png"), color=(i * 60, 10, 10, 255)) for i in range(3)]
    p1 = os.path.join(ws, "det1.gif")
    p2 = os.path.join(ws, "det2.gif")
    assert "确定性 GIF" in tg.make_gif(frames=frames, output=p1, fps=5)
    tg.make_gif(frames=frames, output=p2, fps=5)
    with open(p1, "rb") as f1, open(p2, "rb") as f2:
        assert f1.read() == f2.read(), "相同输入应产出逐字节一致的确定性 GIF"


def test_make_gif_empty_frames(ws):
    assert "frames 为空" in tg.make_gif(frames=[], output=os.path.join(ws, "x.gif"))


# ── 局部重绘 ─────────────────────────────────────────────────

def test_image_inpaint_edits(ws, monkeypatch):
    src = _mk(os.path.join(ws, "inp_base.png"))
    out = os.path.join(ws, "inp_out.png")
    monkeypatch.setattr(tg, "_edit_bytes", lambda *a, **k: (_png_bytes((0, 200, 0, 255)), ""))
    r = tg.image_inpaint(image=src, prompt="加一顶帽子", region="8,8,24,24", output=out)
    assert os.path.exists(out) and "图生图" in r
    assert os.path.exists(os.path.splitext(out)[0] + ".mask.png")


def test_image_inpaint_fallback_patch(ws, monkeypatch):
    src = _mk(os.path.join(ws, "inp2_base.png"))
    out = os.path.join(ws, "inp2_out.png")
    monkeypatch.setattr(tg, "_edit_bytes", lambda *a, **k: (None, "no edits"))
    monkeypatch.setattr(tg, "_gen_bytes", lambda prompt, size: (_png_bytes((250, 220, 0, 255), (32, 32)), ""))
    r = tg.image_inpaint(image=src, prompt="换颜色", region="10,10,20,20", output=out)
    assert os.path.exists(out) and "补丁合成" in r


def test_image_inpaint_requires_region_or_mask(ws):
    src = _mk(os.path.join(ws, "inp3_base.png"))
    assert "region" in tg.image_inpaint(image=src, prompt="x", output=os.path.join(ws, "inp3.png"))


# ── 混合渲染 ─────────────────────────────────────────────────

def test_image_hybrid_edits(ws, monkeypatch):
    base = _mk(os.path.join(ws, "hyb_base.png"))
    out = os.path.join(ws, "hyb_out.png")
    monkeypatch.setattr(tg, "_edit_bytes", lambda *a, **k: (_png_bytes((30, 30, 30, 255), (48, 48)), ""))
    r = tg.image_hybrid(brief="写实质感", base=base, output=out, strength=0.6)
    assert os.path.exists(out) and "混合渲染" in r


def test_image_hybrid_degrades_without_edits(ws, monkeypatch):
    base = _mk(os.path.join(ws, "hyb2_base.png"))
    out = os.path.join(ws, "hyb2_out.png")
    monkeypatch.setattr(tg, "_edit_bytes", lambda *a, **k: (None, "edits 404"))
    r = tg.image_hybrid(brief="写实质感", base=base, output=out)
    assert os.path.exists(out) and "降级交付结构底图" in r


def test_image_hybrid_no_client_without_base(ws, monkeypatch):
    monkeypatch.setattr(tg, "get_active_client", lambda: None)
    assert "没有可用客户端" in tg.image_hybrid(brief="x", output=os.path.join(ws, "hyb3.png"))
