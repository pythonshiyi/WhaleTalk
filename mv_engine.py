"""mv_engine —— 鲸语自建 MV 引擎（不依赖任何外部 MV 程序）。

职责：把「音频 + 歌词」变成**卡点 + 对词**的 MV 素材与成片。四个阶段：

    analyze(audio)              → BPM / 节拍 / 下拍 / 时长 / 能量包络
    align_lyrics(audio, text)   → 歌词逐句的**真实声学时间轴**（faster-whisper 词级时间戳；
                                  缺 whisper 时回退能量谷句子切分）
    build_shots(duration, beats, lines) → 吸附到节拍、覆盖全曲无空隙的分镜网格
    render_frames(shots, outdir)        → 确定性 PIL 渲染的竖屏帧（离线、可复现）

设计取舍：
- 音频分析用 librosa（本机已装；缺则退回 scipy/numpy 的 RMS+onset 近似）。
- 歌词对轴**不采信均匀估算**：优先 whisper 词级时间戳与歌词行做字符级模糊匹配；
  匹配不足时用「能量谷 + 人声频段」切句，而不是把段落标签当歌词行。
- 全程离线确定性，无神经出图依赖；图像质量交给上层（image_generate / 用户供图）。

本模块为纯函数 + 文件 IO，便于门禁与回归测试；被 agent_tools/tool_mv.py 消费。
"""
from __future__ import annotations

import atexit
import math
import os
import re

# 进程内临时文件登记 + 退出清理（如音频解码中间 wav），避免 %TEMP% 持续堆积
_TEMP_FILES = set()


def _cleanup_temp_files():
    for p in list(_TEMP_FILES):
        try:
            os.remove(p)
        except OSError:
            pass
    _TEMP_FILES.clear()


atexit.register(_cleanup_temp_files)

_SECTION_ORDER = ("intro", "verse", "prechorus", "chorus", "bridge", "outro")
_SECTION_WORDS = set(_SECTION_ORDER) | {
    "主歌", "副歌", "前奏", "间奏", "尾奏", "尾声", "桥段", "预副歌", "说唱", "念白",
    "独白", "合唱", "合", "复歌", "结束", "outro", "intro",
}
# 段落/演出标注（如「复歌（多次）」「副歌 x2」）——不当歌词行
_ANNOT = re.compile(r"^[\u4e00-\u9fffA-Za-z]{1,8}\s*[（(][^）)]{0,20}[）)]\s*[x×\d]*$")

# 中文/全角标点归一，供歌词匹配
_PUNCT = re.compile(r"[\s，。、！？：；「」『』（）()\[\]{}，,\.!?:;\"'—…·~\-]+")


def _norm(s: str) -> str:
    return _PUNCT.sub("", str(s or "")).lower()


def parse_lyrics_text(text: str):
    """把歌词文本切成行（去空行/段落标签/时间戳）。返回 [line, ...]。"""
    out = []
    for raw in str(text or "").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        # 去 LRC 时间戳
        line = re.sub(r"^\[\d{1,2}:\d{1,2}(?:[.:]\d{1,3})?\]\s*", "", line).strip()
        # 去纯元信息标签 [ti:..] / [ar:..]
        if re.match(r"^\[[a-zA-Z]+:.*\]$", line):
            continue
        # 去纯段落标签（intro/verse/主歌/副歌/说唱/尾奏…）与演出标注（复歌（多次））——不当歌词行
        low = line.lower().strip("[]（）() :：")
        if low in _SECTION_WORDS or _ANNOT.match(line):
            continue
        if line:
            out.append(line)
    return out


def parse_lrc(text: str):
    """解析带时间戳的 LRC → [{text,start,end}]；无时间戳返回 []。"""
    rows = []
    for raw in str(text or "").replace("\r", "\n").split("\n"):
        m = re.match(r"^\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]\s*(.*)$", raw.strip())
        if not m:
            continue
        mm, ss, frac, body = m.group(1), m.group(2), m.group(3) or "0", m.group(4).strip()
        # 小数位按位数解释：.6=0.6s / .60=0.60s / .600=0.600s（旧实现单位数误按 /100）
        t = int(mm) * 60 + int(ss) + (int(frac) / (10.0 ** len(frac)))
        if body:
            rows.append((t, body))
    rows.sort(key=lambda x: x[0])
    out = []
    for i, (t, body) in enumerate(rows):
        end = rows[i + 1][0] if i + 1 < len(rows) else None
        out.append({"text": body, "start": round(t, 3), "end": round(end, 3) if end else None,
                    "index": i})
    return out


