# FastMV 底座 API（mvrender）

> **定位**：这是一个**通用渲染底座**，不是某一部 MV 的实现。
> 它告诉 AI「可以做到什么」——具体怎么画，由每个项目（MV / 电影 / 短剧 / 动画）
> 当时自行决定。项目只依赖这里的接口，换项目无需改底座。

---

## 0. 一条铁律：速度优先

本项目以**速度**为根本指标（AI 画图本身画质有限，用极致的速度换取快速迭代，
必要时可降分辨率提速度）。因此：

- **能用 GPU 原语画的，就不要用 CPU numpy 全帧运算**（后者是速度杀手）
- 画质差异预算：**全屏 mean < 0.5/255、无可见伪影**即视为等价（不追像素零差）
- 分辨率可按需降（1080×1920 → 720×1280 等），代价是画质换速度

---

## 1. 快速开始

```python
from mvrender.gpu.canvas_gpu import GPUCanvas
from mvrender.gpu import Runtime

rt = Runtime.get(w=1080, h=1920)      # 进程级单例（设备/缓冲池/kernel 缓存）
c = GPUCanvas(1080, 1920, rt=rt)      # GPU 常驻画布

# ── 画元素（全部在 GPU 上完成，CPU 只传参数）──
c.fill_rects([(x, y, x+w, y+h, 1.0)], color=(60, 80, 120), gain=1.0)
c.draw_lines([(x0, y0, x1, y1, thickness, alpha)], color=(200, 220, 240), gain=1.0)
c.fill_circles([(cx, cy, r, alpha)], color=(255, 200, 100), gain=1.0)
c.rasterize(rects=[...], lines=[...], circles=[...], polys=[...],
            color=..., gain=..., blur=0.0)

# ── 出帧 ──
img_u8 = c.out()          # Canvas.out 曲线 + uint8（GPU 完成，只回读一次）
```

---

## 2. 绘制原语（GPU 端光栅化）

统一特点：**CPU 只上传图元参数（几百字节），光栅化 + 模糊 + 乘色合成全在显存**。
相比「CPU `np.zeros((H,W))` + `cv2.xxx` + 全帧广播 + 上传」，实测快 10–20x。

| API | 图元格式 | 用途 |
|---|---|---|
| `fill_rects(rects, color, gain, blur)` | `[(x0,y0,x1,y1,alpha), ...]` 闭区间填充 | 楼体、窗框、UI 板、字幕底 |
| `round_rects(rounds, ...)` | `[(x0,y0,x1,y1,radius,alpha), ...]` | 手机屏、卡片、圆角 UI |
| `stroke_rects(rects, ...)` | `[(x0,y0,x1,y1,thickness,alpha), ...]` | 矩形描边 |
| `draw_lines(lines, ...)` | `[(x0,y0,x1,y1,thickness,alpha), ...]` | 灯杆、栏杆、连线、笔触 |
| `fill_circles(circles, ...)` | `[(cx,cy,r,alpha), ...]` | 灯泡、点、圆点 |
| `fill_polys(polys, ...)` | `[([(x,y),...], alpha), ...]`（≤8 顶点） | 光锥、三角、任意多边形 |
| `rasterize(rects=, lines=, circles=, polys=, round_rects=, stroke_rects=, color, gain, blur, blur_n)` | 上述任意组合 | **一次光栅化多类图元到同一图层**（推荐） |

> **为什么用 `rasterize` 合并**：rects 与 lines 若分两次 `blur+add`，边界处与
> 「全部图元一起模糊」有差异。合并到同一图层才与 CPU 版一致。
>
> **抗锯齿**：line/circle/poly/round_rect 均带 SDF/超采样 AA，与 cv2 `LINE_AA`
> 覆盖率误差 < 1%（`python -m mvrender.gpu.prim_selftest` 门禁）。
>
> **`blur_n`**：降采样倍数。对齐 `vis_core.glow_layer(scale=N)` 语义时必传
> （如 `blur=10, blur_n=6` ⇔ `glow_layer(layer, 10, gain, scale=6)`）。

---

## 3. 合成与贴图

