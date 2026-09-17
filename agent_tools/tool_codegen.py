# -*- coding: utf-8 -*-
"""代码生图（image_codegen）：结构化图像优先走确定性代码通道 + 视觉自评迭代。

设计取自「模型缺的是手不是脑」的工程补法：
    brief / 现有源码 → 模型写自包含 HTML/SVG → Edge 渲染 PNG → 多模态自评 →
    不达标则按审查意见改源码重渲（≤N 轮）→ 交付 PNG + 可再编辑源码。

面向**低熵、强结构**的图像：插画 / 图标 / 信息图 / 像素风 / UI 线框 / 示意图 / 海报 / 标志。
写实照片、高频质感、复杂光照仍应交给 image_generate（扩散后端）——两者互补而非替代。
"""
import os
import re
import time

import permissions
from toolkit import tool
from shared import clamp_int
import deepseek_client as _dc
from security import _safe_url
from deepseek_client import get_active_client, _atomic_write, _extract_json_obj, _http_client, _safe_stream, DEFAULT_BASE_URL


_KINDS = ("illustration", "icon", "infographic", "pixel", "ui", "diagram", "poster", "logo")

_KIND_HINTS = {
    "illustration": "整体插画：明确的构图（前景主体 / 中景 / 背景层次）、统一光源与有限色板、可辨识的主体轮廓与材质暗示。",
    "icon": "图标：极简几何、单一视觉隐喻、居中对齐、留白充足、在给定画布内清晰可辨（可加柔和底或描边）。",
    "infographic": "信息图：标题区 + 数据 / 要点分区 + 图形化编码（条形 / 环形 / 图标），信息层级清晰、网格对齐。",
    "pixel": "像素风：低分辨率网格化造型（用 SVG 小矩形拼像素）、有限色板、硬边无抗锯齿、轮廓可读。",
    "ui": "UI 界面（线框或高保真）：规范栅格、组件（导航 / 卡片 / 按钮 / 输入框）、层级与间距一致、真实文案占位。",
    "diagram": "示意图 / 流程图：节点与有向连线，标签清晰、尽量不交叉、留白合理。",
    "poster": "海报：强对比版面，主标题 / 副标题 / 视觉主体分区，网格与对齐讲究，情绪化色板。",
    "logo": "标志：简洁几何符号（可选标准字），单一概念，可缩放，黑白也可辨识。",
}


def _extract_html(text):
    """从模型输出里取出 HTML 源码（剥代码围栏 + 去掉前置说明）。"""
    t = str(text or "").strip()
    m = re.search(r"```(?:html|HTML)?\s*(.*?)```", t, re.S)
    code = m.group(1).strip() if m else t
    i = code.find("<")
    if i > 0:
        code = code[i:]
    return code.strip()


def _author_prompt(brief, kind, width, height, background):
    bg = f"\n- 背景：{background}。" if str(background or "").strip() else ""
    hint = _KIND_HINTS.get(kind, _KIND_HINTS["illustration"])
    return (
        "你是资深视觉设计师兼前端工程师。请只用一份**自包含** HTML 精确画出下面的画面。\n"
        "硬性要求：\n"
        f"- 画布正好 {width}×{height}px：html,body{{margin:0;padding:0;width:{width}px;height:{height}px;overflow:hidden}}。\n"
        "- 不使用任何外部资源 / 网络字体 / 外链图片；只用系统字体（如 \"Microsoft YaHei\", \"Segoe UI\", sans-serif），"
        "一切用 CSS / 内联 SVG 绘制。\n"
        "- 画面要铺满画布、构图完整，不要出现空白占位或「示例」字样。\n"
        "- 结构清晰：先定构图与色板，再画主体，最后处理细节与光影。\n"
        f"- 类型要求：{hint}{bg}\n"
        "只输出一个 ```html 代码块，不要任何解释。\n\n"
        f"画面：{brief}"
    )


def _revise_prompt(html, brief, kind, critique):
    return (
        "这是你上一版 HTML 渲染出的图，视觉审查意见如下（JSON）：\n"
        f"{critique}\n\n"
        f"目标：{brief}（类型：{kind}）。请**针对性**修正问题（保持可渲染、画布尺寸不变），"
        "只输出一份完整的 ```html 代码块，不要解释。\n"
        "上一版源码：\n```html\n"
        f"{html}\n```"
    )


def _critique_prompt(brief, kind):
    return (
        "你是严格的视觉审查员。对照目标画面审查这张图，只输出 JSON（不要多余文字）：\n"
        '{"score": 0-10, "issues": ["具体问题"], "verdict": "ok" 或 "revise"}\n'
        "评分维度：① 主体/构图是否正确清晰 ② 配色是否协调 ③ 细节与完成度 ④ 是否符合该类型风格。\n"
        "score>=7 且无致命问题才可 verdict=ok，否则 revise。\n"
        f"目标画面：{brief}（类型：{kind}）"
    )


