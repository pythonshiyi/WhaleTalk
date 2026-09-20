# -*- coding: utf-8 -*-
"""片头 / 片尾 / 角标字幕卡。

来源：用户对《三更帖》成片的真实反馈——“作为音乐 MV，前后段都没有专辑名
音乐名制作人的相关字幕体现，非常不专业”。

mvrender 只有歌词渲染（core/lyrics.py），没有作品信息卡。本模块就是补齐它。

内置**可读性自检** `check_visible()`：
  修复时踩过一个坑——`end_card` 的 t 超出 dur 时返回全黑（亮度 0），
  静默失败很难发现。所以提供自检，把隐式失败变成显式断言。

与 mvrender 现有风格对齐：
  · 字体走 `core.vis.get_font` / `FONT_SONG`
  · 返回 float32 RGB (0..1)，与 renderer 的合成习惯一致
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


try:
    # 注意：vis.py 只导出 FONT_SONG/FONT_UI/FONT_HEI/FONT_KAI，**没有 FONT_BODY**。
    # 字体分工：标题/歌名走 FONT_SONG（宋体），说明性小字走 FONT_UI（微软雅黑）。
    from .vis import FONT_SONG, FONT_UI, get_font
    FONT_BODY = FONT_UI
    _HAS_VIS = True
except Exception:  # pragma: no cover - 单独使用本模块时的回退
    _HAS_VIS = False
    FONT_SONG = FONT_BODY = None

    def get_font(size, path=None, *a, **k):
        import os
        from PIL import ImageFont
        for p in (path, r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
                  r"C:\Windows\Fonts\simhei.ttf"):
            if p and os.path.isfile(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    continue
        return ImageFont.load_default()


# =============================================================== 作品信息
@dataclass
class CreditsInfo:
    """作品元信息（片头/片尾共用）。"""
    album: str = ""           # 专辑名（如 《三更帖》）
    song: str = ""            # 歌名（片头用大字）
    artist: str = ""          # 演唱
    producer: str = ""        # 制作人
    studio: str = ""          # 出品
    credits: list = field(default_factory=list)   # [(角色, 姓名), ...] 供片尾

    def to_dict(self) -> dict:
        return {"album": self.album, "song": self.song, "artist": self.artist,
                "producer": self.producer, "studio": self.studio,
                "credits": [list(x) for x in self.credits]}

    @staticmethod
    def from_dict(d) -> "CreditsInfo":
        d = d or {}
        return CreditsInfo(
            album=str(d.get("album") or ""),
            song=str(d.get("song") or ""),
            artist=str(d.get("artist") or ""),
            producer=str(d.get("producer") or ""),
            studio=str(d.get("studio") or ""),
            credits=[tuple(x) for x in (d.get("credits") or [])],
        )


# =============================================================== 内部工具
def _fade(t: float, dur: float, fin: float, fout: float) -> float:
    """透明度包络：0 淡入 fin 秒、dur 淡出 fout 秒；**超出 [0,dur] 返回 0**。

    注意：调用方必须传**局部时间**（从卡片开始计），否则返回 0（全黑）。
    """
    if dur <= 0 or t < 0 or t > dur:
        return 0.0
    return max(0.0, min(min(t / max(fin, 1e-6), 1.0),
                        min((dur - t) / max(fout, 1e-6), 1.0)))


def _center(d: ImageDraw.ImageDraw, cx: float, y: float, text: str, font, fill) -> None:
    if not text:
        return
    bb = d.textbbox((0, 0), text, font=font)
    d.text((cx - (bb[2] - bb[0]) / 2 - bb[0], y), text, font=font, fill=fill)


def _finish(ov: Image.Image, w: int, h: int, glow_r: float = 10.0) -> np.ndarray:
    """带辉光的 RGBA 图层 → RGB float32(0..1)。"""
    glow = ov.filter(ImageFilter.GaussianBlur(glow_r))
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out = Image.alpha_composite(Image.alpha_composite(base, glow), ov)
    return np.clip(np.asarray(out.convert("RGB"), np.float32) / 255.0, 0, 1)


# =============================================================== 片头卡
def title_card(t: float, w: int, h: int, info: CreditsInfo, *,
               accent=(66, 224, 255), dur: float = 7.0) -> np.ndarray:
    """片头卡：专辑名 → 大字歌名 → 演唱/制作人/出品，带辉光与扫光。

    t: **从片头开始的秒数**（需在 [0, dur] 内，否则返回全黑）
    accent/dur 是 keyword-only——曾因把 dur 误传为 accent 而先得 TypeError
    （'float' object is not subscriptable），故强制关键字传参。
    """
    a = _fade(t, dur, fin=1.0, fout=1.2)
    if a <= 0.01:
        return np.zeros((h, w, 3), np.float32)

    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    cx = w / 2.0
    f_album = get_font(max(28, int(w * 0.043)), FONT_BODY)
    f_song = get_font(max(64, int(w * 0.104)), FONT_SONG)
    f_sub = get_font(max(22, int(w * 0.033)), FONT_BODY)
    f_tiny = get_font(max(18, int(w * 0.026)), FONT_BODY)
    acc = (int(accent[0]), int(accent[1]), int(accent[2]))

    rise = (1.0 - min(t / 1.4, 1.0)) * (h * 0.018)
    cy = h * 0.34 - rise
    lw = w * 0.42
    d.line([(cx - lw / 2, cy - h * 0.068), (cx + lw / 2, cy - h * 0.068)],
           fill=acc + (int(200 * a),), width=2)
    _center(d, cx, cy - h * 0.054, info.album, f_album, (228, 240, 255, int(230 * a)))
    song_y = cy + h * 0.005
    _center(d, cx, song_y, info.song, f_song, (255, 255, 255, int(255 * a)))
    for k in range(3):
        dx = cx + (k - 1) * 22
        d.ellipse([dx - 3, song_y + f_song.size * 1.36,
                   dx + 3, song_y + f_song.size * 1.36 + 6],
                  fill=acc + (int(210 * a),))
    y = song_y + f_song.size * 1.66
    for line in (info.artist, info.producer):
        if line:
            _center(d, cx, y, line, f_sub, (216, 228, 248, int(235 * a)))
            y += f_sub.size * 1.5
    if info.studio:
        _center(d, cx, y, info.studio, f_tiny, (176, 194, 220, int(190 * a)))

    rgb = _finish(ov, w, h, glow_r=12.0)
    # 横向扫光
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    sweep = np.exp(-((xx - (t / max(dur, 1e-6)) * 1.6 + 0.3) ** 2) / 0.004)
    rgb = rgb + sweep[:, :, None] * (np.array(acc, np.float32) / 255.0) * 0.14 * a
    return np.clip(rgb, 0, 1)


# =============================================================== 片尾卡
def end_card(t: float, w: int, h: int, info: CreditsInfo, *,
             accent=(255, 82, 170), dur: float = 9.0) -> np.ndarray:
    """片尾卡：歌名 + 制作名单逐行淡入。t 为**局部时间**（[0,dur]）。"""
    a = _fade(t, dur, fin=0.8, fout=1.8)
    if a <= 0.01:
        return np.zeros((h, w, 3), np.float32)

    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    cx = w / 2.0
    f_song = get_font(max(36, int(w * 0.059)), FONT_SONG)
    f_role = get_font(max(20, int(w * 0.029)), FONT_BODY)
    f_name = get_font(max(21, int(w * 0.031)), FONT_SONG)
    f_tiny = get_font(max(16, int(w * 0.024)), FONT_BODY)
    acc = (int(accent[0]), int(accent[1]), int(accent[2]))

    top = h * 0.20
    _center(d, cx, top, info.song, f_song, (255, 255, 255, int(250 * a)))
    d.line([(cx - 110, top + f_song.size * 1.45), (cx + 110, top + f_song.size * 1.45)],
           fill=acc + (int(200 * a),), width=2)

    y = top + f_song.size * 2.2
    step = max(46.0, h * 0.032)
    for i, item in enumerate(info.credits or []):
        try:
            role, name = item[0], item[1]
        except Exception:
            continue
        ai = min(max((t - 0.5 - i * 0.42) / 0.6, 0.0), 1.0) * a
        if ai <= 0.01:
            continue
        yy = y + i * step
        _center(d, cx, yy, role, f_role, (176, 194, 220, int(210 * ai)))
        _center(d, cx, yy + f_role.size, name, f_name, (240, 246, 255, int(240 * ai)))

    y2 = y + len(info.credits or []) * step + h * 0.02
    ab = min(max((t - 0.5 - len(info.credits or []) * 0.42) / 0.8, 0.0), 1.0) * a
    if ab > 0.01:
        _center(d, cx, y2, info.studio, f_tiny, (150, 170, 200, int(200 * ab)))
        _center(d, cx, y2 + f_tiny.size * 1.6, "版权所有 · 鲸语 AI 出品", f_tiny,
                (120, 140, 170, int(170 * ab)))
    return _finish(ov, w, h, glow_r=10.0)


# =============================================================== 角标
def corner_tag(t: float, w: int, h: int, text: str, *,
               accent=(66, 224, 255), alpha: int = 92) -> np.ndarray:
    """左上角常驻小字水印（刻疾微弱，不抢戏，带呼吸）。"""
    if not text:
        return np.zeros((h, w, 3), np.float32)
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    f = get_font(max(16, int(w * 0.024)), FONT_BODY)
    x, y = int(w * 0.041), int(h * 0.027)
    d.text((x, y), text, font=f, fill=(190, 208, 232, alpha))
    d.line([(x, y + f.size * 1.4), (x + f.size * 6.4, y + f.size * 1.4)],
           fill=(int(accent[0]), int(accent[1]), int(accent[2]), int(alpha * 0.56)), width=1)
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rgb = np.asarray(Image.alpha_composite(base, ov).convert("RGB"), np.float32) / 255.0
    return np.clip(rgb * (0.94 + 0.06 * math.sin(t * 0.9)), 0, 1)


# =============================================================== 合成助手
def overlay(base: np.ndarray, card: np.ndarray) -> np.ndarray:
    """把字幕层以 `1-(1-a)(1-b)` 方式叠到画面上（用于片头/片尾卡）。"""
    if card is None:
        return base
    if card.max() <= 0.001:
        return base
    return np.clip(1.0 - (1.0 - np.asarray(base, np.float32)) * (1.0 - card), 0, 1)


def overlay_soft(base: np.ndarray, card: np.ndarray, gain: float = 0.55) -> np.ndarray:
    """弱叠加（用于角标，避免遮画面）。"""
    if card is None or card.max() <= 0.001:
        return base
    return np.clip(1.0 - (1.0 - np.asarray(base, np.float32)) * (1.0 - card * gain), 0, 1)


# =============================================================== 自检
# 各组件“可见”的能量参考阈值（实测值再除以 ~3 作下限）
#   片头/片尾实测 0.0087~0.0795；角标刻意微弱，实测 0.00079
VISIBLE_MIN_ENERGY = {
    "title": 0.003,
    "end": 0.002,
    "tag": 0.0002,
    "default": 0.0008,
}


def check_visible(render_fn, t: float, min_energy: float = None,
                  kind: str = "default") -> dict:
    """检查字幕卡在时刻 t 是否真的可见（防端静默返回全黑）。

    kind: title / end / tag / default —— 决定默认阈值（角标天然微弱）。
    用法：check_visible(lambda: end_card(3.0, W, H, info), 3.0, kind="end")
    """
    if min_energy is None:
        min_energy = VISIBLE_MIN_ENERGY.get(kind, VISIBLE_MIN_ENERGY["default"])
    try:
        img = render_fn()
    except Exception as e:
        return {"visible": False, "error": f"{type(e).__name__}: {e}",
                "kind": kind, "threshold": min_energy}
    arr = np.asarray(img, np.float32)
    energy = float(arr.mean())
    return {"visible": energy > min_energy, "energy": round(energy, 5),
            "max": round(float(arr.max()), 4), "t": t,
            "kind": kind, "threshold": min_energy}