| API | 语义 | 说明 |
|---|---|---|
| `add(layer)` | `buf += layer`（全帧） | layer 需为 `(H,W,3)` |
| `add_region(layer, x0, y0, mode)` | `buf[区域] += layer` | 局部图层（几百 KB 上传） |
| `over_region(layer, x0, y0, op, mask)` | `buf = buf*(1-a) + layer*a` | 局部 over 贴图（等价 `layer_alpha`） |
| `mul_region(layer, x0, y0)` | `buf[区域] *= layer` | 局部压暗 |
| `add_text_mask(mask, x0, y0, color, gain)` | `buf[区域] += mask*color*gain` | 文字遮罩（CPU 出字形 + GPU 合成） |
| `glow_add(cx, cy, r, color, gain, edge)` | 径向光晕（归一化坐标） | 掩膜按参数缓存于显存 |
| `add_rolled(tex, yoff, xoff, color, gain)` | 纹理循环位移合成 | **雨幕/雪/流动纹理**：纹理常驻，每帧一个 kernel |
| `fill_gradient(top, bottom, gamma)` | 竖直渐变 | |

---

## 4. 渲染器与出片

```python
from mvrender.core.renderer_gpu import GpuRenderer
from mvrender.core.shotcache import ShotCache
from mvrender.core.encode import video_args

R = GpuRenderer(shots, ctx, lyrics, w=1080, h=1920)   # shots 为项目自己的镜头表
img = R.render_u8(t)                                   # 单帧 GPU 全管线

# 编码（优先 AMD AMF 硬件编码，GPU 编码，快 12x 且不占 CPU）
cmd = [..., *video_args(quality="balanced", bitrate="12M"), out]

# 镜头级增量缓存：改一镜只重渲一镜
cache = ShotCache(root)
cache.render_range(R, "A8")            # 只渲 A8，其余复用
cache.invalidate("A8.rain")            # 细粒度失效（按 Shot.deps 标签）
```

| 模块 | 职责 |
|---|---|
| `core/project.py` | `Project` / `Shot` / `Ctx` 契约（项目提供 timeline + shots） |
| `core/renderer.py` | CPU 参考渲染器（一致性基准） |
| `core/renderer_gpu.py` | GPU 全管线（画布→warp→转场→post→歌词→tonemap） |
| `core/lyrics.py` | 歌词/字幕（**区域化**：只在字幕带内计算与传输） |
| `core/shotcache.py` | 镜头级缓存 + `invalidate` |
| `core/encode.py` | 硬件编码器选择（AMF > NVENC > QSV > libx264） |
| `gpu/element_ops.py` | 通用绘图内核接管（大 σ 高斯降采样 / glow_layer） |

---

## 5. 性能决策表（画元素时照着选）

| 元素特征 | 该怎么画 | 原因 |
|---|---|---|
| 大面积实心（楼、墙、天空块） | `fill_rects` / `rasterize` | GPU 光栅化免去 CPU 全帧 |
| 大核发光/模糊（σ≥16） | `rasterize(..., blur=σ, blur_n=N)` | GPU 降采样高斯 |
| 细线/图标（稀疏） | `draw_lines`（少量）或 **CPU 画 + add_region** | 极稀疏时 CPU cv2 更快 |
| 文字 | CPU `render_text_mask` + `add_text_mask` | 字形用 PIL，合成走 GPU |
| 流动纹理（雨/雪/水纹） | `add_rolled`（纹理常驻） | 每帧只跑一个位移 kernel |
| 全帧渐变 | `fill_gradient`（GPU） | 免 CPU 广播 |
| **整帧 numpy 运算** | **避免**（这是最大的速度杀手） | 用上面的原语替代 |

> **经验值**（本机 gfx1200）：GPU 全帧单算子 ≈ **0.2ms**；
> CPU 全帧乘加 ≈ **7ms**；一次 23.7MB 上传 ≈ **3ms**。
> 因此「GPU 原语 + 区域上传」远优于「CPU 全帧运算 + 整帧上传」。

---

## 6. 验证与门禁

```bash
python -m mvrender.gpu.selftest        # 算子一致性（画布/高斯/warp/后处理）
python -m mvrender.gpu.prim_selftest   # 绘制原语 vs cv2 覆盖率门禁
python -m mvrender.selftest <项目根>    # 端到端 CPU/GPU 一致性 + 性能
```

---

## 7. 项目作者清单（写新项目时）

1. 提供 `timeline.json` + `beats.json` + `shots`（`build_shots(tl)` 返回镜头定义）
2. 元素函数签名统一为 `fn(canvas, tl, shot, ctx, cache)`，在 `canvas` 上作画
3. **优先用本文件第 2/3 节的 GPU 原语**，避免 `np.zeros((H,W))+cv2+全帧广播`
4. 需要大核模糊/发光时用 `rasterize(..., blur=, blur_n=)`，不要自己写全帧 numpy
5. 出片用 `core/encode.video_args()`（硬件编码）
6. 迭代时用 `ShotCache`（改一镜只重渲一镜）
