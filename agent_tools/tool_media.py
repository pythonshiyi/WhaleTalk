# -*- coding: utf-8 -*-
"""🎨 媒体与图像 —— P0-1 拆分第二批（工具域模块）。

共享符号策略：permissions / security / shared 为独立模块，顶层直接 import；
引用 deepseek_client 的常量与辅助（VISION_MODEL / _detect_image_mime /
_capture_screen_png 等）时依赖加载顺序契约——主文件在共享基建全部定义后
才执行 `from agent_tools import *`，此处 from-import 可安全解析
（见 deepseek_client.py 对应注释）。_capture_screen_png 保留在主文件
（vision_loop 等仍直接调用），本模块经 re-import 复用。
"""

import os
import re
import subprocess
from datetime import datetime

from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import permissions
from security import _safe_url
from shared import OCR_IMAGE_PS
from deepseek_client import (
    VISION_MODEL,
    IMAGE_EXTENSIONS,
    get_active_client,
    is_vision_model,
    _detect_image_mime,
    _safe_stream,
    _capture_screen_png,
    EFFORT_BY_THINKING,
)


@tool(
        {
            "type": "function",
            "function": {
                "name": "image_process",
                "description": "图像处理：缩放/裁剪/旋转/格式转换/加水印（PIL）。ops 用分号分隔多个操作，如 resize=800x600; water=测试水印",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "源图片绝对路径"},
                        "output": {"type": "string", "description": "输出图片绝对路径"},
                        "ops": {"type": "string", "description": "可选：操作串。resize=宽x高; crop=x1,y1,x2,y2; rotate=度数; convert=PNG/JPEG; quality=1-100; water=水印文本"},
                    },
                    "required": ["path", "output"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='图像处理（缩放/裁剪/滤镜/格式转换）',
    preactivate=(('图片', '图像', '截图', '看图', '图表', '视觉执行', '视觉闭环', '屏幕操作'),),
)
def image_process(path, output, ops=""):
    """PIL 图像处理：resize=宽x高; crop=x1,y1,x2,y2; rotate=度数;
    convert=PNG/JPEG; quality=1-100; water=水印文本（右下角）。"""
    if not path or not output:
        return "错误：path 与 output 必填"
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：源图片不存在：{p}"
    out = permissions.resolve(output)
    if not out:
        return "错误：输出路径无效"
    ok, reason = permissions.check_filesystem(out, write=True)
    if not ok:
        return reason
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return "错误：需要 Pillow（pip install pillow）"
    try:
        img = Image.open(p)
        # 防 decompression bomb：先检查像素尺寸，拒绝超大图再进入解码/处理
        try:
            if img.width * img.height > 100_000_000:
                return "错误：图片像素过大（超过 1 亿像素），请先压缩后再处理"
        except Exception:
            pass
        applied = 0
        quality = None
        for op in str(ops or "").split(";"):
            op = op.strip()
            if not op:
                continue
            key, _, val = op.partition("=")
            key = key.strip().lower()
            val = val.strip()
            # 每个操作独立容错：单个操作参数错误明确报错，不吞掉整个处理
            try:
                if key == "resize" and val:
                    w, _, h = val.lower().partition("x")
                    if not (w.isdigit() and h.isdigit()):
                        return f"错误：resize 格式应为 宽x高（如 800x600），收到：{op}"
                    img = img.resize((max(1, int(w)), max(1, int(h))))
                elif key == "crop" and val:
                    parts = [v.strip() for v in val.split(",")]
                    if len(parts) != 4:
                        return f"错误：crop 需要 4 个坐标 x1,y1,x2,y2（如 0,0,100,100），收到：{op}"
                    x1, y1, x2, y2 = (int(v) for v in parts)
                    img = img.crop((x1, y1, x2, y2))
                elif key == "rotate" and val:
                    try:
                        deg = float(val)
                    except ValueError:
                        return f"错误：rotate 需要数字角度（如 90），收到：{op}"
                    img = img.rotate(deg, expand=True)
                elif key == "convert" and val:
                    # PIL convert 需要模式名（RGB/RGBA/L），"JPEG" 这类格式名需映射
                    mode_map = {
                        "JPEG": "RGB", "JPG": "RGB", "PNG": "RGBA",
                        "GRAY": "L", "GREY": "L", "BMP": "RGB", "WEBP": "RGB",
                    }
                    target = mode_map.get(val.upper(), val.upper())
                    img = img.convert(target)
                elif key == "water" and val:
                    draw = ImageDraw.Draw(img)
                    try:
                        font = ImageFont.truetype(
                            "C:/Windows/Fonts/msyh.ttc", max(12, img.width // 20)
                        )
                    except Exception:
                        font = ImageFont.load_default()
                    w, h = img.size
                    tw, th = draw.textbbox((0, 0), val, font=font)[2:4]
                    draw.text((w - tw - 10, h - th - 10), val, fill=(255, 255, 255, 200), font=font)
                elif key == "quality" and val:
                    try:
                        quality = max(1, min(100, int(val)))
                    except ValueError:
                        return f"错误：quality 应为 1-100 的数字，收到：{op}"
                    applied += 1  # quality 不算图像变换，但记录已生效
                    continue
                else:
                    continue  # 未知操作静默跳过（保持向后兼容）
                applied += 1
            except (ValueError, IndexError) as e:
                return f"错误：操作 {op} 参数非法：{e}"
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        save_kw = {"quality": quality} if quality else {}
        img.save(out, **save_kw)
        size = os.path.getsize(out) if os.path.exists(out) else 0
        if applied == 0:
            return f"已复制图像至 {out}（{size} 字节）未做处理（ops 为空或未识别）。支持：resize/crop/rotate/convert/quality/water"
        return f"已处理图像并保存至 {out}（{size} 字节，{applied} 项操作生效）"
    except Exception as e:
        return f"错误：图像处理失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "ocr_image",
                "description": "从图片文件提取文字（Windows OCR，适合截图/扫描件，需系统安装中文语言包）",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "图片文件绝对路径"}},
                    "required": ["path"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='图片文字识别 OCR',
    preactivate=(('图片', '图像', '截图', '看图', '图表', '视觉执行', '视觉闭环', '屏幕操作'),),
)
def ocr_image(path):
    """从图片文件提取文字（Windows OCR，需系统语言包支持）。"""
    if not path or not str(path).strip():
        return "错误：path 必填"
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：图片不存在：{p}"
    ok, reason = permissions.check_filesystem(p, write=False)
    if not ok:
        return reason
    try:
        import tempfile

        fd, ps_path = tempfile.mkstemp(suffix=".ps1")
        os.close(fd)
        try:
            script = OCR_IMAGE_PS.replace("@PATH@", "'" + str(p).replace("'", "''") + "'")
            with open(ps_path, "w", encoding="utf-8-sig") as f:
                f.write(script)
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_path],
                capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace",
            )
            out = (proc.stdout or "").strip()
            return out or "未能识别出文字"
        finally:
            try:
                os.remove(ps_path)
            except OSError:
                pass
    except Exception as e:
        return f"错误：OCR 失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "image_understand",
                "description": "分析指定路径/URL 的图片文件（OCR 提取文字、放大细节、回答图片相关问题）。注意：用户消息中已附带的图片（对话中的图片块）你能直接看到，解读它们无需调用本工具，直接回答即可",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "图片文件绝对路径或 http(s) 图片 URL"},
                        "question": {"type": "string", "description": "可选：要问的问题（默认描述图片内容）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='分析图片文件（OCR/细节/回答图片问题）',
    preactivate=(('图片', '图像', '截图', '看图', '图表', '视觉执行', '视觉闭环', '屏幕操作'),),
)
def image_understand(path, question=""):
    """用多模态模型理解图片（本地文件或 http(s) 图片 URL）。

    自动适配视觉模型：当前客户端模型不支持图片时，自动改用
    deepseek-v4-flash-vision-exp（同一 API Key / 端点），无需手动切换。
    """
    if not str(path or "").strip():
        return "错误：path 必填"
    import base64

    is_url = str(path).strip().lower().startswith(("http://", "https://"))
    if is_url:
        err = _safe_url(path)
        if err:
            return f"错误：{err}"
    else:
        p = permissions.resolve(path)
        if not p or not os.path.isfile(p):
            return f"错误：图片不存在：{path}"
        ok, reason = permissions.check_filesystem(p, write=False)
        if not ok:
            return reason
        try:
            if os.path.getsize(p) > 32 * 1024 * 1024:
                return "错误：图片超过 32MB，请先用 image_process 压缩"
        except OSError:
            pass
    try:
        if is_url:
            try:
                # stream 边读边断：URL 图片大小不可信，防恶意/超大图全量进内存
                with _safe_stream("GET", path, timeout=20) as resp:
                    resp.raise_for_status()
                    img_buf = b""
                    truncated = False
                    for chunk in resp.iter_bytes(64 * 1024):
                        img_buf += chunk
                        if len(img_buf) > 32 * 1024 * 1024:
                            truncated = True
                            break
                if truncated:
                    return "错误：图片下载超过 32MB，请先用 image_process 压缩"
                b64 = base64.b64encode(img_buf).decode("ascii")
                # 格式按文件实际内容（魔数）识别，而非声明的 MIME
                mime = _detect_image_mime(img_buf[:16])
            except Exception as e:
                return f"错误：图片下载失败: {e}"
        else:
            with open(p, "rb") as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode("ascii")
            # 格式按文件实际内容（魔数）识别，而非文件名/扩展名
            mime = _detect_image_mime(raw[:16])
        client = get_active_client()
        if client is None:
            return "错误：没有可用客户端（请先在设置中配置 API Key）"
        model, switched = client.model, False
        if not is_vision_model(model):
            model = VISION_MODEL
            switched = True
        # 跟随前端「思考档」开关（config.thinking，与主对话 chat() 一致）：
        #   none → 关思考；low/medium/high/xhigh/max → 开思考 + 对应 effort。
        # 不能写死 disabled：该模型默认开启思考，复杂截图时思考会吃满
        # max_tokens 导致 content 为空 → 工具误报「模型未返回内容」→ AI
        # 误判"不支持看图"；但也不应无视用户配置强制关思考。
        try:
            import config_utils
            _cfg = config_utils.load_config()
            _thinking = str(_cfg.get("thinking") or "none")
            _max_tokens = int(_cfg.get("max_tokens") or 16384)
        except Exception:
            _thinking, _max_tokens = "none", 16384
        _extra = {"thinking": {"type": "disabled"}}
        if _thinking != "none":
            _effort = EFFORT_BY_THINKING.get(_thinking)
            _extra = {"thinking": {"type": "enabled"}}
            if _effort and _effort != "none":
                _extra["reasoning_effort"] = _effort
        resp = client.client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": str(question or "请描述这张图片的内容")},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            max_tokens=_max_tokens,
            stream=False,
            timeout=120.0,
            extra_body=_extra,
        )
        out = (resp.choices[0].message.content or "").strip()
        if not out:
            return "模型未返回有效内容（图片过大或上游暂未响应），请重试或换小图"
        if switched:
            out += f"\n\n（注：当前模型不支持图片，已自动改用视觉模型 {VISION_MODEL}）"
        return out
    except Exception as e:
        return f"错误：图片理解失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "screen_capture",
                "description": "截取当前屏幕保存到工作区（隐私操作）。配合 ocr_image/image_understand 描述屏幕内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "可选：输出 PNG 绝对路径（默认工作区 screenshots/）"},
                        "area": {"type": "string", "description": "可选：区域 left,top,right,bottom（默认全屏）"},
                    },
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='屏幕截图',
    preactivate=(('截屏', '截个屏', '屏幕截图', '截屏看看'),),
)
def screen_capture(path="", area=""):
    """截取当前屏幕保存到工作区（默认全屏；area 形如 left,top,right,bottom）。"""
    if str(path or "").strip():
        out = permissions.resolve(path)
        if not out:
            return "错误：输出路径无效"
        if not out.lower().endswith(".png"):
            out += ".png"
        ok, reason = permissions.check_filesystem(out, write=True)
        if not ok:
            return reason
    else:
        base = os.path.join(permissions.WORKSPACE_DIR or ".", "screenshots")
        ok, reason = permissions.check_filesystem(base, write=True)
        if not ok:
            return reason
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        out = os.path.join(base, f"screen_{datetime.now():%Y%m%d_%H%M%S}.png")
    try:
        from PIL import ImageGrab

        bbox = None
        if str(area or "").strip():
            try:
                parts = [int(x.strip()) for x in str(area).split(",")]
                if len(parts) == 4:
                    bbox = tuple(parts)
            except (TypeError, ValueError):
                bbox = None
        img = ImageGrab.grab(bbox=bbox)
        img.save(out, "PNG")
        size = os.path.getsize(out)
        permissions.audit("screen_capture", out, f"{size} 字节")
        return f"已截屏保存至 {out}（{size / 1024:.0f} KB），可用 ocr_image / image_understand 分析"
    except Exception as e:
        return f"错误：截屏失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "screen_see",
                "description": "截图并让视觉模型解读当前屏幕（一步完成 截图+看图）。RPA/浏览器操作后自查首选：看清界面后决定下一步（点击/输入/验证）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "可选：要看什么（默认描述屏幕内容）"},
                        "area": {"type": "string", "description": "可选：区域 left,top,right,bottom（默认全屏）"},
                    },
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='截图并让视觉模型解读当前屏幕',
    preactivate=(('图片', '图像', '截图', '看图', '图表', '视觉执行', '视觉闭环', '屏幕操作'), ('截屏', '截个屏', '屏幕截图', '截屏看看')),
)
def screen_see(question="", area=""):
    """截图并让视觉模型解读当前屏幕（一步完成 截图+看图）。

    RPA/浏览器操作后自查首选：看清界面后决定下一步操作（点击/输入/验证）。
    """
    q = str(question or "请描述当前屏幕内容，重点关注界面元素、按钮、文字与状态。").strip()
    try:
        from PIL import ImageGrab  # noqa: F401  # 提前校验依赖，给出明确安装提示
    except ImportError:
        return "错误：屏幕截图需要 Pillow，请先安装：pip install Pillow"
    path = _capture_screen_png(area)
    if not path:
        return "错误：屏幕截图失败"
    return image_understand(path, question=q)