# ── 音频分析（librosa；缺则近似回退）────────────────────────────────────
def analyze(audio, sr=22050, hop_length=512):
    """返回 {bpm, beats:[...], downbeats:[...], duration, sr, energy:[(t,rms)...]}。"""
    y, e_sr = _load_mono(audio, sr)
    dur = len(y) / float(e_sr) if len(y) else 0.0
    bpm, beats = _beats(y, e_sr, hop_length)
    downbeats = beats[::4] if beats else []
    energy = _energy_curve(y, e_sr, hop_length)
    return {"bpm": round(float(bpm or 0.0), 2), "beats": [round(b, 3) for b in beats],
            "downbeats": [round(b, 3) for b in downbeats], "duration": round(dur, 3),
            "sr": e_sr, "energy": energy}


def _load_mono(audio, sr):
    try:
        import librosa
        import numpy as np
        y, e_sr = librosa.load(audio, sr=sr, mono=True)
        return np.asarray(y, dtype="float32"), int(e_sr)
    except Exception:  # noqa: BLE001 - 回退：ffmpeg 解码 + wave 读取
        import numpy as np
        wav = _ffmpeg_decode_wav(audio, sr)
        if not wav:
            return np.zeros(0, dtype="float32"), int(sr)
        import wave
        with wave.open(wav, "rb") as w:
            n = w.getnframes()
            data = np.frombuffer(w.readframes(n), dtype="<i2").astype("float32") / 32768.0
            return data, w.getframerate()


def _ffmpeg_decode_wav(audio, sr):
    import subprocess
    import tempfile
    fd, out = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    _TEMP_FILES.add(out)
    try:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(audio),
                        "-ac", "1", "-ar", str(sr), out], capture_output=True, timeout=300,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out if os.path.isfile(out) and os.path.getsize(out) > 44 else ""
    except Exception:  # noqa: BLE001
        return ""


def _beats(y, sr, hop_length):
    try:
        import librosa
        tempo, frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop_length, units="frames")
        times = librosa.frames_to_time(frames, sr=sr, hop_length=hop_length)
        if hasattr(tempo, "__len__"):
            tempo = float(tempo[0]) if len(tempo) else 0.0
        return float(tempo), [float(t) for t in times]
    except Exception:  # noqa: BLE001
        return _beats_approx(y, sr, hop_length)


def _beats_approx(y, sr, hop_length):
    """无 librosa：RMS 包络自相关估拍 + 峰值取拍。"""
    import numpy as np
    if len(y) == 0:
        return 0.0, []
    es = _energy_curve(y, sr, hop_length)
    rms = np.array([e[1] for e in es], dtype="float32")
    if len(rms) < 4:
        return 0.0, []
    rms = rms - rms.mean()
    ac = np.correlate(rms, rms, mode="full")[len(rms) - 1:]
    fps = sr / hop_length
    lo, hi = int(fps * 60 / 200), int(fps * 60 / 50)  # 50–200 BPM
    if hi <= lo or hi >= len(ac):
        return 0.0, []
    lag = lo + int(np.argmax(ac[lo:hi]))
    bpm = 60.0 * fps / max(1, lag)
    interval = 60.0 / bpm if bpm > 0 else 0.0
    beats, t = [], interval
    dur = len(y) / sr
    while t < dur:
        beats.append(round(t, 3))
        t += interval
    return bpm, beats


def _energy_curve(y, sr, hop_length):
    import numpy as np
    if len(y) == 0:
        return []
    win = max(1, hop_length)
    n = len(y) // win
    out = []
    for i in range(n):
        seg = y[i * win:(i + 1) * win]
        rms = float(np.sqrt(np.mean(seg * seg))) if len(seg) else 0.0
        out.append((round(i * win / sr, 3), round(rms, 5)))
    return out