def _call(client, system, user, max_tokens=6000):
    resp = client.client.chat.completions.create(
        model=client.model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=int(max_tokens),
        stream=False,
        timeout=120.0,
    )
    return (resp.choices[0].message.content or "").strip()


@tool(
    {
        "type": "function",
        "function": {
            "name": "image_codegen",
            "description": "代码生图：用自包含 HTML/CSS/SVG 确定性生成结构化图像（插画/图标/信息图/像素风/UI线框/示意图/海报/标志），自动渲染成 PNG 并让多模态模型自评、按意见改源码迭代（≤N 轮），同时产出 PNG 与可再编辑源码；写实照片/复杂质感请改用 image_generate",
            "parameters": {
                "type": "object",
                "properties": {
                    "brief": {"type": "string", "description": "要画什么（越具体越好：主体/风格/配色/氛围/文字）"},
                    "kind": {"type": "string", "enum": ["illustration", "icon", "infographic", "pixel", "ui", "diagram", "poster", "logo"], "description": "图像类型（默认 illustration）：插画/图标/信息图/像素/UI/示意图/海报/标志"},
                    "width": {"type": "integer", "description": "可选：画布宽 px（默认 1024，128-2048）"},
                    "height": {"type": "integer", "description": "可选：画布高 px（默认 1024，128-2048）"},
                    "output": {"type": "string", "description": "可选：输出 PNG 绝对路径（须在允许目录内；默认工作区 codegen/）"},
                    "max_rounds": {"type": "integer", "description": "可选：生成-自评-修正最大轮数 1-4（默认 2；1=只生成不自评，省成本）"},
                    "refine_source": {"type": "string", "description": "可选：已有 .html 源码路径，在其基础上迭代（不从头生成）"},
                    "background": {"type": "string", "description": "可选：背景要求（如「深色渐变」「透明」「纯白」）"},
                },
                "required": ["brief"],
            },
        },
    },
    groups=['🎨 媒体与图像'],
    phrases='代码生图（自评迭代）',
    preactivate=(('代码生图', '画个插画', '生成插画', '画个图标', '做个信息图', '画个示意图', '像素画', '画个海报'),),
)
def image_codegen(brief="", kind="illustration", width=1024, height=1024,
                  output="", max_rounds=2, refine_source="", background=""):
    """代码优先 + 视觉自评闭环的结构化图像生成。返回产物路径 / 自评 / 错误。"""
    brief = str(brief or "").strip()
    if not brief:
        return "错误：brief 必填（要画什么）"
    kind = str(kind or "illustration").strip().lower()
    if kind not in _KINDS:
        kind = "illustration"
    try:
        w = clamp_int(width, 1024, lo=128, hi=2048)
        h = clamp_int(height, 1024, lo=128, hi=2048)
        rounds = clamp_int(max_rounds, 2, lo=1, hi=4)
    except (TypeError, ValueError):
        return "错误：width/height/max_rounds 必须是数字"

    client = get_active_client()
    if client is None:
        return "错误：没有可用客户端（请先在设置中配置 API Key）"

    # 输出路径
    base_dir = getattr(_dc, "WORKING_DIR", None) or permissions.WORKSPACE_DIR or os.getcwd()
    ts = time.strftime("%Y%m%d-%H%M%S")
    if str(output or "").strip():
        out = permissions.resolve(output) or ""
        if not out:
            return "错误：输出路径无效"
        if not out.lower().endswith((".png", ".jpg", ".jpeg")):
            out += ".png"
    else:
        out = os.path.join(base_dir, "codegen", f"{kind}_{ts}.png")
    ok, reason = permissions.check_filesystem(out, write=True)
    if not ok:
        return reason

    src_path = os.path.splitext(out)[0] + ".html"

    # 首轮：读现有源码 或 让模型写
    html = ""
    if str(refine_source or "").strip():
        sp = permissions.resolve(refine_source)
        if not sp or not os.path.isfile(sp):
            return f"错误：refine_source 不存在：{refine_source}"
        okr, reason_r = permissions.check_filesystem(sp, write=False)
        if not okr:
            return reason_r
        try:
            with open(sp, "r", encoding="utf-8", errors="replace") as f:
                html = f.read(2_000_000)
        except OSError as e:
            return f"错误：读取 refine_source 失败：{e}"

    system = "你是资深视觉设计师兼前端工程师，擅长用纯 CSS/SVG 精确作画。"
    score = None
    issues = []
    verdict = ""
    critique_text = ""
    rounds_used = 0

    for rnd in range(rounds):
        rounds_used = rnd + 1
        try:
            if rnd == 0 and html:
                pass  # 用已有源码
            elif rnd == 0:
                html = _extract_html(_call(client, system, _author_prompt(brief, kind, w, h, background)))
            else:
                html = _extract_html(_call(client, system, _revise_prompt(html, brief, kind, critique_text)))
        except Exception as e:  # noqa: BLE001
            return f"错误：模型生成源码失败（第 {rounds_used} 轮）：{e}"
        if not html:
            return f"错误：模型未返回可用 HTML 源码（第 {rounds_used} 轮）"

        err = _dc.html_render(html=html, output=out, width=w, height=h, scale=2)
        if isinstance(err, str) and err.startswith("错误"):
            try:
                _atomic_write(src_path, html)
            except Exception:  # noqa: BLE001
                pass
            return f"{err}\n源码已保存（可稍后手动渲染）：{src_path}"

        if rounds <= 1:
            break

        try:
            critique_text = _dc.image_understand(out, _critique_prompt(brief, kind))
        except Exception as e:  # noqa: BLE001
            critique_text = f"（自评失败：{e}）"
        obj = _extract_json_obj(critique_text, must_keys=("score",)) if not str(critique_text).startswith("错误") else None
        if obj:
            try:
                score = int(obj.get("score"))
            except (TypeError, ValueError):
                score = None
            issues = [str(x) for x in (obj.get("issues") or [])][:6]
            verdict = str(obj.get("verdict") or "").strip().lower()
        if verdict == "ok":
            break

    try:
        _atomic_write(src_path, html)
    except Exception:  # noqa: BLE001
        pass

    try:
        size = os.path.getsize(out)
    except OSError:
        size = 0
    score_txt = f"{score}/10" if score is not None else "未评分"
    issues_txt = "；".join(issues) if issues else "无"
    return (
        f"已用代码生成图像（{rounds_used} 轮 · 自评 {score_txt} · {size / 1024:.0f} KB）：{out}\n"
        f"可再编辑源码：{src_path}\n"
        f"审查：{issues_txt}\n"
        f"（结构化图像优先用此工具；写实照片/复杂质感请用 image_generate）"
    )


