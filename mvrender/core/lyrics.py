"""歌词渲染：逐字渗入 + 描边 + 轻微发光 + 字带压暗（电影式）。

与旧 engine.LyricRenderer 数值一致（W/H 参数化）。返回 (img, overlay)，
调用方按 `last_dark` 压暗后再叠加 overlay —— 与旧主循环契约保持一致。
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from .vis import FONT_SONG, get_font, render_text_mask


class LyricRenderer:
    def __init__(self, lines, w=1080, h=1920, night_glow=(196, 168, 132)):
        self.lines = lines
        self.w, self.h = int(w), int(h)
        self.night_glow = tuple(night_glow)
        self.cache = {}
        self.last_dark = None

    def _layout(self, text, size, tracking, maxw):
        from PIL import Image, ImageDraw
        dummy = Image.new("L", (10, 10))
        d = ImageDraw.Draw(dummy)
        while size > 26:
            font = get_font(size, FONT_SONG)
            ws = [d.textlength(ch, font=font) for ch in text]
            total = sum(ws) + tracking * (len(text) - 1)
            if total <= maxw:
                return size, ws, total
            size -= 2
        font = get_font(size, FONT_SONG)
        ws = [d.textlength(ch, font=font) for ch in text]
        return size, ws, sum(ws) + tracking * (len(text) - 1)

    def chmask(self, ch, size):
        key = ("ch", ch, size)
        if key not in self.cache:
            self.cache[key] = render_text_mask(ch, size, 130, int(size * 1.7),
                                               font_path=FONT_SONG, xy=(4, 0), tracking=0)
        return self.cache[key]

    def render(self, img, t, ctx, sec=None, day=False):
        W, H = self.w, self.h
        overlay = np.zeros((H, W, 3), np.float32)
        active = ctx.line_at(t)
        self.last_dark = None
        if active is None:
            return img, overlay
        text = active["text"]
        t0, t1 = active["t0"], active["t1"]
        lay_key = ("lay", text)
        if lay_key not in self.cache:
            self.cache[lay_key] = self._layout(text, 52, 8, W - 120)
        size, ws, total = self.cache[lay_key]
        x0 = (W - total) / 2
        y_base = 1436
        span = max(0.6, (t1 - t0) * 0.42)
        layer = np.zeros((H, W), np.float32)
        gl = np.zeros((H, W), np.float32)
        x = x0
        for i, ch in enumerate(text):
            if ch == " ":
                x += ws[i] + 8
                continue
            t_start = t0 - 0.35 + span * (i / max(1, len(text)))
            a = np.clip((t - t_start) / 0.38, 0, 1)
            a = a * a * (3 - 2 * a)
            if t > t1 + 0.5:
                a *= max(0.0, 1 - (t - t1 - 0.5) / 1.0)
            if a > 0.01:
                dy = int((1 - a) * 10)
                mask = self.chmask(ch, size)
                wch = int(min(ws[i] + 10, 120))
                sub = mask[:int(size * 1.7), :wch] * a
                xi, yi = int(x) + 2, y_base + dy - int(size * 1.15)
                x1, y1 = min(W, xi + sub.shape[1]), min(H, yi + sub.shape[0])
                if x1 > xi and y1 > yi:
                    layer[yi:y1, xi:x1] = np.maximum(layer[yi:y1, xi:x1], sub[:y1 - yi, :x1 - xi])
                    gl[yi:y1, xi:x1] = np.maximum(gl[yi:y1, xi:x1], sub[:y1 - yi, :x1 - xi] * 0.7)
            x += ws[i] + 8
        if day:
            base_col = np.array((70, 62, 54), np.float32)
            halo = np.array((255, 250, 240), np.float32)
        elif sec == "副歌":
            base_col = np.array((255, 252, 245), np.float32)
            halo = np.array(self.night_glow, np.float32)
        else:
            base_col = np.array((245, 250, 255), np.float32)
            halo = np.array(self.night_glow, np.float32)
        if "band_a" not in self.cache:
            self.cache["band_a"] = (np.clip(
                1 - np.abs(np.arange(H) - (y_base - 40)) / 190.0, 0, 1) ** 1.2 * 0.32)[:, None]
        k7 = self.cache.setdefault("k7", np.ones((7, 7), np.uint8))
        dark = cv2.dilate(layer, k7) - layer
        dark *= 0.72
        dark = dark + self.cache["band_a"]
        np.clip(dark, 0, 0.92, out=dark)
        overlay += layer[:, :, None] * base_col * 0.98
        g = cv2.GaussianBlur(gl, (0, 0), 9)
        overlay += g[:, :, None] * halo * 0.8
        p = ctx.pulse(t)
        overlay *= (1.0 + 0.10 * p)
        self.last_dark = dark
        return img, overlay