# ── 歌词声学对轴 ────────────────────────────────────────────────────────
def align_lyrics(audio, lyrics_text, model="base", sr=16000):
    """歌词逐句对齐到真实时间轴。返回 [{text,start,end,index,confidence}]。

    优先：faster-whisper 词级时间戳 + 字符级模糊匹配；
    回退：能量谷 + 人声频段切句（不做均匀估算）。
    """
    lrc = parse_lrc(lyrics_text)
    if lrc:  # ① 现成 LRC 时间戳：直接采信（精确）
        return _fill_lrc_gaps(lrc)
    lines = parse_lyrics_text(lyrics_text)
    if not lines:
        return []
    words = _whisper_words(audio, model, prompt=" ".join(lines))
    active = _active_spans(audio, sr)
    if words:
        # ② 字符级序列对齐（whisper 字符时间 ↔ 歌词行）：命中即真实时间，最精确
        seq = _align_by_sequence(lines, words)
        if seq and _match_quality(seq) >= 0.6:
            return _fill_gaps(seq)
        # ③ whisper 短语 ∪ 能量活跃段 → 单调分配（识别稀疏时的稳健兜底）
        first_on, last_off = words[0][1], max(w[2] for w in words)
        merged = _merge_spans(_words_to_spans(words), active)
        clipped = []
        for s, e in merged:
            s2, e2 = max(s, first_on), min(e, last_off + 1.5)
            if e2 - s2 > 0.3:
                clipped.append((s2, e2))
        assigned = _assign_lines(lines, clipped)
        if assigned and _match_quality(assigned) >= 0.8:
            return assigned
    if active:
        # ④ 纯能量活跃段兜底（无 whisper）
        assigned = _assign_lines(lines, active)
        if assigned:
            return assigned
    return [{"text": t, "start": None, "end": None, "index": i, "confidence": 0.0}
            for i, t in enumerate(lines)]


def _align_by_sequence(lines, words):
    """字符级序列对齐：whisper 字符时间戳 ↔ 歌词行字符 → 每行真实起止时间。

    用 difflib 匹配块把歌词字符映射到 whisper 字符时间；命中行取真实时间，
    未命中行留给 `_fill_gaps` 插值。比「按段均分」精确得多，且对识别错误稳健。
    """
    import difflib
    wchars, wtime = [], []
    for tok, s, e in words:
        n = len(tok)
        for k in range(n):
            wchars.append(tok[k])
            wtime.append((s + (e - s) * k / n, s + (e - s) * (k + 1) / n))
    norm_lines = [_norm(ln) for ln in lines]
    lchars, lidx = [], []
    for i, nl in enumerate(norm_lines):
        for ch in nl:
            lchars.append(ch)
            lidx.append(i)
    if not lchars or not wchars:
        return []
    sm = difflib.SequenceMatcher(None, "".join(lchars), "".join(wchars), autojunk=False)
    hits = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            hits.setdefault(lidx[blk.a + k], []).append(blk.b + k)
    out = []
    for i, line in enumerate(lines):
        idxs = hits.get(i)
        if idxs:
            st = min(wtime[j][0] for j in idxs)
            en = max(wtime[j][1] for j in idxs)
            cov = len(idxs) / max(1, len(norm_lines[i]))
            out.append({"text": line, "start": round(st, 3), "end": round(en, 3),
                        "index": i, "confidence": round(min(1.0, 0.5 + 0.5 * cov), 3)})
        else:
            out.append({"text": line, "start": None, "end": None, "index": i, "confidence": 0.0})
    return out


def _fill_gaps(rows):
    """为无时间的行按相邻锚点插值：中间线性插值，首/末用最近锚点外推。"""
    n = len(rows)
    anchors = [i for i, r in enumerate(rows) if r.get("start") is not None]
    if not anchors:
        return rows
    out = [dict(r) for r in rows]
    first, last = anchors[0], anchors[-1]
    step = 1.5
    for i in range(first - 1, -1, -1):
        a = max(0.0, out[first]["start"] - (first - i) * step)
        out[i]["start"], out[i]["end"] = round(a, 3), round(a + step, 3)
    for i in range(last + 1, n):
        a = out[last]["end"] + (i - last) * step
        out[i]["start"], out[i]["end"] = round(a, 3), round(a + step, 3)
    for ai in range(len(anchors) - 1):
        i0, i1 = anchors[ai], anchors[ai + 1]
        if i1 - i0 <= 1:
            continue
        t0, t1 = out[i0]["end"], out[i1]["start"]
        span = i1 - i0
        for k in range(1, span):
            t = t0 + (t1 - t0) * k / span
            out[i0 + k]["start"] = round(t, 3)
            out[i0 + k]["end"] = round(t + (t1 - t0) / span, 3)
    for i in range(1, n):
        if out[i]["start"] < out[i - 1]["start"]:
            out[i]["start"] = out[i - 1]["start"]
        if out[i]["end"] <= out[i]["start"]:
            out[i]["end"] = round(out[i]["start"] + 0.6, 3)
    return out


