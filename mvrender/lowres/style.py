"""风格包协议 —— 底座低分辨率档的题材无关核心。

一个风格包只回答两个问题（对任何题材都成立）：

1. `background(w, h, ctx, shot)` —— 这一镜**不变**的底色（宣纸 / 夜色 / 网格）。
   它是镜头内不变量，由渲染器按镜头缓存，只算一次（「缓存不变量」原则）。
2. `apply(img, t, ctx, shot, bg)` —— 把镜头画出的「像素能量图」翻译成该风格的画面。

风格包**不知道**镜头画的是什么（楼、雨、人、字），只知道画布上的像素能量；
因此像素风、水墨风、扁平矢量可以共用同一批镜头代码 —— 这就是题材无关的关键。

约定：`img` / `bg` 均为 float32 **BGR**、0..255（与底座画布一致）；
`background` 可返回 `None`（无不变底色）。
"""
from __future__ import annotations

import abc

import numpy as np

_REGISTRY: dict[str, type[StylePack]] = {}
_BUILTIN_LOADED = False


class StylePack(abc.ABC):
    """风格包基类。子类须声明唯一 `id` 并实现 `apply`。"""

    id: str = "base"

    def background(self, w: int, h: int, ctx, shot):
        """返回镜头内不变的底色 (h,w,3) float32 BGR，或 None。"""
        return None

    @abc.abstractmethod
    def apply(self, img: np.ndarray, t: float, ctx, shot, bg):
        """把能量图 `img` 翻译成风格化画面（同形状 float32 BGR）。"""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<StylePack {self.id!r}>"


def register(cls: type[StylePack]) -> type[StylePack]:
    """风格包注册装饰器。"""
    if not getattr(cls, "id", None) or cls.id == "base":
        raise ValueError(f"风格包必须有非默认 id：{cls!r}")
    _REGISTRY[str(cls.id)] = cls
    return cls


def _ensure_builtin() -> None:
    global _BUILTIN_LOADED
    if _BUILTIN_LOADED:
        return
    from .styles import ink as _ink  # noqa: F401  (导入即注册)
    from .styles import pixel as _pixel  # noqa: F401
    _BUILTIN_LOADED = True


def names() -> list[str]:
    """已注册风格包 id（升序）。"""
    _ensure_builtin()
    return sorted(_REGISTRY)


def get_style(sid: str) -> StylePack:
    """按 id 取风格包实例。"""
    _ensure_builtin()
    cls = _REGISTRY.get(str(sid))
    if cls is None:
        raise KeyError(f"未知风格包: {sid!r}（可用：{names()}）")
    return cls()


def composite_over(bg: np.ndarray, ink: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """通用合成：`bg*(1-cov) + ink*cov`（cov 为 (h,w) 或 (h,w,1)）。"""
    c = cov[:, :, None] if cov.ndim == 2 else cov
    return bg * (1.0 - c) + ink * c
