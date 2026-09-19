"""OpenCL 内核源码（全量 GPU 化算子）。

设计原则
--------
1. **融合**：把 CPU 侧多个全帧 pass 合并成一次读写，减少 23.7MB/帧 的搬运。
2. **常驻**：画布/掩膜表/噪声/字体位图一旦上传就不再回读，只在出帧时回读 u8。
3. **小内核**：AMD 编译器对复杂内核易慢/失败，保持每个 kernel 逻辑简单。
4. **数据布局**：全帧 float32 一律按 `n*3` 的扁平 BGR 存；掩膜是 `n` 的扁平标量。

已实现算子（对应 CPU 侧热点）
------------------------------
compose/k_add/k_add_mask/k_blend_scalar/k_blend_mask/k_mul_scalar/k_mul_mask
k_canvas_out / k_post_tonemap / k_transition
k_radial_glow       —— radial_glow 掩膜全 GPU 生成（省 mgrid+sqrt 42ms）
k_glow_premul       —— 掩膜 × 预乘色（融合三通道乘法）
k_gauss_h/k_gauss_v —— 分离高斯（水平/垂直），替代大核 GaussianBlur
k_warp              —— 仿射采样（替代 cv2.warpAffine，相机运动）
k_resize_bilinear   —— 双线性缩放（降采样链）
k_noise_compose     —— 值噪声叠加（云/雾，可选）
"""

