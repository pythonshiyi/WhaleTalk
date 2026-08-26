// ── 语音朗读工具：文本清洗 / 分句 / 合成 / 播放队列 / 停止 ──
import { api, getToken, getBase } from "./api.js";

// Markdown 清洗：代码块整段跳过、链接只读文字、去强调与表格符号
const MD_STRIP = [
  [/```[\s\S]*?```/g, " "],
  [/`([^`]*)`/g, "$1"],
  [/!\[[^\]]*\]\([^)]*\)/g, " "],
  [/\[([^\]]*)\]\(([^)]*)\)/g, "$1"],
  /[#*_~>|]+/g, " ",
];

export function cleanForSpeech(md) {
  let s = String(md || "");
  for (const p of MD_STRIP) {
    s = typeof p[1] === "string" && p[1].includes("$") ? s.replace(p[0], p[1]) : s.replace(p[0], p[1]);
  }
  return s.replace(/^\s*[-+*]\s+/gm, "").replace(/\n{2,}/g, "\n").trim();
}

// 按句末标点切句；短句合并、无标点超长句按逗号硬切（服务端同规则兜底）
export function splitSentences(text, limit = 200) {
  const raw = String(text || "").split(/(?<=[。！？；!?\n])\s*/);
  const out = [];
  let buf = "";
  for (let seg of raw) {
    seg = seg.trim();
    if (!seg) continue;
    const cand = buf + seg;
    if (cand.length <= limit) { buf = cand; continue; }
    if (buf) out.push(buf);
    while (seg.length > limit) {
      let cut = seg.lastIndexOf("，", limit);
      cut = cut > 20 ? cut + 1 : limit;
      out.push(seg.slice(0, cut).trim());
      seg = seg.slice(cut);
    }
    buf = seg.trim();
  }
  if (buf.trim()) out.push(buf.trim());
  return out.filter(Boolean);
}

// ── 全局串行播放队列 + 停止 ──
let currentAudio = null;
let queueChain = Promise.resolve();
let generation = 0;           // stopSpeak 时 +1，令旧队列任务作废
const listeners = new Set();  // 状态变化回调 ({speaking:boolean})

function emit() {
  const st = { speaking: !!currentAudio };
  listeners.forEach((fn) => { try { fn(st); } catch {} });
}

export function onSpeechState(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function isSpeaking() {
  return !!currentAudio;
}

// 调后端合成（清洗由调用方完成后传入），返回 {url} 或抛错
async function synthesize(text, opts = {}) {
  const d = await api.api("/v1/tts/synthesize", {
    method: "POST",
    signal: AbortSignal.timeout(45000),
    body: JSON.stringify({
      text,
      rate: opts.rate,
      volume: opts.volume,
      voice: opts.voice,
      engine: opts.engine || "",
    }),
  });
  return d; // {ok,url,cached,engine}
}

async function playUrl(urlPath, volumePct) {
  const resp = await fetch(`${getBase()}${urlPath}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!resp.ok) throw new Error(`音频加载失败 ${resp.status}`);
  const blobUrl = URL.createObjectURL(await resp.blob());
  return await new Promise((resolve, reject) => {
    const audio = new Audio(blobUrl);
    audio.volume = Math.max(0, Math.min(1, (volumePct ?? 100) / 100));
    audio.onended = () => resolve();
    audio.onerror = () => reject(new Error("播放失败"));
    currentAudio = audio;
    emit();
    audio.play().catch(reject);
  });
}

/**
 * 朗读一段文本（进入全局串行队列）。
 * opts: {rate,volume,voice,engine,signal?} — signal.aborted 时任务作废。
 */
export function enqueueSpeak(text, opts = {}) {
  const gen = generation;
  queueChain = queueChain.then(async () => {
    if (gen !== generation || !text) return;
    try {
      const r = await synthesize(text.slice(0, 4000), opts);
      if (gen !== generation) return;
      await playUrl(r.url, opts.volume);
    } catch (e) {
      console.warn("[tts]", e.message);
    } finally {
      if (gen === generation) { currentAudio = null; emit(); }
    }
  });
  return queueChain;
}

/** 停止当前朗读并清空队列；返回是否真的有播放被打断 */
export function stopSpeak() {
  const had = !!currentAudio;
  generation += 1;
  if (currentAudio) {
    try { currentAudio.pause(); } catch {}
    currentAudio = null;
  }
  emit();
  return had;
}

// ── 语音设置（服务端 voice_config 的前端缓存）──
let voiceCfg = null;

export function invalidateVoiceConfig() {
  voiceCfg = null;
}

export async function getVoiceConfig(force = false) {
  if (voiceCfg && !force) return voiceCfg;
  const fallback = { auto_mode: "off", rate: 0, volume: 100, voice: "" };
  try {
    const d = await api.api("/v1/config");
    voiceCfg = d.voice_config ? { ...fallback, ...d.voice_config } : fallback;
  } catch {
    return fallback;
  }
  return voiceCfg;
}
