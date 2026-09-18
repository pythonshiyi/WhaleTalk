"""tool_mv —— 微电影/MV 一键成片编排（工具域模块）。

把「分镜脚本 → 逐镜出图 → 逐镜配音 → 合成带运镜/转场/字幕/BGM 的成片」
串成一次工具调用；底层复用 tool_desktop 的 image_generate / tts_save /
_mv_compose（ffmpeg）与 shared 的钳制工具。

加载顺序：本模块在 agent_tools/__init__ 中于 tool_desktop 之后导入，
故可安全 `from agent_tools.tool_desktop import ...`。
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


# ═══════════════════════════════════════════════════════════════════════════
# AI MV 上游引擎接入（薄封装，方案 ②）
#
# 「听觉解析 + 卡点 + 对词」交给同机的 AI MV 生产线上游程序（含 librosa 等重依赖），
# 本工具**只通过 subprocess 调它的 mv_api/CLI**，绝不 import 其重依赖、不改动它。
# 定位：mv_produce 负责「听 + 卡点分镜 + 可选成片」，mv_compose 负责「合成」。
#
# 关键修复（治「错且自认对」）：所有动作返回**对齐证据 + 自检门禁**，未过门禁不得
# 宣称完成；时间轴一律来自上游引擎，禁止手写 SVG/ffmpeg 估算歌词与镜头位置。
# ═══════════════════════════════════════════════════════════════════════════
_MV_ENV_HOME = "AI_MV_HOME"
_MV_ENV_PY = "AI_MV_PYTHON"
_MV_PY_CACHE = {}  # python 路径 -> 是否可 import librosa/soundfile/scipy


def _mv_find_root(explicit=""):
    """定位 AI MV 项目根（含 mv_api.py + app/cli.py）；找不到返回 ""。"""
    cands = []
    for x in (explicit, os.environ.get(_MV_ENV_HOME)):
        if x:
            cands.append(x)
    try:
        import config_utils
        cfg_home = str(config_utils.load_config().get("mv_home") or "").strip()
        if cfg_home:
            cands.append(cfg_home)
    except Exception:  # noqa: BLE001
        pass
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
    cands += [
        os.path.join(os.path.dirname(base), "MV"),
        os.path.join(base, "MV"),
        r"D:\jingyu\MV",
    ]
    for c in cands:
        try:
            c = os.path.abspath(os.path.expanduser(str(c)))
            if os.path.isfile(os.path.join(c, "mv_api.py")) and \
                    os.path.isfile(os.path.join(c, "app", "cli.py")):
                return c
        except Exception:  # noqa: BLE001
            continue
    return ""


def _mv_can_import(py):
    """该解释器能否 import MV 依赖（结果缓存；失败不抛）。"""
    if not py or not os.path.isfile(py):
        return False
    if py in _MV_PY_CACHE:
        return _MV_PY_CACHE[py]
    ok = False
    try:
        r = subprocess.run([py, "-c", "import librosa,soundfile,scipy"],
                           capture_output=True, timeout=90,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = r.returncode == 0
    except Exception:  # noqa: BLE001
        ok = False
    _MV_PY_CACHE[py] = ok
    return ok


def _mv_python(root):
    """挑一个能跑 MV 的解释器：项目 venv > AI_MV_PYTHON > 当前解释器；都不行返回 ""。"""
    cands = []
    venv = (os.path.join(root, ".venv", "Scripts", "python.exe") if os.name == "nt"
            else os.path.join(root, ".venv", "bin", "python"))
    if os.path.isfile(venv):
        cands.append(venv)
    if os.environ.get(_MV_ENV_PY):
        cands.append(os.environ[_MV_ENV_PY])
    cands.append(sys.executable)
    for p in cands:
        if _mv_can_import(p):
            return p
    return ""


def _mv_run(root, py, subcmd, timeout, offline):
    """执行 `python -m app.cli <subcmd>`，返回 (stdout, err)。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # offline=False 时 MV 导演层需要 DeepSeek Key：复用鲸语已配的 key
    if not offline and not env.get("DEEPSEEK_API_KEY"):
        try:
            import config_utils
            key = str(config_utils.load_config().get("api_key") or "").strip()
            if key:
                env["DEEPSEEK_API_KEY"] = key
        except Exception:  # noqa: BLE001
            pass
    argv = [py, "-m", "app.cli"] + list(subcmd)
    try:
        p = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return None, f"超时（>{timeout}s）；大工程可调大 timeout 参数或改用后台 start_process"
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    if p.returncode != 0:
        return None, (p.stderr or p.stdout or "")[-1500:]
    return p.stdout or "", ""


