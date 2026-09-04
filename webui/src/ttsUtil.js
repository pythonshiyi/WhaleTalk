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

// ═══════════════ 单一顺序播放引擎（V3 · 确定性、绝不重复、绝不乱序）═══════════════
// 设计铁律：同一时刻只有一条"说话任务"在推进；每条待读文本进 FIFO 后由唯一消费者
// 按序取出——取出即移出队列，绝不回头重读 → 不可能"重复上一句"。
// 停止/暂停是简单标志，只在句子边界检查；不搞并发泵、不搞多套播放器互相抢。
// ---------------------------------------------------------------------------
let speaking = false;          // 是否正有一条任务在推进
let stopSeq = 0;               // 单调递增的"作废代"：stopSpeak 时 +1，旧消费者据此立即退出
let currentSeq = 0;            // 当前消费循环所属代
const idleWaiters = new Set(); // 等当前任务结束的 resolve（speakText 的 promise）
const queued = [];             // 待读队列 [{ text, opts }]

function _pump() {
  if (speaking) return;        // 已有消费循环在跑 → 由它继续；新入队项会被它按序消费
  if (!queued.length) return;
  speaking = true;
  const seq = ++currentSeq;    // 本代
  // 延迟到微任务启动消费者：杜绝任何同步重入 / 栈溢出
  queueMicrotask(async () => {
    try {
      while (queued.length && seq === stopSeq) {
        const item = queued.shift();               // 取出即移出
        if (!item || !item.text) continue;
        _setPhase("synth");
        let url;
        try {
          const r = await synthesize(item.text.slice(0, 4000), item.opts || {});
          url = r && r.url;
        } catch (e) { _speakFail(e); emit(); url = null; }
        if (seq !== stopSeq) break;
        if (!url) continue;                          // 本句合成失败 → 跳过下一句
        if (item.onStart) item.onStart();
        _setPhase("speak");
        try { await playUrl(url, item.opts ? item.opts.volume : 100); }
        catch (e) { _speakFail(e); emit(); }
        if (seq !== stopSeq) break;
        // 若后面还有句 → 极短的自然呼吸，绝不回头
        if (queued.length) {
          _setPhase("breath");
          await delay(90);
        }
      }
    } finally {
      if (seq === currentSeq) speaking = false;   // 仅当代仍是当前代时才复位占用
      _setPhase("idle");
      const ws = [...idleWaiters]; idleWaiters.clear();
      ws.forEach((res) => res());
      emit();
      // 若期间又有新入队（stop 后紧跟 enqueue）→ 延迟由新消费者接管，避免新文本被晾着
      if (queued.length && !speaking) queueMicrotask(_pump);
    }
  });
}

function _enqueueOne(item) {
  queued.push(item);
  // 若当前没有活跃消费者在跑 → 起一个接管这批
  if (!speaking) _pump();
}

/** 朗读一段文本（单句，进全局顺序队列；用于逐句/自动喂入）。不阻塞调用方。 */
export function enqueueSpeak(text, opts = {}, onStart) {
  if (!String(text || "").trim()) return;
  _enqueueOne({ text: String(text).trim(), opts: opts || {}, onStart });
}

/** 返回一个随流式喂入的说话器：feed(定型句)/finish()/cancel()——都走同一顺序队列。 */
export function createStreamSpeaker(opts) {
  return {
    feed(sentence) {
      const s = String(sentence || "").trim();
      if (s) _enqueueOne({ text: s, opts: opts || {} });
    },
    finish() { /* 顺序队列天然等队空即结束；无需额外动作 */ },
    cancel() { stopSpeak(); },
  };
}

/**
 * 分句朗读一段文本（手动/全文）：拆成句后按序进队列，返回在整段读完时 resolve 的 Promise。
 * cb: {onStart,onSpeak,onDone,onError}
 */
export function speakText(text, opts = {}, cb = {}) {
  const chunks = splitSentences(cleanForSpeech(String(text || "")), 200);
  if (!chunks.length) {
    const e = new Error("没有可朗读的内容");
    lastError = e.message; _setPhase("idle"); emit();
    if (cb.onError) cb.onError(e);
    return Promise.reject(e);
  }
  // 手动朗读排队到末尾（不抢占正在的自动朗读，避免清队竞态吞掉本条）
  const donePromise = new Promise((resolve) => { idleWaiters.add(resolve); });
  cb.onStart && cb.onStart();
  for (let i = 0; i < chunks.length; i++) {
    _enqueueOne({
      text: chunks[i], opts: opts || {},
      onStart: () => { if (cb.onSpeak) cb.onSpeak(chunks[i]); },
    });
  }
  // 结束回调挂在 idleWaiters 的同一批 resolve 上（顺序队列队空统一触发）
  donePromise.then(() => { if (cb.onDone) cb.onDone(); });
  return donePromise;
}

/**
 * 环境打断「暂停」：简化为一键停止当前朗读并清队（真人对话：我一开口 AI 即静音）。
 * （不再做"暂停可续"，因其异步 park 是此前卡死/栈溢出的根源；续读由用户再触发即可。）
 */
export function pauseSpeak() { stopSpeak(); }

/** 兼容旧 API：环境"暂停"后没有续读语义，resume 为空操作（要重读请用户再点/再发）。 */
export function resumeSpeak() { /* no-op */ }

/** 停止当前朗读并清空队列（新消息/手动明确打断 → 永久停）。同步生效，后续 enqueue 不受影响。 */
export function stopSpeak() {
  stopSeq += 1;                     // 作废当前消费者（旧循环下次检查即退出，不读残留）
  queued.length = 0;                // 立即清队：旧句一个不剩
  if (currentAudio) {
    try { currentAudio.pause(); } catch (e) { silentWarn(e, "ttsUtil"); }
  }
  // 解除正在播放/合成的 await，让旧消费者尽快退出
  if (currentResolve) { const r = currentResolve; currentResolve = null; r(); }
  _setPhase("idle");
  emit();
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
