# mvrender.lowres —— 低分辨率极速档 + 风格包

> 定位：**不是第四套引擎**，而是给 `mvrender` 底座补上缺失的「低分辨率档」。
> 底座 `core.renderer.Renderer(w, h, ...)` 与 `API.md` 早已声明「分辨率可按需降」，
> 本包把这个能力落成可复用的实现，并补上**题材无关**的风格包机制。

## 为什么可能快

```
渲染成本 ≈ 逻辑画布像素数 × 每像素运算量 × 帧数 ÷ 并行度
```

传统全高清渲染把第一、三项都推高（1920×1080 × 数千帧）。本档把第一项砍到 1/16
（480×270），再靠「镜头内不变量缓存」压掉重复的每像素运算量，即可数量级提速。
这是**用画质换速度**的档位，适合像素 / 水墨 / 扁平矢量 / 几何抽象风格。

## 模块

| 文件 | 职责 |
|---|---|
| `plan.py` | 分辨率规划：目标尺寸 → 逻辑画布 + 整数放大倍数（自动求最优） |
| `canvas.py` | 逻辑画布：低分辨率绘制原语，对齐底座 / GPUCanvas 图元格式 |
| `style.py` | **风格包协议**（题材无关的关键）+ 注册表 |
| `styles/pixel.py` | 像素风格包（有限色阶 + 夜/日底色） |
| `styles/ink.py` | 水墨风格包（宣纸底 + 墨分五色 + 洇染 / 湿边） |
| `renderer_lowres.py` | 低分辨率渲染器，**实现底座 `Renderer` 的 render 契约** |
| `synth.py` | 合成镜头 / Ctx（基准与自检用） |

工具：

```bash
python -m mvrender.lowres.verify_agnostic     # 题材无关性验证
python -m mvrender.lowres.bench_real          # 逐帧耗时 / 全片估算 / 风格对比
python -m mvrender.lowres.diag_bottleneck     # 分阶段瓶颈诊断（验证缓存不变量）
python -m pytest tests/test_lowres.py         # 回归测试
```

## 接入方式（不改镜头代码）

镜头契约不变：`Shot.fn(canvas, tl, shot, ctx, cache)`，仍按 `ctx.w / ctx.h` 与画布作画。

```python
from mvrender.lowres import LowResRenderer

# 与 GpuRenderer(shots, ctx, lyrics, w=, h=) 同签名，多出 scale / style
r = LowResRenderer(shots, ctx, w=1080, h=1920, scale=4, style="ink")
frame = r.render_u8(t)     # uint8 全分辨率帧（已整数放大 + 风格化）
```

- `scale=None` 时由 `plan.py` 自动求最优（默认逻辑像素预算 480×270）。
- `style` 取 `"pixel"` / `"ink"`，也可直接传 `StylePack` 实例。
- 渲染器在**逻辑分辨率**下完成 draw / 相机 / 转场 / 后处理 / 风格化，
  最后整数倍最近邻放大；歌词在全分辨率上叠（避免低分辨率字形糊）。

## 风格包协议

一个风格包只回答两个问题，因此与题材无关：

```python
class StylePack:
    id: str
    def background(self, w, h, ctx, shot):   # 镜头内不变的底色（可缓存），或 None
        return None
    def apply(self, img, t, ctx, shot, bg):  # 把「像素能量图」翻译成该风格画面
        ...
```

风格包不知道镜头画的是什么（楼、雨、人、字），只知道画布上的像素能量；
所以同一批镜头代码可以换风格出片 —— 这是「题材无关」的关键。

## 关键不变量：底色缓存

`background(...)` 是**镜头内不变量**（宣纸底 / 夜色底不随帧变化），由渲染器按
`(镜头名, 逻辑尺寸)` 缓存，只算一次。实测（`diag_bottleneck.py`）：

```
首帧（缓存 miss）: background ≈ 14 ms
稳态（缓存 hit） : background ≈ 0.003 ms   ← 快 4000x
```

早期实现漏了这一步，每帧重算分形噪声纸底，占了 94% 的耗时 —— 这正是
「缓存不变量」原则要防的坑。

## 诚实的边界

- 这是**画质换速度**的档位：放大会显出像素块，细节 / 复杂光影损失明显。
- 适合快速试稿、批量出片、像素 / 水墨 / 扁平风格；不适合要求高画质的最终成品。
- `bench_real.py` 默认跑**合成镜头**（不依赖外部工程），测的是本档自身开销；
  真实数字请用 `--root <曲目工程>` 指向实际工程。