def _mv_resolve_audio(audio):
    a = str(audio or "").strip()
    if not a:
        return ""
    p = permissions.resolve(a)
    if p and os.path.isfile(p):
        return p
    return os.path.abspath(a) if os.path.isfile(a) else ""


def _mv_lyrics_arg(lyrics):
    """lyrics 可为 .lrc/.txt 路径，或原始歌词文本。返回 (参数列表, 需清理的临时文件)。"""
    text = str(lyrics or "").strip()
    if not text:
        return [], ""
    p = permissions.resolve(text)
    if p and os.path.isfile(p) and p.lower().endswith((".lrc", ".txt", ".json")):
        return ["--lyrics", p], ""
    fd, tmp = tempfile.mkstemp(prefix="wt_mvlyrics_", suffix=".txt")
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    return ["--lyrics", tmp], tmp


def _mv_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _mv_verify_grid(shots, duration):
    """镜头时间网格自检：覆盖全曲 [0,duration]、单调、无空隙。返回 [(label, ok, detail)]。"""
    checks = []
    if not shots:
        return [("分镜非空", False, "shots 为空")]
    try:
        starts = [float(s.get("t_start", 0.0)) for s in shots]
        ends = [float(s.get("t_end", 0.0)) for s in shots]
    except Exception:  # noqa: BLE001
        return [("分镜字段可解析", False, "t_start/t_end 非法")]
    # 卡点分镜常从第一个拍点起（留极短引子），故允许 ≤2s；重点是后续无空隙、覆盖到片尾
    checks.append(("首镜起点接近开头", starts[0] <= 2.0, f"t_start={starts[0]:.3f}s"))
    if duration:
        checks.append(("末镜覆盖到片尾", ends[-1] >= float(duration) - 0.5,
                       f"t_end={ends[-1]:.3f} / 时长={float(duration):.3f}"))
    monotonic = all(ends[i] <= starts[i + 1] + 1e-6 for i in range(len(shots) - 1))
    checks.append(("镜头时间单调递增", monotonic, ""))
    gaps = [starts[i + 1] - ends[i] for i in range(len(shots) - 1)]
    max_gap = max(gaps) if gaps else 0.0
    checks.append(("镜头间无空隙", max_gap <= 0.05, f"最大间隙 {max_gap:.3f}s"))
    return checks


def _mv_verify_render(final, manifest):
    """成片自检：文件存在 + 时长与音频一致 + 网格覆盖 + 字幕在片内。返回 (checks)。"""
    checks = []
    exists = bool(final) and os.path.isfile(final) and os.path.getsize(final) > 0
    size_mb = round(os.path.getsize(final) / 1048576, 2) if exists else 0.0
    checks.append(("终片存在且非空", exists, f"{size_mb} MB" if exists else final or "缺失"))
    dur = float(manifest.get("duration") or 0.0)
    vdur = _ff_media_duration(final) if exists else 0.0
    if vdur and dur:
        checks.append(("视频时长与音频一致", abs(vdur - dur) <= 1.0,
                       f"视频 {vdur:.2f}s / 音频 {dur:.2f}s"))
    checks.extend(_mv_verify_grid(manifest.get("shots") or [], dur))
    caps = manifest.get("captions") or []
    if caps:
        in_range = all(float(c.get("start", 0)) >= -0.5 and float(c.get("end", 0)) <= dur + 0.5
                       for c in caps)
        checks.append(("字幕全部落在片内", in_range, f"{len(caps)} 句 · 0–{dur:.1f}s"))
    return checks