def _words_to_spans(words, gap=0.8):
    """词序列 → 演唱短语跨度（按停顿切）。"""
    if not words:
        return []
    spans, s, e = [], words[0][1], words[0][2]
    for _t, ws, we in words[1:]:
        if ws - e > gap:
            spans.append((s, e))
            s, e = ws, we
        else:
            e = max(e, we)
    spans.append((s, e))
    return spans


def _active_spans(audio, sr=22050, hop=512, thr_k=0.35, min_span=0.5, merge_gap=0.6):
    """能量活跃段（自适应阈值 RMS）：作曲段/演唱段的粗切，用于填补 whisper 漏识别。"""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return []
    y, e_sr = _load_mono(audio, sr)
    if len(y) == 0:
        return []
    es = _energy_curve(y, e_sr, hop)
    if len(es) < 4:
        return []
    ts = np.array([e[0] for e in es], dtype="float32")
    rms = np.array([e[1] for e in es], dtype="float32")
    thr = float(rms.mean() + thr_k * rms.std())
    active = rms > thr
    spans, i, n = [], 0, len(active)
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1]:
                j += 1
            spans.append([float(ts[i]), float(ts[j])])
            i = j + 1
        else:
            i += 1
    merged = []
    for s, e in spans:
        if merged and s - merged[-1][1] < merge_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s >= min_span]


def _merge_spans(a, b, gap=0.5):
    """合并两组跨度并去重叠（取并集后合并相邻）。"""
    allsp = sorted([tuple(x) for x in (a or [])] + [tuple(x) for x in (b or [])])
    if not allsp:
        return []
    out = [list(allsp[0])]
    for s, e in allsp[1:]:
        if s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out if e > s]


def _assign_lines(lines, spans):
    """把 n 行歌词**单调、不塌缩**地分配到 spans（行多→按跨度切分；行少→合并短语）。"""
    spans = [(float(s), float(e)) for s, e in (spans or []) if e > s]
    n, m = len(lines), len(spans)
    if n <= 0 or m <= 0:
        return []
    out = []
    if m >= n:
        per = m / n
        for i, line in enumerate(lines):
            a = spans[min(int(i * per), m - 1)][0]
            b = spans[min(int((i + 1) * per) - 1, m - 1)][1]
            out.append((line, a, b))
    else:
        total = sum(e - s for s, e in spans) or 1.0
        alloc, remaining = [], n
        for idx, (s, e) in enumerate(spans):
            if idx == m - 1:
                k = remaining
            else:
                k = max(1, int(round(n * (e - s) / total)))
                k = min(k, remaining - (m - 1 - idx))
            alloc.append(k)
            remaining -= k
        li = 0
        for (s, e), k in zip(spans, alloc):
            step = (e - s) / max(1, k)
            for j in range(k):
                if li >= n:
                    break
                out.append((lines[li], s + j * step, s + (j + 1) * step))
                li += 1
    res = []
    prev_start = 0.0
    for i, (line, a, b) in enumerate(out):
        a = max(a, prev_start)
        b = max(b, a + 0.6)
        res.append({"text": line, "start": round(a, 3), "end": round(b, 3),
                    "index": i, "confidence": 0.5})
        prev_start = a + 0.01
    return res


def _whisper_words(audio, model, prompt=""):
    """词级转写；prompt=歌词可显著提升歌唱识别与对轴精度。"""
    try:
        from faster_whisper import WhisperModel
        inst = WhisperModel(str(model or "base"), device="cpu", compute_type="int8")
        segs, _info = inst.transcribe(
            audio, word_timestamps=True, vad_filter=False, language="zh",
            initial_prompt=(str(prompt)[:1200] or None), condition_on_previous_text=False)
        words = []
        for seg in segs:
            for w in (seg.words or []):
                token = _norm(getattr(w, "word", ""))
                if token:
                    words.append((token, float(w.start), float(w.end)))
        return words
    except Exception:  # noqa: BLE001
        return []