# ══════════════════════════════════════════════════════════════
# 图像后端（OpenAI 兼容）+ 帧 / 掩膜 / 控制图 基础能力
# ══════════════════════════════════════════════════════════════
def _img_backend():
    key = str(getattr(_dc, "IMAGE_GEN_KEY", "") or "").strip()
    base = str(getattr(_dc, "IMAGE_GEN_BASE", "") or "").strip().rstrip("/") or DEFAULT_BASE_URL
    model = str(getattr(_dc, "IMAGE_GEN_MODEL", "") or "gpt-image-1")
    return key, base, model


def _bytes_from_resp(data):
    import base64
    items = (data or {}).get("data") or []
    if not items:
        return None, "接口未返回图片"
    it = items[0]
    if it.get("b64_json"):
        return base64.b64decode(it["b64_json"]), ""
    if it.get("url"):
        err = _safe_url(it["url"])
        if err:
            return None, f"返回的下载地址不安全：{err}"
        try:
            with _safe_stream("GET", it["url"], timeout=60) as r:
                r.raise_for_status()
                buf = b""
                for chunk in r.iter_bytes(64 * 1024):
                    buf += chunk
                    if len(buf) > 20 * 1024 * 1024:
                        return None, "图片超过 20MB 上限"
            return buf, ""
        except Exception as e:  # noqa: BLE001
            return None, f"下载返回图片失败：{e}"
    return None, "接口返回格式无法解析"


def _gen_bytes(prompt, size):
    """文生图后端（OpenAI 兼容 /images/generations）→ (png 字节, err)。"""
    key, base, model = _img_backend()
    if not key:
        return None, "未配置图片生成（config.json 的 image_api_key / image_base_url / image_model）"
    try:
        resp = _http_client().post(
            f"{base}/images/generations",
            json={"model": model, "prompt": str(prompt), "n": 1, "size": str(size), "response_format": "b64_json"},
            headers={"Authorization": f"Bearer {key}"}, timeout=120.0,
        )
        resp.raise_for_status()
        return _bytes_from_resp(resp.json())
    except Exception as e:  # noqa: BLE001
        return None, f"图片生成失败：{e}"


def _edit_bytes(image_bytes, prompt, size, mask_bytes=None):
    """图生图 / 局部重绘后端（OpenAI 兼容 /images/edits，multipart）→ (png 字节, err)。"""
    key, base, model = _img_backend()
    if not key:
        return None, "未配置图片生成（config.json 的 image_api_key / image_base_url / image_model）"
    files = {"image": ("image.png", image_bytes, "image/png")}
    if mask_bytes:
        files["mask"] = ("mask.png", mask_bytes, "image/png")
    try:
        resp = _http_client().post(
            f"{base}/images/edits",
            files=files,
            data={"model": model, "prompt": str(prompt), "n": "1", "size": str(size), "response_format": "b64_json"},
            headers={"Authorization": f"Bearer {key}"}, timeout=120.0,
        )
        resp.raise_for_status()
        return _bytes_from_resp(resp.json())
    except Exception as e:  # noqa: BLE001
        return None, f"图生图/局部重绘接口不可用：{e}"