def _mv_report(kind, ev, checks, extra=None):
    """统一输出：证据 + 自检门禁 + 判定。"""
    lines = [f"[AI MV · {kind}]"]
    lines += [f"· {k}：{v}" for k, v in ev.items()]
    lines.append("· 自检门禁：")
    allok = True
    for label, ok, detail in checks:
        lines.append(f"  {'✅' if ok else '❌'} {label}" + (f"（{detail}）" if detail else ""))
        allok = allok and bool(ok)
    lines.append("判定：" + ("PASS —— 可交付。" if allok else
                             "FAIL —— 不得宣称完成；请修复后重跑，勿以容器规格冒充内容正确。"))
    if extra:
        lines += extra
    lines.append("（完成门禁：时间轴一律来自 AI MV 引擎；禁止手写 SVG/ffmpeg 估算歌词与镜头位置。）")
    return "\n".join(lines)


def _mv_lyrics_text(lyrics):
    """取歌词**原文**（路径→读取；否则视为文本）——原生引擎需要文本而非路径。"""
    text = str(lyrics or "").strip()
    if not text:
        return ""
    p = permissions.resolve(text)
    if p and os.path.isfile(p) and p.lower().endswith((".txt", ".lrc", ".json")):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:  # noqa: BLE001
            return ""
    return text


def _mv_palette(style):
    s = str(style or "").lower()
    if "qing" in s or "hua" in s:
        return "qinghua"
    if "night" in s or "citypop" in s:
        return "citypop_night_v1"
    return "qinghua"


def _mv_native_available():
    try:
        import mv_engine  # noqa: F401
        import librosa  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _mv_native(action, audio_abs, lyrics, style, out, output, images_dir,
                offline, resolution, fps, timeout, engine_model):
    """原生引擎路径（不依赖外部 MV 程序）。返回报告文本；不可用返回 None。

    action: plan / storyboard / render。分析 + 声学歌词对轴 + 卡点分镜全部本地完成。
    """
    try:
        import mv_engine as me
    except Exception:  # noqa: BLE001
        return None
    text = _mv_lyrics_text(lyrics)
    analysis = me.analyze(audio_abs)
    dur = float(analysis.get("duration") or 0.0)
    lines = me.align_lyrics(audio_abs, text, model=engine_model) if text else []
    shots = me.build_shots(dur, analysis.get("beats") or [], lines)
    if not shots:
        return "错误：原生分镜为空（音频时长异常？）"

    if action == "plan":
        hit = sum(1 for ln in lines if ln.get("start") is not None)
        checks = me.verify_shots(shots, dur, lines)
        ev = {"引擎": "native（自建）", "BPM": analysis.get("bpm"), "时长": f"{dur:.2f}s",
              "节拍": len(analysis.get("beats") or []), "分镜": len(shots),
              "歌词句": f"{hit}/{len(lines)}", "whisper": engine_model}
        show = lines[:6]
        extra = ["· 前几句歌词轴："] + [f"  {ln.get('start')}-{ln.get('end')}  {ln.get('text')}"
                                        for ln in show]
        return _mv_report("plan", ev, checks, extra=extra)

    if action == "storyboard":
        sb = [{"prompt": f"歌曲意境镜头 {s['index'] + 1}，青花/中国风", "subtitle": s.get("lyric_ref") or "",
               "duration": s["duration"], "effect": "kenburns"} for s in shots]
        cover = sum(s["duration"] for s in shots)
        checks = [("分镜非空", bool(sb), f"{len(sb)} 镜"),
                  ("时长合计≈全曲", abs(cover - dur) <= 2.0, f"合 {cover:.2f}s / 总 {dur:.2f}s")]
        ev = {"引擎": "native（自建）", "分镜": len(sb), "总时长": f"{dur:.2f}s",
              "分辨率": f"{resolution}@{fps}fps", "BPM": analysis.get("bpm")}
        return _mv_report("storyboard", ev, checks,
                          extra=["下一步：把该 storyboard 传给 mv_compose 出图合成。"])

    # render：PIL 确定性帧 + 原生歌词 SRT + ffmpeg 合成
    frames_dir = os.path.join(permissions.WORKSPACE_DIR or os.path.dirname(audio_abs),
                              "video", f"mvnative_{datetime.now():%Y%m%d_%H%M%S}")
    frames = me.render_frames(shots, frames_dir, palette=_mv_palette(style),
                              w=int(resolution.split("x")[0]) if "x" in str(resolution) else 1080,
                              h=int(resolution.split("x")[1]) if "x" in str(resolution) else 1920)
    if not frames:
        return "错误：原生帧渲染失败（PIL 缺失？）"
    srt_path = os.path.join(frames_dir, "lyrics.srt")
    if lines:
        me.build_srt(lines, srt_path)
    out_mp4 = str(output or "").strip()
    if not out_mp4:
        out_mp4 = os.path.join(os.path.dirname(frames_dir), f"mv_{datetime.now():%Y%m%d_%H%M%S}.mp4")
    mates = [f["path"] for f in frames]
    durs = [s["duration"] for s in shots]
    # transition=0（硬切）：交叉转场会按重叠时长缩短总长，破坏「成片时长=歌曲时长」
    res, note, err = _mv_compose(mates, out_mp4, durations=durs, resolution=str(resolution),
                                 fps=int(fps), effect="kenburns", transition=0.0,
                                 audio=audio_abs, subtitle=(srt_path if lines else ""),
                                 workdir=frames_dir)
    if err:
        return f"错误：原生合成失败：{err}"
    final = out_mp4 if str(out_mp4).lower().endswith(".mp4") else out_mp4 + ".mp4"
    produced = os.path.isfile(final) and os.path.getsize(final) > 0
    if not produced:
        return f"错误：原生合成未产出成片（{final}）"
    checks = [("终片存在且非空", produced, f"{os.path.getsize(final) / 1048576:.2f} MB"),
              ("镜头数=分镜数", len(frames) == len(shots), f"{len(frames)} 帧")]
    checks += me.verify_shots(shots, dur, lines)
    vd = _ff_media_duration(final)
    if vd and dur:
        checks.append(("成片时长≈音频", abs(vd - dur) <= 1.0, f"{vd:.2f}s / {dur:.2f}s"))
    ev = {"引擎": "native（自建）", "成片": final, "BPM": analysis.get("bpm"),
          "时长": f"{dur:.2f}s", "分镜": len(shots),
          "歌词句": sum(1 for ln in lines if ln.get("start") is not None), "调色": _mv_palette(style)}
    return _mv_report("render", ev, checks)


