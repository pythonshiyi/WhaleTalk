"""图像生成后端厂商适配（OpenAI 兼容 / MOSS 文生图）。

MOSS 文生图（https://platform.moss.cn/docs/reference/images-generations）与通用
OpenAI images API 的差异：
  - 仅支持 ``response_format=url``（不支持 b64_json）；
  - ``n`` 仅支持 1；
  - ``size`` 每边 256–2048、为 16 的倍数，且总像素不超过 2097152；
  - 单次任务可能耗时较长（官方示例超时 900s）；
  - 额外支持 negative_prompt / prompt_enhance / seed / num_inference_steps /
    guidance_scale / output_watermark。

本模块把这些差异收敛为纯函数，供 image_generate / image_codegen 复用。
"""

import math
import re

MOSS_MIN_SIDE = 256
MOSS_MAX_SIDE = 2048
MOSS_MAX_PIXELS = 2097152
MOSS_STEP = 16
MOSS_TIMEOUT = 900.0


def is_moss(base="", model=""):
    """是否走 MOSS 文生图后端（按端点域名或模型 ID 判断）。"""
    b = str(base or "").lower()
    m = str(model or "").lower()
    return m.startswith("moss-image") or "mosi.cn" in b or "moss.cn" in b


def normalize_moss_size(size):
    """把「宽x高」规范到 MOSS 约束内；无法解析时返回空串。

    规则：每边取 16 的倍数、限制在 256–2048；总像素超限时按比例缩小。
    """
    m = re.match(r"^\s*(\d{2,5})\s*[x×*]\s*(\d{2,5})\s*$", str(size or ""))
    if not m:
        return ""
    w, h = int(m.group(1)), int(m.group(2))

    def _fit(v):
        v = ((int(v) + MOSS_STEP // 2) // MOSS_STEP) * MOSS_STEP
        return max(MOSS_MIN_SIDE, min(MOSS_MAX_SIDE, v))

    w, h = _fit(w), _fit(h)
    if w * h > MOSS_MAX_PIXELS:
        k = math.sqrt(MOSS_MAX_PIXELS / float(w * h))
        w = max(MOSS_MIN_SIDE, min(MOSS_MAX_SIDE, int(w * k) // MOSS_STEP * MOSS_STEP))
        h = max(MOSS_MIN_SIDE, min(MOSS_MAX_SIDE, int(h * k) // MOSS_STEP * MOSS_STEP))
        while w * h > MOSS_MAX_PIXELS and (w > MOSS_MIN_SIDE or h > MOSS_MIN_SIDE):
            if w >= h and w > MOSS_MIN_SIDE:
                w -= MOSS_STEP
            elif h > MOSS_MIN_SIDE:
                h -= MOSS_STEP
            else:
                break
    return f"{w}x{h}"


def gen_timeout(base="", model=""):
    """图像生成请求超时：MOSS 可能跑很久，给 900s；其余保持 300s。"""
    return MOSS_TIMEOUT if is_moss(base, model) else 300.0


def build_gen_payload(model, base, prompt, *, n=1, size="1024x1024",
                      negative_prompt="", prompt_enhance=False, seed=None,
                      num_inference_steps=None, guidance_scale=None,
                      watermark=True):
    """构造 /images/generations 请求体（按后端自动切换 response_format 等）。"""
    moss = is_moss(base, model)
    payload = {
        "model": model,
        "prompt": str(prompt),
        "n": 1 if moss else int(n),
        "size": str(size),
        "response_format": "url" if moss else "b64_json",
    }
    if moss:
        if str(negative_prompt or "").strip():
            payload["negative_prompt"] = str(negative_prompt).strip()
        if prompt_enhance:
            payload["prompt_enhance"] = True
        if seed is not None:
            payload["seed"] = int(seed)
        if num_inference_steps is not None:
            payload["num_inference_steps"] = int(num_inference_steps)
        if guidance_scale is not None:
            payload["guidance_scale"] = float(guidance_scale)
        payload["output_watermark"] = {"enabled": bool(watermark)}
    return payload
