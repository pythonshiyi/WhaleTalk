"""tool_desktop —— P0-1 批量拆分（工具域模块）：🖱 桌面与视觉语音.

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
阈值常量/锁统一从 shared 导入（P1-3 下沉：见 shared.py「工具域阈值与锁」节）；
仅剩余辅助函数仍依赖主文件加载顺序契约（在 `from agent_tools import *` 前已定义）。
"""

import json
import os
import re
import threading
import time
from datetime import datetime

import deepseek_client as _dc  # 可变注入配置动态访问（dc.X 注入后立即生效）
import permissions
from agent_tools.tool_media import image_understand  # 已迁工具跨模块复用
from deepseek_client import (
    _ACTIVE_SPEAK,
    _ACTIVE_SPEAK_LOCK,
    _WHISPER_CACHE,
    _WHISPER_CACHE_LOCK,
    _WHISPER_LOOP_LOCK,
    DEFAULT_BASE_URL,
    _capture_screen_png,
    _extract_json_obj,
    _ffmpeg_run,
    _ffmpeg_video_encode_args,
    _http_client,
    _mic_record_once,
    _parse_scroll,
    _rpa_ready,
    _safe_stream,
    _speak_aloud,
    get_active_client,
)
from security import _safe_url
from shared import (  # P1-3: 阈值常量下沉 shared
    _BYE_PAT,
    _TEAM_ROLE_PRESETS,
    _VISION_LOOP_ACTIONS,
    MEDIA_FORMATS,
    MEDIA_MAX_INPUT,
    RPA_FAILSAFE,
    clamp_int,
    over_limit,
)
from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export


