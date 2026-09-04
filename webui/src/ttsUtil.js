// ── 语音朗读工具：文本清洗 / 分句 / 合成 / 播放队列 / 停止 ──
import { getToken, getBase, getConfig } from "./api.js";

import { silentWarn } from "./quiet.js";
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
    s = s.replace(p[0], p[1]);
  }
  return s.replace(/^\s*[-+*]\s+/gm, "").replace(/\n{2,}/g, "\n").trim();
}

// 按句末标点切句；长句内部按逗号软切（流式朗读低延迟开播）、无标点超长句硬切。
// 规则：句末标点（。！？；!?\n）优先；段内累积超过 SOFT（40 字）才按逗号/顿号软切，
// 避免短句碎片化；单句上限 limit（默认 200）无标点硬切。
export function splitSentences(text, limit = 200) {
  const SOFT = 40;
  // 按句末标点/换行切成"段"（每个边界段 = 一个完整句 或 句首未完部分）
  const raw = String(text || "").split(/(?<=[。！？；!?\n])\s*/);
  const out = [];
  const emit = (s) => { s = s.trim(); if (s) out.push(s); };
  const pushPart = (part) => {
    // 把一个"段"按 ≤limit 切进 out（优先按逗号软切、否则硬切）
    let p = part;
    while (p.length > limit) {
      let c = p.lastIndexOf("，", limit);
      c = c > 20 ? c + 1 : limit;
      emit(p.slice(0, c));
      p = p.slice(c);
    }
    emit(p);
  };
  for (let seg of raw) {
    seg = seg.trim();
    if (!seg) continue;
    // 段内软切：超 SOFT 的整句按逗号/顿号切开成 ≤limit 的碎片，让流式尽快开播
    const pieces = [];
    let part = seg;
    while (part.length > SOFT) {
      const cut = Math.max(part.lastIndexOf("，", SOFT), part.lastIndexOf(",", SOFT), part.lastIndexOf("、", SOFT));
      if (cut <= 0) break;
      pieces.push(part.slice(0, cut + 1));
      part = part.slice(cut + 1);
    }
    if (part) pieces.push(part);
    for (const pc of pieces) pushPart(pc);
  }
  return out.filter(Boolean);
}

// 流式自动朗读专用：同 splitSentences，但「尾段可能不完整」的判断交给调用方（长尾段也播）
export function splitSentencesForStream(text) {
  return splitSentences(text, 200);
}

// ── 全局朗读状态（单一顺序引擎共享）──
let currentAudio = null;
let currentResolve = null;  // 当前播放的 resolve 句柄，stopSpeak/pauseSpeak 时调用解除挂起的 playUrl
let loadingCount = 0;         // 合成中的任务数（点击后立即有反馈）
let lastError = "";           // 最近一次朗读失败原因
let phase = "idle";           // idle|synth|speak|breath|paused —— 供 UI 呈现自然说话态
const listeners = new Set();  // 状态变化回调 ({speaking,loading,error,phase})

function _setPhase(p) {
  if (phase === p) return;
  phase = p;
  emit();
}

function emit() {
  const st = { speaking: !!currentAudio || phase === "speak", loading: loadingCount > 0, error: lastError, phase };
  listeners.forEach((fn) => { try { fn(st); } catch (e) { silentWarn(e, "ttsUtil"); } });
}

