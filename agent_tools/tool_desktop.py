# -*- coding: utf-8 -*-
"""tool_desktop —— P0-1 批量拆分（工具域模块）：🖱 桌面与视觉语音.

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
引用 deepseek_client 的常量与辅助依赖加载顺序契约——主文件在共享基建全部定义后
才执行 `from agent_tools import *`，此处 from-import 可安全解析。
"""

import json
import os
import re
import threading
import time
from datetime import datetime

import permissions

from security import _safe_url
from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import deepseek_client as _dc  # 可变注入配置动态访问（dc.X 注入后立即生效）
from deepseek_client import (

    DEFAULT_BASE_URL,
    MEDIA_FORMATS,
    MEDIA_MAX_INPUT,
    RPA_FAILSAFE,
    _ACTIVE_SPEAK,
    _ACTIVE_SPEAK_LOCK,
    _BYE_PAT,
    _TEAM_ROLE_PRESETS,
    _VISION_LOOP_ACTIONS,
    _WHISPER_CACHE,
    _WHISPER_CACHE_LOCK,
    _WHISPER_LOOP_LOCK,
    _capture_screen_png,
    _extract_json_obj,
    _ffmpeg_run,
    _http_client,
    _mic_record_once,
    _parse_scroll,
    _rpa_ready,
    _safe_stream,
    _speak_aloud,
    get_active_client,
)
from agent_tools.tool_media import image_understand  # 已迁工具跨模块复用