def _resolve_out(path, ext=".png", subdir="codegen"):
    """输出路径解析 + 权限校验；未给则落到工作区子目录。返回 (path, err)。"""
    p = str(path or "").strip()
    if p:
        out = permissions.resolve(p) or ""
        if not out:
            return "", "错误：输出路径无效"
        if ext and not out.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            out += ext
    else:
        base_dir = os.path.join(getattr(_dc, "WORKING_DIR", None) or permissions.WORKSPACE_DIR or os.getcwd(), subdir)
        out = os.path.join(base_dir, f"out_{time.strftime('%Y%m%d-%H%M%S')}{ext}")
    ok, reason = permissions.check_filesystem(out, write=True)
    if not ok:
        return "", reason
    return out, ""


def _load_pil(path):
    from PIL import Image
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return None, f"错误：图片不存在：{path}"
    ok, reason = permissions.check_filesystem(p, write=False)
    if not ok:
        return None, reason
    try:
        return Image.open(p).convert("RGBA"), ""
    except Exception as e:  # noqa: BLE001
        return None, f"错误：读取图片失败：{e}"


def _pix_bytes(im):
    import io
    b = io.BytesIO()
    im.convert("RGBA").save(b, format="PNG")
    return b.getvalue()


def _parse_hex(s, default=(0, 0, 0, 0)):
    m = re.match(r"^#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$", str(s or "").strip())
    if not m:
        return default
    r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    a = int(m.group(2), 16) if m.group(2) else 255
    return (r, g, b, a)


