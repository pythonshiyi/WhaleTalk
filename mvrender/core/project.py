"""曲目工程契约（Project / Shot / Ctx）。

设计目标
--------
把「引擎」与「单曲创意」解耦：
  · 引擎（mvrender.core.renderer）只认 Shot 契约与 Ctx 信号；
  · 每首歌是一个 Project：提供 timeline（分辨率/时长/歌词） + shots（镜头表）。
这样一次建立、所有 MV 复用。

Shot 契约
---------
每个镜头是一个 `draw(canvas, tl, shot, ctx, cache)` 函数，在画布上作画。
canvas 可以是 CPU 的 `core.canvas.Canvas` 或 GPU 的 `gpu.canvas_gpu.GPUCanvas`
（同接口），因此同一套镜头代码在两种后端下都能跑。
"""
from __future__ import annotations

import json
import math
import os

import numpy as np


class Ctx:
    """全局上下文：节拍 / 能量 / 分频曲线 / 歌词 / 段落。与旧 engine.Ctx 契约一致。"""

    def __init__(self, tl, bt, w=1080, h=1920):
        self.tl = tl
        self.w, self.h = int(w), int(h)
        self.beats = np.array(bt["beats"], np.float32)
        self.bs = np.array(bt["beat_strength"], np.float32)
        self.period = float(bt["period"])
        self.duration = float(bt["duration"])
        rc = np.array(bt["rms_curve"], np.float32)
        self.rms_t, self.rms_v = rc[:, 0], rc[:, 1]
        self.rms_v = self.rms_v / (np.percentile(self.rms_v, 97) + 1e-9)
        pc = np.array(bt["perc_curve"], np.float32)
        self.perc_t, self.perc_v = pc[:, 0], np.clip(pc[:, 1], 0, 1.5)
        hc = np.array(bt["harm_curve"], np.float32)
        self.harm_t, self.harm_v = hc[:, 0], np.clip(hc[:, 1], 0, 1.5)
        vc = np.array(bt["vocal_curve"], np.float32)
        self.vocal_t, self.vocal_v = vc[:, 0], np.clip(vc[:, 1], 0, 1.5)
        # 歌词行兼容：本仓库各曲的 timeline.json 字段名不同 ——
        # 三更帖用 `who`（"男"/"女"/"合"），釉下青/旧城慢无 `who`。
        # 早前 `ctx.line_at` 只存在于各曲 engine.Ctx，mvrender.Ctx 漏了它，
        # 导致 post() 在读段落色调时崩溃。这里一并补齐默认值：
        #   who 缺失 → "合"；sec 缺失 → ""。
        self.lines = []
        for _ln in tl.get("lines", tl.get("lyrics", [])) or []:
            d = dict(_ln)
            d.setdefault("who", "合")
            d.setdefault("sec", "")
            self.lines.append(d)
        self.seg = {}
        for ln in self.lines:
            s = self.seg.setdefault(ln["sec"], [ln["t0"], ln["t1"]])
            s[1] = max(s[1], ln["t1"])

    def line_at(self, t, lead=1.2, tail=1.6):
        """当前歌词行（含前后余量）；无则 None。

        【踩坑】此前 mvrender.Ctx 漏实现 line_at，但 renderer.post / lyrics 都直接
        调用它 → AttributeError: 'Ctx' object has no attribute 'line_at'。
        各曲 engine.Ctx 里有这个方法（签名也含 lead/tail），此处按同签名补齐。
        """
        for ln in self.lines:
            if ln["t0"] - lead <= t <= ln["t1"] + tail:
                return ln
        return None

    def energy(self, t):
        return float(np.interp(t, self.rms_t, self.rms_v))

    def perc(self, t):
        return float(np.interp(t, self.perc_t, self.perc_v))

    def harm(self, t):
        return float(np.interp(t, self.harm_t, self.harm_v))

    def vocal(self, t):
        return float(np.interp(t, self.vocal_t, self.vocal_v))

    def beat_info(self, t):
        i = int(np.searchsorted(self.beats, t))
        if i <= 0:
            return float(self.beats[0]), float(t - self.beats[0]), 1.0
        b = self.beats[i - 1]
        s = float(self.bs[min(i - 1, len(self.bs) - 1)])
        return float(b), float(t - b), s

    def pulse(self, t, decay=7.0, power=1.4):
        b, dt, s = self.beat_info(t)
        p = math.exp(-dt * decay)
        return p * (0.45 + 0.85 * min(1.0, s * 1.8)) ** power

    def beat_phase(self, t):
        b, dt, s = self.beat_info(t)
        return dt / self.period

    def is_strong(self, t):
        b, dt, s = self.beat_info(t)
        return s > 0.28

    def who_at(self, t):
        ln = self.line_at(t)
        return ln["who"] if ln else "合"

    def progress(self, t):
        return float(np.clip(t / max(1e-6, self.duration), 0, 1))