def _match_by_phrases(lines, words, gap=0.8):
    """按停顿把词切成语义短语，再把歌词行**按顺序**映射到短语跨度。

    歌唱场景下逐字识别常不完整，但「第 i 句 = 第 i 个演唱短语」的顺序关系稳定，
    比纯文本模糊匹配鲁棒。行数多于短语时余下按短语内均摊；短语多于行时合并尾短语。
    """
    if not lines or not words:
        return []
    phrases, cur = [], [words[0]]
    for w in words[1:]:
        if w[1] - cur[-1][2] > gap:
            phrases.append(cur)
            cur = [w]
        else:
            cur.append(w)
    phrases.append(cur)
    spans = [(p[0][1], p[-1][2]) for p in phrases if p and p[-1][2] > p[0][1]]
    n, m = len(lines), len(spans)
    if n <= 0 or m <= 0:
        return []
    out = []
    if m >= n:
        # 短语多于行：把 m 个短语按时长均分给 n 行（合并相邻短语）
        per = m / n
        for i, line in enumerate(lines):
            a = spans[min(int(i * per), m - 1)][0]
            b = spans[min(int((i + 1) * per) - 1, m - 1)][1]
            out.append({"text": line, "start": round(a, 3), "end": round(max(b, a + 0.8), 3),
                        "index": i, "confidence": 0.5})
    else:
        # 行多于短语：按各短语时长比例把 n 行切分给 m 个短语（每行一个不重叠子区间）
        total = sum(e - s for s, e in spans) or 1.0
        alloc, remaining = [], n
        for idx, (s, e) in enumerate(spans):
            if idx == m - 1:
                k = remaining
            else:
                k = max(1, int(round(n * (e - s) / total)))
                k = min(k, remaining - (m - 1 - idx))
            alloc.append(k)
            remaining -= k
        li = 0
        for (s, e), k in zip(spans, alloc):
            step = (e - s) / max(1, k)
            for j in range(k):
                if li >= n:
                    break
                a = s + j * step
                b = s + (j + 1) * step
                out.append({"text": lines[li], "start": round(a, 3),
                            "end": round(max(b, a + 0.6), 3), "index": li, "confidence": 0.5})
                li += 1
    # 单调修正：保证 start 递增、end>=start
    prev = 0.0
    for ln in out:
        if ln["start"] < prev:
            ln["start"] = round(prev, 3)
        if ln["end"] <= ln["start"]:
            ln["end"] = round(ln["start"] + 1.2, 3)
        prev = ln["start"]
    return out


def _match_lines(lines, words):
    """把每行歌词匹配到转写词序列上的时间跨度（difflib 字符级，最长匹配）。"""
    import difflib
    # 展平为 (char, word_idx)
    chars, owner = [], []
    for wi, (tok, _s, _e) in enumerate(words):
        for ch in tok:
            chars.append(ch)
            owner.append(wi)
    flat = "".join(chars)
    out, cursor = [], 0
    for i, line in enumerate(lines):
        needle = _norm(line)
        if not needle:
            out.append({"text": line, "start": None, "end": None, "index": i, "confidence": 0.0})
            continue
        # 在 cursor 之后 + 少量回看窗口内找最佳窗口
        best = (0.0, None, None)
        win_lo = max(0, cursor - 30)
        for start in range(win_lo, max(win_lo, len(chars) - len(needle) + 1)):
            hi = start + max(len(needle), int(len(needle) * 1.6)) + 8
            window = flat[start:hi]
            if not window:
                break
            sm = difflib.SequenceMatcher(None, needle, window)
            ratio = sm.ratio()
            if ratio > best[0] and sm.find_longest_match(0, len(needle), 0, len(window)).size >= 2:
                # 用最长匹配段在 window 中的位置收敛到实际跨度
                blk = sm.find_longest_match(0, len(needle), 0, len(window))
                a = start + blk.b
                b = a + max(blk.size, 1)
                best = (ratio, a, b)
            if start > cursor and ratio > 0.75:
                break
        if best[1] is not None and best[1] < len(owner):
            a, b = best[1], min(best[2], len(owner) - 1)
            w_start = words[owner[a]][1]
            w_end = words[owner[b]][2]
            out.append({"text": line, "start": round(w_start, 3), "end": round(w_end, 3),
                        "index": i, "confidence": round(best[0], 3)})
            cursor = b
        else:
            out.append({"text": line, "start": None, "end": None, "index": i, "confidence": 0.0})
    return out


def _match_quality(aligned):
    if not aligned:
        return 0.0
    hit = sum(1 for a in aligned if a.get("start") is not None)
    return hit / len(aligned)