def _fit(img, w, h):
    from PIL import Image
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGBA", (w, h), (0, 0, 0, 0))
    s = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * s)), max(1, int(ih * s))
    r = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(r, ((w - nw) // 2, (h - nh) // 2), r)
    return canvas


def _nearest_size(w, h):
    allowed = (256, 512, 768, 1024, 1536, 2048)
    near = lambda x: min(allowed, key=lambda a: abs(a - x))  # noqa: E731
    return f"{near(w)}x{near(h)}"


def _region_box(region, w, h):
    m = re.match(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$", str(region or ""))
    if not m:
        return None
    x, y, rw, rh = (int(g) for g in m.groups())
    x = max(0, min(x, w - 1)); y = max(0, min(y, h - 1))
    rw = max(1, min(rw, w - x)); rh = max(1, min(rh, h - y))
    return (x, y, rw, rh)


def _frames_to_images(frames, cell, out_dir, subdir="frames"):
    """frames 元素可为图片路径或内联 HTML 片段（以 < 开头）。返回 (PIL 列表, err)。"""
    from PIL import Image
    imgs = []
    for i, f in enumerate(frames or []):
        s = str(f or "").strip()
        if not s:
            continue
        if s.startswith("<"):
            cw, ch = (cell or (512, 512))
            d = os.path.join(out_dir, subdir)
            try:
                os.makedirs(d, exist_ok=True)
            except OSError:
                pass
            tmp = os.path.join(d, f"frame_{i:03d}.png")
            r = _dc.html_render(html=s, output=tmp, width=cw, height=ch, scale=1)
            if isinstance(r, str) and r.startswith("错误"):
                return [], r
            imgs.append(Image.open(tmp).convert("RGBA"))
        else:
            im, e = _load_pil(s)
            if e:
                return [], e
            imgs.append(im)
    if not imgs:
        return [], "错误：frames 为空"
    return imgs, ""


def _save_gif(imgs, path, fps, loop, background=(0, 0, 0, 255)):
    """确定性 GIF：固定时长/循环、固定量化（中位切分、无抖动）、不优化。"""
    from PIL import Image
    durs = max(20, int(round(1000.0 / max(1, int(fps)))))
    quant = []
    for im in imgs:
        canvas = Image.new("RGBA", im.size, background)
        comp = Image.alpha_composite(canvas, im).convert("RGB")
        quant.append(comp.quantize(colors=256, dither=0))
    quant[0].save(path, format="GIF", save_all=True, append_images=quant[1:],
                  duration=durs, loop=int(loop), disposal=2, optimize=False)


# ══════════════════════════════════════════════════════════════
# 局部重绘
# ══════════════════════════════════════════════════════════════
@tool(
    {
        "type": "function",
        "function": {
            "name": "image_inpaint",
            "description": "局部重绘：给图片圈定区域（region=xywh）或提供掩膜，按 prompt 只重画该区域（优先走图生图 /images/edits，后端不支持时自动用「生成补丁+羽化合成」兜底），产出新图与掩膜",
            "parameters": {
                "type": "object",
                "properties": {
                    "image": {"type": "string", "description": "底图绝对路径"},
                    "prompt": {"type": "string", "description": "要把该区域改成什么"},
                    "region": {"type": "string", "description": "重绘区域 x,y,w,h（与 mask 二选一）"},
                    "mask": {"type": "string", "description": "可选：掩膜图路径（白=重绘，黑=保留），与 region 二选一"},
                    "output": {"type": "string", "description": "输出 PNG 绝对路径（默认工作区 codegen/）"},
                    "feather": {"type": "integer", "description": "可选：边缘羽化半径 0-64（默认 12）"},
                },
                "required": ["image", "prompt"],
            },
        },
    },
    groups=['🎨 媒体与图像'],
    phrases='局部重绘',
    preactivate=(('局部重绘', '改这块', '重画这块', '涂抹重绘', 'inpaint'),),
)
def image_inpaint(image="", prompt="", region="", mask="", output="", feather=12):
    """局部重绘：region/mask 圈定 → 图生图优先、补丁合成兜底。"""
    import io
    from PIL import Image, ImageDraw, ImageFilter
    prompt = str(prompt or "").strip()
    if not prompt:
        return "错误：prompt 必填"
    base_img, err = _load_pil(image)
    if err:
        return err
    w, h = base_img.size
    try:
        feather = clamp_int(feather, 12, lo=0, hi=64)
    except (TypeError, ValueError):
        feather = 12

    if str(mask or "").strip():
        m_img, e = _load_pil(mask)
        if e:
            return e
        m = m_img.convert("L").resize((w, h), Image.LANCZOS)
    else:
        box = _region_box(region, w, h)
        if not box:
            return "错误：region 需为 x,y,w,h 或提供 mask"
        m = Image.new("L", (w, h), 0)
        x, y, rw, rh = box
        ImageDraw.Draw(m).rectangle([x, y, x + rw - 1, y + rh - 1], fill=255)
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))

    out, oe = _resolve_out(output, ".png")
    if oe:
        return oe
    mask_path = os.path.splitext(out)[0] + ".mask.png"
    try:
        os.makedirs(os.path.dirname(mask_path) or ".", exist_ok=True)
        with open(mask_path, "wb") as mf:
            mf.write(_pix_bytes(m.convert("RGBA")))
    except OSError:
        pass

    size = _nearest_size(w, h)
    data, e = _edit_bytes(_pix_bytes(base_img), prompt, size, _pix_bytes(m.convert("RGB")))
    mode = "edits"
    if e:
        # 兜底：按掩膜包围盒生成补丁，羽化合成
        bbox = m.getbbox()
        if not bbox:
            return "错误：掩膜为空"
        bx0, by0, bx1, by1 = bbox
        pw, ph = bx1 - bx0, by1 - by0
        pdata, e2 = _gen_bytes(prompt, _nearest_size(pw, ph))
        if e2:
            return f"错误：局部重绘失败（图生图不可用：{e}；补丁生成也失败：{e2}）"
        patch = Image.open(io.BytesIO(pdata)).convert("RGBA").resize((pw, ph), Image.LANCZOS)
        layer = base_img.copy()
        layer.paste(patch, (bx0, by0))
        result = Image.composite(layer, base_img, m)
        mode = "patch"
        data = _pix_bytes(result)
    try:
        with open(out, "wb") as f:
            f.write(data)
    except OSError as ex:
        return f"错误：写出失败：{ex}"
    permissions.audit("image_inpaint", out, prompt[:80])
    return (
        f"已局部重绘（{'图生图' if mode == 'edits' else '补丁合成兜底'}）：{out}\n"
        f"掩膜：{mask_path}\n（如需先看效果再决定，可再用 image_understand 复核）"
    )


# ══════════════════════════════════════════════════════════════
# 控制图（边缘 / 线稿 / 灰阶 / 剪影 / 阈值）
# ══════════════════════════════════════════════════════════════
@tool(
    {
        "type": "function",
        "function": {
            "name": "control_map",
            "description": "生成控制图（确定性、纯本地）：从图片提取 edge 边缘 / lineart 线稿 / gray 灰阶 / silhouette 剪影 / threshold 阈值二值图，可作 ControlNet 式结构条件或线稿素材",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "源图片绝对路径"},
                    "kind": {"type": "string", "enum": ["edge", "lineart", "gray", "silhouette", "threshold"], "description": "控制图类型（默认 edge）"},
                    "output": {"type": "string", "description": "输出 PNG 绝对路径（默认工作区 codegen/）"},
                    "blur": {"type": "integer", "description": "可选：提取前高斯模糊半径 0-16（默认 1，抑噪）"},
                    "threshold": {"type": "integer", "description": "可选：阈值 0-255（lineart/threshold/silhouette 用，默认 128）"},
                    "invert": {"type": "boolean", "description": "可选：反相（默认 false）"},
                },
                "required": ["src"],
            },
        },
    },
    groups=['🎨 媒体与图像'],
    phrases='生成控制图（边缘/线稿）',
    preactivate=(('控制图', '提取线稿', '边缘图', '线稿图', 'controlnet'),),
)
def control_map(src="", kind="edge", output="", blur=1, threshold=128, invert=False):
    """确定性控制图：Sobel 边缘 / 线稿 / 灰阶 / 剪影 / 阈值。"""
    from PIL import Image, ImageFilter
    import numpy as np
    kind = str(kind or "edge").strip().lower()
    if kind not in ("edge", "lineart", "gray", "silhouette", "threshold"):
        kind = "edge"
    img, err = _load_pil(src)
    if err:
        return err
    try:
        b = clamp_int(blur, 1, lo=0, hi=16)
        th = clamp_int(threshold, 128, lo=0, hi=255)
    except (TypeError, ValueError):
        b, th = 1, 128
    rgb = img.convert("RGB")

    if kind == "silhouette":
        a = np.asarray(img)
        if a.shape[2] == 4 and a[..., 3].min() < 250:
            mask = a[..., 3] > th
        else:
            ref = a[0, 0, :3].astype(int)
            dist = np.abs(a[..., :3].astype(int) - ref).sum(-1)
            mask = dist > th
        out = Image.fromarray((mask * 255).astype("uint8"), "L")
    else:
        gray = rgb.convert("L")
        if b > 0:
            gray = gray.filter(ImageFilter.GaussianBlur(b))
        if kind == "gray":
            out = gray
        elif kind == "threshold":
            out = gray.point(lambda v: 255 if v > th else 0)
        else:
            g = np.asarray(gray, dtype=np.float32)
            p = np.pad(g, 1, mode="edge")
            gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
            gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
            mag = np.hypot(gx, gy)
            mx = float(mag.max())
            if mx > 0:
                mag = mag / mx * 255.0
            edge = Image.fromarray(mag.astype("uint8"), "L")
            if kind == "lineart":
                out = edge.point(lambda v: 0 if v > th else 255)
            else:
                out = edge
    if invert:
        out = Image.eval(out, lambda v: 255 - v)

    out_path, oe = _resolve_out(output, ".png")
    if oe:
        return oe
    try:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        out.save(out_path, format="PNG")
    except OSError as ex:
        return f"错误：写出失败：{ex}"
    permissions.audit("control_map", out_path, kind)
    return f"已生成 {kind} 控制图：{out_path}（{out.size[0]}x{out.size[1]}，纯本地确定性）"


