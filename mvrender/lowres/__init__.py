"""mvrender.lowres —— 底座低分辨率极速档 + 风格包机制。

定位
----
不是第四套引擎，而是 `mvrender` 底座缺失的一个档位：
底座 `core.renderer.Renderer(w, h, ...)` 与 `API.md` 早已声明「分辨率可按需降」，
本包把这个能力落成可复用的实现，并补上**题材无关**的风格包协议。

    plan.py             目标尺寸 → 逻辑画布 + 整数放大倍数（自动求最优）
    canvas.py           逻辑画布（低分辨率绘制原语，对齐底座 / GPU 图元格式）
    style.py            风格包协议（题材无关的关键）
    styles/pixel.py     像素风格包
    styles/ink.py       水墨风格包
    renderer_lowres.py  低分辨率渲染器（实现底座 render 契约）
    synth.py            合成镜头 / Ctx（基准与自检用，题材无关）

用法：
    from mvrender.lowres import LowResRenderer
    r = LowResRenderer(shots, ctx, w=1080, h=1920, scale=4, style="ink")
    frame_u8 = r.render_u8(t)
"""
from __future__ import annotations

from . import style
from .canvas import LowResCanvas, upscale
from .plan import Plan, plan
from .renderer_lowres import LowResRenderer
from .style import StylePack, get_style

__all__ = [
    "LowResCanvas",
    "LowResRenderer",
    "Plan",
    "StylePack",
    "get_style",
    "plan",
    "style",
    "upscale",
]