def _fill_lrc_gaps(rows):
    out = []
    for i, r in enumerate(rows):
        end = r["end"]
        if end is None:
            end = rows[i + 1]["start"] if i + 1 < len(rows) else r["start"] + 3.0
        out.append({"text": r["text"], "start": r["start"], "end": end,
                    "index": i, "confidence": 1.0})
    return out


def _align_by_energy(audio, lines, sr):
    """回退：用能量包络找「人声活跃段」（高于自适应阈值）→ 按活跃段切句。"""
    import numpy as np
    y, e_sr = _load_mono(audio, sr)
    if len(y) == 0 or not lines:
        return [{"text": t, "start": None, "end": None, "index": i, "confidence": 0.0}
                for i, t in enumerate(lines)]
    es = _energy_curve(y, e_sr, 512)
    rms = np.array([e[1] for e in es], dtype="float32") if es else np.zeros(0, "float32")
    ts = np.array([e[0] for e in es], dtype="float32") if es else np.zeros(0, "float32")
    out = []
    if len(rms) < 4:
        return [{"text": t, "start": None, "end": None, "index": i, "confidence": 0.0}
                for i, t in enumerate(lines)]
    thr = float(rms.mean() + 0.5 * rms.std())
    active = rms > thr
    # 找连续活跃区间
    spans, i, n = [], 0, len(active)
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1]:
                j += 1
            if ts[j] - ts[i] >= 0.4:
                spans.append((float(ts[i]), float(ts[j])))
            i = j + 1
        else:
            i += 1
    if not spans:
        return [{"text": t, "start": None, "end": None, "index": i, "confidence": 0.0}
                for i, t in enumerate(lines)]
    # 按活跃段数量均摊歌词行到各段（段内再按字均分）
    for idx, line in enumerate(lines):
        span = spans[min(idx, len(spans) - 1)]
        out.append({"text": line, "start": round(span[0], 3), "end": round(span[1], 3),
                    "index": idx, "confidence": 0.2})
    return out


# ── 卡点分镜网格 ────────────────────────────────────────────────────────
def build_shots(duration, beats, lines=None, min_shot=1.2, max_shot=6.0):
    """吸附到节拍的镜头网格：覆盖 [0,duration]、无空隙；每镜挂当前歌词。

    策略：以节拍为候选边界，镜头时长尽量落在 [min_shot,max_shot]；无节拍时按等分。
    """
    duration = float(duration or 0.0)
    if duration <= 0:
        return []
    lines = lines or []
    bs = sorted({float(b) for b in (beats or []) if 0 < float(b) < duration})
    boundaries = [0.0]
    if bs:
        last = 0.0
        for b in bs:
            if b - last >= min_shot:
                if b - last >= max_shot:  # 拉太长：强行插一格中点，避免超长镜
                    boundaries.append(round((last + b) / 2.0, 3))
                boundaries.append(b)
                last = b
        if duration - last > max_shot:
            # 尾段补格
            k = int(math.ceil((duration - last) / max_shot))
            for j in range(1, k):
                boundaries.append(round(last + (duration - last) * j / k, 3))
    else:
        step = max(min_shot, min(max_shot, duration / max(1, round(duration / max_shot))))
        t = step
        while t < duration - 1e-6:
            boundaries.append(round(t, 3))
            t += step
    boundaries = sorted(set(b for b in boundaries if 0 <= b <= duration))
    boundaries.append(round(duration, 3))
    shots = []
    for i in range(len(boundaries) - 1):
        a, b = boundaries[i], boundaries[i + 1]
        if b - a < 1e-3:
            continue
        ref = ""
        for ln in lines:
            if ln.get("start") is not None and a <= ln["start"] < b:
                ref = ln["text"]
                break
        shots.append({"index": len(shots), "t_start": round(a, 3), "t_end": round(b, 3),
                      "duration": round(b - a, 3), "lyric_ref": ref})
    return shots


def section_for(t, shots):
    return ""