export function onSpeechState(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function isSpeaking() {
  return !!currentAudio || phase === "speak";
}

// 句间自然呼吸停顿（毫秒）：根据前一句结尾的标点给不同停顿，营造真人说话节奏。
// 。和段落 → 稍长；，/、 → 极短；？！…… → 思考感略长；结尾无标点(未完) → 短。
export function breathMs(prevPart) {
  const s = String(prevPart || "");
  const last = s.slice(-1);
  if (!last) return 120;
  if (last === "。" || last === "\n") return 240;
  if (last === "？" || last === "！" || last === "…") return 320;
  if (last === "，" || last === "、") return 90;
  if (last === "；" || last === ";") return 180;
  return 160; // 无标点结尾的未完片段
}

const delay = (ms) => new Promise((res) => setTimeout(res, ms));

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
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return false;
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
      if (avg > 0.3) {  // 用户开口（中低频能量明显）→ 暂停朗读（环境打断；可续）
        pauseSpeak();
      }
    }, 200);
    return true;
  } catch (e) {
    // 失败时回收已创建的资源，避免残留麦克风占用
    disableVoiceInterrupt();
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
  if (bargeInStream) { try { bargeInStream.getTracks().forEach((t) => t.stop()); } catch (e) { silentWarn(e, "ttsUtil"); } }
  if (bargeInCtx) { try { bargeInCtx.close(); } catch (e) { silentWarn(e, "ttsUtil"); } }
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
  } catch (e) { silentWarn(e, "ttsUtil"); }
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
    try { localStorage.removeItem("whaletalk.api.token"); } catch (e) { silentWarn(e, "ttsUtil"); }
    try {
      const tr = await fetch(`${getBase()}/v1/token`, { signal: AbortSignal.timeout(2500) });
      if (tr.ok) {
        const tj = await tr.json();
        if (tj.token) { try { localStorage.setItem("whaletalk.api.token", tj.token); } catch (e) { silentWarn(e, "ttsUtil"); } tok = tj.token; }
      }
    } catch (e) { silentWarn(e, "ttsUtil"); }
    r = await once(tok);
  }
  if (!r.ok) {
    let msg = `合成失败 ${r.status}`;
    try { const j = await r.json(); if (j && j.error) msg = j.error; } catch (e) { silentWarn(e, "ttsUtil"); }
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
    try { localStorage.removeItem("whaletalk.api.token"); } catch (e) { silentWarn(e, "ttsUtil"); }
    try {
      const tr = await fetch(`${getBase()}/v1/token`, { signal: AbortSignal.timeout(2500) });
      if (tr.ok) {
        const tj = await tr.json();
        if (tj.token) { try { localStorage.setItem("whaletalk.api.token", tj.token); } catch (e) { silentWarn(e, "ttsUtil"); } }
      }
    } catch (e) { silentWarn(e, "ttsUtil"); }
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
      try { URL.revokeObjectURL(blobUrl); } catch (e) { silentWarn(e, "ttsUtil"); }
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

// ═══════════════ 整段音频引擎（V4 · 不再"一句一读"，一次合成一大段连续播放）═══════════════
// 之前的错误：按 ≤200 字逐句合成→逐句播放，句间永远有合成间隙 + 竞态 → 卡顿/重复/卡死。
// V4 正解：一次性把整段(≤3900字)合成成一条长音频，用 Audio 从头放到尾(浏览器原生连续)，
// 超过后端 4000 上限才切成少数几大块顺序续播——绝无"每句一停"。
// 用"朗读作废代 speakSeq"保证严格独占：任何时刻只有最新一次朗读在播，其它一律被作废。
// ---------------------------------------------------------------------------
let speakSeq = 0;                 // 全局"朗读作废代"：每开始一段新朗读 +1；旧朗读据此立即停止
const MAX = 3900;                // 单次合成字符上限(后端上限 4000，留余量)
const alive = (seq) => seq === speakSeq;   // 该朗读是否仍是最新(未被新朗读作废)

// 把清洗后的整段切成 ≤MAX 的大块（优先在句末/标点切，尽量少切块）
function chunkWhole(cleanText) {
  const t = String(cleanText || "").trim();
  if (!t) return [];
  if (t.length <= MAX) return [t];
  const out = [];
  let buf = "";
  for (const seg of t.split(/(?<=[。！？；!?\n])/)) {
    if ((buf + seg).length > MAX && buf) { out.push(buf.trim()); buf = ""; }
    buf += seg;
    if (buf.length > MAX * 1.5) {
      out.push(buf.slice(0, MAX).trim());
      buf = buf.slice(MAX);
    }
  }
  if (buf.trim()) out.push(buf.trim());
  return out.filter(Boolean);
}

// 在"只属于代 seq"的前提下按序合成并播放大块；一旦 seq 被新朗读作废立即静音退出。
// 这是唯一真正播音频的地方——同一时刻只会有一条在跑(因为只有持有当前 seq 的能播)。
async function playPieces(seq, pieces, opts, onSpeak) {
  for (let i = 0; i < pieces.length; i++) {
    if (!alive(seq)) return;
    _setPhase("synth");
    let url = null;
    try {
      const r = await synthesize(pieces[i], opts || {});
      url = r && r.url;
    } catch (e) { _speakFail(e); emit(); }
    if (!alive(seq)) return;
    if (!url) continue;
    onSpeak && onSpeak(pieces[i]);
    _setPhase("speak");
    try { await playUrl(url, opts ? opts.volume : 100); }
    catch (e) { _speakFail(e); emit(); }
    if (!alive(seq)) return;
    if (i < pieces.length - 1) { _setPhase("breath"); await delay(110); }  // 大块间极短
  }
}

/** 一次性朗读整段（手动/全文）：独占作废其它朗读，整段(或大块)连续播放，绝不并发叠读。
 *  返回整段播完时 resolve。cb:{onStart,onSpeak,onDone,onError} */
export async function speakText(text, opts = {}, cb = {}) {
  const pieces = chunkWhole(cleanForSpeech(String(text || "")));
  if (!pieces.length) {
    const e = new Error("没有可朗读的内容");
    lastError = e.message; _setPhase("idle"); emit();
    if (cb.onError) cb.onError(e);
    throw e;
  }
  stopSpeak();                    // 先停掉任何在播/排队 → 使本段成为唯一新朗读
  const seq = ++speakSeq;
  cb.onStart && cb.onStart();
  try {
    await playPieces(seq, pieces, opts, (t) => cb.onSpeak && cb.onSpeak(t));
    _setPhase("idle");
    if (alive(seq)) cb.onDone && cb.onDone();
  } catch (e) {
    _setPhase("idle");
    if (alive(seq)) { _speakFail(e); emit(); cb.onError && cb.onError(new Error(lastError)); }
  }
}

/**
 * 自动朗读（sentence 随流式）：一个会话 = 一条独占朗读。流式文本缓冲累积，
 * 积成整块后按序连续播放(同代内多块串行，绝不重入 speakText 造成叠读)。
 * feed(累积)/flush()(立即读缓冲)/finish()(读残尾并结束)。
 */
export function createStreamSpeaker(opts, onSpeak) {
  stopSpeak();                    // 新建自动朗读会话 → 作废旧朗读，独占
  const seq = ++speakSeq;
  let buf = "";
  let idle = true;
  const playWhole = async (text) => {
    const pieces = chunkWhole(cleanForSpeech(String(text || "")));
    if (!pieces.length || !alive(seq)) return;
    idle = false;
    try { await playPieces(seq, pieces, opts, onSpeak); }
    catch (e) { silentWarn(e, "ttsUtil"); }
    idle = true;
    if (buf.trim() && alive(seq)) queueMicrotask(drain);   // 读块期间又有累积 → 继续
  };
  const drain = () => { if (buf.trim() && idle && alive(seq)) { const t = buf.trim(); buf = ""; playWhole(t); } };
  return {
    feed(text) {
      if (!alive(seq)) return;
      buf += String(text || "");
      const b = buf.trim();
      const sentenceEnd = /[。！？；!?\n]$/.test(b);
      if ((sentenceEnd && b.length >= 40) || b.length >= MAX) drain();
    },
    flush() { if (alive(seq)) drain(); },
    finish() { if (alive(seq)) drain(); },
    cancel() { stopSpeak(); },
  };
}

/** 朗读一段文本（单句/小段；兼容旧调用）。 */
export function enqueueSpeak(text, opts = {}, onStart) {
  const s = String(text || "").trim();
  if (!s) return;
  // 丢进一次性整段朗读(不阻塞调用方)
  speakText(s, opts, { onSpeak: () => onStart && onStart() }).catch(() => {});
}

/** 停止当前朗读（新消息/手动明确/环境声 → 作废并静音；下一段新朗读将独占）。 */
export function stopSpeak() {
  speakSeq += 1;                  // 作废所有在播/在合成的朗读代
  if (currentAudio) {
    try { currentAudio.pause(); } catch (e) { silentWarn(e, "ttsUtil"); }
  }
  if (currentResolve) { const r = currentResolve; currentResolve = null; r(); }
  _setPhase("idle");
  emit();
}

/** 环境打断：即停（真人对话：我一开口 AI 就静音）。 */
export function pauseSpeak() { stopSpeak(); }
/** 兼容：环境"暂停"无续读，resume 为空操作。 */
export function resumeSpeak() { /* no-op */ }

// ── 语音设置（服务端 voice_config 的前端缓存）──
let voiceCfg = null;

export function invalidateVoiceConfig() {
  voiceCfg = null;
}

export async function getVoiceConfig(force = false) {
  if (voiceCfg && !force) return voiceCfg;
  const fallback = { auto_mode: "off", rate: 0, volume: 100, voice: "" };
  try {
    const d = await getConfig();
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
      const cleanup = () => URL.revokeObjectURL(url);
      a.onended = () => { cleanup(); resolve(true); };
      a.onerror = () => { cleanup(); reject(new Error("播放失败")); };
      a.play().catch((e) => { cleanup(); reject(e); });
    } catch (e) { reject(e); }
  });
}
