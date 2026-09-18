"""tool_mv —— 微电影/MV 一键成片编排（工具域模块）。

把「分镜脚本 → 逐镜出图 → 逐镜配音 → 合成带运镜/转场/字幕/BGM 的成片」
串成一次工具调用；底层复用 tool_desktop 的 image_generate / tts_save /
_mv_compose（ffmpeg）与 shared 的钳制工具。

加载顺序：本模块在 agent_tools/__init__ 中于 tool_desktop 之后导入，
故可安全 `from agent_tools.tool_desktop import ...`。
"""

import json
import os
import re
import shutil
from datetime import datetime

import permissions
from agent_tools.tool_desktop import (
    _ff_media_duration,
    _ffmpeg_run,
    _mv_compose,
    _mv_concat_list,
    _mv_norm_audio,
    image_generate,
    tts_save,
)
from shared import clamp_float, clamp_int
from toolkit import tool


def _mv_ts(t):
    """秒 → SRT 时间戳 HH:MM:SS,mmm。"""
    total_ms = int(round(max(0.0, float(t)) * 1000))
    h, rem = divmod(total_ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _mv_make_srt(cues, path):
    """cues: [(start_sec, end_sec, text), ...] → 写 SRT 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        for i, (a, b, txt) in enumerate(cues, 1):
            f.write(f"{i}\n{_mv_ts(a)} --> {_mv_ts(b)}\n{txt}\n\n")


@tool(
        {
            "type": "function",
            "function": {
                "name": "mv_compose",
                "description": "一键成片：输入分镜脚本（每镜：画面描述 prompt / 已有图 image / 旁白 narration / 字幕 subtitle / 时长 duration），自动逐镜出图（image_generate）、逐镜配音（TTS），再合成带 Ken Burns 运镜、交叉转场、字幕与背景音乐的微电影 / MV / 图片故事短片，输出 mp4。也可只传已备好的图片做纯剪辑合成",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "storyboard": {
                            "type": "array",
                            "description": "分镜数组，每镜一个对象，按顺序成片",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "prompt": {"type": "string", "description": "该镜画面描述（未给 image 时用于出图）"},
                                    "image": {"type": "string", "description": "可选：该镜已有图片/视频绝对路径（优先于 prompt 出图）"},
                                    "narration": {"type": "string", "description": "可选：该镜旁白文本（自动合成语音作为音轨）"},
                                    "subtitle": {"type": "string", "description": "可选：该镜字幕文本（默认取 narration）"},
                                    "duration": {"type": "number", "description": "可选：该镜最短时长秒数（有旁白时按语音长度自动顺延）"},
                                    "effect": {"type": "string", "description": "可选：该镜运镜 none/kenburns/kenburns-in/kenburns-out"},
                                },
                            },
                        },
                        "output": {"type": "string", "description": "可选：成片输出 mp4 绝对路径（默认工作区 video/mv_时间戳.mp4）"},
                        "resolution": {"type": "string", "description": "可选：分辨率「宽x高」，默认 1920x1080"},
                        "fps": {"type": "integer", "description": "可选：帧率 1-120，默认 30"},
                        "duration": {"type": "number", "description": "可选：无旁白镜头的默认时长秒数，默认 3"},
                        "effect": {"type": "string", "description": "可选：全局默认运镜 none/kenburns/kenburns-in/kenburns-out，默认 kenburns"},
                        "transition": {"type": "number", "description": "可选：镜头间交叉淡化秒数（0=硬切，默认 0.5）"},
                        "generate_images": {"type": "boolean", "description": "可选：未给 image 的镜头是否自动出图，默认 true"},
                        "image_size": {"type": "string", "description": "可选：出图尺寸「宽x高」，默认跟随 resolution"},
                        "narrate": {"type": "boolean", "description": "可选：是否自动合成旁白语音，默认 true"},
                        "voice": {"type": "string", "description": "可选：TTS 音色名子串（如 Huihui / Xiaoxiao，留空=系统默认）"},
                        "rate": {"type": "integer", "description": "可选：TTS 语速 -10~10，默认 0"},
                        "bgm": {"type": "string", "description": "可选：背景音乐文件绝对路径（0.25 音量与旁白混音）"},
                        "subtitle": {"type": "boolean", "description": "可选：是否把旁白烧录成字幕，默认 true（需 ffmpeg 含 libass）"},
                        "reference": {"type": "string", "description": "可选：全局参考图路径或 URL（角色/画风一致性），所有自动出图的镜头都以它为参考图"},
                    },
                    "required": ["storyboard"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='一键成片（分镜→出图→配音→合成微电影/MV）',
    preactivate=(('微电影', 'mv', '一键成片', '分镜', '故事板', '短片', '配音成片'),),
)
def mv_compose(storyboard, output="", resolution="1920x1080", fps=30, duration=3,
               effect="kenburns", transition=0.5, generate_images=True,
               image_size="", narrate=True, voice="", rate=0, bgm="", subtitle=True,
               reference=""):
    """分镜脚本 → 微电影/MV 成片（出图 + 配音 + 运镜/转场/字幕/BGM 合成）。

    storyboard 每镜：{prompt, image, narration, subtitle, duration, effect}。
    出图与配音失败不中断成片（记入日志并降级），素材缺失才报错。
    """
    shots = storyboard
    if isinstance(shots, dict):
        shots = shots.get("shots") or shots.get("storyboard") or []
    if isinstance(shots, str):
        try:
            shots = json.loads(shots)
        except Exception:
            return "错误：storyboard 需为数组（或 JSON 数组字符串）"
        if isinstance(shots, dict):
            shots = shots.get("shots") or shots.get("storyboard") or []
    if not isinstance(shots, list) or not shots:
        return "错误：storyboard 至少需要一个镜头"
    if len(shots) > 80:
        shots = shots[:80]

    m = re.match(r"^(\d{2,4})\s*[x×*]\s*(\d{2,4})$", str(resolution or "").strip().lower())
    if not m:
        return "错误：resolution 应为「宽x高」（如 1920x1080）"
    W, H = int(m.group(1)), int(m.group(2))
    if not (64 <= W <= 4096 and 64 <= H <= 4096):
        return f"错误：resolution 每边须在 64-4096（图片生成上限）：{resolution}"
    fps = clamp_int(fps, 30, lo=1, hi=120)
    base_dur = clamp_float(duration, 3.0, lo=0.5, hi=600.0)
    trans = clamp_float(transition, 0.5, lo=0.0, hi=5.0)
    try:
        rate = clamp_int(rate, 0, lo=-10, hi=10)
    except Exception:
        rate = 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = os.path.join(permissions.WORKSPACE_DIR or ".", "video")
    workdir = os.path.join(base_dir, f"mv_{stamp}")
    try:
        os.makedirs(workdir, exist_ok=True)
    except Exception as e:
        return f"错误：工作目录创建失败: {e}"
    if str(output or "").strip():
        out = permissions.resolve(output)
        if not out:
            return "错误：输出路径无效"
        if not out.lower().endswith(".mp4"):
            out += ".mp4"
        ok, reason = permissions.check_filesystem(out, write=True)
        if not ok:
            return reason
        try:
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        except Exception:
            pass
    else:
        out = os.path.join(base_dir, f"mv_{stamp}.mp4")

    mats, seg_durs, audio_parts, cues, log = [], [], [], [], []
    timeline = 0.0
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            return f"错误：第 {i + 1} 个镜头应为对象（含 prompt/image/narration 等字段）"
        img = str(shot.get("image") or "").strip()
        prompt = str(shot.get("prompt") or "").strip()
        narration = str(shot.get("narration") or "").strip()
        sub_raw = shot.get("subtitle")
        sub_txt = narration if sub_raw is None else str(sub_raw).strip()

        # 1) 素材：已有图优先，否则自动出图
        if not img and generate_images and prompt:
            dst = os.path.join(workdir, f"shot_{i:03d}.png")
            r = image_generate(prompt, path=dst, size=image_size or resolution,
                               reference=reference)
            if str(r).startswith("错误"):
                return f"错误：第 {i + 1} 镜出图失败：{r}"
            img = dst
        if not img:
            return (f"错误：第 {i + 1} 镜没有可用素材（未提供 image，且"
                    "generate_images=false 或 prompt 为空）")
        rp = permissions.resolve(img)
        if not rp or not os.path.isfile(rp):
            return f"错误：第 {i + 1} 镜素材不存在：{img}"
        mats.append(rp)

        # 2) 时长：有旁白按语音长度顺延，否则用镜头/全局时长
        sd = clamp_float(shot.get("duration"), base_dur, lo=0.5, hi=600.0)
        audio_file = ""
        if narration and narrate:
            wav = os.path.join(workdir, f"voice_{i:03d}.wav")
            rr = tts_save(narration, wav, rate=rate, voice=voice)
            if os.path.isfile(wav) and os.path.getsize(wav) > 44:
                audio_file = wav
                ad = _ff_media_duration(wav)
                if ad:
                    sd = max(sd, round(ad + 0.35, 3))
            else:
                log.append(f"第 {i + 1} 镜配音跳过：{str(rr)[:80]}")
        seg_durs.append(sd)

        # 3) 归一化音频（旁白 pad 到 sd；无旁白生成等长静音，保证音画严格对齐）
        norm = os.path.join(workdir, f"aud_{i:03d}.wav")
        okk, aerr = _mv_norm_audio(audio_file, sd, norm)
        if okk:
            audio_parts.append(norm)
        else:
            log.append(f"第 {i + 1} 镜音频归一化失败：{aerr}")

        # 4) 字幕轨
        if subtitle and sub_txt:
            cues.append((timeline, timeline + sd, sub_txt))
        timeline += sd

    # 合并旁白（各段等长归一化后 concat -c copy，总长 = 视频总长）
    combined_audio = ""
    if narrate and audio_parts:
        combined_audio = os.path.join(workdir, "narration.wav")
        if len(audio_parts) == 1:
            try:
                shutil.copyfile(audio_parts[0], combined_audio)
            except Exception:
                combined_audio = ""
        else:
            lst = os.path.join(workdir, "audio_concat.txt")
            _mv_concat_list(audio_parts, lst)
            code, text = _ffmpeg_run(["-hide_banner", "-y", "-f", "concat", "-safe", "0",
                                      "-i", lst, "-c", "copy", combined_audio])
            if code != 0:
                log.append(f"旁白拼接失败，已跳过音轨：{(text or '')[-120:]}")
                combined_audio = ""

    srt_path = ""
    if subtitle and cues:
        srt_path = os.path.join(workdir, "subs.srt")
        try:
            _mv_make_srt(cues, srt_path)
        except Exception as e:
            log.append(f"字幕生成失败：{e}")
            srt_path = ""

    res, note, err = _mv_compose(
        mats, out, durations=seg_durs, duration=base_dur, effect=effect,
        transition=trans, resolution=resolution, fps=fps,
        audio=combined_audio, bgm=bgm, subtitle=srt_path, workdir=workdir)
    if err:
        return f"错误：合成失败：{err}"
    size = os.path.getsize(res) if os.path.exists(res) else 0
    permissions.audit("mv_compose", res, f"{len(mats)} 镜 {size} 字节")
    tail = ("\n提示：\n" + "\n".join("· " + x for x in log)) if log else ""
    return (
        f"成片已生成：{res}\n"
        f"镜头 {len(mats)} 个，素材总长约 {timeline:.1f}s，分辨率 {resolution}@{fps}fps\n"
        f"旁白：{'有' if combined_audio else '无'}　字幕：{'有' if srt_path else '无'}　"
        f"BGM：{'有' if str(bgm or '').strip() else '无'}\n"
        f"合成：{note}{tail}"
    )


__all__ = ['mv_compose']