class Shot:
    """镜头：时间区间 + 作画函数 + 相机/转场参数。

    与旧 engine.Shot 契约一致，但增加了 `deps`（依赖标签），供增量缓存失效判定。
    """

    def __init__(self, t0, t1, name, fn, cam="float", tin=0.7, tout=0.7,
                 tin_kind="dissolve", tout_kind="dissolve", day=False,
                 deps=None, **kw):
        self.t0, self.t1 = float(t0), float(t1)
        self.name = name
        self.fn = fn
        self.cam = cam
        self.tin, self.tout = float(tin), float(tout)
        self.tin_kind, self.tout_kind = tin_kind, tout_kind
        self.day = bool(day)
        self.deps = tuple(deps or ())     # 例如 ("rain", "lamps")
        self.kw = kw
        self.st = {}

    @property
    def dur(self):
        return self.t1 - self.t0

    def draw(self, t, ctx, cache, canvas_factory):
        """用给定画布工厂作画（CPU 或 GPU）。"""
        c = canvas_factory()
        tl = t - self.t0
        self.fn(c, tl, self, ctx, cache)
        return c


class Project:
    """一首 MV 的工程：timeline + beats + shots。

    子类/工程脚本需提供：
      · timeline（dict：fps/duration/resolution/lines/…）
      · beats（dict：beats/period/rms_curve/…）
      · shots（list[Shot]）
    """

    def __init__(self, root, tl=None, bt=None, shots=None):
        self.root = str(root)
        self.tl = tl
        self.bt = bt
        self.shots = list(shots or [])

    # ── 加载 ───────────────────────────────────────────────────────
    @staticmethod
    def load_json(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def from_workdir(cls, root, work="work", shots_builder=None):
        """从 work/timeline.json + work/beats.json 加载，并用 shots_builder 建镜头表。"""
        root = os.path.abspath(root)
        workdir = os.path.join(root, work)
        tl = cls.load_json(os.path.join(workdir, "timeline.json"))
        bt = cls.load_json(os.path.join(workdir, "beats.json"))
        shots = []
        if shots_builder is not None:
            defs = shots_builder(tl)
            for d in defs:
                if isinstance(d, Shot):
                    shots.append(d)
                else:
                    t0, t1, name, fn, cam, tin, tout, tik, tok, day = d[:10]
                    deps = d[10] if len(d) > 10 else None
                    shots.append(Shot(t0, t1, name, fn, cam=cam, tin=tin, tout=tout,
                                      tin_kind=tik, tout_kind=tok, day=day, deps=deps))
        return cls(root, tl, bt, shots)

    @property
    def fps(self):
        return int(self.tl["fps"])

    @property
    def duration(self):
        return float(self.tl["duration"])

    @property
    def resolution(self):
        return tuple(self.tl["resolution"])

    @property
    def total_frames(self):
        return int(round(self.duration * self.fps))

    def ctx(self):
        w, h = self.resolution
        return Ctx(self.tl, self.bt, w=w, h=h)
