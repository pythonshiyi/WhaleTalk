"""分辨率规划：目标尺寸 → 逻辑画布 + 整数放大倍数。

设计约束
--------
1. **放大必须是整数倍**：非整数倍放大（如 1080→1920 的 1.777x）会产生
   非均匀的方块与摩尔纹，破坏像素 / 水墨风格。因此 `out = logical × scale`。
2. **逻辑像素有预算**：低分辨率的收益正比于像素减少。默认预算取
   480×270=129600 —— 这是实测出来的「像素 / 水墨风格甜点」。
3. **输出必须 ≥ 目标尺寸**：`out = ceil(target / scale) × scale`，天然全屏覆盖，
   调用方再裁到目标尺寸即可（裁掉的部分 < scale 像素）。
"""
from __future__ import annotations

from dataclasses import dataclass

# 默认逻辑像素预算：480×270（1920×1080 的 1/16）。实测甜点。
DEFAULT_PIXEL_BUDGET = 480 * 270
DEFAULT_MAX_SCALE = 8
DEFAULT_MIN_SCALE = 2


@dataclass(frozen=True)
class Plan:
    """一次分辨率规划的结果。"""

    target_w: int
    target_h: int
    lw: int
    lh: int
    scale: int

    @property
    def out_w(self) -> int:
        return self.lw * self.scale

    @property
    def out_h(self) -> int:
        return self.lh * self.scale

    @property
    def pixels(self) -> int:
        return self.lw * self.lh

    @property
    def speedup(self) -> float:
        """相对在目标分辨率上逐像素运算的绘制量倍数。"""
        return (self.target_w * self.target_h) / max(1, self.pixels)

    def describe(self) -> str:
        return (f"{self.lw}×{self.lh} 逻辑画布 ×{self.scale} 放大 "
                f"→ {self.out_w}×{self.out_h}（目标 {self.target_w}×{self.target_h}，"
                f"绘制量 1/{self.speedup:.1f}）")


def logical_size(target_w: int, target_h: int, scale: int) -> tuple[int, int]:
    """目标尺寸在给定整数放大倍数下的逻辑画布尺寸（向上取整）。"""
    s = max(1, int(scale))
    lw = max(1, -(-int(target_w) // s))     # ceil 除法
    lh = max(1, -(-int(target_h) // s))
    return lw, lh


def plan(target_w: int, target_h: int, *, scale: int | None = None,
         pixel_budget: int = DEFAULT_PIXEL_BUDGET,
         max_scale: int = DEFAULT_MAX_SCALE,
         min_scale: int = DEFAULT_MIN_SCALE) -> Plan:
    """求最优低分辨档。

    - `scale` 给定 → 直接用（下限 1）。
    - `scale=None` → 在 [min_scale, max_scale] 里选**最小**的整数放大倍数，
      使逻辑像素数不超过 `pixel_budget`。放大倍数最小 ⇒ 逻辑画布最大 ⇒ 画质最好，
      这正是「把画质压到速度预算之内」的最优解。
    - 若连 `max_scale` 都放不进预算，则退到 `max_scale`（最快档）。
    """
    tw, th = int(target_w), int(target_h)
    if tw <= 0 or th <= 0:
        raise ValueError(f"目标尺寸必须为正：{target_w}×{target_h}")

    if scale is not None:
        s = max(1, int(scale))
        lw, lh = logical_size(tw, th, s)
        return Plan(tw, th, lw, lh, s)

    lo = max(1, int(min_scale))
    hi = max(lo, int(max_scale))
    for s in range(lo, hi + 1):
        lw, lh = logical_size(tw, th, s)
        if lw * lh <= pixel_budget:
            return Plan(tw, th, lw, lh, s)
    lw, lh = logical_size(tw, th, hi)
    return Plan(tw, th, lw, lh, hi)