def _mv_do_render(root, py, audio_abs, ly_args, offline_flag, offline, out, timeout, style):
    """上游 render：出片 + manifest，跑自检门禁，返回报告文本。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jid = f"mv_{stamp}"
    out_root = (os.path.abspath(out) if str(out or "").strip()
                else os.path.join(permissions.WORKSPACE_DIR or root, "video"))
    cmd = ["run", "--audio", audio_abs, "--style", str(style), "--out", out_root,
           "--job-id", jid, *ly_args, *offline_flag]
    _stdout, err = _mv_run(root, py, cmd, timeout, offline)
    if err:
        return f"错误：render 失败：{err}"
    job_dir = os.path.join(out_root, jid)
    res = _mv_json(os.path.join(job_dir, "job_result.json"))
    arts = res.get("artifacts") or {}
    final = arts.get("final") or ""
    manifest = _mv_json(arts.get("manifest") or "")
    if not final or not os.path.isfile(final):
        return f"错误：render 未产出终片（{final or job_dir}）"
    checks = _mv_verify_render(final, manifest)
    ev = {"终片": final, "封面": arts.get("cover"), "manifest": arts.get("manifest"),
          "BPM": manifest.get("_bpm") or res.get("bpm"),
          "时长": f"{float(manifest.get('duration') or 0):.2f}s",
          "分镜": len(manifest.get("shots") or []), "字幕句": len(manifest.get("captions") or []),
          "风格": manifest.get("style_id"), "离线": offline}
    return _mv_report("render", ev, checks,
                      extra=["（下游合成工具 mv_compose 只负责把分镜合成为 mp4；听觉/卡点/对词由本引擎完成。）"])


@tool(
        {
            "type": "function",
            "function": {
                "name": "mv_produce",
                "description": "专业音乐 MV 制作（唯一正确入口）：把一首歌（音频）+ 歌词交给同机的 AI MV 上游引擎，自动做 BPM/节拍/段落分析 + 歌词逐句对轴 + 卡点镜头规划。action=plan 出分镜计划（含对齐证据）、storyboard 出可直接喂 mv_compose 的分镜包、compose=分镜→mv_compose 出图合成（高画质；出图不可用时自动回退上游占位画面）、render 上游直接出片（卡点+对词，含封面/文案/manifest）、styles 列风格包。**做 MV 就用它**，绝不要手写 SVG/ffmpeg 去估算歌词与镜头时间轴（那必然对不上）。返回含自检门禁，未 PASS 不得宣称完成。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["plan", "storyboard", "compose", "render", "styles"], "description": "plan=分镜计划(快) / storyboard=出 mv_compose 分镜包 / compose=分镜→mv_compose 出图合成 / render=直接出片 / styles=列风格包"},
                        "engine": {"type": "string", "enum": ["native", "external"], "description": "native=鲸语自建引擎（默认，本地音频分析+声学歌词对轴+卡点分镜+确定性帧，不依赖外部程序）；external=调用可选的外部 AI MV 程序"},
                        "whisper_model": {"type": "string", "description": "可选：声学歌词对轴用的 whisper 模型 tiny/base/small/medium（默认 small，越大越准越慢）"},
                        "audio": {"type": "string", "description": "歌曲音频路径（wav/mp3，ffmpeg 可解码）"},
                        "lyrics": {"type": "string", "description": "可选：歌词（.lrc/.txt 绝对路径，或直接贴歌词文本；有逐句时间戳的 lrc 对齐最准）"},
                        "style": {"type": "string", "description": "可选：风格包 id（默认 citypop_night_v1；用 action=styles 查看）"},
                        "out": {"type": "string", "description": "可选：成片输出根目录（render 用；默认工作区 video）"},
                        "output": {"type": "string", "description": "可选：compose 成片 mp4 绝对路径（默认工作区 video/mv_时间戳.mp4）"},
                        "images_dir": {"type": "string", "description": "可选：compose 用外部图片目录（按镜头顺序配图，跳过自动出图；图数=镜头数）"},
                        "offline": {"type": "boolean", "description": "可选：true=纯本地确定性（不调 DeepSeek，默认）；false=调 DeepSeek 生成分镜文案（复用鲸语已配 Key）"},
                        "generate_images": {"type": "boolean", "description": "可选：storyboard 模式是否让调用方（mv_compose）自行出图，默认 false"},
                        "resolution": {"type": "string", "description": "可选：分辨率「宽x高」，默认 1080x1920（竖屏抖音）"},
                        "fps": {"type": "integer", "description": "可选：帧率，默认 30"},
                        "timeout": {"type": "integer", "description": "可选：超时秒数，默认 1800（render 出片较慢）"},
                        "mv_home": {"type": "string", "description": "可选：AI MV 程序目录（默认自动探测 / 环境变量 AI_MV_HOME）"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['🎨 媒体与图像'],
    phrases='做音乐MV（音频+歌词→卡点对词分镜/成片）',
    preactivate=(('微电影', 'mv', '一键成片', '分镜', '故事板', '短片', '配音成片'),
                 ('歌词', '卡点', '对词', '对轴', '歌曲', '音乐视频', '抖音mv')),
)
def mv_produce(action="plan", audio="", lyrics="", style="citypop_night_v1", out="",
               output="", images_dir="", offline=True, generate_images=False,
               resolution="1080x1920", fps=30, timeout=1800, mv_home="",
               engine="native", whisper_model="small"):
    """音频+歌词 → 卡点对词分镜/成片。

    默认走**鲸语自建原生引擎**（mv_engine：本地音频分析 + 声学歌词对轴 + 卡点分镜 +
    确定性帧 + ffmpeg 合成），不依赖任何外部 MV 程序；engine=external 时才调用可选外部程序。
    """
    act = str(action or "plan").strip().lower()
    timeout = clamp_int(timeout, 1800, lo=60, hi=36000)

    # ── 原生引擎优先（自建、不依赖外部）──────────────────────────────
    native_eng = str(engine or "native").lower() != "external"
    if native_eng and act in ("plan", "storyboard", "render") and _mv_native_available():
        audio_abs = _mv_resolve_audio(audio)
        if not audio_abs:
            return f"错误：音频文件不存在或未提供：{audio}"
        native = _mv_native(act, audio_abs, lyrics, style, out, output, images_dir,
                            offline, resolution, fps, timeout, str(whisper_model or "small"))
        if native is not None:
            return native

    root = _mv_find_root(mv_home)
    if not root:
        return ("错误：未找到 AI MV 程序（需含 mv_api.py 与 app/cli.py）。"
                "请将其放到「鲸语同级目录/MV」，或设置环境变量 AI_MV_HOME 指向项目根。")
    py = _mv_python(root)
    if not py:
        return ("错误：找不到能运行 AI MV 的 Python 环境（缺 librosa/soundfile/scipy）。\n"
                f"对某个解释器执行：pip install -r {os.path.join(root, 'requirements.txt')}\n"
                f"或设置环境变量 AI_MV_PYTHON 指向已装依赖的解释器。（当前解释器：{sys.executable}）")

    if act == "styles":
        out_s, err = _mv_run(root, py, ["styles"], 120, True)
        if err:
            return f"错误：风格包列举失败：{err}"
        return f"[AI MV · styles] 可用风格包：\n{(out_s or '').strip()}\n（项目：{root}）"

    if act not in ("plan", "storyboard", "compose", "render"):
        return "错误：action 需为 plan / storyboard / compose / render / styles"

    audio_abs = _mv_resolve_audio(audio)
    if not audio_abs:
        return f"错误：音频文件不存在或未提供：{audio}"
    ly_args, tmp_ly = _mv_lyrics_arg(lyrics)
    offline_flag = ["--offline"] if offline else []
    try:
        if act in ("plan", "storyboard"):
            tmp_dir = tempfile.mkdtemp(prefix="wt_mv_")
            if act == "plan":
                jf = os.path.join(tmp_dir, "plan.json")
                cmd = ["plan", "--audio", audio_abs, "--style", str(style),
                       "--json", jf, *ly_args, *offline_flag]
            else:
                jf = os.path.join(tmp_dir, "storyboard.json")
                cmd = ["storyboard", "--audio", audio_abs, "--style", str(style),
                       "--json", jf, "--resolution", str(resolution), "--fps", str(int(fps)),
                       *ly_args, *offline_flag]
                if generate_images:
                    cmd.append("--generate-images")
            _stdout, err = _mv_run(root, py, cmd, timeout, offline)
            if err:
                return f"错误：{act} 失败：{err}"
            data = _mv_json(jf)
            if not data:
                return f"错误：{act} 未产出结果文件（{jf}）"
            if act == "plan":
                shots = data.get("shots") or []
                dur = float(data.get("duration") or 0.0)
                checks = _mv_verify_grid(shots, dur)
                caps = len(data.get("captions") or [])
                ev = {"项目": root, "BPM": data.get("bpm"), "时长": f"{dur:.2f}s",
                      "分镜": len(shots), "节拍": data.get("beats"),
                      "段落": " / ".join(data.get("sections") or []),
                      "歌词句": caps, "卡点对齐": data.get("beat_aligned"),
                      "分镜文件": jf}
                return _mv_report("plan", ev, checks)
            # storyboard
            sb = data.get("storyboard") or []
            meta = data.get("meta") or {}
            cover = sum(float(s.get("duration") or 0) for s in sb)
            checks = [("分镜非空", bool(sb), f"{len(sb)} 镜"),
                      ("时长合计≈全曲", abs(cover - float(meta.get("duration") or cover)) <= 2.0,
                       f"合 {cover:.2f}s / 总 {float(meta.get('duration') or 0):.2f}s")]
            checks.extend(_mv_verify_grid(
                [{"t_start": 0.0, "t_end": 0.0}], 0)[:0])  # 占位（storyboard 无绝对时间轴）
            ev = {"项目": root, "分镜": len(sb), "风格": meta.get("style_id"),
                  "总时长": f"{float(meta.get('duration') or 0):.2f}s",
                  "分辨率": f"{data.get('resolution')}@{data.get('fps')}fps",
                  "音频": data.get("audio"), "分镜包文件": jf}
            return _mv_report("storyboard", ev, checks,
                              extra=["下一步：把该 storyboard 连同 resolution/fps/audio 传给 mv_compose 合成。"])
        if act == "render":
            return _mv_do_render(root, py, audio_abs, ly_args, offline_flag,
                                 offline, out, timeout, style)

        # compose：上游分镜 → 鲸语 mv_compose 出图合成（高画质；出图不可用则回退 render）
        tmp_dir = tempfile.mkdtemp(prefix="wt_mvc_")
        jf = os.path.join(tmp_dir, "storyboard.json")
        cmd = ["storyboard", "--audio", audio_abs, "--style", str(style),
               "--json", jf, "--resolution", str(resolution), "--fps", str(int(fps)),
               *ly_args, *offline_flag]
        _stdout, err = _mv_run(root, py, cmd, timeout, offline)
        if err:
            return f"错误：取分镜失败：{err}"
        bundle = _mv_json(jf)
        sb = bundle.get("storyboard") or []
        meta = bundle.get("meta") or {}
        if not sb:
            return "错误：storyboard 为空（无法合成）"
        total = float(meta.get("duration") or 0.0)
        gen_images = bool(generate_images)
        if str(images_dir or "").strip():
            d = permissions.resolve(images_dir) or os.path.abspath(images_dir)
            imgs = []
            if os.path.isdir(d):
                imgs = [os.path.join(d, fn) for fn in sorted(os.listdir(d))
                        if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
            if imgs:
                for i, it in enumerate(sb):
                    if i < len(imgs):
                        it["image"] = imgs[i]
                gen_images = False
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_mp4 = str(output or "").strip()
        if not out_mp4:
            base_dir = (os.path.abspath(out) if str(out or "").strip()
                        else os.path.join(permissions.WORKSPACE_DIR or root, "video"))
            out_mp4 = os.path.join(base_dir, f"mv_{ts}.mp4")
        res = mv_compose(
            storyboard=sb, output=out_mp4,
            resolution=str(bundle.get("resolution") or resolution),
            fps=int(bundle.get("fps") or fps),
            duration=float(bundle.get("duration") or 3),
            effect=str(bundle.get("effect") or "kenburns"),
            transition=float(bundle.get("transition") or 0.0),
            generate_images=gen_images, narrate=bool(bundle.get("narrate")),
            subtitle=bool(bundle.get("subtitle", True)), bgm=str(bundle.get("bgm") or ""))
        final = out_mp4 if out_mp4.lower().endswith(".mp4") else out_mp4 + ".mp4"
        # 关键：以「终片真实存在」为准，绝不允许在无产物时判 PASS（治假完成）
        produced = os.path.isfile(final) and os.path.getsize(final) > 0
        if (str(res).startswith("错误") or not produced) and gen_images:
            # 出图不可用 / 合成未产出 → 回退上游占位画面，保证仍是正确成片
            fallback = _mv_do_render(root, py, audio_abs, ly_args, offline_flag,
                                     offline, out, timeout, style)
            return "⚠ 图像生成/合成未产出成片，已回退上游占位画面（卡点/对词仍正确）：\n" + fallback
        if str(res).startswith("错误") or not produced:
            return f"错误：合成未产出成片（{final}）。{res}"
        cover = sum(float(x.get("duration") or 0) for x in sb)
        checks = [("终片存在且非空", produced, f"{os.path.getsize(final) / 1048576:.2f} MB"),
                  ("分镜非空", bool(sb), f"{len(sb)} 镜"),
                  ("镜头时长合计≈全曲", abs(cover - total) <= 2.0, f"合 {cover:.2f}s / 总 {total:.2f}s")]
        if total:
            vd = _ff_media_duration(final)
            if vd:
                checks.append(("成片时长≈音频", abs(vd - total) <= 1.0, f"{vd:.2f}s / {total:.2f}s"))
        img_mode = "外部图" if str(images_dir or "").strip() else ("自动出图" if gen_images else "无图")
        ev = {"成片": final, "分镜": len(sb), "出图": img_mode,
              "风格": meta.get("style_id"), "总时长": f"{total:.2f}s",
              "分辨率": f"{bundle.get('resolution')}@{bundle.get('fps')}fps"}
        return _mv_report("compose", ev, checks,
                          extra=["（分镜时间轴来自上游引擎；mv_compose 仅按分镜出图/合成。）"])
    finally:
        if tmp_ly:
            with contextlib.suppress(OSError):
                os.remove(tmp_ly)


__all__ = ['mv_compose', 'mv_produce']
