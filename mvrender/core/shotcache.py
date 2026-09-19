"""镜头级增量渲染缓存（迭代速度的核心）。

解决的问题
----------
全片渲染一次要 ~35 分钟（6000 帧 × ~350ms）。检查成片后常只需改**一个镜头**
（如「A8 的雨太密了」），却要重跑整片 —— 这是迭代最贵的地方。

本模块提供：
    cache = ShotCache(root)              # 磁盘缓存（按镜头分文件）
    cache.render_range(renderer, "A8")   # 只渲 A8，其余读缓存
    cache.invalidate("A8")               # A8 变化
    cache.invalidate("A8.rain")          # 细粒度失效（依赖标签）
    cache.invalidate_all()

设计
----
1. **按镜头分文件**：`<root>/.mvrender_cache/<project>/<shot>.npz`（逐帧压缩存储），
   改一个镜头只重写它自己的文件，其余原样复用。
2. **依赖标签（deps）**：Shot.deps 声明该镜头依赖的资产（如 "rain"），
   `invalidate("A8.rain")` 只失效引用该标签的镜头。
3. **指纹校验**：文件头存 `(帧数, fps, 分辨率, 镜头时间区间, deps 版本)`，
   不匹配即视为失效 —— 防止改了镜头时长却复用旧缓存。
4. **缩略校验**：可选的帧抽样哈希，用于「渲染器/引擎升级」后的全局失效判定。

注意：缓存的是**已渲染的 uint8 帧**（BGR），因此与渲染后端解耦 ——
CPU 渲过的帧可被 GPU 复用，反之亦然（前提是像素一致，本项目已保证）。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil

import numpy as np

CACHE_DIR = ".mvrender_cache"


class ShotCache:
    def __init__(self, root, project=None, enabled=True):
        self.root = os.path.abspath(root)
        self.project = project or os.path.basename(self.root)
        self.enabled = bool(enabled)
        self.dir = os.path.join(self.root, CACHE_DIR, self.project)
        self._deps_ver = {}          # shot_name -> 上次记录的 deps 签名

    # ── 路径 ────────────────────────────────────────────────────────
    def _path(self, shot_name):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(shot_name))
        return os.path.join(self.dir, f"{safe}.npz")

    def _meta_path(self, shot_name):
        return self._path(shot_name) + ".json"

    # ── 指纹 ────────────────────────────────────────────────────────
    @staticmethod
    def fingerprint(shot, fps, res, deps_ver=""):
        h = hashlib.sha1()
        h.update(f"{shot.t0:.6f}|{shot.t1:.6f}|{fps}|{res}|{deps_ver}".encode())
        return h.hexdigest()

    # ── 查询 / 读写 ─────────────────────────────────────────────────
    def has(self, shot, fps, res):
        if not self.enabled:
            return False
        p, mp = self._path(shot.name), self._meta_path(shot.name)
        if not (os.path.isfile(p) and os.path.isfile(mp)):
            return False
        try:
            with open(mp, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:  # noqa: BLE001
            return False
        return meta.get("fp") == self.fingerprint(shot, fps, res,
                                                  self._deps_sig(shot))

    def _deps_sig(self, shot):
        deps = tuple(sorted(getattr(shot, "deps", ()) or ()))
        return ",".join(deps)

    def load(self, shot):
        """读缓存的帧数组 (n, H, W, 3) uint8；失败返回 None。"""
        try:
            z = np.load(self._path(shot.name))
            return z["frames"]
        except Exception:  # noqa: BLE001
            return None

    def save(self, shot, frames, fps, res):
        if not self.enabled:
            return False
        try:
            os.makedirs(self.dir, exist_ok=True)
            np.savez_compressed(self._path(shot.name), frames=frames)
            with open(self._meta_path(shot.name), "w", encoding="utf-8") as f:
                json.dump({"fp": self.fingerprint(shot, fps, res, self._deps_sig(shot)),
                           "name": shot.name, "t0": shot.t0, "t1": shot.t1,
                           "fps": fps, "res": list(res), "n": int(len(frames)),
                           "deps": list(getattr(shot, "deps", ()) or ())},
                          f, ensure_ascii=False, indent=1)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ── 渲染（带缓存）────────────────────────────────────────────────
    def render_shot(self, renderer, shot, fps, res, force=False):
        """渲染单个镜头的全部帧（命中缓存则直接返回）。

        返回 (frames uint8, from_cache bool)。帧序与全局帧号一致：
        第 i 帧对应 t = shot.t0 + i/fps。
        """
        if not force and self.has(shot, fps, res):
            fr = self.load(shot)
            if fr is not None:
                return fr, True
        n = max(1, int(round(shot.dur * fps)))
        frames = np.empty((n, renderer.h, renderer.w, 3), np.uint8)
        for i in range(n):
            t = shot.t0 + i / fps
            frames[i] = renderer.render_u8(t)
        self.save(shot, frames, fps, res)
        return frames, False

    def render_range(self, renderer, start_name, end_name=None, fps=None, res=None,
                     force=False):
        """渲染 [start_name, end_name] 区间的镜头（含端点）；其余不碰。"""
        names = [s.name for s in renderer.shots]
        i0 = names.index(start_name)
        i1 = names.index(end_name) if end_name else i0
        out = {}
        for s in renderer.shots[i0:i1 + 1]:
            fr, cached = self.render_shot(renderer, s, fps, res, force=force)
            out[s.name] = (fr, cached)
        return out

    def render_all(self, renderer, fps, res, force=False):
        out = {}
        for s in renderer.shots:
            out[s.name] = self.render_shot(renderer, s, fps, res, force=force)
        return out

    # ── 失效 ────────────────────────────────────────────────────────
    def invalidate(self, key, shots=None):
        """失效指定镜头；key 支持 "A8" 或 "A8.rain"（依赖标签）。

        返回被失效的镜头名列表。
        """
        keys = set(str(key).split("+"))
        hit = []
        for s in (shots if shots is not None else []):
            name = s.name
            deps = set(getattr(s, "deps", ()) or ())
            for k in keys:
                if "." in k:
                    # "A8.rain"：镜头名匹配 A8 且依赖含 rain
                    shot_part, dep_part = k.split(".", 1)
                    if (name == shot_part or name.startswith(shot_part)) and dep_part in deps:
                        hit.append(name)
                        break
                elif k in deps:
                    # 纯依赖标签（如 "rain"）：失效所有依赖它的镜头
                    hit.append(name)
                    break
                elif name == k or name.startswith(k):
                    hit.append(name)
                    break
        if shots is None:                       # 无镜头表：按文件前缀删
            for k in keys:
                base = k.split(".")[0]
                if not os.path.isdir(self.dir):
                    continue
                for fn in os.listdir(self.dir):
                    if fn.startswith(base):
                        try:
                            os.remove(os.path.join(self.dir, fn))
                            hit.append(fn)
                        except OSError:
                            pass
            return hit
        for name in set(hit):
            for p in (self._path(name), self._meta_path(name)):
                with contextlib.suppress(OSError):
                    os.remove(p)
        return sorted(set(hit))

    def invalidate_all(self):
        if os.path.isdir(self.dir):
            shutil.rmtree(self.dir, ignore_errors=True)
        return True

    # ── 状态 ────────────────────────────────────────────────────────
    def status(self):
        if not os.path.isdir(self.dir):
            return {"shots": 0, "bytes": 0}
        files = [os.path.join(self.dir, f) for f in os.listdir(self.dir)]
        return {"shots": len([f for f in files if f.endswith(".npz")]),
                "bytes": sum(os.path.getsize(f) for f in files if os.path.isfile(f))}
