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
let currentResolve = null;  // 当前播放的 resolve 句柄，stopSpeak 时调用以解除挂起的 playUrl
let queueChain = Promise.resolve();
let generation = 0;           // stopSpeak 时 +1，令旧队列任务作废
let loadingCount = 0;         // 合成中的任务数（点击后立即有反馈）
let lastError = "";           // 最近一次朗读失败原因
const listeners = new Set();  // 状态变化回调 ({speaking,loading,error})

function emit() {
  const st = { speaking: !!currentAudio, loading: loadingCount > 0, error: lastError };
  listeners.forEach((fn) => { try { fn(st); } catch {} });
}

export function onSpeechState(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function isSpeaking() {
  return !!currentAudio;
}

// ── 「说话即打断」barge-in：朗读时监听麦克风，检测到用户说话即停止朗读 ──
// 权限门控 + 默认关闭；需用户在设置开启并授予麦克风权限。纯前端、无 whisper 依赖，
// 用音量阈值近似（用户一开口 → 环境声响 → 停止）。
let bargeInOn = false;
let bargeInCtx = null;
let bargeInAnalyser = null;
let bargeInStream = null;
let bargeInTimer = null;

async function _startBargeIn() {
  try {
    if (!window.AudioContext && !window.webkitAudioContext) return false;
    bargeInCtx = new (window.AudioContext || window.webkitAudioContext)();
    bargeInStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    bargeInAnalyser = bargeInCtx.createAnalyser();
    bargeInAnalyser.fftSize = 512;
    bargeInAnalyser.smoothingTimeConstant = 0.5;
    const src = bargeInCtx.createMediaStreamSource(bargeInStream);
    src.connect(bargeInAnalyser);
    const buf = new Uint8Array(bargeInAnalyser.frequencyBinCount);
    bargeInTimer = setInterval(() => {
      if (!currentAudio || !bargeInAnalyser) return;
      bargeInAnalyser.getByteFrequencyData(buf);
      let sum = 0;
      const n = Math.floor(buf.length * 0.6);  // 只取中低频（人声区）
      for (let i = 0; i < n; i++) sum += buf[i];
      const avg = sum / (n || 1) / 255;
      if (avg > 0.3) {  // 用户开口（中低频能量明显）→ 打断朗读
        stopSpeak();
      }
    }, 200);
    return true;
  } catch (e) {
    return false;
  }
}

export async function enableVoiceInterrupt() {
  if (bargeInOn) return true;
  const ok = await _startBargeIn();
  if (ok) bargeInOn = true;
  return ok;
}

export function disableVoiceInterrupt() {
  bargeInOn = false;
  if (bargeInTimer) { clearInterval(bargeInTimer); bargeInTimer = null; }
  if (bargeInStream) { try { bargeInStream.getTracks().forEach((t) => t.stop()); } catch {} }
  if (bargeInCtx) { try { bargeInCtx.close(); } catch {} }
  bargeInStream = null; bargeInCtx = null; bargeInAnalyser = null;
}

// 在用户点击手势内"解锁"音频管线（页面刚加载+合成超过瞬态窗口的场景）
let primed = false;
export function primeAudio() {
  if (primed) return;
  try {
    const a = new Audio("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=");
    a.muted = true;
    const p = a.play();
    if (p && p.then) p.then(() => { primed = true; a.pause(); }).catch(() => {});
    primed = true;
  } catch {}
}

// 调后端合成（清洗由调用方完成后传入），返回 {url} 或抛错（含后端 error 详情）
async function synthesize(text, opts = {}) {
  const body = JSON.stringify({
    text,
    rate: opts.rate,
    volume: opts.volume,
    voice: opts.voice,
    engine: opts.engine || "",
  });
  const once = (tok) => fetch(`${getBase()}/v1/tts/synthesize`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` },
    body,
    signal: AbortSignal.timeout(60000),
  });
  let tok = getToken();
  let r = await once(tok);
  if (r.status === 401) {
    try { localStorage.removeItem("whaletalk.api.token"); } catch {}
    try {
      const tr = await fetch(`${getBase()}/v1/token`, { signal: AbortSignal.timeout(2500) });
      if (tr.ok) {
        const tj = await tr.json();
        if (tj.token) { try { localStorage.setItem("whaletalk.api.token", tj.token); } catch {} tok = tj.token; }
      }
    } catch {}
    r = await once(tok);
  }
  if (!r.ok) {
    let msg = `合成失败 ${r.status}`;
    try { const j = await r.json(); if (j && j.error) msg = j.error; } catch {}
    throw new Error(msg);
  }
  return await r.json(); // {ok,url,cached,engine}
}

async function fetchAudio(urlPath) {
  const once = () => fetch(`${getBase()}${urlPath}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  let resp = await once();
  if (resp.status === 401) {
    try { localStorage.removeItem("whaletalk.api.token"); } catch {}
    try {
      const tr = await fetch(`${getBase()}/v1/token`, { signal: AbortSignal.timeout(2500) });
      if (tr.ok) {
        const tj = await tr.json();
        if (tj.token) { try { localStorage.setItem("whaletalk.api.token", tj.token); } catch {} }
      }
    } catch {}
    resp = await once();
  }
  return resp;
}

async function playUrl(urlPath, volumePct) {
  const resp = await fetchAudio(urlPath);
  if (!resp.ok) throw new Error(`音频加载失败 ${resp.status}`);
  const blobUrl = URL.createObjectURL(await resp.blob());
  return await new Promise((resolve, reject) => {
    const audio = new Audio(blobUrl);
    audio.volume = Math.max(0, Math.min(1, (volumePct ?? 100) / 100));
    let settled = false;
    let watchdog = null;
    const clearWatch = () => { if (watchdog) { clearTimeout(watchdog); watchdog = null; } };
    const done = () => {
      if (settled) return;
      settled = true;
      clearWatch();
      if (currentAudio === audio) currentAudio = null;
      if (currentResolve === done) currentResolve = null;
      try { URL.revokeObjectURL(blobUrl); } catch {}
      resolve();
    };
    audio.onloadedmetadata = () => {
      // 兜底：即使 onended 不触发（无输出设备/播放卡住），也按时长+4s 强制结束，
      // 避免朗读按钮永远停在「播放中」。
      if (settled) return;
      const d = audio.duration;
      if (d && isFinite(d) && d > 0) watchdog = setTimeout(done, d * 1000 + 4000);
    };
    audio.onended = done;
    audio.onerror = () => {
      if (settled) return;
      settled = true;
      clearWatch();
      if (currentAudio === audio) currentAudio = null;
      if (currentResolve === done) currentResolve = null;
      reject(new Error("音频解码/播放失败"));
    };
    currentAudio = audio;
    currentResolve = done;
    emit();
    audio.play().catch((e) => {
      if (settled) return;
      settled = true;
      clearWatch();
      if (currentAudio === audio) currentAudio = null;
      if (currentResolve === done) currentResolve = null;
      reject(e && e.name === "NotAllowedError" ? e : new Error("播放失败: " + (e && e.name || "")));
    });
  });
}

// 把异常原因翻译成人话
function _speakFail(e) {
  const raw = String((e && e.message) || e);
  if (/NotAllowed/.test(raw)) lastError = "浏览器拦截了播放：请先点击页面任意处再重试";
  else if (/语音包|中文离线|无中文/.test(raw)) lastError = "本机无中文离线语音包且在线音色不可用，请安装 edge-tts 或中文语音包";
  else if (/不可朗读|没有可朗读|无可朗读/.test(raw)) lastError = "没有可朗读的内容";
  else if (/edge-tts|在线音色/.test(raw)) lastError = "在线音色合成失败（网络或服务不可用）";
  else if (/合成失败|合成/.test(raw)) lastError = "合成失败（服务端/引擎）";
  else lastError = raw || "朗读失败";
}

/**
 * 朗读一段文本（进入全局串行队列）。返回本条任务专属 Promise：
 * 开始播放时回调 onStart()；失败时 reject（message 已转为人话）。
 * opts: {rate,volume,voice,engine}
 */
export function enqueueSpeak(text, opts = {}, onStart) {
  const gen = generation;
  const run = queueChain.then(async () => {
    if (gen !== generation || !text) return;
    loadingCount++; lastError = ""; emit();
    let done = false;
    const dec = () => { if (!done) { done = true; loadingCount--; emit(); } };
    let r;
    try {
      r = await synthesize(text.slice(0, 4000), opts);
      if (gen !== generation) { dec(); return; }
      dec();
      onStart && onStart();
    } catch (e) {
      dec();
      _speakFail(e);
      emit();
      throw new Error(lastError);
    }
    try {
      await playUrl(r.url, opts.volume);
    } catch (e) {
      _speakFail(e);
      emit();
      throw new Error(lastError);
    } finally {
      if (gen === generation && !currentAudio) emit();
    }
  });
  queueChain = run.catch(() => {});
  return run;
}

/**
 * 分句朗读一段文本（逐句合成播放，进全局串行队列）。
 * 手动朗读长回复时使用：按 ≤200 字分句，避免单次超长合成（也为解决服务端超时误判）；
 * 边读边播，取消只需 stopSpeak()（全局 generation 递增即作废后续句）。
 * cb: {onStart(), onSpeak(part), onDone(), onError(err)}
 */
export function speakText(text, opts = {}, cb = {}) {
  const chunks = splitSentences(cleanForSpeech(String(text || "")), 200);
  if (!chunks.length) {
    const e = new Error("没有可朗读的内容");
    lastError = e.message; emit();
    cb.onError && cb.onError(e);
    return Promise.reject(e);
  }
  cb.onStart && cb.onStart();
  const gen = generation;
  let i = 0;
  return new Promise((resolve, reject) => {
    const step = () => {
      // 数量枚举完，或已调用 stopSpeak（generation 递增）→ 结束本轮
      if (gen !== generation || i >= chunks.length) { cb.onDone && cb.onDone(); resolve(); return; }
      const part = chunks[i++];
      enqueueSpeak(part, opts, () => cb.onSpeak && cb.onSpeak(part))
        .then(step)
        .catch((e) => {
          if (gen !== generation) { cb.onDone && cb.onDone(); resolve(); return; }
          cb.onError && cb.onError(e);
          reject(e);
        });
    };
    step();
  });
}

/** 停止当前朗读并清空队列；返回是否真的有播放被打断 */
export function stopSpeak() {
  const had = !!currentAudio;
  generation += 1;
  if (currentAudio) {
    try { currentAudio.pause(); } catch {}
    currentAudio = null;
    // 解除当前挂起的 playUrl，避免队列因 onended 不触发而永久卡死
    const r = currentResolve;
    currentResolve = null;
    if (r) r();
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

// ── 测试出声：直接用浏览器播一段 440Hz 提示音（不依赖后端，验证 浏览器→扬声器 链路）──
export function playTestTone(seconds = 0.6) {
  return new Promise((resolve, reject) => {
    try {
      const sr = 8000, n = Math.floor(sr * seconds);
      const buf = new ArrayBuffer(44 + n * 2);
      const dv = new DataView(buf);
      const ws = (o, t) => { for (let i = 0; i < t.length; i++) dv.setUint8(o + i, t.charCodeAt(i)); };
      ws(0, "RIFF"); dv.setUint32(4, 36 + n * 2, true); ws(8, "WAVEfmt ");
      dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
      dv.setUint32(24, sr, true); dv.setUint32(28, sr * 2, true); dv.setUint16(32, 2, true);
      dv.setUint16(34, 16, true); ws(36, "data"); dv.setUint32(40, n * 2, true);
      for (let i = 0; i < n; i++) dv.setInt16(44 + i * 2, Math.round(12000 * Math.sin((2 * Math.PI * 440 * i) / sr)), true);
      const url = URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
      const a = new Audio(url);
      a.onended = () => { URL.revokeObjectURL(url); resolve(true); };
      a.onerror = () => reject(new Error("播放失败"));
      a.play().catch(reject);
    } catch (e) { reject(e); }
  });
}