@tool(
        {
            "type": "function",
            "function": {
                "name": "chart_read",
                "description": "图表截图 → 结构化数据 + 解读（折线/柱状/饼图/散点等，适合读报表/仪表盘截图）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "图表图片文件绝对路径"},
                        "question": {"type": "string", "description": "可选：针对图表的具体问题"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='图表截图→结构化数据+解读',
    preactivate=(('图片', '图像', '截图', '看图', '图表', '视觉执行', '视觉闭环', '屏幕操作'),),
)
def chart_read(path, question=""):
    """图表截图 → 结构化数据 + 解读（折线/柱状/饼图/散点等）。"""
    if not str(path or "").strip():
        return "错误：path 必填"
    q = str(question or "").strip()
    if q:
        q += "；"
    q += (
        "请解读这张图表：1) 标题与图表类型；2) 坐标轴/图例/数据点，尽量精确给出数值；"
        "3) 关键趋势与结论。数据可用 Markdown 表格输出时请表格化。"
    )
    return image_understand(path, question=q)


@tool(
        {
            "type": "function",
            "function": {
                "name": "screenshot_to_html",
                "description": "UI/网页截图 → 还原为 HTML+CSS 页面（前端还原），可保存到文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "截图文件绝对路径"},
                        "out_path": {"type": "string", "description": "可选：输出 HTML 绝对路径（默认仅返回代码）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='UI截图→HTML/CSS前端还原',
    preactivate=(('截图转', 'ui转代码', '前端还原', '截图还原'),),
)
def screenshot_to_html(path, out_path=""):
    """UI/网页截图 → 还原为 HTML+CSS 页面（可保存到文件）。"""
    if not str(path or "").strip():
        return "错误：path 必填"
    q = (
        "请把这张截图还原成等价的 HTML+CSS 页面：像素级还原布局、配色、文字、间距与元素位置，"
        "输出完整可用的 HTML（CSS 内联，<html> 到 </html> 全量代码），只输出代码，不要解释。"
    )
    result = image_understand(path, question=q)
    if str(out_path or "").strip():
        # 视觉调用失败（错误文案）时绝不写入目标文件，原样返回错误
        if not result or str(result).startswith("错误"):
            return result or "错误：图片理解未返回内容"
        out = permissions.resolve(out_path)
        if not out:
            return "错误：输出路径无效"
        if not out.lower().endswith(".html"):
            out += ".html"
        ok, reason = permissions.check_filesystem(out, write=True)
        if not ok:
            return reason
        code = re.sub(r"^```(?:html|htm)?\s*|\s*```$", "", result.strip(), flags=re.I)
        with open(out, "w", encoding="utf-8") as f:
            f.write(code)
        permissions.audit("screenshot_to_html", out, str(path)[:80])
        return f"已根据截图生成 HTML 保存至 {out}\n\n{result}"
    return result


@tool(
        {
            "type": "function",
            "function": {
                "name": "debug_screenshot",
                "description": "报错/异常截图 → 识别错误并给出诊断与修复建议（错误码/文案/行号/原因/修复）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "报错截图文件绝对路径"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='报错截图→诊断修复建议',
    preactivate=(('报错截图', '错误截图', '异常截图', '报错诊断'),),
)
def debug_screenshot(path):
    """报错/异常截图 → 识别错误并给出诊断与修复建议。"""
    if not str(path or "").strip():
        return "错误：path 必填"
    q = (
        "这是报错/异常截图。请：1) 识别错误类型与关键信息（报错文案、错误码、行号、堆栈线索）；"
        "2) 分析可能原因；3) 给出具体的修复建议（需要时可提到相关文件/函数）。"
    )
    return image_understand(path, question=q)


@tool(
        {
            "type": "function",
            "function": {
                "name": "scan_read",
                "description": "扫描件/文档图片读取（图表、公式、手写、印刷混排），返回 Markdown 结构化内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "扫描件/文档图片绝对路径"},
                        "question": {"type": "string", "description": "可选：要提取/回答的内容"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='扫描件/文档图片读取（图表/公式/手写）',
    preactivate=(('扫描件', '文档图片', '识别图表', '识别公式'),),
)
def scan_read(path, question=""):
    """扫描件/文档图片读取（图表、公式、手写、印刷体混排）。"""
    if not str(path or "").strip():
        return "错误：path 必填"
    q = str(question or "").strip()
    if q:
        q += "；"
    q += (
        "这是扫描件/文档图片。请完整提取其中的文字、图表、公式与数据，保持原有结构，"
        "用 Markdown 呈现；手写内容按可辨识程度尽量转写，不确定处标注。"
    )
    return image_understand(path, question=q)


@tool(
        {
            "type": "function",
            "function": {
                "name": "image_batch",
                "description": "批量视觉分析文件夹内图片：逐张理解后汇总报告（小并发，适合图库/截图/素材批量整理）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder": {"type": "string", "description": "图片所在目录绝对路径"},
                        "question": {"type": "string", "description": "可选：每张图要回答的问题（默认描述）"},
                        "pattern": {"type": "string", "description": "可选：文件通配符（默认 *.png，如 *.jpg / *.png 可组合）"},
                        "max": {"type": "integer", "description": "可选：最多分析张数（1-200，默认 100）"},
                    },
                    "required": ["folder"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='批量视觉分析文件夹图片并汇总',
    preactivate=(('批量看图', '批量分析图片', '批量识别', '整理图库'),),
)
def image_batch(folder, question="", pattern="*.png", max=100):
    """批量视觉分析文件夹内图片：逐张理解后汇总报告（小并发）。"""
    if not str(folder or "").strip():
        return "错误：folder 必填"
    base = permissions.resolve(folder)
    if not base or not os.path.isdir(base):
        return f"错误：目录不存在：{folder}"
    ok, reason = permissions.check_filesystem(base, write=False)
    if not ok:
        return reason
    try:
        m = int(max or 100)
    except (TypeError, ValueError):
        m = 100
    limit = 1 if m < 1 else 200 if m > 200 else m
    # 防路径穿越：pattern 含 .. 等分隔符时，glob 可能越过允许目录返回外部文件。
    # 收集后用规范化路径强校验「必须位于 base 之内」，越界文件一律丢弃。
    import glob

    base_norm = os.path.normpath(base)
    files = []
    for f in sorted(glob.glob(os.path.join(base, str(pattern or "*.png")))):
        try:
            inside = os.path.commonpath([base_norm, os.path.normpath(f)]) == base_norm
        except ValueError:
            inside = False
        if inside and os.path.isfile(f) and f.lower().endswith(IMAGE_EXTENSIONS):
            files.append(f)
    if not files:
        return f"错误：目录 {base} 内没有匹配「{pattern}」的图片"
    files = files[:limit]
    q = str(question or "请描述这张图片的主要内容，并用一句话概括。").strip()
    results = [None] * len(files)

    def _one(i, p):
        results[i] = image_understand(p, question=q)

    import concurrent.futures as _cf

    with _cf.ThreadPoolExecutor(max_workers=min(4, len(files))) as ex:
        futures = [ex.submit(_one, i, p) for i, p in enumerate(files)]
        for _f in _cf.as_completed(futures):
            pass
    lines = []
    for i, p in enumerate(files):
        lines.append(f"### {os.path.basename(p)}\n{results[i] or '（分析失败）'}")
    lines.append(f"\n—— 共分析 {len(files)} 张图片 ——")
    return "\n\n".join(lines)


__all__ = ['image_process', 'ocr_image', 'image_understand', 'screen_capture', 'screen_see', 'chart_read', 'screenshot_to_html', 'debug_screenshot', 'scan_read', 'image_batch']
