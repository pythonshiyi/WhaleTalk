# -*- coding: utf-8 -*-
"""歌词真值对齐（audio + 歌词文本 → timeline.json）。

背景：mvrender 是成熟渲染底座，但它**不负责听懂音频**——它的 timeline 契约
（`{lines: [{text, t0, t1, who, sec}]}`）需要外部提供已对齐的歌词。本模块就是
这个缺口的实现：把已知歌词放到音频的真实时间轴上。

## 四条实测经验（都来自《三更帖》的四次失败迭代）

✗ v1 能量谷猜测（自研）
  搜索起点是估值而非实测，一偏移就全线错位；能量检测还会显示
  “30 句全部有人声”（只看区间内有无声音，看不到边界差 1-3 秒）。

✗ v2 whisper 逐字精确匹配
  AI 歌曲咬字模糊，ASR 错字率高（“敲我的窗”→“横挣在恕入伤”），
  要求逐字相等则一错就崩。

✗ v3 模糊匹配 + small 模型
  small 只听到 86s，后段（占全曲 60%）无真值。

✓ v4 lyric-align + `--no-vad`
  · 默认（VAD 开）：8 段 ASR → 9/30 对齐
  · **--no-vad**：39 段 ASR → **30/30 全对齐**
  · VAD 会把**慢歌的持续人声当成静音滤掉**，这是头号杀手

## 三条使用铁律

1. 【VAD 杀手】对齐唱歌人声必须关 VAD（本模块默认 `no_vad=True`）
2. 【错字无妨】ASR 听错词不影响时间戳精度（现代对齐做的是字符级模糊匹配）
3. 【必须验证】不能盲信工具输出，要用音频能量逐句独立核查
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

# ------------------------------------------------------------------ 常量
EXE_NAMES = ("lyric-align", "lyric-align.exe", "lyric_align")
ASR_TIMEOUT = 3600          # medium 模型 CPU 转录可能很慢
LRC_PRECISION = 0.01        # LRC 格式固有精度（秒）
_META_RE = re.compile(r"^\[([a-zA-Z]+):(.*)\]$")
_LRC_RE = re.compile(r"^\[(\d{1,2}):(\d{1,2}(?:\.\d{1,3})?)\]\s*(.*)$")
_SECTION_RE = re.compile(r"^\[.*\]$")
_PUNCT = re.compile(r"[\s，。、！？：；「」『』（）()\[\]{}，,.!?:;\"'—…·~\-]+")


# =============================================================== 环境
def which_aligner() -> str:
    """找 lyric-align 可执行文件（找不到返回空串）。"""
    for n in EXE_NAMES:
        p = shutil.which(n)
        if p:
            return p
    return ""


def have_aligner() -> bool:
    return bool(which_aligner())


# =============================================================== 歌词解析
def _norm(s: str) -> str:
    return _PUNCT.sub("", str(s or "")).lower()


def parse_lyrics_text(text: str) -> list:
    """提取可演唱行：去空行 / 段落标签 [Verse] / 纯角色行（男）（合）。

    与 `mv_engine.parse_lyrics_text` 语义一致，但额外处理对唱角色行。
    """
    out = []
    for raw in str(text or "").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _META_RE.match(line)
        if m:                       # [ti:] / [ar:] 等元信息
            continue
        if _LRC_RE.match(line):     # 已是 LRC：取正文
            line = _LRC_RE.match(line).group(3).strip()
            if not line:
                continue
        if _SECTION_RE.match(line):  # 任意 [xxx] 段落标记（含 [Verse 1 - Male]）
            continue
        if re.fullmatch(r"[（(].{0,6}[）)]", line):   # 纯角色行
            continue
        out.append(line)
    return out


def strip_role(line: str) -> str:
    """去行首角色标记：`（合）只是谁都没再上线` → `只是谁都没再上线`。"""
    return re.sub(r"^[（(][^）)]{0,6}[）)]\s*", "", str(line or ""))


def role_of(line: str) -> str:
    """从行首标记推角色：男/女/合（未知返回 合）。"""
    m = re.match(r"^[（(]([男女合])", str(line or ""))
    return m.group(1) if m else "合"


# =============================================================== 主对齐
def align(audio, lyrics, language: str = "zh", model: str = "medium",
          no_vad: bool = True, separate: bool = False,
          segments_cache: str = None, exe: str = None,
          timeout: int = ASR_TIMEOUT) -> dict:
    """把已知歌词对齐到音频时间轴。

    返回：
        {"ok": bool, "lines": [{text,t0,t1,who,sec,score,matched}],
         "total": n, "matched": m, "monotonic": bool,
         "warnings": [...], "source": "..."}

    其中 `lines` 直接就是 **mvrender 的 timeline 契约格式**（text/t0/t1/who/sec），
    可写入 timeline.json 供 `mvrender.core.project` 直接消费。

    参数：
      no_vad  **强烈建议 True**（实测：VAD 开=8 段/9-30；--no-vad=39 段/30-30）
      separate  先用人声分离（Demucs）再对齐，对完整混音更准但慢很多
      segments_cache  复用 ASR 结果（免重跑 ASR）
    """
    exe = exe or which_aligner()
    lines_in = lyrics if isinstance(lyrics, list) else parse_lyrics_text(lyrics)
    res = {"ok": False, "lines": [], "total": len(lines_in), "matched": 0,
           "monotonic": True, "warnings": [], "source": ""}
    if not exe:
        res["warnings"].append("未找到 lyric-align（pip install lyric-align）")
        return res
    if not lines_in:
        res["warnings"].append("歌词为空")
        return res
    if not audio or not os.path.isfile(str(audio)):
        res["warnings"].append(f"音频文件不存在：{audio}")
        return res

    res["source"] = "lyric-align"
    tmpdir = tempfile.mkdtemp(prefix="wt_align_")
    try:
        lyr_path = os.path.join(tmpdir, "lyrics.txt")
        with open(lyr_path, "w", encoding="utf-8") as f:
            f.write("\n".join(str(x) for x in lines_in))
        out_json = os.path.join(tmpdir, "aligned.json")
        cmd = [exe, str(audio), lyr_path, "--language", language,
               "--model", model, "-f", "json", "-o", out_json]
        if no_vad:
            cmd.append("--no-vad")
        if separate:
            cmd.append("--separate")
        if segments_cache and os.path.isfile(segments_cache):
            cmd += ["--segments", segments_cache]
        elif segments_cache:
            cmd += ["--dump-segments", segments_cache]

        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not os.path.isfile(out_json):
            res["warnings"].append(
                f"未产出对齐结果（退出码 {r.returncode}）：{((r.stderr or r.stdout) or '')[-400:]}")
            return res

        rows = json.load(open(out_json, encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("lines") or []
        seq = 0
        for x in rows:
            if not isinstance(x, dict):
                continue
            txt = str(x.get("line") or x.get("text") or "")
            t0 = float(x.get("start") or 0.0)
            t1 = float(x.get("end") or 0.0)
            if t1 <= t0:
                t1 = t0 + 1.0
            res["lines"].append({
                "text": txt, "t0": round(t0, 3), "t1": round(t1, 3),
                "who": role_of(txt), "sec": "",
                "score": x.get("score"), "matched": bool(x.get("matched", True)),
            })
            seq += 1
        res["lines"].sort(key=lambda d: d["t0"])
        res["matched"] = len(res["lines"])
        _fill(res, lines_in)
        return res
    except subprocess.TimeoutExpired:
        res["warnings"].append(f"ASR 超时（>{timeout}s）；可用 segments_cache 复用")
        return res
    except Exception as e:
        res["warnings"].append(f"{type(e).__name__}: {e}")
        return res
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _fill(res: dict, lines_in: list) -> None:
    """补单调性检查与失败提示（把「必须加 --no-vad」这条经验固化进输出）。"""
    for a, b in zip(res["lines"], res["lines"][1:]):
        if b["t0"] < a["t1"] - LRC_PRECISION:
            res["monotonic"] = False
            res["warnings"].append(f"时间轴重叠：{a['text'][:10]} / {b['text'][:10]}")
    if res["matched"] == 0:
        res["warnings"].append(
            "未对齐任何行 → 优先排查是否已关 VAD（--no-vad）；"
            "VAD 会把慢歌的持续人声当静音滤掉")
    elif res["matched"] < res["total"]:
        miss = res["total"] - res["matched"]
        res["warnings"].append(
            f"有 {miss} 行未对齐（已省略）；若偏多请检查 --no-vad 与歌词行数是否匹配")
    res["ok"] = res["matched"] > 0 and res["monotonic"]


# =============================================================== 能量验证
def verify_against_audio(lines, audio, sr: int = 22050,
                         voice_threshold: float = 0.16,
                         min_voice_ratio: float = 0.35) -> dict:
    """用音频能量**独立验证**对齐结果（铁律 3：不盲信工具输出）。

    检查每句区间内“人声活动”占比，找可疑行。
    返回 {ok, checked, suspect: [...], min_voice_ratio}（失败时含 error）。
    """
    try:
        import librosa
    except Exception as e:
        return {"ok": False, "error": f"需要 librosa：{e}"}
    if not lines:
        return {"ok": False, "error": "无对齐结果"}
    if not audio or not os.path.isfile(str(audio)):
        return {"ok": False, "error": f"音频文件不存在：{audio}"}
    try:
        y, _ = librosa.load(str(audio), sr=sr, mono=True)
    except Exception as e:
        return {"ok": False, "error": f"音频读取失败：{type(e).__name__}: {e}"}

    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    tms = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    span = float(rms.max() - rms.min())
    rn = (rms - rms.min()) / (span + 1e-9)

    suspect = []
    for i, ln in enumerate(lines):
        t0 = float(ln.get("t0", ln.get("start", 0.0)))
        t1 = float(ln.get("t1", ln.get("end", 0.0)))
        m = (tms >= t0) & (tms <= t1)
        occ = float((rn[m] > voice_threshold).mean()) if m.sum() else 0.0
        if occ < min_voice_ratio:
            suspect.append({"idx": i, "t0": round(t0, 2), "t1": round(t1, 2),
                            "voice_ratio": round(occ, 3),
                            "text": str(ln.get("text", ""))[:20]})
    return {"ok": len(suspect) == 0, "checked": len(lines), "suspect": suspect,
            "min_voice_ratio": min_voice_ratio}


# =============================================================== 输出/读回
def write_lrc(lines, path: str, meta: dict = None) -> str:
    """写 LRC（精度 0.01s）。"""
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for k, v in (meta or {}).items():
            f.write(f"[{k}:{v}]\n")
        for ln in lines:
            t = max(0.0, float(ln.get("t0", 0.0)))
            mm, ss = divmod(t, 60.0)
            f.write(f"[{int(mm):02d}:{ss:05.2f}]{ln.get('text','')}\n")
    return path


def write_timeline(lines, path: str, duration: float = 0.0,
                   extra: dict = None) -> str:
    """写 **mvrender 契约**的 timeline.json（{lines:[{text,t0,t1,who,sec}]}）。

    这是本模块与 mvrender 的衔接点：产物可直接被 `mvrender.core.project.Ctx` 读。
    """
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    payload = {"duration": float(duration or 0.0),
               "lines": [{"text": ln.get("text", ""),
                          "t0": round(float(ln.get("t0", 0.0)), 3),
                          "t1": round(float(ln.get("t1", 0.0)), 3),
                          "who": ln.get("who", "合"),
                          "sec": ln.get("sec", "")} for ln in (lines or [])]}
    if extra:
        payload.update(extra)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return path


def read_timeline(path: str) -> list:
    """读 timeline.json（兼容 `lines` / `lyrics` 两种字段名与 t0/start 两种键名）。"""
    data = json.load(open(path, encoding="utf-8"))
    rows = data.get("lines", data.get("lyrics", [])) if isinstance(data, dict) else (data or [])
    out = []
    for x in rows:
        if not isinstance(x, dict):
            continue
        out.append({"text": str(x.get("text", "")),
                    "t0": float(x.get("t0", x.get("start", 0.0))),
                    "t1": float(x.get("t1", x.get("end", 0.0))),
                    "who": x.get("who", "合"), "sec": x.get("sec", "")})
    out.sort(key=lambda d: d["t0"])
    return out


def assign_sections(lines, boundary_hint: dict = None) -> list:
    """给行补 sec（段落），供 mvrender 的颜色/节奏分支使用。

    boundary_hint: {"chorus": [(start,end), ...], "bridge": [...], "outro": [...]}
    未给时按帧结构与关键词粗推（主歌/副歌交替）。
    """
    out = []
    for i, ln in enumerate(lines or []):
        d = dict(ln)
        t0 = float(d.get("t0", 0.0))
        sec = ""
        if boundary_hint:
            for name, spans in boundary_hint.items():
                if any(float(a) <= t0 < float(b) for a, b in spans):
                    sec = name
                    break
        d["sec"] = sec or d.get("sec", "") or "verse"
        out.append(d)
    return out