# ══════════════════════════════════════════════════════════════
# 多帧精灵表 / 确定性 GIF
# ══════════════════════════════════════════════════════════════
def _cell_size(frame_size, imgs):
    m = re.match(r"^\s*(\d+)\s*[x×]\s*(\d+)\s*$", str(frame_size or ""))
    if m:
        return int(m.group(1)), int(m.group(2))
    w = max(im.size[0] for im in imgs)
    h = max(im.size[1] for im in imgs)
    return w, h


@tool(
    {
        "type": "function",
        "function": {
            "name": "sprite_sheet",
            "description": "多帧精灵表：把多帧（图片路径或内联 HTML 片段）拼成网格精灵表或横向长条，可选同时导出确定性 GIF；每帧自动等比缩放居中",
            "parameters": {
                "type": "object",
                "properties": {
                    "frames": {"type": "array", "items": {"type": "string"}, "description": "帧列表：每项为图片路径，或以 < 开头的内联 HTML 片段（自动渲染成帧）"},
                    "output": {"type": "string", "description": "输出 PNG 绝对路径（默认工作区 codegen/）"},
                    "layout": {"type": "string", "enum": ["grid", "strip"], "description": "grid 网格（默认）/ strip 横向长条"},
                    "columns": {"type": "integer", "description": "可选：网格列数（默认取平方根向上取整）"},
                    "frame_size": {"type": "string", "description": "可选：单元格尺寸 WxH（默认取各帧最大尺寸）"},
                    "background": {"type": "string", "description": "可选：底色 #RRGGBB[AA]（默认透明）"},
                    "gif": {"type": "string", "description": "可选：同时导出 GIF 的绝对路径"},
                    "fps": {"type": "integer", "description": "可选：GIF 帧率（默认 8）"},
                    "loop": {"type": "integer", "description": "可选：GIF 循环次数，0=无限（默认 0）"},
                },
                "required": ["frames", "output"],
            },
        },
    },
    groups=['🎨 媒体与图像'],
    phrases='合成精灵表',
    preactivate=(('精灵表', '精灵图', '帧序列图', '拼帧', 'sprite sheet'),),
)
def sprite_sheet(frames=None, output="", layout="grid", columns=0, frame_size="", background="", gif="", fps=8, loop=0):
    """多帧 → 网格/长条精灵表（+ 可选确定性 GIF）。"""
    from PIL import Image
    import math
    out, oe = _resolve_out(output, ".png")
    if oe:
        return oe
    out_dir = os.path.dirname(out) or "."
    imgs, err = _frames_to_images(frames, None, out_dir)
    if err:
        return err
    cw, ch = _cell_size(frame_size, imgs)
    n = len(imgs)
    if str(layout).lower() == "strip":
        cols = n
    else:
        cols = int(columns) if int(columns or 0) > 0 else max(1, math.ceil(math.sqrt(n)))
    rows = max(1, math.ceil(n / cols))
    bg = _parse_hex(background, (0, 0, 0, 0))
    sheet = Image.new("RGBA", (cols * cw, rows * ch), bg)
    for i, im in enumerate(imgs):
        r, c = divmod(i, cols)
        sheet.paste(_fit(im, cw, ch), (c * cw, r * ch), _fit(im, cw, ch))
    try:
        os.makedirs(out_dir, exist_ok=True)
        sheet.save(out, format="PNG")
    except OSError as ex:
        return f"错误：写出精灵表失败：{ex}"
    msg = f"已合成精灵表：{out}（{n} 帧 · {cols}×{rows} 网格 · 单元 {cw}x{ch}）"
    if str(gif or "").strip():
        g, ge = _resolve_out(gif, ".gif")
        if ge:
            return msg + "\n" + ge
        try:
            _save_gif(imgs, g, fps, loop, _parse_hex(background, (0, 0, 0, 255)))
            msg += f"\nGIF：{g}（{fps}fps · loop={loop} · 确定性）"
        except Exception as ex:  # noqa: BLE001
            msg += f"\n错误：GIF 导出失败：{ex}"
    permissions.audit("sprite_sheet", out, f"{n} frames {cols}x{rows}")
    return msg