KERNEL_SRC = r"""
/* ───────────────────────── 工具 ───────────────────────── */
static inline uchar lut8(__global const uchar *lut, float v)
{
    int u = (int)v;                       /* 向零截断，与 numpy astype 一致 */
    u = (u < 0) ? 0 : ((u > 255) ? 255 : u);
    return lut[u];
}

static inline float clampf(float v, float lo, float hi)
{
    return (v < lo) ? lo : ((v > hi) ? hi : v);
}

/* ───────────────────── 画布合成 ───────────────────── */

/* buf += layer */
__kernel void k_add(__global float *buf, __global const float *layer)
{
    const int i = get_global_id(0);
    buf[i] += layer[i];
}

/* buf += layer * mask（mask 为 HxW 标量） */
__kernel void k_add_mask(__global float *buf,
                         __global const float *layer,
                         __global const float *mask)
{
    const int i = get_global_id(0);
    buf[i] += layer[i] * mask[i / 3];
}

/* buf = buf*(1-a) + layer*a（标量 alpha） */
__kernel void k_blend_scalar(__global float *buf,
                             __global const float *layer,
                             const float a)
{
    const int i = get_global_id(0);
    buf[i] = buf[i] * (1.0f - a) + layer[i] * a;
}

/* buf = buf*(1-a) + layer*a（HxW alpha） */
__kernel void k_blend_mask(__global float *buf,
                           __global const float *layer,
                           __global const float *alpha)
{
    const int i = get_global_id(0);
    const float a = alpha[i / 3];
    buf[i] = buf[i] * (1.0f - a) + layer[i] * a;
}

/* buf *= m（标量） */
__kernel void k_mul_scalar(__global float *buf, const float m)
{
    const int i = get_global_id(0);
    buf[i] *= m;
}

/* buf *= m（HxW） */
__kernel void k_mul_mask(__global float *buf, __global const float *m)
{
    const int i = get_global_id(0);
    buf[i] *= m[i / 3];
}

/* ───────────────────── 输出 / 后处理 ───────────────────── */

/* Canvas.out：一次曲线 + uint8（与 vis_core.Canvas.out 逐像素一致） */
__kernel void k_canvas_out(__global const float *buf,
                           __global uchar *out,
                           const float gain)
{
    const int i = get_global_id(0);
    float x = buf[i] * gain;
    if (x < 0.0f) x = 0.0f;
    if (x > 255.0f) x = 255.0f;
    float v = 255.0f * (x / 255.0f) / (1.0f + 0.25f * (x / 255.0f)) * 1.25f;
    out[i] = (uchar)((v < 0.0f) ? 0.0f : ((v > 255.0f) ? 255.0f : v));
}

/* Renderer.post + to_uint8 融合：img*vig*gain → +flash → 通道乘子 → clip/u8 → LUT

   cmul_b/g/r 为「段落级色调」的逐通道乘子（图普通为 1,1,1；夜戏 B 通道 1.05；
   桥段/尾段分别对 R/B、R/G 微调）——把 CPU post 里三处 `img[:,:,k] *= x`
   一并融合，避免多次全帧 pass。 */
__kernel void k_post_tonemap(__global const float *img,
                             __global const float *vig,
                             __global const uchar *lut,
                             __global uchar *out,
                             const float gain, const float flash,
                             const float cmul_b, const float cmul_g, const float cmul_r)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    float v = img[i] * vig[pix] * gain + flash;
    v *= (c == 0) ? cmul_b : ((c == 1) ? cmul_g : cmul_r);
    out[i] = lut8(lut, v);
}

/* post 前置（输出 float，不做 tonemap）：img*vig*gain → +flash → 通道乘子

   为何单独一个 kernel：CPU 主循环顺序是 `post → 歌词 → to_uint8`，歌词必须
   作用在 post 后的 float 域。若把 post 与 tonemap 融成一个 kernel，就没法在
   中间插入歌词层；若整段 post 走 CPU，则每帧多两次全帧（±23.7MB）搬运。 */
__kernel void k_post_float(__global const float *img,
                           __global const float *vig,
                           __global float *out,
                           const float gain, const float flash,
                           const float cmul_b, const float cmul_g, const float cmul_r)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    float v = img[i] * vig[pix] * gain + flash;
    v *= (c == 0) ? cmul_b : ((c == 1) ? cmul_g : cmul_r);
    out[i] = v;
}

/* 转场合成：old*(1-m) + new*m (+ring*glow) */
__kernel void k_transition(__global const float *oldp,
                           __global const float *newp,
                           __global const float *mask,
                           __global const float *ringp,
                           __global float *out,
                           const float cb, const float cg, const float cr,
                           const int use_ring)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const float m = mask[pix];
    float v = oldp[i] * (1.0f - m) + newp[i] * m;
    if (use_ring) {
        /* 系数必须是 1.4（与 core.transition.apply_transition 一致）。
           旧 ocl_gpu_kernel.py 写成 1.6，会让转场发光环偏亮（实测 max|Δ| 104）。 */
        const float rg = clampf(ringp[pix], 0.0f, 1.0f) * 1.4f;
        const int c = i % 3;
        v += rg * ((c == 0) ? cb : ((c == 1) ? cg : cr));
    }
    out[i] = v;
}

/* ───────────────────── 径向光晕（全 GPU 生成掩膜）───────────────────── */

/* 与 vis_core.radial_glow 数值等价：掩膜在 device 上生成并就地乘预乘色。
   cx/cy 为归一化坐标；r 相对 min(w,h)；edge 收束外圈（0=不启用）。
   输出 out[i] = mask[pix] * color[c] * gain */
__kernel void k_radial_glow(__global float *out,
                            const int w, const int h,
                            const float cx, const float cy, const float r,
                            const float power, const float feather,
                            const float edge,           /* <=0 表示不启用 */
                            const float cb, const float cg, const float cr,
                            const float gain)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int x = pix % w;
    const int y = pix / w;
    const float mn = (w < h) ? (float)w : (float)h;
    const float dx = ((float)x / (float)w - cx) * ((float)w / mn);
    const float dy = ((float)y / (float)h - cy) * ((float)h / mn);
    const float d = sqrt(dx * dx + dy * dy);
    const float rr = (r > 1e-4f) ? r : 1e-4f;
    const float fe = (feather > 1e-3f) ? feather : 1e-3f;
    float m = 1.0f / (1.0f + pow(d / rr, power) * (1.0f / fe));
    if (edge > 0.0f) {
        const float e0 = edge;
        const float f = clampf(1.0f - (d - e0) / (e0 * 0.45f), 0.0f, 1.0f);
        m = m * (f * f * (3.0f - 2.0f * f));
    }
    const int c = i % 3;
    const float col = (c == 0) ? cb : ((c == 1) ? cg : cr);
    out[i] = m * col * gain;
}

/* 掩膜 × 预乘色：out(HxWx3) = mask(HxW) * color，供缓存掩膜后复用 */
__kernel void k_glow_premul(__global float *out,
                            __global const float *mask,
                            const float cb, const float cg, const float cr)
{
    const int i = get_global_id(0);
    const int c = i % 3;
    const float col = (c == 0) ? cb : ((c == 1) ? cg : cr);
    out[i] = mask[i / 3] * col;
}

/* 掩膜生成（HxW 标量），供缓存后多次预乘使用 */
__kernel void k_glow_mask(__global float *out,
                          const int w, const int h,
                          const float cx, const float cy, const float r,
                          const float power, const float feather, const float edge)
{
    const int pix = get_global_id(0);
    const int x = pix % w;
    const int y = pix / w;
    const float mn = (w < h) ? (float)w : (float)h;
    const float dx = ((float)x / (float)w - cx) * ((float)w / mn);
    const float dy = ((float)y / (float)h - cy) * ((float)h / mn);
    const float d = sqrt(dx * dx + dy * dy);
    const float rr = (r > 1e-4f) ? r : 1e-4f;
    const float fe = (feather > 1e-3f) ? feather : 1e-3f;
    float m = 1.0f / (1.0f + pow(d / rr, power) * (1.0f / fe));
    if (edge > 0.0f) {
        const float e0 = edge;
        const float f = clampf(1.0f - (d - e0) / (e0 * 0.45f), 0.0f, 1.0f);
        m = m * (f * f * (3.0f - 2.0f * f));
    }
    out[pix] = m;
}

/* ───────────────────── 分离高斯（替代大核 GaussianBlur）───────────────────── */

/* 水平方向一维高斯：src/dst 均为 HxW 单通道 float32，本 kernel 只写 dst */
__kernel void k_gauss_h(__global const float *src,
                        __global float *dst,
                        __global const float *kern,
                        const int w, const int h, const int rad)
{
    const int pix = get_global_id(0);
    const int x = pix % w;
    const int y = pix / w;
    float acc = 0.0f;
    for (int k = -rad; k <= rad; k++) {
        int xx = x + k;
        if (xx < 0) xx = 0;
        if (xx >= w) xx = w - 1;
        acc += src[y * w + xx] * kern[k + rad];
    }
    dst[pix] = acc;
}

/* 垂直方向一维高斯 */
__kernel void k_gauss_v(__global const float *src,
                        __global float *dst,
                        __global const float *kern,
                        const int w, const int h, const int rad)
{
    const int pix = get_global_id(0);
    const int x = pix % w;
    const int y = pix / w;
    float acc = 0.0f;
    for (int k = -rad; k <= rad; k++) {
        int yy = y + k;
        if (yy < 0) yy = 0;
        if (yy >= h) yy = h - 1;
        acc += src[yy * w + x] * kern[k + rad];
    }
    dst[pix] = acc;
}

/* 三通道分离高斯的水平/垂直共用（按 HxWx3 扁平化，逐通道模糊） */
__kernel void k_gauss3_h(__global const float *src,
                         __global float *dst,
                         __global const float *kern,
                         const int w, const int h, const int rad)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    const int x = pix % w;
    const int y = pix / w;
    float acc = 0.0f;
    for (int k = -rad; k <= rad; k++) {
        int xx = x + k;
        if (xx < 0) xx = 0;
        if (xx >= w) xx = w - 1;
        acc += src[(y * w + xx) * 3 + c] * kern[k + rad];
    }
    dst[i] = acc;
}

__kernel void k_gauss3_v(__global const float *src,
                         __global float *dst,
                         __global const float *kern,
                         const int w, const int h, const int rad)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    const int x = pix % w;
    const int y = pix / w;
    float acc = 0.0f;
    for (int k = -rad; k <= rad; k++) {
        int yy = y + k;
        if (yy < 0) yy = 0;
        if (yy >= h) yy = h - 1;
        acc += src[(yy * w + x) * 3 + c] * kern[k + rad];
    }
    dst[i] = acc;
}

/* ───────────────────── 几何：仿射采样 / 缩放 ───────────────────── */

/* 仿射重映射（BORDER_REPLICATE，双线性）：替代 cv2.warpAffine
   M = [a00 a01 a02; a10 a11 a12]（2x3），输出与输入同尺寸 HxWx3。

   【坐标约定】必须与 cv2.warpAffine 一致：M 表达的是「**源 → 目标**」变换，
   故目标像素取源的坐标要用**逆变换 M⁻¹** 计算。实测（本机 cv2 5.0）：
     M=[[1,0,10],[0,1,-7]] ⇒ dst[100,200] == src[107,190]
   即 sx = x - 10、sy = y + 7（取反的平移）。若按 M 正向取源会给出
   镜像/反向位移（实测偏差达 194/255，整幅错位）。
   为保持接口与 cv2 一致，这里在 CPU 侧先对 M 求逆再传入 kernel。 */
__kernel void k_warp3(__global const float *src, __global float *dst,
                      const float a00, const float a01, const float a02,
                      const float a10, const float a11, const float a12,
                      const int w, const int h)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    const int x = pix % w;
    const int y = pix / w;
    const float sx = a00 * (float)x + a01 * (float)y + a02;
    const float sy = a10 * (float)x + a11 * (float)y + a12;
    float fx = clampf(sx, 0.0f, (float)(w - 1));
    float fy = clampf(sy, 0.0f, (float)(h - 1));
    const int x0 = (int)fx;
    const int y0 = (int)fy;
    const int x1 = (x0 + 1 < w) ? (x0 + 1) : x0;
    const int y1 = (y0 + 1 < h) ? (y0 + 1) : y0;
    const float tx = fx - (float)x0;
    const float ty = fy - (float)y0;
    const float v00 = src[(y0 * w + x0) * 3 + c];
    const float v10 = src[(y0 * w + x1) * 3 + c];
    const float v01 = src[(y1 * w + x0) * 3 + c];
    const float v11 = src[(y1 * w + x1) * 3 + c];
    const float top = v00 + (v10 - v00) * tx;
    const float bot = v01 + (v11 - v01) * tx;
    dst[i] = top + (bot - top) * ty;
}

/* 双线性缩放（单通道）：替代 cv2.resize(INTER_LINEAR) */
__kernel void k_resize1(__global const float *src,
                        __global float *dst,
                        const int sw, const int sh,
                        const int dw, const int dh)
{
    const int pix = get_global_id(0);
    const int x = pix % dw;
    const int y = pix / dw;
    const float sx = ((float)x + 0.5f) * ((float)sw / (float)dw) - 0.5f;
    const float sy = ((float)y + 0.5f) * ((float)sh / (float)dh) - 0.5f;
    float fx = clampf(sx, 0.0f, (float)(sw - 1));
    float fy = clampf(sy, 0.0f, (float)(sh - 1));
    const int x0 = (int)fx;
    const int y0 = (int)fy;
    const int x1 = (x0 + 1 < sw) ? (x0 + 1) : x0;
    const int y1 = (y0 + 1 < sh) ? (y0 + 1) : y0;
    const float tx = fx - (float)x0;
    const float ty = fy - (float)y0;
    const float v00 = src[y0 * sw + x0];
    const float v10 = src[y0 * sw + x1];
    const float v01 = src[y1 * sw + x0];
    const float v11 = src[y1 * sw + x1];
    const float top = v00 + (v10 - v00) * tx;
    const float bot = v01 + (v11 - v01) * tx;
    dst[pix] = top + (bot - top) * ty;
}

/* 双线性缩放（HxWx3 扁平） */
__kernel void k_resize3(__global const float *src,
                        __global float *dst,
                        const int sw, const int sh,
                        const int dw, const int dh)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    const int x = pix % dw;
    const int y = pix / dw;
    const float sx = ((float)x + 0.5f) * ((float)sw / (float)dw) - 0.5f;
    const float sy = ((float)y + 0.5f) * ((float)sh / (float)dh) - 0.5f;
    float fx = clampf(sx, 0.0f, (float)(sw - 1));
    float fy = clampf(sy, 0.0f, (float)(sh - 1));
    const int x0 = (int)fx;
    const int y0 = (int)fy;
    const int x1 = (x0 + 1 < sw) ? (x0 + 1) : x0;
    const int y1 = (y0 + 1 < sh) ? (y0 + 1) : y0;
    const float tx = fx - (float)x0;
    const float ty = fy - (float)y0;
    const float v00 = src[(y0 * sw + x0) * 3 + c];
    const float v10 = src[(y0 * sw + x1) * 3 + c];
    const float v01 = src[(y1 * sw + x0) * 3 + c];
    const float v11 = src[(y1 * sw + x1) * 3 + c];
    const float top = v00 + (v10 - v00) * tx;
    const float bot = v01 + (v11 - v01) * tx;
    dst[i] = top + (bot - top) * ty;
}

/* ───────────────────── 大核高斯（降采样等价）───────────────────── */

/* 一维盒式模糊（水平/垂直）：降采样后的大 σ 高斯用盒式近似，误差可忽略。
   仅用于 downsample 路径（σ 已按 N 缩小）。 */
__kernel void k_box_h(__global const float *src, __global float *dst,
                      const int w, const int h, const int rad)
{
    const int pix = get_global_id(0);
    const int x = pix % w;
    const int y = pix / w;
    float acc = 0.0f;
    int cnt = 0;
    for (int k = -rad; k <= rad; k++) {
        int xx = x + k;
        if (xx < 0) continue;
        if (xx >= w) continue;
        acc += src[y * w + xx];
        cnt++;
    }
    dst[pix] = (cnt > 0) ? acc / (float)cnt : src[pix];
}

__kernel void k_box_v(__global const float *src, __global float *dst,
                      const int w, const int h, const int rad)
{
    const int pix = get_global_id(0);
    const int x = pix % w;
    const int y = pix / w;
    float acc = 0.0f;
    int cnt = 0;
    for (int k = -rad; k <= rad; k++) {
        int yy = y + k;
        if (yy < 0) continue;
        if (yy >= h) continue;
        acc += src[yy * w + x];
        cnt++;
    }
    dst[pix] = (cnt > 0) ? acc / (float)cnt : src[pix];
}

/* ───────────────────── GPU 端光栅化（批量图元）───────────────────── */

/* 批量矩形填充：layer(HxW 单通道) = max(命中矩形的 alpha)。
   rects 为扁平 [x0,y0,x1,y1,a]*n（已钳到画布）。
   用途：把「CPU zeros + 循环 cv2.rectangle + 全帧广播 + 上传」整条链搬到 GPU ——
   只需上传几百字节的图元参数，GPU 光栅化 + 后续 blur/kernel 合成全在显存。 */
__kernel void k_fill_rects(__global float *layer,
                           __global const float *rects,
                           const int n, const int w, const int h)
{
    const int pix = get_global_id(0);
    const int x = pix % w;
    const int y = pix / w;
    float v = 0.0f;
    for (int i = 0; i < n; i++) {
        const int x0 = (int)rects[i * 5 + 0];
        const int y0 = (int)rects[i * 5 + 1];
        const int x1 = (int)rects[i * 5 + 2];
        const int y1 = (int)rects[i * 5 + 3];
        /* cv2.rectangle(...,-1) 是**闭区间**填充（含两端点），保持一致 */
        if (x >= x0 && x <= x1 && y >= y0 && y <= y1) {
            const float a = rects[i * 5 + 4];
            v = fmax(v, a);
        }
    }
    layer[pix] = fmax(layer[pix], v);
}

/* 批量线段（简单 DDA，无抗锯齿）：lines = [x0,y0,x1,y1,thick,a]*n */
__kernel void k_draw_lines(__global float *layer,
                           __global const float *lines,
                           const int n, const int w, const int h)
{
    const int pix = get_global_id(0);
    const int px = pix % w;
    const int py = pix / w;
    float v = 0.0f;
    for (int i = 0; i < n; i++) {
        const float ax = lines[i * 6 + 0], ay = lines[i * 6 + 1];
        const float bx = lines[i * 6 + 2], by = lines[i * 6 + 3];
        const float th = lines[i * 6 + 4];
        const float a = lines[i * 6 + 5];
        const float dx = bx - ax, dy = by - ay;
        const float len2 = dx * dx + dy * dy + 1e-6f;
        float t = ((float)px - ax) * dx + ((float)py - ay) * dy;
        t = t / len2;
        if (t < 0.0f) t = 0.0f;
        if (t > 1.0f) t = 1.0f;
        const float qx = ax + t * dx - (float)px;
        const float qy = ay + t * dy - (float)py;
        const float d = sqrt(qx * qx + qy * qy);
        /* SDF 抗锯齿（1px 过渡），逼近 cv2.LINE_AA —— 硬边 DDA 与 AA 在斜线边缘
           有 ~55/255 的可辨差异（实测），加 AA 后收敛到边缘级。 */
        const float aa = clampf(th * 0.5f + 0.5f - d, 0.0f, 1.0f);
        if (aa > 0.0f) {
            v = fmax(v, a * aa);
        }
    }
    layer[pix] = fmax(layer[pix], v);
}

/* 批量实心圆：circles = [cx,cy,r,a]*n */
__kernel void k_fill_circles(__global float *layer,
                             __global const float *circles,
                             const int n, const int w, const int h)
{
    const int pix = get_global_id(0);
    const int px = pix % w;
    const int py = pix / w;
    float v = 0.0f;
    for (int i = 0; i < n; i++) {
        const float cx = circles[i * 4 + 0], cy = circles[i * 4 + 1];
        const float r = circles[i * 4 + 2], a = circles[i * 4 + 3];
        const float dx = (float)px - cx, dy = (float)py - cy;
        const float d = sqrt(dx * dx + dy * dy);
        /* SDF 抗锯齿（1px 过渡），逼近 cv2.circle(...,LINE_AA) */
        const float aa = clampf(r + 0.5f - d, 0.0f, 1.0f);
        if (aa > 0.0f) {
            v = fmax(v, a * aa);
        }
    }
    layer[pix] = fmax(layer[pix], v);
}

/* ───────────────────── 稀疏区域合成 ───────────────────── */

/* 把**局部**图层（rw×rh×3）合成到画布 (x0,y0) 区域：
   buf[(y0+y)*cw + (x0+x)] op= layer[y*rw + x]

   用途：稀疏绘制（mvrender.core.sparse）在 GPU 画布上的高效路径——只需上传
   局部小层（几百 KB），不必回读/重传整帧 23.7MB。 */
__kernel void k_add_region(__global float *buf,
                           __global const float *layer,
                           const int x0, const int y0,
                           const int rw, const int rh, const int cw)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    const int x = pix % rw;
    const int y = pix / rw;
    const long di = ((long)(y0 + y) * cw + (x0 + x)) * 3 + c;
    buf[di] += layer[i];
}

/* 滚动纹理合成：buf += tex[(y+yoff)%h, (x+xoff)%w] * color * gain

   用于雨幕等「静态纹理 + 每帧循环位移」的层：原实现每帧
   np.roll(tex)（全帧 8.3MB 拷贝）+ `tex[:,:,None]*tint*w`（全帧广播）
   + 全帧上传，三层即 ~75MB/帧。改为纹理一次常驻显存、每帧只跑一个 kernel。 */
__kernel void k_rain(__global float *buf,
                     __global const float *tex,
                     const int w, const int h,
                     const int yoff, const int xoff,
                     const float cb, const float cg, const float cr)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    const int x = pix % w;
    const int y = pix / w;
    /* 与 numpy 一致：np.roll(tex, off)[y] = tex[(y - off) % n] */
    int sy = y - yoff;
    sy = sy % h; if (sy < 0) sy += h;
    int sx = x - xoff;
    sx = sx % w; if (sx < 0) sx += w;
    const float t = tex[sy * w + sx];
    const float col = (c == 0) ? cb : ((c == 1) ? cg : cr);
    buf[i] += t * col;
}

/* 区域覆盖 / 相乘（mode: 0=over, 1=mul） */
__kernel void k_region_mode(__global float *buf,
                            __global const float *layer,
                            __global const float *alpha,
                            const int has_alpha,
                            const int x0, const int y0,
                            const int rw, const int rh, const int cw,
                            const int mode)  /* 0=over 1=mul */
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    const int x = pix % rw;
    const int y = pix / rw;
    const long di = ((long)(y0 + y) * cw + (x0 + x)) * 3 + c;
    const float a = has_alpha ? alpha[pix] : 1.0f;
    if (mode == 1) {
        buf[di] *= layer[i] * a;
    } else {
        buf[di] = buf[di] * (1.0f - a) + layer[i] * a;
    }
}

/* ───────────────────── 元素层重算子 ───────────────────── */

/* 升采样（双线性）+ 乘标量 gain：融合 glow_layer 的 `resize(LINEAR) → *gain`。
   仅用于**放大**，与 cv2.INTER_LINEAR 语义一致。 */
__kernel void k_scale1(__global const float *src, __global float *dst,
                       const int sw, const int sh, const int dw, const int dh,
                       const float gain)
{
    const int pix = get_global_id(0);
    const int x = pix % dw;
    const int y = pix / dw;
    const float sx = ((float)x + 0.5f) * ((float)sw / (float)dw) - 0.5f;
    const float sy = ((float)y + 0.5f) * ((float)sh / (float)dh) - 0.5f;
    float fx = clampf(sx, 0.0f, (float)(sw - 1));
    float fy = clampf(sy, 0.0f, (float)(sh - 1));
    const int x0 = (int)fx;
    const int y0 = (int)fy;
    const int x1 = (x0 + 1 < sw) ? (x0 + 1) : x0;
    const int y1 = (y0 + 1 < sh) ? (y0 + 1) : y0;
    const float tx = fx - (float)x0;
    const float ty = fy - (float)y0;
    const float v00 = src[y0 * sw + x0];
    const float v10 = src[y0 * sw + x1];
    const float v01 = src[y1 * sw + x0];
    const float v11 = src[y1 * sw + x1];
    const float top = v00 + (v10 - v00) * tx;
    const float bot = v01 + (v11 - v01) * tx;
    dst[pix] = (top + (bot - top) * ty) * gain;
}

/* 降采样：**盒式区域平均**（等价 cv2.INTER_AREA），用于 glow 的 1/N 缩小。

   为什么不能用双线性：scale=6 时双线性只取 4 个样本，会漏掉 36 个源像素的
   大部分能量，导致光晕整体偏暗/抖动（实测 8bit 误差达 25）。区域平均与
   INTER_AREA 语义一致（对整除倍率几乎逐像素相同）。 */
__kernel void k_downsample1(__global const float *src, __global float *dst,
                            const int sw, const int sh, const int dw, const int dh)
{
    const int pix = get_global_id(0);
    const int x = pix % dw;
    const int y = pix / dw;
    const int x0 = x * sw / dw;
    const int x1 = (x + 1) * sw / dw;
    const int y0 = y * sh / dh;
    const int y1 = (y + 1) * sh / dh;
    float acc = 0.0f;
    int cnt = 0;
    for (int yy = y0; yy < y1; yy++) {
        for (int xx = x0; xx < x1; xx++) {
            acc += src[yy * sw + xx];
            cnt++;
        }
    }
    dst[pix] = (cnt > 0) ? (acc / (float)cnt) : 0.0f;
}

/* 元素层合成：buf += mask(HxW) * color * gain —— 融合 mist 等
   「标量场 × 颜色广播」的整帧临时数组（原为 23.7MB/次） */
__kernel void k_add_field(__global float *buf,
                          __global const float *field,
                          const float cb, const float cg, const float cr,
                          const float gain)
{
    const int i = get_global_id(0);
    const int c = i % 3;
    const float col = (c == 0) ? cb : ((c == 1) ? cg : cr);
    buf[i] += field[i / 3] * col * gain;
}

/* mist 一类：由预生成的噪声场 + 时间/漂移参数直接算出标量场并合成，
   省掉 CPU 侧的 mgrid/sin/cos/广播（实测 mist 129ms/镜头）。 */
__kernel void k_mist(__global float *buf,
                     __global const float *noise,
                     const int w, const int h,
                     const float t, const float drift_x, const float drift_y,
                     const float op,
                     const float cb, const float cg, const float cr)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const int c = i % 3;
    const int x = pix % w;
    const int y = pix / w;
    const float n = noise[pix];
    const float yy = (float)y * 0.0016f;
    const float xx = (float)x * 0.0016f;
    float s = sin(xx * 3.0f + t * drift_x * 6.283f + n * 5.0f)
            * cos(yy * 2.0f - t * drift_y * 6.283f + n * 4.0f);
    s = s * 0.5f + 0.5f;
    float m = pow(s, 1.5f) * op * (0.4f + 0.6f * n);
    const float col = (c == 0) ? cb : ((c == 1) ? cg : cr);
    buf[i] += m * col;
}

/* ───────────────────── 图层贴图（layer_alpha）───────────────────── */

/* 把局部图层 sub（区域尺寸）以 over/add 方式贴到 buf 的 (x0,y0)：
   这里按「全帧索引」分发，越界像素自行跳过。sub 为全画布尺寸的稀疏图层时最省。 */
__kernel void k_layer_over(__global float *buf,
                           __global const float *layer,   /* HxWx3 全画布稀疏 */
                           __global const float *alpha,   /* HxW 全画布（可为 0 长度表示标量） */
                           const int has_alpha, const float a0)
{
    const int i = get_global_id(0);
    const int pix = i / 3;
    const float a = has_alpha ? alpha[pix] * a0 : a0;
    buf[i] = buf[i] * (1.0f - a) + layer[i] * a;
}
"""