@tool(
        {
            "type": "function",
            "function": {
                "name": "rpa_screen_size",
                "description": "获取当前屏幕分辨率（桌面 RPA 坐标用；依赖 pyautogui，缺失时返回安装指引）",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    groups=['🖱 桌面自动化'],
    phrases='获取屏幕尺寸',
    preactivate=(('点击屏幕', '移动鼠标', '键盘输入', '模拟按键', '屏幕坐标', '模拟滚轮', '桌面自动化'),),
)
def rpa_screen_size():
    """当前屏幕分辨率（RPA 坐标用）。"""
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    try:
        import pyautogui
        w, h = pyautogui.size()
        return f"屏幕分辨率：{w} x {h}"
    except Exception as e:
        return f"错误：获取屏幕尺寸失败: {e}"


def _parse_rgb(spec):
    """C7: 解析 'r,g,b' 或 '#rrggbb' 为 (r,g,b) 三元组；非法/空返回 None。"""
    s = str(spec or "").strip()
    if not s:
        return None
    if s.startswith("#") and len(s) == 7:
        try:
            return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            return None
    parts = [p.strip() for p in s.split(",")]
    if len(parts) == 3:
        try:
            vals = tuple(int(p) for p in parts)
            if all(0 <= v <= 255 for v in vals):
                return vals
        except ValueError:
            return None
    return None


@tool(
        {
            "type": "function",
            "function": {
                "name": "rpa_click",
                "description": "桌面 RPA：模拟鼠标点击屏幕坐标 (x,y)，button=left/right/middle；可选 wait_sec 点击前延迟、wait_pixel 轮询等待目标像素颜色匹配后再点击",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "屏幕 X 坐标"},
                        "y": {"type": "integer", "description": "屏幕 Y 坐标"},
                        "button": {"type": "string", "description": "可选：left/right/middle，默认 left"},
                        "clicks": {"type": "integer", "description": "可选：连击次数 1-5，默认 1"},
                        "wait_sec": {"type": "number", "description": "可选：点击前固定等待秒数（0-60，默认 0）"},
                        "wait_pixel": {"type": "string", "description": "可选：期望像素颜色 'r,g,b' 或 '#rrggbb'，轮询 (x,y) 处颜色匹配后点击；超时则不点击报错"},
                        "wait_timeout": {"type": "number", "description": "可选：wait_pixel 轮询超时秒数（1-60，默认 10）"},
                    },
                    "required": ["x", "y"],
                },
            },
        },
    groups=['🖱 桌面自动化'],
    phrases='模拟点击（屏幕坐标）',
    preactivate=(('点击屏幕', '移动鼠标', '键盘输入', '模拟按键', '屏幕坐标', '模拟滚轮', '桌面自动化'),),
)
def rpa_click(x, y, button="left", clicks=1, wait_sec=0, wait_pixel="", wait_timeout=10):
    """模拟鼠标点击；支持点击前固定延迟或轮询等待目标像素颜色。"""
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    try:
        import pyautogui
        x = int(x)
        y = int(y)
        button = str(button or "left").strip().lower()
        from shared import clamp_int
        clicks = clamp_int(clicks, 1, lo=1, hi=5)  # D4: 收敛钳制（非法/缺省→1）
        if button not in ("left", "right", "middle"):
            return "错误：button 仅支持 left/right/middle"
        # C7: 点击前等待（固定延迟 / 像素颜色轮询）
        try:
            wait_sec = max(0.0, min(60.0, float(wait_sec or 0)))
        except (TypeError, ValueError):
            wait_sec = 0.0
        try:
            wait_timeout = max(1.0, min(60.0, float(wait_timeout or 10)))
        except (TypeError, ValueError):
            wait_timeout = 10.0
        target_rgb = _parse_rgb(wait_pixel)
        if wait_pixel and not target_rgb:
            return f"错误：wait_pixel 格式非法：{wait_pixel}（应为 'r,g,b' 或 '#rrggbb'）"
        if wait_sec > 0:
            time.sleep(wait_sec)
        if target_rgb:
            deadline = time.time() + wait_timeout
            while time.time() < deadline:
                if tuple(pyautogui.pixel(x, y)) == target_rgb:
                    break
                time.sleep(0.3)
            else:
                return f"错误：等待像素颜色 {wait_pixel} 超时（{wait_timeout:.0f}s），未执行点击"
        pyautogui.FAILSAFE = RPA_FAILSAFE
        pyautogui.click(x, y, button=button, clicks=clicks)
        permissions.audit("rpa_click", f"{x},{y}", f"{button} x{clicks}")
        note = f"（等待 {wait_sec:.0f}s）" if wait_sec > 0 else ""
        note = note or (f"（像素 {wait_pixel} 匹配后点击）" if target_rgb else "")
        return f"已点击 ({x}, {y})，{button} 键 x{clicks}{note}"
    except Exception as e:
        return f"错误：RPA 点击失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "rpa_type",
                "description": "桌面 RPA：模拟键盘输入文本（需先点击目标输入框聚焦，可设按键间隔）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要输入的文本"},
                        "interval": {"type": "number", "description": "可选：每个字符间隔秒数，默认 0.02"},
                    },
                    "required": ["text"],
                },
            },
        },
    groups=['🖱 桌面自动化'],
    phrases='模拟键盘输入',
    preactivate=(('点击屏幕', '移动鼠标', '键盘输入', '模拟按键', '屏幕坐标', '模拟滚轮', '桌面自动化'),),
)
def rpa_type(text, interval=0.02):
    """模拟键盘输入文本。"""
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    if not str(text or ""):
        return "错误：text 必填"
    try:
        import pyautogui
        interval = max(0.0, min(0.2, float(interval or 0.02)))
        pyautogui.FAILSAFE = RPA_FAILSAFE
        s = str(text)
        if any(ord(c) > 127 for c in s):
            # pyautogui.typewrite 只支持其键盘映射表内的键，中文等非 ASCII 会**静默无效**；
            # 改走剪贴板 + Ctrl+V 输入 Unicode 文本。
            _dc._win_clipboard_set(s)
            pyautogui.hotkey("ctrl", "v")
            permissions.audit("rpa_type", "剪贴板粘贴", s[:60])
            return f"已通过剪贴板粘贴 {len(s)} 个字符（含非 ASCII，如中文）"
        pyautogui.typewrite(s, interval=interval)
        permissions.audit("rpa_type", "键盘输入", s[:60])
        return f"已输入 {len(s)} 个字符"
    except Exception as e:
        return f"错误：RPA 输入失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "rpa_hotkey",
                "description": "桌面 RPA：模拟组合键，如 ctrl+c / alt+tab / ctrl+shift+esc",
                "parameters": {
                    "type": "object",
                    "properties": {"keys": {"type": "string", "description": "组合键串，+ 分隔"}},
                    "required": ["keys"],
                },
            },
        },
    groups=['🖱 桌面自动化'],
    phrases='模拟快捷键',
    preactivate=(('点击屏幕', '移动鼠标', '键盘输入', '模拟按键', '屏幕坐标', '模拟滚轮', '桌面自动化'),),
)
def rpa_hotkey(keys):
    """模拟组合键，如 ctrl+c / alt+tab / ctrl+shift+esc。"""
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    if not str(keys or "").strip():
        return "错误：keys 必填"
    try:
        import pyautogui
        seq = [str(k).strip().lower() for k in str(keys).replace(" ", "").split("+") if str(k).strip()]
        if not seq:
            return "错误：keys 格式应为 ctrl+c 或 alt+tab"
        _valid = set(getattr(pyautogui, "KEYBOARD_KEYS", []) or [])
        _bad = [k for k in seq if _valid and k not in _valid]
        if _bad:
            return f"错误：未知按键 {_bad}（示例：ctrl+c / alt+tab / ctrl+shift+esc）"
        pyautogui.FAILSAFE = RPA_FAILSAFE
        pyautogui.hotkey(*seq)
        permissions.audit("rpa_hotkey", "+".join(seq), "组合键")
        return f"已按下组合键 {'+'.join(seq)}"
    except Exception as e:
        return f"错误：RPA 组合键失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "rpa_move",
                "description": "桌面 RPA：把鼠标移动到屏幕坐标 (x,y)，可指定移动耗时",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X 坐标"},
                        "y": {"type": "integer", "description": "Y 坐标"},
                        "duration": {"type": "number", "description": "可选：移动耗时秒，默认 0.2"},
                    },
                    "required": ["x", "y"],
                },
            },
        },
    groups=['🖱 桌面自动化'],
    phrases='移动鼠标',
    preactivate=(('点击屏幕', '移动鼠标', '键盘输入', '模拟按键', '屏幕坐标', '模拟滚轮', '桌面自动化'),),
)
def rpa_move(x, y, duration=0.2):
    """移动鼠标到坐标。"""
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    try:
        import pyautogui
        x, y = int(x), int(y)
        duration = max(0.0, min(2.0, float(duration or 0.2)))
        pyautogui.FAILSAFE = RPA_FAILSAFE
        pyautogui.moveTo(x, y, duration=duration)
        return f"鼠标已移动到 ({x}, {y})"
    except Exception as e:
        return f"错误：RPA 移动失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "rpa_scroll",
                "description": "桌面 RPA：滚动鼠标滚轮（正数向上滚动，负数向下滚动，可指定位置）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "clicks": {"type": "integer", "description": "滚动格数 -50~50"},
                        "x": {"type": "integer", "description": "可选：滚动位置 X"},
                        "y": {"type": "integer", "description": "可选：滚动位置 Y"},
                    },
                    "required": ["clicks"],
                },
            },
        },
    groups=['🖱 桌面自动化'],
    phrases='滚动页面',
    preactivate=(('点击屏幕', '移动鼠标', '键盘输入', '模拟按键', '屏幕坐标', '模拟滚轮', '桌面自动化'),),
)
def rpa_scroll(clicks, x=None, y=None):
    """滚动鼠标滚轮（正数向上，负数向下）。"""
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    try:
        import pyautogui
        pyautogui.FAILSAFE = RPA_FAILSAFE  # 未设置时角触中止失效（其它 RPA 工具都已设）
        n = max(-50, min(50, int(clicks or 0)))
        if x is not None and y is not None:
            pyautogui.scroll(n, x=int(x), y=int(y))
        else:
            pyautogui.scroll(n)
        permissions.audit("rpa_scroll", str(n), "滚轮")
        return f"已滚动 {n} 格"
    except Exception as e:
        return f"错误：RPA 滚动失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "rpa_screenshot",
                "description": "桌面 RPA：截取整个屏幕保存为 PNG（默认保存到工作区）",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "可选：输出 PNG 绝对路径"}},
                    "required": [],
                },
            },
        },
    groups=['🖱 桌面自动化'],
    phrases='屏幕区域截图',
    preactivate=(('点击屏幕', '移动鼠标', '键盘输入', '模拟按键', '屏幕坐标', '模拟滚轮', '桌面自动化'),),
)
def rpa_screenshot(path=""):
    """截取当前屏幕保存为 PNG（不指定路径保存到工作区）。"""
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    if str(path or "").strip():
        ok, reason = permissions.check_filesystem(path, write=True)
        if not ok:
            return reason
        p = permissions.resolve(path)
    else:
        p = permissions.resolve(os.path.join(
            permissions.WORKSPACE_DIR or "", f"rpa_screen_{datetime.now():%Y%m%d_%H%M%S}.png"
        ))
    if not p:
        return "错误：截图路径无效"
    try:
        import pyautogui
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        img = pyautogui.screenshot()
        img.save(p)
        permissions.audit("rpa_screenshot", p, "屏幕截图")
        return f"已截屏保存至 {p}"
    except Exception as e:
        return f"错误：RPA 截屏失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "screen_find_click",
                "description": "视觉定位点击闭环：截屏后用视觉模型按自然语言描述定位界面元素（如「右上角关闭按钮」），算出坐标并自动点击——看图+操作一步完成，适合自动化 Web 应用/旧桌面软件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "要点击的目标描述（自然语言，如「登录按钮」「搜索框右侧的放大镜图标」）"},
                        "area": {"type": "string", "description": "可选：限定区域 left,top,right,bottom（默认全屏，区域越小定位越准）"},
                        "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "可选：鼠标键（默认 left）"},
                        "dry_run": {"type": "boolean", "description": "可选：true 只定位不点击（先确认位置再动手）"},
                        "verify": {"type": "boolean", "description": "可选：点击后 0.6s 再截一张自查图（默认 true）"},
                    },
                    "required": ["target"],
                },
            },
        },
    groups=['🎨 媒体与图像', '🖱 桌面自动化'],
    phrases='视觉定位点击（看图即点，一句话指定目标）',
    preactivate=(('图片', '图像', '截图', '看图', '图表', '视觉执行', '视觉闭环', '屏幕操作'),),
)
def screen_find_click(target, area="", button="left", dry_run=False, verify=True):
    """视觉定位点击闭环：截图 → 视觉模型定位目标元素坐标 → 移动鼠标点击。

    把 screen_see（看）与 rpa_click（点）合成一步——用自然语言描述目标即可，
    如「右上角的关闭按钮」「登录按钮」。验证方式：视觉定位得到的是截图像素坐标。
    """
    t = str(target or "").strip()
    if not t:
        return "错误：target 必填（要点的目标描述，如「确定按钮」）"
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    try:
        from PIL import ImageGrab  # noqa: F401  提前校验依赖
    except ImportError:
        return "错误：屏幕截图需要 Pillow，请先安装：pip install Pillow"
    path = _capture_screen_png(area)
    if not path:
        return "错误：屏幕截图失败"
    try:
        from PIL import Image
        with Image.open(path) as im:
            img_w, img_h = im.size
    except Exception as e:
        return f"错误：读取截图尺寸失败: {e}"
    q = (
        f"这是 {img_w}x{img_h} 像素的屏幕截图。请在图中找到满足以下描述的界面元素：「{t}」。"
        '只输出一个 JSON 对象（不要解释、不要代码块围栏），格式：'
        '{"found": true, "label": "元素文字", "left": 整数, "top": 整数, "right": 整数, "bottom": 整数}。'
        "坐标为该元素外接框在该截图像素坐标系下的值。找不到时输出 {\"found\": false}。"
    )
    answer = image_understand(path, question=q)
    obj = _extract_json_obj(answer, must_keys=("left",))
    if obj is None or not obj.get("found"):
        snippet = str(answer or "")[:200].replace("\n", " ")
        return f"未能从屏幕上定位目标「{t}」。模型反馈：{snippet or '（无）'}"
    try:
        left, top = int(obj["left"]), int(obj["top"])
        right, bottom = int(obj.get("right", left)), int(obj.get("bottom", top))
    except (TypeError, ValueError):
        return f"错误：定位结果坐标不合法：{obj}"
    x = max(0, min(img_w - 1, (left + right) // 2))
    y = max(0, min(img_h - 1, (top + bottom) // 2))
    # area 截图是裁剪后的子图，模型坐标是子图坐标系；点击需加回区域左上偏移，
    # 否则指定区域时越点越偏（区域越小偏移越大）。
    ax = ay = 0
    if str(area or "").strip():
        try:
            _ap = [int(v.strip()) for v in str(area).split(",")]
            if len(_ap) == 4:
                ax, ay = _ap[0], _ap[1]
        except (TypeError, ValueError):
            ax = ay = 0
    x += ax
    y += ay
    label = str(obj.get("label", ""))[:40]
    preview = f"已定位目标「{t}」→ 元素 {label} 外接框 ({left},{top})-({right},{bottom})，中心 ({x},{y})"
    if dry_run:
        return f"{preview}（dry_run 仅定位未点击）"
    try:
        import pyautogui
        btn = str(button or "left").strip().lower()
        if btn not in ("left", "right", "middle"):
            return "错误：button 仅支持 left/right/middle"
        pyautogui.FAILSAFE = RPA_FAILSAFE
        pyautogui.click(x, y, button=btn)
        permissions.audit("screen_find_click", f"{x},{y}", str(t)[:60])
    except Exception as e:
        return f"{preview}\n错误：RPA 点击失败: {e}"
    note = ""
    if verify:
        time.sleep(0.6)
        check = _capture_screen_png(area)
        if check:
            note = f"\n点击后自查截图已保存：{check}（可用 screen_see 进一步确认效果）"
    return f"{preview}，已{btn}键点击完成。{note}".replace("\n\n", "\n")


@tool(
        {
            "type": "function",
            "function": {
                "name": "vision_loop",
                "description": "视觉自动操作闭环：截屏→视觉模型判断当前状态→决定并执行下一步动作（点击/输入/滚动）→再截屏验证→直到目标达成。适合『看着屏幕』自主完成的多步操作（填表、点按钮、验证界面变化、操作旧桌面软件）。goal 用自然语言描述目标",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "要达到的目标（自然语言，如「登录并进入主页」「把表单填完并提交」）"},
                        "steps": {"type": "string", "description": "可选：操作步骤提示或背景，帮助模型判断（如「先点登录，再输账号密码」）"},
                        "max_iters": {"type": "integer", "description": "可选：最多闭环轮数（1-12，默认 5）"},
                        "area": {"type": "string", "description": "可选：限定区域 left,top,right,bottom（默认全屏）"},
                    },
                    "required": ["goal"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='视觉操作闭环（看屏幕→动作→再验证，自主达成目标）',
    preactivate=(('图片', '图像', '截图', '看图', '图表', '视觉执行', '视觉闭环', '屏幕操作'),),
)
def vision_loop(goal, steps="", max_iters=5, area=""):
    """视觉自动操作闭环：截屏 → 视觉模型判断当前状态 → 决定下一步动作 → 执行 → 再截屏验证，直到目标达成。

    适合"看着屏幕自主完成"的多步操作（填表、点按钮、验证界面变化等）。
    由视觉模型输出 JSON 动作序列：{"action": done|click|type|scroll|describe, ...}。
    """
    g = str(goal or "").strip()
    if not g:
        return "错误：goal 必填（要视觉闭环完成的自然语言目标，如「登录页面并看到主页」）"
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    try:
        from PIL import ImageGrab  # noqa: F401  # 提前校验依赖
    except ImportError:
        return "错误：屏幕截图需要 Pillow，请先安装：pip install Pillow"
    try:
        from shared import clamp_int
        max_iters = clamp_int(max_iters, 5, lo=1, hi=12)  # D4: 收敛钳制
    except (TypeError, ValueError):
        max_iters = 5
    steps_hint = str(steps or "").strip()
    step_delay = 0.6

    log = []
    achieved = False
    for i in range(1, max_iters + 1):
        path = _capture_screen_png(area)
        if not path:
            return f"错误：第 {i} 轮截图失败（已执行 {i - 1} 轮）\n" + "\n".join(log)
        try:
            from PIL import Image
            with Image.open(path) as im:
                img_w, img_h = im.size
        except Exception as e:
            return f"错误：读取截图尺寸失败: {e}"
        q = (
            f"这是 {img_w}x{img_h} 像素的屏幕截图。当前视觉闭环目标：「{g}」。"
            + (f"操作步骤提示：{steps_hint}。已完成动作：{'；'.join(log) if log else '无'}。" if (steps_hint or log) else "")
            + "请判断当前屏幕状态并输出下一步动作，只输出一个 JSON 对象（不要解释、不要代码块围栏），格式："
            '{"status": "判断一句话", "action": "done|click|type|scroll|describe", '
            '"target": "动作对象描述(click)，或要输入的文本(type)，或方向与次数如向下3(scroll)", '
            '"area": "可选 目标区域 left,top,right,bottom(缩小点更准)"}。'
            "目标已达成或无法进一步推进时 action 为 done。"
        )
        answer = image_understand(path, question=q)
        obj = _extract_json_obj(answer, must_keys=("action",))
        action = str((obj or {}).get("action") or "").strip().lower()
        if action not in _VISION_LOOP_ACTIONS:
            log.append(f"第{i}轮：模型输出无法识别，已停止。模型反馈：{str(answer)[:160]}")
            break
        status = str((obj or {}).get("status") or "")[:120]
        if action == "done":
            achieved = True
            log.append(f"第{i}轮：达成。{status}")
            break
        target = str((obj or {}).get("target") or "").strip()
        sub_area = str((obj or {}).get("area") or area or "").strip()
        try:
            if action == "click":
                r = screen_find_click(target or "当前焦点", area=sub_area)
                log.append(f"第{i}轮点击：{str(r)[:120]}")
            elif action == "type":
                r = rpa_type(target or "", interval=0.02)
                log.append(f"第{i}轮输入：{str(r)[:120]}")
            elif action == "scroll":
                r = rpa_scroll(_parse_scroll(target))
                log.append(f"第{i}轮滚动：{str(r)[:120]}")
            elif action == "describe":
                log.append(f"第{i}轮观察：{status or '已观察'}")
        except Exception as e:
            log.append(f"第{i}轮{action}失败：{str(e)[:120]}")
            break
        time.sleep(step_delay)
    else:
        log.append(f"已达最大轮数 {max_iters}，结束（可通过第二次调用继续）")

    if achieved:
        return "视觉闭环完成（已达成目标）：\n" + "\n".join(log[-12:])
    return "未能达成视觉闭环目标（已停止，未确认达成，请检查或重试）：\n" + "\n".join(log[-12:])


@tool(
        {
            "type": "function",
            "function": {
                "name": "tts_save",
                "description": "把文本合成为语音 WAV 文件（Windows SAPI 中文语音），可调语速（rate -10~10）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要合成的文本"},
                        "path": {"type": "string", "description": "输出 WAV 文件绝对路径（须在允许目录内）"},
                        "rate": {"type": "integer", "description": "可选：语速 -10~10，默认 0"},
                        "voice": {"type": "string", "description": "可选：音色名子串（如 Huihui / Xiaoxiao，留空=系统默认）"},
                    },
                    "required": ["text", "path"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='文字转语音（存文件）',
    preactivate=(('朗读', '语音播报', '文字转语音', '读给我听', '停止朗读', 'tts'),),
)
def tts_save(text, path, rate=0, voice=""):
    """语音合成保存为 WAV 文件（Windows SAPI，可选 pywin32；无则用 PowerShell）。

    voice 为音色名子串（不区分大小写）：在系统已装 SAPI 音色里匹配第一个命中者，
    留空用默认音色。匹配不到时静默回退默认音色。
    """
    if not text or not str(text).strip():
        return "错误：text 必填"
    if not path or not str(path).strip():
        return "错误：path 必填"
    p = permissions.resolve(path)
    if not p:
        return "错误：路径无效"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    except Exception:
        pass
    try:
        import pythoncom
        import win32com.client

        synth = str(text)[:8000]  # 文本上限：超长合成会长时间占住共享工具线程池
        result = {"err": None}

        def _speak():
            pythoncom.CoInitialize()
            stream = None
            try:
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                if str(voice or "").strip():
                    try:
                        want = str(voice).strip().lower()
                        installed = speaker.GetVoices()
                        for vi in range(installed.Count):
                            item = installed.Item(vi)
                            if want in str(item.GetDescription()).lower():
                                speaker.Voice = item
                                break
                    except Exception:
                        pass
                stream = win32com.client.Dispatch("SAPI.SpFileStream")
                stream.Open(p, 3)  # SSFMCreateForWrite
                speaker.AudioOutputStream = stream
                try:
                    speaker.Rate = max(-10, min(10, int(rate or 0)))
                except (TypeError, ValueError):
                    pass
                # Speak 同步阻塞且无法安全强杀：放后台线程执行，主路径只等 60s
                speaker.Speak(synth)
            except Exception as e:
                result["err"] = e
            finally:
                # COM 资源成对释放：Speak 抛异常也要关流 + CoUninitialize（防单元泄漏）
                if stream is not None:
                    try:
                        stream.Close()
                    except Exception:
                        pass
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

        t = threading.Thread(target=_speak, daemon=True)
        t.start()
        t.join(timeout=60.0)
        if t.is_alive():
            return (
                f"语音合成进行中（文本较长，后台继续生成），稍后可在 {p} 查看。"
                "需要控制时长请缩短文本。"
            )
        if result["err"]:
            raise result["err"]
        try:
            size = os.path.getsize(p) if os.path.exists(p) else 0
        except OSError:
            size = 0
        if size < 100:
            return (
                f"已生成语音文件 {p}（{size} 字节）但内容可能为空："
                "系统未安装中文语音包（设置 → 时间和语言 → 语音）时 SAPI 无可用音色"
            )
        return f"已合成语音保存至 {p}"
    except ImportError:
        return "错误：需要 pywin32（pip install pywin32）"
    except Exception as e:
        return f"错误：语音合成失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "speech_to_text",
                "description": "本地语音转文字（依赖 faster-whisper，离线识别；首次运行自动下载所选模型）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "音频文件绝对路径（wav/mp3/m4a 等）"},
                        "model": {"type": "string", "description": "可选：tiny/base/small/medium/large-v3（默认 base，tiny 最快）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='语音转文字',
    preactivate=(('语音转文字', '语音识别', '听写'),),
)
def speech_to_text(path, model="base"):
    """本地语音转文字（依赖 faster-whisper，离线识别）。
    model: tiny/base/small/medium/large-v3（首次运行需下载对应模型，tiny/base 较小）。"""
    if not str(path or "").strip():
        return "错误：path 必填"
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：音频文件不存在：{path}"
    model_name = str(model or "base").strip().lower()
    if model_name not in ("tiny", "base", "small", "medium", "large-v3"):
        model_name = "base"
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "错误：需要 faster-whisper（pip install faster-whisper），安装后重试"
    try:
        inst = _WHISPER_CACHE.get(model_name)
        if inst is None:
            with _WHISPER_CACHE_LOCK:
                inst = _WHISPER_CACHE.get(model_name)
                if inst is None:
                    inst = WhisperModel(model_name, device="cpu", compute_type="int8")
                    _WHISPER_CACHE[model_name] = inst
        segments, _info = inst.transcribe(p)
        text = "".join(seg.text for seg in segments).strip()
        return text or "（未识别出语音内容）"
    except Exception as e:
        return f"错误：语音识别失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "tts_speak",
                "description": "朗读文本：立即返回，后台通过扬声器播放（配对 tts_stop 可随时打断）。适合提醒、播报、读结果给用户听",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要朗读的文本（≤4000 字，超出部分不朗读）"},
                        "voice": {"type": "string", "description": "可选：音色名子串（如 Huihui / Xiaoxiao，留空=系统默认）"},
                        "rate": {"type": "integer", "description": "可选：语速 -10~10（默认 0）"},
                        "volume": {"type": "integer", "description": "可选：音量 0~100（默认 100）"},
                        "save_path": {"type": "string", "description": "可选：同时把合成结果另存为 WAV 文件"},
                    },
                    "required": ["text"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='立即朗读（后台播放，可打断）',
    preactivate=(('朗读', '语音播报', '文字转语音', '读给我听', '停止朗读', 'tts'),),
)
def tts_speak(text, voice="", rate=0, volume=100, save_path=""):
    """朗读文本（立即返回，后台播放；配对 tts_stop 可随时停止）。

    save_path 可选：同时把合成结果另存为 WAV 文件。
    """
    t = str(text or "").strip()
    if not t:
        return "错误：text 必填"
    truncated = len(t) > 4000
    if truncated:
        t = t[:4000]  # 与 _speak_aloud 的实际上限对齐（此前声明 8000 但只读 4000）
    sid = _speak_aloud(t, rate=rate, volume=volume, voice=voice, label="工具朗读")
    if not sid:
        return "错误：本机没有可用的 TTS 引擎（需要 pywin32 的 SAPI），或系统无中文语音包"
    out = f"✅ 已开始后台朗读（会话 {sid}）。可用 tts_stop 停止；语速 {rate}，音量 {volume}。"
    if truncated:
        out += "\n（注意：文本超过 4000 字，仅朗读前 4000 字）"
    p = str(save_path or "").strip()
    if p:
        saved = tts_save(t[:4000], p, rate=rate)
        out += f"\n另存：{saved}"
    return out


@tool(
        {
            "type": "function",
            "function": {
                "name": "tts_stop",
                "description": "停止朗读：立即中断后台播放（传 sid 只停指定会话，留空停止全部）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sid": {"type": "string", "description": "可选：tts_speak 返回的会话 id（留空=全部停止）"},
                    },
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='停止朗读',
    preactivate=(('朗读', '语音播报', '文字转语音', '读给我听', '停止朗读', 'tts'),),
)
def tts_stop(sid=""):
    """停止朗读：sid 为空停止全部当前朗读，否则只停指定会话。返回实际停止数。"""
    s = str(sid or "").strip()
    stopped = 0
    targets = []
    with _ACTIVE_SPEAK_LOCK:
        if s:
            if s in _ACTIVE_SPEAK:
                targets.append((s, _ACTIVE_SPEAK[s]))
        else:
            targets = list(_ACTIVE_SPEAK.items())
    for _k, entry in targets:
        try:
            # 只置停止标志：由朗读线程在自己的 COM 单元内完成 purge。跨线程调用 SAPI
            # COM 对象是未定义行为（会引发 MMDevApi 卸载竞态 → 原生访问冲突终止进程）。
            entry["event"].set()
            stopped += 1
        except Exception:
            pass
    return f"已停止 {stopped} 个朗读会话" if stopped else "当前没有进行中的朗读"


@tool(
        {
            "type": "function",
            "function": {
                "name": "voice_chat_loop",
                "description": "实时语音对话循环：麦克风听用户说一句 → 本地转写 → AI 回复 → 直接朗读出声，循环多轮直到说完「再见」。适合免打字的快速问答节奏（需本机麦克风与 faster-whisper/sounddevice）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rounds": {"type": "integer", "description": "可选：最多对话轮数（1-20，默认 3；中途说再见即结束）"},
                        "model": {"type": "string", "description": "可选：whisper 模型 tiny/base/small（默认 base）"},
                        "max_seconds": {"type": "integer", "description": "可选：每轮录音最长秒数（默认 15）"},
                        "speak": {"type": "boolean", "description": "可选：是否朗读回复（默认 true）"},
                        "rate": {"type": "integer", "description": "可选：语速 -10 到 10（默认 0 正常）"},
                    },
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='实时语音对话（听一句答一句）',
    preactivate=(('语音对话', '语音聊天', '语音交互', '免打字'),),
)
def voice_chat_loop(rounds=3, model="base", max_seconds=15, speak=True, rate=0):
    """全双工语音对话循环：麦克风听一句 → 转写 → 思考回复 → 朗读，循环多轮。

    依赖：sounddevice+numpy（录音）、faster-whisper（转写）、pywin32 SAPI（朗读）。
    说「再见/拜拜/结束对话」即挂断。适合不想打字的快速问答节奏。
    """
    from shared import clamp_int
    rounds_n = 3
    try:
        rounds_n = clamp_int(rounds, 3, lo=1, hi=20)
    except (TypeError, ValueError):
        rounds_n = 3
    client = get_active_client()
    if client is None:
        return "错误：没有可用客户端（请先在设置中配置 API Key）"
    with _WHISPER_LOOP_LOCK:  # 同一时间只允许一个语音会话占用麦克风
        log = []
        ended = False
        for i in range(rounds_n):
            wav, err = _mic_record_once(max_seconds=max_seconds)
            if err:
                return "\n".join(log) + ("\n" if log else "") + err
            if not wav:
                if i == 0:
                    return "未听到说话内容（前几秒完全安静）。请确认麦克风可用后重试"
                log.append(f"（第 {i + 1} 轮未听到声音，语音会话结束）")
                break
            heard = speech_to_text(wav, model=model)
            heard = str(heard or "").strip()
            low = heard.lower()
            if not heard or heard.startswith(("错误", "（未识别")):
                log.append(f"第 {i + 1} 轮：没能听清，可以再说一遍")
                if speak:
                    _speak_aloud("没听清，请再说一遍", rate=rate)
                continue
            log.append(f"🗣 你说：{heard}")
            if any(p in low for p in _BYE_PAT):
                if speak:
                    _speak_aloud("好的，下次再聊，再见！", rate=rate)
                log.append("👋 会话由你结束，再见！")
                ended = True
                break
            prompt = (
                "你在与用户进行实时语音对话：回复请口语化、简洁自然（一两句话最好，不要 Markdown、"
                "不要列表和长篇大论），因为内容会被转成语音朗读。\n\n用户说：" + heard
            )
            try:
                resp = client.client.chat.completions.create(
                    model=client.model,
                    messages=[
                        {"role": "system", "content": "你是语音助手鲸语，用最短的话把事情说明白。"},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=1024,
                    stream=False,
                    timeout=120.0,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                reply = (resp.choices[0].message.content or "").strip() or "（我在想……没想出说什么）"
            except Exception as e:
                reply = f"网络好像出了点问题：{e}"
                log.append(f"🤔 系统异常：{reply}")
                break
            log.append(f"🐋 鲸语：{reply}")
            if speak:
                _speak_aloud(reply, rate=rate)
        if not ended and len(log) and not log[-1].startswith(("（", "👋")):
            log.append("（本轮语音会话结束）")
        permissions.audit("voice_chat_loop", f"rounds={rounds_n}", f"共 {len(log)} 行")
        return "\n".join(log)


def _load_ref_image(ref):
    """读取参考图（本地路径或 http(s) URL）为 multipart 文件三元组。

    返回 (错误字符串|None, (文件名, 字节, mime)|None)。32MB 上限，地址走 SSRF 校验。
    """
    ref = str(ref or "").strip()
    raw = None
    name = "reference.png"
    if ref.lower().startswith(("http://", "https://")):
        err = _safe_url(ref)
        if err:
            return f"参考图地址不安全（{err}）", None
        try:
            with _safe_stream("GET", ref, timeout=60) as r:
                r.raise_for_status()
                buf = b""
                for chunk in r.iter_bytes(64 * 1024):
                    buf += chunk
                    if len(buf) > 32 * 1024 * 1024:
                        return "参考图超过 32MB 上限", None
                raw = buf
        except Exception as e:
            return f"参考图下载失败: {e}", None
    else:
        rp = permissions.resolve(ref)
        if not rp or not os.path.isfile(rp):
            return f"参考图不存在：{ref}", None
        ok, reason = permissions.check_filesystem(rp, write=False)
        if not ok:
            return reason, None
        try:
            if os.path.getsize(rp) > 32 * 1024 * 1024:
                return "参考图超过 32MB 上限", None
            with open(rp, "rb") as f:
                raw = f.read()
        except OSError as e:
            return f"参考图读取失败: {e}", None
        name = os.path.basename(rp)
    try:
        mime = _dc._detect_image_mime(raw[:16])
    except Exception:
        mime = "image/png"
    return None, (name, raw, mime)


def _save_gen_items(items, out, num):
    """把接口返回的图片项写入 out（num>1 时第二张起加 _2/_3 后缀）。

    返回 (已保存路径列表, 错误字符串|None)。单张上限 20MB，URL 走 SSRF 校验。
    """
    import base64

    saved = []
    for i, it in enumerate(items[:max(1, num)]):
        if not isinstance(it, dict):
            return saved, "接口返回的图片项格式异常"
        if i == 0:
            dst = out
        else:
            stem, ext = os.path.splitext(out)
            dst = f"{stem}_{i + 1}{ext}"
        raw = None
        if it.get("b64_json"):
            try:
                raw = base64.b64decode(it["b64_json"])
            except Exception:
                return saved, "接口返回的图片数据无法解码"
        elif it.get("url"):
            dl = str(it["url"])
            err = _safe_url(dl)
            if err:
                return saved, f"图片接口返回了不安全的下载地址（{err}）"
            try:
                with _safe_stream("GET", dl, timeout=120) as r:
                    total = 0
                    buf = b""
                    for chunk in r.iter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > 20 * 1024 * 1024:
                            return saved, "图片下载超过 20MB 上限，已放弃保存"
                        buf += chunk
                    raw = buf
            except Exception as e:
                return saved, f"图片下载失败: {e}"
        else:
            return saved, "接口返回格式无法解析"
        if len(raw) > 20 * 1024 * 1024:
            return saved, "接口返回的图片超过 20MB 上限，已中止"
        try:
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            with open(dst, "wb") as f:
                f.write(raw)
        except OSError as e:
            return saved, f"图片写入失败: {e}"
        saved.append(dst)
    return saved, None


@tool(
        {
            "type": "function",
            "function": {
                "name": "image_generate",
                "description": "生成或编辑图片（OpenAI 兼容 images API，需配置 image_api_key/image_base_url/image_model）。纯文生图只传 prompt；要沿用参考图保持角色/画风一致，传 reference（自动走 /images/edits 图生图/编辑），mask 可指定重绘区域。size 接受任意「宽x高」（每边 64-4096，支持 1920x1080/1080x1920 等视频比例）或 auto；n 可一次出多张",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "图片描述提示词，或对参考图的编辑指令"},
                        "path": {"type": "string", "description": "可选：输出路径（默认工作区 images/）；n>1 时第二张起自动加 _2/_3 序号后缀"},
                        "size": {"type": "string", "description": "可选：尺寸「宽x高」（每边 64-4096，任意比例，如 1024x1024 / 1920x1080 / 1080x1920）或 auto，默认 1024x1024"},
                        "n": {"type": "integer", "description": "可选：一次生成的张数 1-10（默认 1）"},
                        "reference": {"type": "string", "description": "可选：参考图路径或 http(s) URL；传入即走图生图/编辑（/images/edits），用于角色与画风跨镜头一致"},
                        "mask": {"type": "string", "description": "可选：遮罩图路径（透明区域=需要重绘处），仅在传 reference 时生效"},
                    },
                    "required": ["prompt"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='文生图/图生图（参考图编辑）',
    preactivate=(('文生图', 'ai绘图', '生成一张图', '画一张'), ('图生图', '参考图', '保持角色一致', '改图', '修图', '编辑图片')),
)
def image_generate(prompt, path="", size="1024x1024", n=1, reference="", mask=""):
    """生成或编辑图片（OpenAI 兼容 images API）。

    无 reference：POST /images/generations（文生图）。
    有 reference：POST /images/edits（图生图/编辑，multipart），用于角色/画风一致。
    size 支持任意「宽x高」（每边 64-4096）或 auto；n 支持 1-10 张。
    """
    p = str(prompt or "").strip()
    if not p:
        return "错误：prompt 必填"
    sz = str(size or "1024x1024").strip().lower()
    if sz != "auto":
        m = re.match(r"^(\d{2,4})\s*[x×*]\s*(\d{2,4})$", sz)
        if not m:
            return f"错误：size 非法：{size}（应为「宽x高」如 1024x1024 / 1920x1080 / 1080x1920，或 auto）"
        w, h = int(m.group(1)), int(m.group(2))
        if not (64 <= w <= 4096 and 64 <= h <= 4096):
            return f"错误：size 每边须在 64-4096 之间：{size}"
        sz = f"{w}x{h}"
    try:
        num = clamp_int(n, 1, lo=1, hi=10)
    except (TypeError, ValueError):
        num = 1
    key = str(_dc.IMAGE_GEN_KEY or "").strip()
    if not key:
        return "错误：未配置图片生成（config.json 的 image_api_key / image_base_url / image_model）"
    base = str(_dc.IMAGE_GEN_BASE or "").strip().rstrip("/") or DEFAULT_BASE_URL
    if str(path or "").strip():
        out = permissions.resolve(path)
        if not out:
            return "错误：输出路径无效"
        if not out.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            out += ".png"
        ok, reason = permissions.check_filesystem(out, write=True)
        if not ok:
            return reason
    else:
        base_dir = os.path.join(permissions.WORKSPACE_DIR or ".", "images")
        ok, reason = permissions.check_filesystem(base_dir, write=True)
        if not ok:
            return reason
        try:
            os.makedirs(base_dir, exist_ok=True)
        except Exception:
            pass
        out = os.path.join(base_dir, f"gen_{datetime.now():%Y%m%d_%H%M%S}.png")
    try:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    except Exception:
        pass
    ref = str(reference or "").strip()
    mask_p = str(mask or "").strip()
    try:
        headers = {"Authorization": f"Bearer {key}"}
        if ref:
            err, img = _load_ref_image(ref)
            if err:
                return f"错误：{err}"
            files = {"image": img}
            if mask_p:
                err2, mk = _load_ref_image(mask_p)
                if err2:
                    return f"错误：遮罩图{err2}"
                files["mask"] = mk
            resp = _http_client().post(
                f"{base}/images/edits",
                data={
                    "model": _dc.IMAGE_GEN_MODEL,
                    "prompt": p,
                    "n": str(num),
                    "size": sz,
                    "response_format": "b64_json",
                },
                files=files,
                headers=headers,
                timeout=300.0,
            )
        else:
            resp = _http_client().post(
                f"{base}/images/generations",
                json={
                    "model": _dc.IMAGE_GEN_MODEL,
                    "prompt": p,
                    "n": num,
                    "size": sz,
                    "response_format": "b64_json",
                },
                headers=headers,
                timeout=300.0,
            )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except Exception:
            return f"错误：图片接口返回非 JSON（HTTP {getattr(resp, 'status_code', '?')}）"
        items = payload.get("data") or []
        if not items:
            return "错误：接口未返回图片"
        saved, err = _save_gen_items(items, out, num)
        if err:
            return f"错误：{err}"
        if not saved:
            return "错误：接口未返回可保存的图片"
        permissions.audit("image_generate", "；".join(saved), p[:80])
        return f"已生成 {len(saved)} 张图片：" + "、".join(saved)
    except Exception as e:
        return f"错误：图片生成失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "qrcode",
                "description": "二维码：generate 把文本/链接生成 PNG 二维码；read 识别本地图片中的二维码（可识别多个）。识别需 pyzbar，缺失时降级提示",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "generate / read"},
                        "text": {"type": "string", "description": "generate 必填：要编码的内容（链接/文本）"},
                        "output": {"type": "string", "description": "generate 必填：输出 PNG 路径"},
                        "image_path": {"type": "string", "description": "read 必填：待识别图片路径"},
                        "size": {"type": "integer", "description": "可选：生成边长像素（默认 300，64-1024）"},
                        "error_correction": {"type": "string", "description": "可选：纠错等级 L/M/Q/H（默认 M）"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='二维码生成/识别',
    preactivate=(('二维码', '生成二维码', '识别二维码'),),
)
def qrcode(action="generate", text="", output="", image_path="", size=300, error_correction="M"):
    """二维码生成与识别。"""
    act = str(action or "generate").strip().lower()
    if act not in ("generate", "read"):
        return "错误：action 仅支持 generate / read"
    if act == "generate":
        if not str(text or "").strip():
            return "错误：generate 需要 text（要编码的内容）"
        if not str(output or "").strip():
            return "错误：generate 需要 output（PNG 路径）"
        try:
            import qrcode
        except ImportError:
            return "未安装 qrcode，请先执行 pip_install qrcode 后重试"
        try:
            from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q
        except Exception:
            ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q, ERROR_CORRECT_H = 1, 0, 3, 2
        out = permissions.resolve(output)
        if not out:
            return "错误：输出路径无效"
        if not out.lower().endswith(".png"):
            out += ".png"
        ok, reason = permissions.check_filesystem(out, write=True)
        if not ok:
            return reason
        try:
            s = clamp_int(size or 300, 300, lo=64, hi=1024)
        except (TypeError, ValueError):
            s = 300
        ec_map = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M,
                  "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
        ec = ec_map.get(str(error_correction or "M").upper(), ERROR_CORRECT_M)
        try:
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            qr = qrcode.QRCode(version=None, error_correction=ec, box_size=10, border=2)
            qr.add_data(str(text))
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            if img.size != (s, s):
                img = img.resize((s, s))
            img.save(out)
            permissions.audit("qrcode_generate", out, f"{len(str(text))} 字符")
            return f"已生成二维码: {out}（{s}x{s}px）"
        except Exception as e:
            return f"错误：二维码生成失败: {e}"
    # read
    if not str(image_path or "").strip():
        return "错误：read 需要 image_path"
    ok, reason = permissions.check_filesystem(image_path, write=False)
    if not ok:
        return reason
    p = permissions.resolve(image_path)
    if not p or not os.path.isfile(p):
        return f"错误：图片不存在：{image_path}"
    try:
        import pyzbar.pyzbar as pyzbar
        from PIL import Image
    except Exception:
        # pyzbar 在 Windows 依赖系统 zbar DLL，缺失时 import 即抛异常 → 统一降级提示
        return (
            "未安装 pyzbar（Windows 需系统 zbar 库，pip_install pyzbar 后还需安装 "
            "zbar DLL）。降级方案：可先用 ocr_image 对图片做粗识别"
        )
    try:
        img = Image.open(p).convert("RGB")
        results = pyzbar.decode(img)
        if not results:
            return "未识别到二维码（可尝试 ocr_image 粗识别）"
        lines = [f"识别到 {len(results)} 个二维码："]
        for r in results:
            lines.append("· " + r.data.decode("utf-8", errors="replace"))
        return "\n".join(lines)
    except Exception as e:
        return f"错误：二维码识别失败: {e}"


def _ff_media_duration(path, timeout=20):
    """用 ffmpeg -i 的 stderr 解析媒体时长（秒）；失败返回 None。"""
    code, text = _ffmpeg_run(["-hide_banner", "-i", path], timeout=timeout)
    if code is None:
        return None
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text or "")
    if not m:
        return None
    try:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        return None


def _ff_subtitle_filter(sub_path):
    """构造 ffmpeg subtitles 滤镜串（Windows 盘符冒号等做转义）。"""
    s = str(sub_path).replace("\\", "/")
    s = (s.replace(":", "\\:").replace("'", r"\'")
         .replace("[", "\\[").replace("]", "\\]").replace(",", "\\,"))
    return f"subtitles=filename='{s}'"


def _mv_norm_audio(src, dur, out):
    """把音频统一为 44.1k/立体声/pcm_s16le 且时长恰为 dur；src 为空则生成等长静音。

    返回 (ok, 错误字符串)。归一化后各片段格式一致，concat 可 -c copy 无缝拼接。
    """
    d = max(0.05, float(dur or 0.05))
    args = ["-hide_banner", "-y"]
    if src:
        args += ["-i", src, "-af", "apad"]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    args += ["-t", f"{d:.3f}", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", out]
    code, text = _ffmpeg_run(args)
    if code != 0:
        return False, (text or "")[-200:]
    return True, ""


def _mv_concat_list(paths, list_path):
    """写 ffmpeg concat 清单（正斜杠 + 单引号转义）。"""
    with open(list_path, "w", encoding="utf-8") as f:
        for p in paths:
            f.write("file '" + str(p).replace("\\", "/").replace("'", "'\\''") + "'\n")


def _mv_finish(src, output, audio="", bgm="", subtitle=""):
    """给已拼接的无声视频叠加旁白/BGM/字幕并编码落盘。

    返回 (输出路径|"", 说明, 错误|"")。旁白全音量、BGM 0.25 音量混音。
    """
    try:
        ok, reason = permissions.check_filesystem(output, write=True)
        if not ok:
            return "", "", reason
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    except Exception:
        pass
    nar, bg, sub = "", "", ""
    if str(audio or "").strip():
        rp = permissions.resolve(audio)
        if not rp or not os.path.isfile(rp):
            return "", "", f"音频不存在：{audio}"
        nar = rp
    if str(bgm or "").strip():
        rp = permissions.resolve(bgm)
        if not rp or not os.path.isfile(rp):
            return "", "", f"BGM 不存在：{bgm}"
        bg = rp
    if str(subtitle or "").strip():
        rp = permissions.resolve(subtitle)
        if not rp or not os.path.isfile(rp):
            return "", "", f"字幕不存在：{subtitle}"
        sub = rp
    if not (nar or bg or sub):
        import shutil
        try:
            shutil.copyfile(src, output)
        except Exception as e:
            return "", "", f"落盘失败: {e}"
        return output, "无音轨/字幕", ""
    args = ["-hide_banner", "-y", "-i", src]
    idx = 1
    nar_i = bg_i = None
    if nar:
        args += ["-i", nar]
        nar_i = idx
        idx += 1
    if bg:
        # BGM 循环铺满整片，配合 -shortest 由视频长度决定成片时长（否则短 BGM 会截断视频）
        args += ["-stream_loop", "-1", "-i", bg]
        bg_i = idx
        idx += 1
    if sub:
        args += ["-vf", _ff_subtitle_filter(sub)]
    if nar and bg:
        args += ["-filter_complex",
                 f"[{nar_i}:a]volume=1.0[a1];[{bg_i}:a]volume=0.25[a2];"
                 "[a1][a2]amix=inputs=2:duration=longest,apad[aout]",
                 "-map", "0:v", "-map", "[aout]", "-c:a", "aac"]
    elif nar or bg:
        a_i = nar_i if nar else bg_i
        # apad 铺满静音，配合 -shortest 由视频长度决定时长（短音频不再截断视频）
        args += ["-map", "0:v", "-map", f"{a_i}:a", "-c:a", "aac", "-af", "apad"]
    else:
        args += ["-map", "0:v", "-an"]
    args += _ffmpeg_video_encode_args("h264") + ["-shortest", output]
    code, text = _ffmpeg_run(args)
    if code != 0:
        return "", "", f"成片编码失败：{(text or '')[-400:]}"
    note = f"音频={'有' if (nar or bg) else '无'}，字幕={'有' if sub else '无'}"
    return output, note, ""


def _mv_compose(items, output, durations=None, duration=3.0, effect="kenburns",
                transition=0.0, resolution="1920x1080", fps=30,
                audio="", bgm="", subtitle="", workdir=""):
    """图片/视频序列 → 成片（每段运镜 + 可选交叉转场 + 旁白/BGM/字幕）。

    durations 给出每段时长（秒），缺省用 duration。返回 (输出路径|"", 说明, 错误|"")。
    """
    items = [str(x) for x in (items or []) if str(x or "").strip()]
    if not items:
        return "", "", "items 为空（至少一张图片或一段视频）"
    m = re.match(r"^(\d{2,4})\s*[x×*]\s*(\d{2,4})$", str(resolution or "").strip().lower())
    if not m:
        return "", "", f"resolution 非法：{resolution}（应为「宽x高」如 1920x1080）"
    W, H = int(m.group(1)), int(m.group(2))
    if not (16 <= W <= 7680 and 16 <= H <= 7680):
        return "", "", f"resolution 每边须在 16-7680：{resolution}"
    try:
        fps = clamp_int(fps or 30, 30, lo=1, hi=120)
    except (TypeError, ValueError):
        fps = 30
    eff = str(effect or "none").strip().lower()
    if eff not in ("none", "kenburns", "kenburns-in", "kenburns-out"):
        eff = "kenburns"
    try:
        trans = max(0.0, min(5.0, float(transition or 0)))
    except (TypeError, ValueError):
        trans = 0.0
    resolved = []
    for it in items:
        rp = permissions.resolve(it)
        if not rp or not os.path.isfile(rp):
            return "", "", f"素材不存在：{it}"
        ok, reason = permissions.check_filesystem(rp, write=False)
        if not ok:
            return "", "", reason
        resolved.append(rp)
    if not workdir:
        base_dir = os.path.join(permissions.WORKSPACE_DIR or ".", "video")
        workdir = os.path.join(base_dir, f"compose_{datetime.now():%Y%m%d_%H%M%S}")
    try:
        os.makedirs(workdir, exist_ok=True)
    except Exception as e:
        return "", "", f"工作目录创建失败: {e}"
    segs = [os.path.join(workdir, f"seg_{i:03d}.mp4") for i in range(len(resolved))]
    seg_durs = [0.0] * len(resolved)
    seg_errs = [None] * len(resolved)

    def _render_seg(i, src, seg):
        """渲染/转码单个素材段（独立 ffmpeg 进程，可并行）。返回 (i, 时长|None, 错误|None)。"""
        is_img = src.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"))
        if is_img:
            d = duration
            if durations and i < len(durations):
                d = durations[i]
            try:
                d = max(0.2, float(d or 3.0))
            except (TypeError, ValueError):
                d = 3.0
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                  f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
            if eff != "none":
                frames = max(1, int(round(d * fps)))
                step = 0.12 / frames
                if eff == "kenburns-out":
                    z = f"if(eq(on,1),1.12,max(zoom-{step:.6f},1.0))"
                else:
                    z = f"min(zoom+{step:.6f},1.12)"
                vf += (f",zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                       f":d={frames}:s={W}x{H}:fps={fps}")
            code, text = _ffmpeg_run([
                "-hide_banner", "-y", "-loop", "1", "-i", src, "-t", f"{d:.3f}",
                "-vf", vf, *_ffmpeg_video_encode_args("h264"),
                "-r", str(fps), "-an", seg])
            if code != 0:
                return i, None, f"第 {i + 1} 个素材渲染失败：{(text or '')[-300:]}"
            return i, d, None
        d = 0.0
        if durations and i < len(durations):
            try:
                d = max(0.0, float(durations[i] or 0))
            except (TypeError, ValueError):
                d = 0.0
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
        args = ["-hide_banner", "-y", "-i", src]
        if d > 0:
            args += ["-t", f"{d:.3f}"]
        args += ["-vf", vf, *_ffmpeg_video_encode_args("h264"),
                 "-r", str(fps), "-an", seg]
        code, text = _ffmpeg_run(args)
        if code != 0:
            return i, None, f"第 {i + 1} 个素材转码失败：{(text or '')[-300:]}"
        dd = d or _ff_media_duration(seg) or 0.0
        return i, max(0.2, float(dd)), None

    # 并行渲染各段（独立 ffmpeg 进程，吃满多核；WHALETALK_MV_RENDER_WORKERS 可配，0=自动）
    try:
        _workers = clamp_int(os.environ.get("WHALETALK_MV_RENDER_WORKERS", 0), 0, lo=0, hi=16)
    except (TypeError, ValueError):
        _workers = 0
    if _workers <= 0:
        _workers = min(6, max(1, (os.cpu_count() or 4) - 2))
    _workers = max(1, min(_workers, len(resolved)))
    if _workers > 1 and len(resolved) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=_workers, thread_name_prefix="mvseg") as _ex:
            for i, dur_i, err_i in _ex.map(
                    lambda p: _render_seg(*p),
                    [(i, resolved[i], segs[i]) for i in range(len(resolved))]):
                if err_i:
                    seg_errs[i] = err_i
                elif dur_i is not None:
                    seg_durs[i] = dur_i
    else:
        for i in range(len(resolved)):
            _i, dur_i, err_i = _render_seg(i, resolved[i], segs[i])
            if err_i:
                seg_errs[i] = err_i
            elif dur_i is not None:
                seg_durs[i] = dur_i
    for e in seg_errs:
        if e:
            return "", "", e
    silent = os.path.join(workdir, "silent.mp4")
    if trans > 0 and len(segs) > 1:
        args = []
        for s in segs:
            args += ["-i", s]
        fc = []
        prev = "[0:v]"
        offset = 0.0
        for i in range(1, len(segs)):
            offset += max(0.0, seg_durs[i - 1] - trans)
            outl = f"[vx{i}]"
            fc.append(f"{prev}[{i}:v]xfade=transition=fade:duration={trans:.3f}:"
                      f"offset={offset:.3f}{outl}")
            prev = outl
        args += ["-filter_complex", ";".join(fc), "-map", prev,
                 *_ffmpeg_video_encode_args("h264"),
                 "-r", str(fps), silent]
        code, text = _ffmpeg_run(args)
        if code != 0:
            return "", "", f"转场拼接失败：{(text or '')[-300:]}"
    else:
        lst = os.path.join(workdir, "concat.txt")
        _mv_concat_list(segs, lst)
        code, text = _ffmpeg_run(["-hide_banner", "-y", "-f", "concat", "-safe", "0",
                                  "-i", lst, "-c", "copy", silent])
        if code != 0:
            return "", "", f"拼接失败：{(text or '')[-300:]}"
    out, note, err = _mv_finish(silent, output, audio=audio, bgm=bgm, subtitle=subtitle)
    if err:
        return "", "", err
    return out, f"{len(segs)} 段合成，{note}", ""


@tool(
        {
            "type": "function",
            "function": {
                "name": "media_ffmpeg",
                "description": "音视频处理与视频合成（ffmpeg）。info 读媒体信息；thumbnail 截帧；transcode 转码；extract_audio 提取音频；compose 把图片/视频序列合成为成片（支持 Ken Burns 运镜、交叉转场、旁白、BGM 混音、字幕烧录）；subtitles/mux 给已有视频加字幕与音轨；run 直接传入任意 ffmpeg 参数（法无禁止皆可为，仅受用户命令黑名单约束）。输入超 2GB 或耗时超 300 秒会拒绝",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "info / thumbnail / transcode / extract_audio / compose / subtitles / mux / run"},
                        "input": {"type": "string", "description": "源文件绝对路径（info/thumbnail/transcode/extract_audio/subtitles/mux 用；compose 未传 items 时也可作单一素材）"},
                        "output": {"type": "string", "description": "输出路径（thumbnail/transcode/extract_audio/compose/subtitles/mux 必填）"},
                        "time": {"type": "string", "description": "可选：thumbnail 截图时间点，如 00:01:30（默认开头 1 秒）"},
                        "width": {"type": "integer", "description": "可选：transcode 输出宽度（16-7680，保持宽高比）"},
                        "format": {"type": "string", "description": "可选：转码/提取输出格式：mp4/mp3/webm/mkv/avi/mov/ogg/flac/wav"},
                        "args": {"type": "array", "items": {"type": "string"}, "description": "action=run 时的完整 ffmpeg 参数数组（不含 ffmpeg 本身），如 [\"-i\",\"in.mp4\",\"-vf\",\"hflip\",\"-y\",\"out.mp4\"]；请自带 -y 避免交互挂起"},
                        "items": {"type": "array", "items": {"type": "string"}, "description": "compose 的素材顺序列表（图片/视频绝对路径，按顺序拼接）"},
                        "audio": {"type": "string", "description": "可选：旁白/人声音轨文件路径（compose/subtitles/mux；全音量）"},
                        "bgm": {"type": "string", "description": "可选：背景音乐文件路径（compose/subtitles/mux；以 0.25 音量与旁白混音）"},
                        "subtitle": {"type": "string", "description": "可选：字幕文件（.srt/.ass）绝对路径，烧录进画面（compose/subtitles/mux；需 ffmpeg 含 libass）"},
                        "duration": {"type": "number", "description": "可选：compose 每个图片素材的默认时长秒数（默认 3）"},
                        "transition": {"type": "number", "description": "可选：compose 相邻镜头的交叉淡化秒数（0=硬切，默认 0）"},
                        "effect": {"type": "string", "description": "可选：compose 图片运镜 none/kenburns/kenburns-in/kenburns-out（默认 kenburns）"},
                        "resolution": {"type": "string", "description": "可选：compose 输出分辨率「宽x高」（默认 1920x1080）"},
                        "fps": {"type": "integer", "description": "可选：compose 输出帧率（1-120，默认 30）"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='音视频处理与视频合成（含参数直通）',
    preactivate=(('ffmpeg', '视频处理', '转码', '提取音频', '视频截图', '剪辑'), ('合成视频', '图片转视频', '加字幕', '配乐', '混音', '运镜', '转场')),
)
def media_ffmpeg(action="info", input="", output="", time="", width=0, format="",
                 args=None, items=None, audio="", bgm="", subtitle="",
                 duration=3.0, transition=0.0, effect="kenburns",
                 resolution="1920x1080", fps=30):
    """音视频处理与视频合成（ffmpeg）。

    action=run 直通任意 ffmpeg 参数（argv 直传、无 shell，仅受用户命令黑名单约束）；
    action=compose 把图片/视频序列合成为成片（运镜/转场/旁白/BGM/字幕）；
    subtitles/mux 给已有视频加字幕与音轨；其余保持原有 info/thumbnail/transcode/extract_audio。
    """
    act = str(action or "info").strip().lower()
    if act not in ("info", "thumbnail", "transcode", "extract_audio",
                   "compose", "subtitles", "mux", "run"):
        return ("错误：action 仅支持 info / thumbnail / transcode / extract_audio / "
                "compose / subtitles / mux / run")

    # ---- run：任意 ffmpeg 参数直通（argv 直传，无 shell；仅过用户命令黑名单）----
    if act == "run":
        raw = args
        if isinstance(raw, str):
            import shlex
            try:
                # Windows 路径含反斜杠：posix 模式会吞掉，改用 posix=False
                raw = shlex.split(raw, posix=(os.name != "nt"))
            except ValueError as e:
                return f"错误：args 解析失败: {e}"
        if not isinstance(raw, (list, tuple)) or not raw:
            return ('错误：action=run 需要 args 数组，如 '
                    '["-i","in.mp4","-vf","hflip","-y","out.mp4"]')
        argv = [str(x) for x in raw]
        ok, reason, _ = permissions.check_shell(" ".join(argv))
        if not ok:
            return reason
        code, text = _ffmpeg_run(argv)
        if code is None:
            return f"错误：{text}"
        tail = (text or "").strip()
        head = "ffmpeg 执行完成（退出码 0）" if code == 0 else f"ffmpeg 退出码 {code}"
        return head + (f"\n{tail[-1500:]}" if tail else "")

    src = ""
    if str(input or "").strip():
        ok, reason = permissions.check_filesystem(input, write=False)
        if not ok:
            return reason
        src = permissions.resolve(input)
        if not src or not os.path.isfile(src):
            return f"错误：源文件不存在：{input}"
        try:
            if over_limit(os.path.getsize(src), MEDIA_MAX_INPUT):
                return f"错误：输入文件超过 {MEDIA_MAX_INPUT // 1024 // 1024}MB 上限"
        except OSError:
            pass

    # ---- compose：图片/视频序列 → 成片 ----
    if act == "compose":
        if not str(output or "").strip():
            return "错误：compose 需要 output（输出路径）"
        out = permissions.resolve(output)
        if not out:
            return "错误：输出路径无效"
        if not out.lower().endswith((".mp4", ".mkv", ".mov", ".webm", ".avi")):
            out += ".mp4"
        ok, reason = permissions.check_filesystem(out, write=True)
        if not ok:
            return reason
        mats = list(items) if isinstance(items, (list, tuple)) and items else ([src] if src else [])
        res, note, err = _mv_compose(
            mats, out, durations=None, duration=duration, effect=effect,
            transition=transition, resolution=resolution, fps=fps,
            audio=audio, bgm=bgm, subtitle=subtitle)
        if err:
            return f"错误：{err}"
        size = os.path.getsize(res) if os.path.exists(res) else 0
        permissions.audit("media_ffmpeg_compose", res, note)
        return f"已合成视频保存至 {res}（{size / 1024 / 1024:.1f} MB；{note}）"

    # ---- subtitles / mux：给已有视频加字幕/音轨 ----
    if act in ("subtitles", "mux"):
        if not src:
            return f"错误：{act} 需要 input（源视频）"
        if not str(output or "").strip():
            return f"错误：{act} 需要 output（输出路径）"
        if not (str(audio or "").strip() or str(bgm or "").strip() or str(subtitle or "").strip()):
            return f"错误：{act} 至少需要 audio / bgm / subtitle 之一"
        out = permissions.resolve(output)
        if not out:
            return "错误：输出路径无效"
        if not out.lower().endswith((".mp4", ".mkv", ".mov", ".webm", ".avi")):
            out += ".mp4"
        res, note, err = _mv_finish(src, out, audio=audio, bgm=bgm, subtitle=subtitle)
        if err:
            return f"错误：{err}"
        size = os.path.getsize(res) if os.path.exists(res) else 0
        permissions.audit("media_ffmpeg_" + act, res, note)
        return f"已保存至 {res}（{size / 1024 / 1024:.1f} MB；{note}）"

    if not src:
        return f"错误：{act} 需要 input"
    if act == "info":
        code, text = _ffmpeg_run(["-hide_banner", "-i", src], timeout=20)
        if code is None:
            return f"错误：{text}"
        lines = []
        for ln in (text or "").splitlines():
            s = ln.strip()
            if s.startswith("Duration:"):
                dur = s.split("Duration:", 1)[1].split(",", 1)[0].strip()
                lines.append("时长: " + dur)
            elif s.startswith("Stream #"):
                lines.append("流: " + s.split("Stream #", 1)[1].strip())
        if not lines:
            return f"错误：无法解析媒体信息（ffmpeg 输出：{(text or '')[:200]}）"
        return "\n".join([f"文件名: {os.path.basename(src)}"] + lines)
    # thumbnail / transcode / extract_audio 需要 output
    if not str(output or "").strip():
        return f"错误：{act} 需要 output（输出路径）"
    out = permissions.resolve(output)
    if not out:
        return "错误：输出路径无效"
    if act == "thumbnail":
        if not out.lower().endswith((".png", ".jpg", ".jpeg")):
            out += ".png"
        ok, reason = permissions.check_filesystem(out, write=True)
        if not ok:
            return reason
        ts = str(time or "").strip()
        if ts and not re.match(r"^\d{1,2}:\d{2}:\d{2}(\.\d+)?$|^\d+(\.\d+)?$", ts):
            return "错误：time 格式应为 HH:MM:SS 或秒数（如 00:01:30）"
        args = ["-hide_banner", "-y", "-ss", ts or "1", "-i", src, "-frames:v", "1", "-q:v", "2", out]
        code, text = _ffmpeg_run(args, timeout=60)
        if code is None:
            return f"错误：{text}"
        if code != 0:
            return f"错误：截图失败：{(text or '')[-300:]}"
        size = os.path.getsize(out) if os.path.exists(out) else 0
        permissions.audit("media_ffmpeg_thumbnail", out, f"{size} 字节")
        return f"已截图保存至 {out}（{size / 1024:.0f} KB，时间点 {ts or '1s'}）"
    fmt = str(format or "").strip().lower().lstrip(".")
    if not fmt and out:
        fmt = os.path.splitext(out)[1].lower().lstrip(".")  # 未指定 format 时按输出扩展名推断
    if fmt not in MEDIA_FORMATS:
        return (
            f"错误：format 非法：{format or '（空）'}（支持 {'/'.join(sorted(MEDIA_FORMATS))}；"
            "未指定时会按输出扩展名推断）"
        )
    if not out.lower().endswith(("." + fmt, ".jpg")):
        out += "." + fmt
    ok, reason = permissions.check_filesystem(out, write=True)
    if not ok:
        return reason
    if act == "transcode":
        w = 0
        if width:
            try:
                w = clamp_int(width, 16, lo=16, hi=7680)
            except (TypeError, ValueError):
                return "错误：width 应为 16-7680 的整数"
        args = ["-hide_banner", "-y", "-i", src]
        if w:
            args += ["-vf", f"scale={w}:-2"]
        if fmt in ("mp3", "ogg", "flac", "wav"):
            args += ["-vn"]
        elif fmt == "webm":
            # webm 容器只收 vp8/vp9/av1 与 opus/vorbis：给 h264/aac 源直接 remux 会失败
            args += ["-c:v", "libvpx-vp9", "-b:v", "1M", "-c:a", "libopus"]
        elif fmt in ("mp4", "mkv", "avi", "mov"):
            # 视频容器：优先 GPU 编码（AMF/NVENC/QSV），无 GPU 自动回退 libx264
            args += _ffmpeg_video_encode_args("h264")
        elif w:
            args += ["-c:v", "libx264", "-preset", "veryfast"]
        args += [out]
    else:  # extract_audio
        # 按容器选兼容音频编码器（旧实现 ogg/mp4 等一律 libmp3lame → 容器不兼容、写不出文件）
        _acodec = {
            "mp3": "libmp3lame", "flac": "flac", "wav": "pcm_s16le",
            "ogg": "libvorbis", "webm": "libopus", "mp4": "aac",
            "mov": "aac", "mkv": "aac",
        }
        acodec = _acodec.get(fmt, "libmp3lame")
        args = ["-hide_banner", "-y", "-i", src, "-vn", "-acodec", acodec, out]
    code, text = _ffmpeg_run(args)
    if code is None:
        return f"错误：{text}"
    if code != 0:
        return f"错误：处理失败：{(text or '')[-300:]}"
    size = os.path.getsize(out) if os.path.exists(out) else 0
    permissions.audit("media_ffmpeg", out, f"{act} {size} 字节")
    return f"已{'转码' if act == 'transcode' else '提取音频'}保存至 {out}（{size / 1024 / 1024:.1f} MB）"


@tool(
        {
            "type": "function",
            "function": {
                "name": "team_run",
                "description": "多智能体团队协作编排：协调者把总目标拆解为多步计划，各专业角色（研究员/工程师/评审/设计师/分析师或自定义）按流水线接力执行（共享黑板传递中间成果），最后综合成完整交付物",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "总目标（一句话说清要交付什么）"},
                        "roles": {"type": "array", "items": {"type": "string"}, "description": "可选：团队成员角色名列表（默认 [研究员,工程师,评审]；自定义角色名会按名字推断专长，最多 5 个）"},
                        "steps": {"type": "integer", "description": "可选：限制最大步数（0-8）。不填则由协调者自行拆解（默认约 6 步）"},
                    },
                    "required": ["goal"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='多智能体团队协作编排',
    preactivate=(('多智能体', '团队协作', '分工协作', '角色分工'),),
)
def team_run(goal, roles=("研究员", "工程师", "评审"), steps=0):
    """多智能体协作编排：协调者拆解任务 → 各专业角色按流水线接力 → 综合产出报告。

    roles 传角色名列表（研究员/工程师/评审/设计师/分析师，可自定义任意角色名并附专长描述，
    如 ["前端工程师(React)", "测试工程师"]）；每个角色的产出对后续角色可见（共享黑板）。
    """
    g = str(goal or "").strip()
    if not g:
        return "错误：goal 必填"
    client = get_active_client()
    if client is None:
        return "错误：没有可用客户端（请先在设置中配置 API Key）"

    role_list = []
    for r in (roles or ()):  # 支持传字符串数组
        rs = str(r or "").strip()
        if rs and rs not in role_list:
            role_list.append(rs)
    if not role_list:
        role_list = ["研究员", "工程师", "评审"]
    role_list = role_list[:5]
    try:
        steps_n = clamp_int(steps or 0, 0, lo=0, hi=8)
    except (TypeError, ValueError):
        steps_n = 0

    def _chat(system, user, tokens=2048):
        for attempt in range(2):
            try:
                resp = client.client.chat.completions.create(
                    model=client.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=tokens,
                    stream=False,
                    timeout=180.0,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception:
                if attempt == 0:
                    time.sleep(1)
        return ""

    role_brief = "\n".join(f"- {r}：{_TEAM_ROLE_PRESETS.get(r, '该领域专业智能体')}" for r in role_list)
    plan_raw = _chat(
        "你是多智能体团队的协调者。把总目标拆解为按序执行的子任务并指派给合适的角色，"
        "只输出一个 JSON 对象：{\"tasks\": [{\"role\": \"角色名\", \"task\": \"该步要做的事\"}]}，最多 6 步，"
        "角色必须从给定名单中选择。最后一步应是综合/评审类收尾。",
        f"【团队名单】\n{role_brief}\n\n【总目标】\n{g}",
    )
    tasks = []
    parsed = None
    m = re.search(r"\{.*\}", plan_raw, re.S)
    if m:
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("tasks"), list):
        for it in parsed["tasks"]:
            if isinstance(it, dict) and str(it.get("task") or "").strip():
                r = str(it.get("role") or "").strip() or role_list[0]
                tasks.append((r, str(it["task"]).strip()))
    if not tasks:  # 计划失败时退化为默认流水线，保证功能可用
        tasks = [(role_list[i % len(role_list)], f"{g}（默认流水线步骤 {i + 1}）") for i in range(min(3, len(role_list)))]
    if steps_n:
        tasks = tasks[:steps_n]

    board = []  # 共享黑板：(role, task, output)
    lines = [f"🎯 总目标：{g}", "", "📋 执行计划："]
    for i, (r, t) in enumerate(tasks, 1):
        lines.append(f"  {i}. [{r}] {t}")
    lines.append("")
    for i, (role, task) in enumerate(tasks, 1):
        preset = _TEAM_ROLE_PRESETS.get(role, "")
        prior = ""
        if board:
            digest = "\n".join(
                f"[{rr}] {tt} → {oo[:1200]}" for rr, tt, oo in board[-3:]
            )
            prior = f"\n\n【前序成果黑板】\n{digest}"
        sys_p = f"你是团队中的「{role}」。{preset} 只以本角色身份完成分配的任务，输出干货结论（不超过 600 字）。"
        out = _chat(sys_p, f"【总目标】{g}\n【你的任务】{task}{prior}")
        if not out:
            out = "（本步骤执行失败，继续后续步骤）"
        board.append((role, task, out))
        lines.append(f"── 第 {i} 步 · [{role}] ──\n{out}\n")
    final = _chat(
        "你是多智能体团队的最终综合者。汇总各角色成果，产出面向用户的完整交付物："
        "关键结论在前，方案/代码/清单居中，风险与后续行动殿后。使用 Markdown。",
        f"【总目标】{g}\n\n" + "\n\n".join(f"[{r}·{t}]\n{o}" for r, t, o in board),
        tokens=3000,
    )
    lines.append("═══ 🏁 最终综合 ═══")
    lines.append(final or "（综合阶段失败，请参考上方各角色产出）")
    permissions.audit("team_run", str(g)[:80], f"{len(tasks)} 步")
    # 附加结构化 JSON 段，供前端渲染"多智能体流水线"步骤面板（对文本可读性无影响）
    try:
        struct = {
            "team_steps": [
                {"role": r, "task": t, "output": o[:800]}
                for r, t, o in board
            ],
            "final": (final or "")[:2000],
        }
        lines.append("\n\n__TEAM_JSON__" + json.dumps(struct, ensure_ascii=False))
    except Exception:
        pass
    return "\n".join(lines)


__all__ = ['rpa_screen_size', 'rpa_click', 'rpa_type', 'rpa_hotkey', 'rpa_move', 'rpa_scroll', 'rpa_screenshot', 'screen_find_click', 'vision_loop', 'tts_save', 'speech_to_text', 'tts_speak', 'tts_stop', 'voice_chat_loop', 'image_generate', 'qrcode', 'media_ffmpeg', 'team_run']