@tool(
    {
        "type": "function",
        "function": {
            "name": "make_gif",
            "description": "确定性 GIF：把多帧（图片路径或内联 HTML 片段）合成动画 GIF——固定帧率/循环、固定调色板量化、无抖动、不优化，结果可复现",
            "parameters": {
                "type": "object",
                "properties": {
                    "frames": {"type": "array", "items": {"type": "string"}, "description": "帧列表：图片路径或内联 HTML 片段"},
                    "output": {"type": "string", "description": "输出 GIF 绝对路径（默认工作区 codegen/）"},
                    "fps": {"type": "integer", "description": "可选：帧率（默认 8，1-60）"},
                    "loop": {"type": "integer", "description": "可选：循环次数，0=无限（默认 0）"},
                    "frame_size": {"type": "string", "description": "可选：统一帧尺寸 WxH（默认取各帧最大尺寸）"},
                    "background": {"type": "string", "description": "可选：不透明底色 #RRGGBB（默认黑；GIF 无 alpha）"},
                },
                "required": ["frames", "output"],
            },
        },
    },
    groups=['🎨 媒体与图像'],
    phrases='合成确定性 GIF',
    preactivate=(('做个gif', '生成gif', '动图', '逐帧动画'),),
)
def make_gif(frames=None, output="", fps=8, loop=0, frame_size="", background="#000000"):
    """多帧 → 确定性 GIF。"""
    out, oe = _resolve_out(output, ".gif")
    if oe:
        return oe
    out_dir = os.path.dirname(out) or "."
    imgs, err = _frames_to_images(frames, None, out_dir)
    if err:
        return err
    cw, ch = _cell_size(frame_size, imgs)
    imgs = [_fit(im, cw, ch) for im in imgs]
    try:
        fps_i = clamp_int(fps, 8, lo=1, hi=60)
        loop_i = max(0, int(loop))
    except (TypeError, ValueError):
        fps_i, loop_i = 8, 0
    try:
        os.makedirs(out_dir, exist_ok=True)
        _save_gif(imgs, out, fps_i, loop_i, _parse_hex(background, (0, 0, 0, 255)))
    except Exception as ex:  # noqa: BLE001
        return f"错误：GIF 导出失败：{ex}"
    try:
        size = os.path.getsize(out)
    except OSError:
        size = 0
    permissions.audit("make_gif", out, f"{len(imgs)} frames")
    return f"已生成确定性 GIF：{out}（{len(imgs)} 帧 · {fps_i}fps · loop={loop_i} · {size / 1024:.0f} KB）"