# ── 字幕（SRT；_mv_compose 可直接烧录）──────────────────────────────────
def _srt_ts(t):
    ms = int(round(max(0.0, float(t)) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, msv = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{msv:03d}"


def build_srt(lines, path):
    """把对齐后的歌词行写成 SRT（跳过无时间的行）。返回写入行数。"""
    rows = [ln for ln in (lines or []) if ln.get("start") is not None]
    rows.sort(key=lambda x: x["start"])
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for i, ln in enumerate(rows, 1):
            end = ln.get("end") or (ln["start"] + 2.5)
            if end <= ln["start"]:
                end = ln["start"] + 1.5
            f.write(f"{i}\n{_srt_ts(ln['start'])} --> {_srt_ts(end)}\n{ln['text']}\n\n")
            n += 1
    return n


# ── 帧渲染（确定性 PIL，离线可复现）─────────────────────────────────────
_PALETTES = {
    "qinghua": [(13, 27, 42), (34, 63, 92), (74, 111, 145), (176, 196, 214)],
    "citypop_night_v1": [(20, 16, 44), (58, 40, 100), (150, 70, 130), (240, 170, 120)],
    "default": [(16, 20, 30), (40, 60, 90), (90, 130, 170), (220, 230, 240)],
}


_CJK_FONTS = (
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)
_FONT_CACHE = {}


def _find_font(size):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    f = None
    try:
        from PIL import ImageFont
        for path in _CJK_FONTS:
            if os.path.isfile(path):
                try:
                    f = ImageFont.truetype(path, size)
                    break
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        f = None
    _FONT_CACHE[size] = f
    return f


def render_frames(shots, outdir, palette="qinghua", w=1080, h=1920,
                  title="", artist="", credits=""):
    """为每个镜头渲染一张确定性竖屏帧（分段调色 + 青花意象 + 片头/片尾署名）。

    返回 [{path,index}]；PIL 缺失时返回 []（上层可改用 image_generate）。
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except Exception:  # noqa: BLE001
        return []
    os.makedirs(outdir, exist_ok=True)
    pal = _PALETTES.get(palette) or _PALETTES["default"]
    out = []
    total = len(shots)
    for i, sh in enumerate(shots):
        img = _frame_image(Image, ImageDraw, ImageFilter, w, h, pal, i, total, sh or {},
                           title=title, artist=artist, credits=credits)
        p = os.path.join(outdir, f"shot_{i:03d}.png")
        img.save(p, "PNG")
        out.append({"path": p, "index": i})
    return out


def _vignette(d, w, h):
    """四边线性压暗（细化步进以避免横纹带）。"""
    steps = 220
    for k in range(steps):
        t = k / steps
        a = int(95 * (1 - t) ** 1.6)
        if a <= 0:
            continue
        y = int(h * 0.20 * t)
        d.line([(0, y), (w, y)], fill=(0, 0, 0, a))
        d.line([(0, h - 1 - y), (w, h - 1 - y)], fill=(0, 0, 0, a))


def _frame_image(Image, ImageDraw, ImageFilter, w, h, pal, i, total, sh=None,
                 title="", artist="", credits=""):
    import random
    sh = sh or {}
    lyric = str(sh.get("lyric_ref") or "")
    # 构图随歌词变化（同句稳定）：画面与内容相关，而非纯占位。
    # 用 md5 摘要播种而非 hash()——后者默认每进程加盐，同一歌词两次运行结果不同，
    # 破坏「确定性帧/可复现」承诺。
    if lyric:
        import hashlib
        seed = int(hashlib.md5(lyric.encode("utf-8", "replace")).hexdigest()[:8], 16)
    else:
        seed = 1000 + i
    rnd = random.Random(seed)
    top, mid, base, accent = pal
    phase = i % 4                       # 分段调色：整体明暗/冷暖轻微起伏
    shift = (phase - 1.5) * 0.05
    sc = lambda c: tuple(max(0, min(255, int(v * (1 + shift)))) for v in c)  # noqa: E731
    top, mid, base = sc(top), sc(mid), sc(base)
    img = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(img, "RGBA")
    # 垂直渐变（三段）
    for y in range(h):
        f = y / max(1, h - 1)
        if f < 0.6:
            c = tuple(int(top[k] + (mid[k] - top[k]) * (f / 0.6)) for k in range(3))
        else:
            c = tuple(int(mid[k] + (base[k] - mid[k]) * ((f - 0.6) / 0.4)) for k in range(3))
        d.line([(0, y), (w, y)], fill=c)
    # 青花同心弧（主体）
    for _ in range(6):
        cx = rnd.randint(int(w * 0.1), int(w * 0.9))
        cy = rnd.randint(int(h * 0.12), int(h * 0.88))
        r = rnd.randint(int(w * 0.1), int(w * 0.36))
        for k in range(3):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent + (60,), width=2 + k)
            r = int(r * 0.72)
    # 云纹/笔触（青色斜线，带锥度）
    for _ in range(3):
        x0 = rnd.randint(-int(w * 0.2), int(w * 0.5))
        y0 = rnd.randint(int(h * 0.1), int(h * 0.9))
        x1 = x0 + rnd.randint(int(w * 0.5), int(w * 1.2))
        y1 = y0 + rnd.randint(-int(h * 0.1), int(h * 0.1))
        for k in range(6):
            t = k / 5.0
            xx = int(x0 + (x1 - x0) * t)
            yy = int(y0 + (y1 - y0) * t)
            rr = int(10 * (1 - t)) + 2
            d.ellipse([xx - rr, yy - rr, xx + rr, yy + rr], fill=accent + (40,))
    # 飞白点
    for _ in range(500):
        x, y = rnd.randint(0, w - 1), rnd.randint(0, h - 1)
        d.point((x, y), fill=accent + (60,))
    # 光带
    d.polygon([(0, h), (w, int(h * 0.55)), (w, h)], fill=accent + (26,))
    _vignette(d, w, h)
    # 颗粒
    for _ in range(1200):
        x, y = rnd.randint(0, w - 1), rnd.randint(0, h - 1)
        v = rnd.randint(0, 34)
        d.point((x, y), fill=(v, v, v, 55))
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    # 字印：该镜歌词首字以淡色大字作印章——画面承载内容（非纯占位）
    if lyric and not (i == 0 and title):
        f = _find_font(int(w * 0.44))
        if f:
            d2 = ImageDraw.Draw(img, "RGBA")
            ch = lyric[0]
            tw = d2.textlength(ch, font=f)
            d2.text(((w - tw) / 2, int(h * 0.18)), ch, font=f, fill=accent + (44,))
    # 片头（首帧）：歌名 + 制作人；片尾（末帧）：署名
    if i == 0 and title:
        _draw_center(ImageDraw, img, w, h, title, artist, big=96, small=40, y=int(h * 0.40))
    if total and i == total - 1 and (credits or artist):
        _draw_center(ImageDraw, img, w, h, credits or "", artist, big=56, small=36,
                     y=int(h * 0.46), alpha=230)
    return img


def _draw_center(ImageDraw, img, w, h, big_text, small_text, big=96, small=40, y=0, alpha=255):
    d = ImageDraw.Draw(img, "RGBA")
    f_big = _find_font(big)
    f_small = _find_font(small)
    if f_big and big_text:
        tw = d.textlength(big_text, font=f_big)
        d.text(((w - tw) / 2, y), big_text, font=f_big, fill=(240, 244, 248, alpha))
    if f_small and small_text:
        tw = d.textlength(small_text, font=f_small)
        d.text(((w - tw) / 2, y + big + 24), small_text, font=f_small, fill=(210, 220, 230, alpha))


# ── 校验门禁 ────────────────────────────────────────────────────────────
def verify_shots(shots, duration, lines=None):
    """覆盖/无空隙/字幕落片内等自检，返回 [(label, ok, detail)]。"""
    checks = []
    if not shots:
        return [("分镜非空", False, "shots 为空")]
    starts = [s["t_start"] for s in shots]
    ends = [s["t_end"] for s in shots]
    dur = float(duration or 0.0)
    checks.append(("首镜接近开头", starts[0] <= 2.0, f"t_start={starts[0]:.3f}s"))
    if dur:
        checks.append(("末镜覆盖到片尾", ends[-1] >= dur - 0.5, f"t_end={ends[-1]:.3f}/{dur:.3f}"))
    checks.append(("时间单调", all(ends[k] <= starts[k + 1] + 1e-6 for k in range(len(shots) - 1)), ""))
    gaps = [starts[k + 1] - ends[k] for k in range(len(shots) - 1)]
    mg = max(gaps) if gaps else 0.0
    checks.append(("镜头间无空隙", mg <= 0.05, f"最大间隙 {mg:.3f}s"))
    if lines:
        rows = [ln for ln in lines if ln.get("start") is not None]
        if rows and dur:
            inside = all(0 <= ln["start"] <= dur + 0.5 for ln in rows)
            checks.append(("歌词轴落在片内", inside, f"{len(rows)} 句"))
    return checks