@tool(
        {
            "type": "function",
            "function": {
                "name": "rpa_screen_size",
                "description": "获取当前屏幕分辨率（桌面 RPA 坐标用，需 pyautogui）",
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


@tool(
        {
            "type": "function",
            "function": {
                "name": "rpa_click",
                "description": "桌面 RPA：模拟鼠标点击屏幕坐标 (x,y)，button=left/right/middle",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "屏幕 X 坐标"},
                        "y": {"type": "integer", "description": "屏幕 Y 坐标"},
                        "button": {"type": "string", "description": "可选：left/right/middle，默认 left"},
                        "clicks": {"type": "integer", "description": "可选：连击次数 1-5，默认 1"},
                    },
                    "required": ["x", "y"],
                },
            },
        },
    groups=['🖱 桌面自动化'],
    phrases='模拟点击（屏幕坐标）',
    preactivate=(('点击屏幕', '移动鼠标', '键盘输入', '模拟按键', '屏幕坐标', '模拟滚轮', '桌面自动化'),),
)
def rpa_click(x, y, button="left", clicks=1):
    """模拟鼠标点击。"""
    ok, hint = _rpa_ready()
    if not ok:
        return hint
    try:
        import pyautogui
        x = int(x)
        y = int(y)
        button = str(button or "left").strip().lower()
        clicks = max(1, min(5, int(clicks or 1)))
        if button not in ("left", "right", "middle"):
            return "错误：button 仅支持 left/right/middle"
        pyautogui.FAILSAFE = RPA_FAILSAFE
        pyautogui.click(x, y, button=button, clicks=clicks)
        permissions.audit("rpa_click", f"{x},{y}", f"{button} x{clicks}")
        return f"已点击 ({x}, {y})，{button} 键 x{clicks}"
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
        pyautogui.typewrite(str(text), interval=interval)
        permissions.audit("rpa_type", "键盘输入", str(text)[:60])
        return f"已输入 {len(str(text))} 个字符"
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
        max_iters = max(1, min(12, int(max_iters or 5)))
    except (TypeError, ValueError):
        max_iters = 5
    steps_hint = str(steps or "").strip()
    step_delay = 0.6

    log = []
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

    return "视觉闭环完成：\n" + "\n".join(log[-12:])


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
                    },
                    "required": ["text", "path"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='文字转语音（存文件）',
    preactivate=(('朗读', '语音播报', '文字转语音', '读给我听', '停止朗读', 'tts'),),
)
def tts_save(text, path, rate=0):
    """语音合成保存为 WAV 文件（Windows SAPI，可选 pywin32；无则用 PowerShell）。"""
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
                "description": "本地语音转文字（faster-whisper 离线识别，未安装时提示安装；首次运行自动下载所选模型）",
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
    """本地语音转文字（faster-whisper，未安装时提示先安装）。
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
                        "text": {"type": "string", "description": "要朗读的文本（≤8000 字）"},
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
    if len(t) > 8000:
        t = t[:8000]
    sid = _speak_aloud(t, rate=rate, volume=volume, voice=voice, label="工具朗读")
    if not sid:
        return "错误：本机没有可用的 TTS 引擎（需要 pywin32 的 SAPI），或系统无中文语音包"
    out = f"✅ 已开始后台朗读（会话 {sid}）。可用 tts_stop 停止；语速 {rate}，音量 {volume}。"
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
    for k, entry in targets:
        try:
            entry["event"].set()
            sp = entry.get("voice")
            if sp is not None:
                import pythoncom

                def _purge(sp=sp):
                    pythoncom.CoInitialize()
                    try:
                        sp.Speak("", 1 | 2)  # async + purge：立即中断并清空朗读队列
                    finally:
                        pythoncom.CoUninitialize()

                threading.Thread(target=_purge, daemon=True).start()
            stopped += 1
        except Exception:
            pass
    return stopped


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
    rounds_n = 3
    try:
        rounds_n = max(1, min(20, int(rounds or 3)))
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


@tool(
        {
            "type": "function",
            "function": {
                "name": "image_generate",
                "description": "生成图片（需在 config.json 配置 image_api_key/image_base_url/image_model，OpenAI 兼容 images API）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "图片描述提示词"},
                        "path": {"type": "string", "description": "可选：输出路径（默认工作区 images/）"},
                        "size": {"type": "string", "description": "可选：尺寸如 1024x1024"},
                    },
                    "required": ["prompt"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='文生图',
    preactivate=(('文生图', 'ai绘图', '生成一张图', '画一张'),),
)
def image_generate(prompt, path="", size="1024x1024"):
    """生成图片（需配置 image_api_key / image_base_url / image_model，OpenAI 兼容接口）。"""
    p = str(prompt or "").strip()
    if not p:
        return "错误：prompt 必填"
    # size 白名单校验：非法尺寸让模型自纠（接口对任意字符串返回 400，报错不友好）
    sz = str(size or "1024x1024").strip().lower()
    if not re.match(r"^(256|512|768|1024|1536|2048)x(256|512|768|1024|1536|2048)$", sz):
        return (
            f"错误：size 非法：{size}（支持 256/512/768/1024/1536/2048 的正方形或 "
            "两者组合，如 1024x1024 / 1536x1024）"
        )
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
        resp = _http_client().post(
            f"{base}/images/generations",
            json={
                "model": _dc.IMAGE_GEN_MODEL,
                "prompt": p,
                "n": 1,
                "size": sz,
                "response_format": "b64_json",
            },
            headers={"Authorization": f"Bearer {key}"},
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") or []
        if not items:
            return "错误：接口未返回图片"
        import base64

        if items[0].get("b64_json"):
            with open(out, "wb") as f:
                f.write(base64.b64decode(items[0]["b64_json"]))
        elif items[0].get("url"):
            # URL 图片大小不可信：20MB 上限，防写满磁盘；返回地址也做 SSRF 校验
            dl_url = str(items[0]["url"])
            err = _safe_url(dl_url, allow_loopback=False)
            if err:
                return f"错误：图片接口返回了不安全的下载地址（{err}）"
            try:
                with _safe_stream("GET", dl_url, allow_loopback=False, timeout=60) as r:
                    total = 0
                    truncated = False
                    with open(out, "wb") as f:
                        for chunk in r.iter_bytes(64 * 1024):
                            total += len(chunk)
                            if total > 20 * 1024 * 1024:
                                truncated = True
                                break
                            f.write(chunk)
            except Exception:
                try:
                    os.remove(out)
                except OSError:
                    pass
                raise
            if truncated:
                try:
                    os.remove(out)
                except OSError:
                    pass
                return "错误：图片下载超过 20MB 上限，已放弃保存"
        else:
            return "错误：接口返回格式无法解析"
        size_b = os.path.getsize(out)
        permissions.audit("image_generate", out, p[:80])
        return f"已生成图片保存至 {out}（{size_b / 1024:.0f} KB）"
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
            from qrcode.constants import ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q, ERROR_CORRECT_H
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
            s = max(64, min(1024, int(size or 300)))
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
        from PIL import Image
        import pyzbar.pyzbar as pyzbar
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


@tool(
        {
            "type": "function",
            "function": {
                "name": "media_ffmpeg",
                "description": "音视频处理：info 读取时长/分辨率/码率/音频信息；thumbnail 指定时间点截图；transcode 转码（mp4/mp3 等）；extract_audio 提取音频。输入超 2GB 或耗时超 300 秒会拒绝",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "info / thumbnail / transcode / extract_audio"},
                        "input": {"type": "string", "description": "源文件绝对路径"},
                        "output": {"type": "string", "description": "thumbnail/transcode/extract_audio 必填：输出路径"},
                        "time": {"type": "string", "description": "可选：截图时间点，如 00:01:30（默认取开头 1 秒）"},
                        "width": {"type": "integer", "description": "可选：转码输出宽度（16-7680，保持宽高比）"},
                        "format": {"type": "string", "description": "可选：转码/提取输出格式：mp4/mp3/webm/mkv/avi/mov/ogg/flac/wav"},
                    },
                    "required": ["action", "input"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='音视频处理（ffmpeg）',
    preactivate=(('ffmpeg', '视频处理', '转码', '提取音频', '视频截图', '剪辑'),),
)
def media_ffmpeg(action="info", input="", output="", time="", width=0, format=""):
    """音视频：info / thumbnail / transcode / extract_audio（参数白名单化）。"""
    act = str(action or "info").strip().lower()
    if act not in ("info", "thumbnail", "transcode", "extract_audio"):
        return "错误：action 仅支持 info / thumbnail / transcode / extract_audio"
    if not str(input or "").strip():
        return "错误：input 必填"
    ok, reason = permissions.check_filesystem(input, write=False)
    if not ok:
        return reason
    src = permissions.resolve(input)
    if not src or not os.path.isfile(src):
        return f"错误：源文件不存在：{input}"
    try:
        if os.path.getsize(src) > MEDIA_MAX_INPUT:
            return "错误：输入文件超过 2GB 上限"
    except OSError:
        pass
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
    if fmt not in MEDIA_FORMATS:
        return (
            f"错误：format 非法：{format or '（空）'}（支持 {'/'.join(sorted(MEDIA_FORMATS))}；"
            "如未指定可按输出扩展名推断）"
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
                w = max(16, min(7680, int(width)))
            except (TypeError, ValueError):
                return "错误：width 应为 16-7680 的整数"
        args = ["-hide_banner", "-y", "-i", src]
        if w:
            args += ["-vf", f"scale={w}:-2", "-c:v", "libx264", "-preset", "veryfast"]
        if fmt in ("mp3", "ogg", "flac", "wav"):
            args += ["-vn"]
        args += [out]
    else:  # extract_audio
        # 强制转码（copy 与目标容器可能不兼容；testsrc 无音频流时 mp3 也能正常产出空流）
        acodec = "libmp3lame" if fmt == "mp3" else ("flac" if fmt == "flac" else "pcm_s16le" if fmt == "wav" else "libmp3lame")
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
                        "steps": {"type": "integer", "description": "可选：限制最大步数（默认协调者自行拆解，最多 6 步）"},
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
        steps_n = max(0, min(8, int(steps or 0)))
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