# ══════════════════════════════════════════════════════════════
# 混合渲染（代码定低频结构 + 神经补高频质感）
# ══════════════════════════════════════════════════════════════
@tool(
    {
        "type": "function",
        "function": {
            "name": "image_hybrid",
            "description": "混合渲染：先用代码渲染结构底图（或用给定底图），再走图生图后端补高频质感，按 strength 混合；后端不支持图生图时优雅降级为「交付结构底图」",
            "parameters": {
                "type": "object",
                "properties": {
                    "brief": {"type": "string", "description": "画面描述（结构 + 期望质感）"},
                    "base": {"type": "string", "description": "可选：结构底图路径（不给则用代码先生成结构底图）"},
                    "kind": {"type": "string", "enum": ["illustration", "icon", "infographic", "pixel", "ui", "diagram", "poster", "logo"], "description": "结构底图类型（默认 illustration）"},
                    "width": {"type": "integer", "description": "可选：宽 px（默认 1024，128-2048）"},
                    "height": {"type": "integer", "description": "可选：高 px（默认 1024，128-2048）"},
                    "output": {"type": "string", "description": "输出 PNG 绝对路径（默认工作区 codegen/）"},
                    "strength": {"type": "number", "description": "可选：神经质感占比 0-1（默认 0.5；1=全神经，0=纯结构）"},
                    "background": {"type": "string", "description": "可选：结构底图背景要求"},
                },
                "required": ["brief"],
            },
        },
    },
    groups=['🎨 媒体与图像'],
    phrases='混合渲染（结构+质感）',
    preactivate=(('混合渲染', '代码加质感', '图生图', 'img2img', '结构加细节'),),
)
def image_hybrid(brief="", base="", kind="illustration", width=1024, height=1024,
                 output="", strength=0.5, background=""):
    """代码结构底图 → 图生图补质感 → 按 strength 混合；不可用则降级交付底图。"""
    import io
    from PIL import Image
    brief = str(brief or "").strip()
    if not brief:
        return "错误：brief 必填"
    kind = str(kind or "illustration").strip().lower()
    if kind not in _KINDS:
        kind = "illustration"
    try:
        w = clamp_int(width, 1024, lo=128, hi=2048)
        h = clamp_int(height, 1024, lo=128, hi=2048)
    except (TypeError, ValueError):
        return "错误：width/height 必须是数字"
    try:
        s = max(0.0, min(1.0, float(strength)))
    except (TypeError, ValueError):
        s = 0.5
    out, oe = _resolve_out(output, ".png")
    if oe:
        return oe
    out_dir = os.path.dirname(out) or "."

    # 结构底图
    if str(base or "").strip():
        base_img, err = _load_pil(base)
        if err:
            return err
    else:
        client = get_active_client()
        if client is None:
            return "错误：没有可用客户端（请先在设置中配置 API Key）"
        try:
            html = _extract_html(_call(client, "你是资深视觉设计师兼前端工程师。",
                                       _author_prompt(brief + "（此为结构底图：只铺构图、主体色块、光影骨架，细节留白）", kind, w, h, background)))
        except Exception as e:  # noqa: BLE001
            return f"错误：结构底图生成失败：{e}"
        if not html:
            return "错误：模型未返回结构底图源码"
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError:
            pass
        struct = os.path.join(out_dir, os.path.splitext(os.path.basename(out))[0] + ".structure.png")
        r = _dc.html_render(html=html, output=struct, width=w, height=h, scale=1)
        if isinstance(r, str) and r.startswith("错误"):
            return r
        base_img = Image.open(struct).convert("RGBA")

    data, e = _edit_bytes(_pix_bytes(base_img), brief, _nearest_size(*base_img.size))
    if e:
        # 优雅降级：交付结构底图（确定性可复现）
        try:
            with open(out, "wb") as f:
                f.write(_pix_bytes(base_img))
        except OSError as ex:
            return f"错误：写出失败：{ex}"
        return (f"神经后端不支持图生图，已降级交付结构底图：{out}\n"
                f"（原因：{e}；结构底图本身即确定性产物，可再交人工/其它后端上色）")
    try:
        edited = Image.open(io.BytesIO(data)).convert("RGB").resize(base_img.size, Image.LANCZOS)
        base_rgb = base_img.convert("RGB")
        mixed = Image.blend(base_rgb, edited, s)
        os.makedirs(out_dir, exist_ok=True)
        mixed.save(out, format="PNG")
    except Exception as ex:  # noqa: BLE001
        return f"错误：混合写出失败：{ex}"
    permissions.audit("image_hybrid", out, brief[:80])
    return (f"已混合渲染（神经质感 {int(s * 100)}%）：{out}\n"
            f"（结构由代码定、质感由扩散补——需要更写实的纯扩散图请直接用 image_generate）")


__all__ = ["image_codegen", "image_inpaint", "control_map", "sprite_sheet", "make_gif", "image_hybrid"]
