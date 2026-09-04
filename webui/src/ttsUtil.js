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

// ── 全局串行播放队列 + 停止 ──
let currentAudio = null;
let currentResolve = null;  // 当前播放的 resolve 句柄，stopSpeak 时调用以解除挂起的 playUrl
let queueChain = Promise.resolve();
let generation = 0;           // stopSpeak 时 +1，令旧队列任务作废
let loadingCount = 0;         // 合成中的任务数（点击后立即有反馈）
let lastError = "";           // 最近一次朗读失败原因
let phase = "idle";           // idle|synth|speak|breath|paused —— 供 UI 呈现自然说话态
let pausedGen = -1;           // 环境打断「暂停」时的 generation（resume 用；>0 表示可续）
let pauseResumeHooks = null;  // 环境暂停/继续的钩子（由 speakText 循环注册）
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

/**
 * 朗读一段文本（单句，串行；供 speakText 内部与单句手动用）。
 * opts: {rate,volume,voice,engine}；phase 驱动到 "synth"(合成) / "speak"(开口)。
 */
export function enqueueSpeak(text, opts = {}, onStart) {
  const gen = generation;
  const run = queueChain.then(async () => {
    if (gen !== generation || !text) return;
    loadingCount++; lastError = ""; _setPhase("synth"); emit();
    let done = false;
    const dec = () => { if (!done) { done = true; loadingCount--; if (!currentAudio) _setPhase("idle"); emit(); } };
    let r;
    try {
      r = await synthesize(text.slice(0, 4000), opts);
      if (gen !== generation) { dec(); return; }
      dec();
      onStart && onStart();
    } catch (e) {
      dec();
      _speakFail(e);
      _setPhase("idle");
      emit();
      throw new Error(lastError);
    }
    try {
      _setPhase("speak");
      await playUrl(r.url, opts.volume);
      if (gen === generation) _setPhase("idle");
    } catch (e) {
      _setPhase("idle");
      _speakFail(e);
      emit();
      throw new Error(lastError);
    }
  });
  queueChain = run.catch(() => {});
  return run;
}

/**
 * 分句朗读一段文本（自然朗读引擎）：边说边读、句间自然呼吸停顿、可环境暂停可续。
 *
 * 比旧版增强：
 * ① 预合成：当前句在播时提前合成下一句（隐藏合成延迟 → 句间不再卡顿）；
 * ② 句间呼吸：按上一句结尾标点给 90-320ms 自然停顿（真人节奏），末句不拖尾；
 * ③ 分层打断：环境声 → pauseSpeak（暂停可续）；新消息/手动 → stopSpeak（永久停）。
 * cb: {onStart(), onSpeak(part), onDone(), onError(err)}
 */
export async function speakText(text, opts = {}, cb = {}) {
  const raw = cleanForSpeech(String(text || ""));
  const chunks = splitSentences(raw, 200);
  if (!chunks.length) {
    const e = new Error("没有可朗读的内容");
    lastError = e.message; _setPhase("idle"); emit();
    cb.onError && cb.onError(e);
    throw e;
  }
  cb.onStart && cb.onStart();
  const gen = ++generation;   // 使旧朗读作废，本轮独占
  const isPaused = () => pausedGen === gen;
  const cache = new Map();    // 本轮预合成缓存（局部，互不干扰）

  // 预合成索引 i 的 url（合成本身不播放），供轮到即播
  const prefetch = (i) => {
    if (i < 0 || i >= chunks.length) return Promise.resolve(null);
    if (!cache.has(i)) {
      cache.set(i,
        synthesize(chunks[i].slice(0, 4000), opts).then((r) => r.url).catch(() => null));
    }
    return cache.get(i);
  };

  try {
    let i = 0;
    await prefetch(0); // 预合成第 0 句，尽早开口
    if (gen !== generation) return;
    while (i < chunks.length) {
      // 环境暂停检查：停在句边界，待 resume 续读（不吞句、不重读）
      if (isPaused()) {
        _setPhase("paused");
        await new Promise((res) => { pauseWaitResolvers.add(res); });
        if (gen !== generation) return;  // 暂停期间被 stop
        _setPhase("synth");
        continue;
      }
      const part = chunks[i];
      // 播当前句时后台预合成下一句 → 隐藏合成延迟，句间不卡顿
      if (i + 1 < chunks.length) prefetch(i + 1);
      const url = cache.get(i) || (await synthesize(part.slice(0, 4000), opts).then((x) => x.url));
      if (gen !== generation) return;
      _setPhase("speak");
      cb.onSpeak && cb.onSpeak(part);
      await playUrlRaw(url, opts.volume);
      if (gen !== generation) return;
      // 句间自然呼吸（末句不拖尾）
      if (i < chunks.length - 1 && !isPaused()) {
        _setPhase("breath");
        await delay(breathMs(part));
        if (gen !== generation) return;
      }
      i++;
    }
    _setPhase("idle");
    cb.onDone && cb.onDone();
  } catch (e) {
    _setPhase("idle");
    if (gen === generation) { _speakFail(e); emit(); cb.onError && cb.onError(new Error(lastError)); }
  } finally {
    cache.clear();
    if (gen === generation) _setPhase("idle");
    if (pausedGen === gen) pausedGen = -1;
  }
}

// 暂停等待集合（speakText 内部控制，暴露 pause/resume 接口）
let pauseWaitResolvers = new Set();
// 流式朗读器的唤醒回调（createStreamSpeaker 注册；resumeSpeak 唤醒它们续读）
const streamWakers = new Set();

/** 环境打断「暂停」：停住当前音频，下次在句边界续读（不重读本句）。 */
export function pauseSpeak() {
  if (phase !== "speak" && phase !== "breath" && phase !== "synth") return;
  if (currentAudio) {
    try { currentAudio.pause(); } catch (e) { silentWarn(e, "ttsUtil"); }
    const r = currentResolve; currentResolve = null;
    // 不立即置空 currentAudio，让 playUrl 的 done 自然结算
    if (r) setTimeout(r, 0);
  }
  pausedGen = pausedGen >= 0 ? pausedGen : generation;
  _setPhase("paused");
}

/** 从环境暂停中恢复：继续朗读（从下一个未读句开始）。 */
export function resumeSpeak() {
  if (phase !== "paused") return;
  pausedGen = -1;
  _setPhase("synth");
  const wakes = [...pauseWaitResolvers];
  pauseWaitResolvers.clear();
  wakes.forEach((res) => res());
  // 唤醒被暂停的流式朗读器（createStreamSpeaker 注册的 wakePlay）
  const ws = [...streamWakers];
  streamWakers.clear();
  ws.forEach((w) => w());
}

/** 停止当前朗读并清空队列（新消息/手动明确打断 → 永久停）；返回是否有播放被打断 */
export function stopSpeak() {
  const had = !!currentAudio || phase !== "idle";
  generation += 1;
  pausedGen = -1;
  if (currentAudio) {
    try { currentAudio.pause(); } catch (e) { silentWarn(e, "ttsUtil"); }
    currentAudio = null;
    const r = currentResolve;
    currentResolve = null;
    if (r) r();
  }
  _setPhase("idle");
  emit();
  return had;
}

/**
 * 从「后端相对 urlPath」直接播放一段音频（不经本模块 synthesize）。
 * 关键：后端音频端点需要 Bearer 鉴权 → 不能 `new Audio(urlPath)`(浏览器带不上头)，
 * 必须像 playUrl 一样先 fetchAudio(带 Authorization) 拿 blob 再播。speakText/流式朗读器预合成用它。
 */
function playUrlRaw(urlPath, volumePct) {
  return playUrl(urlPath, volumePct);
}

// ═══════════ 流式句子自动朗读器（sentence auto）：边说边读、后台预合成、句间无缝 ═══════════
// 解决旧 feedAuto「每句 enqueueSpeak 串行合成→等→播」造成的句句停顿。
// 机制：句子随流式到达 feed() 进队 → 播放器提前把后续句合成好(url) → 轮到即 playUrlRaw 无缝续播，
// 合成延迟被前一句的播放时间掩盖，句间只剩自然呼吸间隙。
function createStreamSpeaker(opts, onSpeak) {
  const gen = ++generation;          // 独占；stopSpeak(++) 即作废本会话
  const items = [];                  // 到达顺序的句子文本
  const urls = new Map();            // index -> 已合成的 url 或 null(失败)
  let head = 0;                      // 已消费(播放或已跳过)的下标
  let synthPos = 0;                  // 已提交合成的下标(不含)
  let playing = false;
  let finished = false;              // feed 不再有新句(finish() 已调)
  let ended = false;                 // 全部播完/被停
  const active = () => gen === generation;
  let wake = null;                   // 播放推进器的唤醒句柄

  const pumpPlay = () => {
    // 环境暂停：停在句边界，待 resume（由 resumeSpeak 触发 wake；不自旋）
    if (pausedGen === gen) { _setPhase("paused"); return; }
    if (playing || ended || !active()) return;
    while (head < items.length) {
      if (urls.has(head)) {
        const u = urls.get(head); head++;
        if (!u) continue;  // 该句合成失败，跳过继续下一句
        playing = true; _setPhase("speak");
        const part = items[head - 1];
        onSpeak && onSpeak(part);
        playUrlRaw(u, opts.volume)
          .then(() => {
            playing = false;
            if (!active()) { ended = true; return; }
            // 环境暂停 → 停在句边界，待 resume 续读
            if (pausedGen === gen) { _setPhase("paused"); wakePlay(); return; }
            if (head < items.length) {
              // 还有下一句 → 句间自然呼吸后无缝续播
              _setPhase("breath");
              delay(breathMs(part)).then(() => {
                if (active()) {
                  if (pausedGen === gen) { _setPhase("paused"); }
                  else { _setPhase("idle"); wakePlay(); }
                }
              });
            } else {
              _setPhase("idle");
              wakePlay();  // 触发 pumpPlay 判断是否 finish
            }
          })
          .catch(() => { playing = false; if (active()) { _setPhase("idle"); wakePlay(); } });
        pumpSynth();  // 播当前句时，把已入队的后续句也预合成（无缝续播的关键）
        return;  // 等当前句播完再由 wakePlay 续
      }
      break;  // 该下标还没合成好，等合成
    }
    if (finished && head >= items.length && !playing && pausedGen !== gen) { _finishSession(); }
  };

  const pumpSynth = () => {
    // 提前合成：一次最多发起 2 个在飞合成，其余等前面完成（防并发过多排队）
    // 保证轮到某句时其 url 通常已就绪 → 无缝续播，不再句句等合成。
    let inflight = 0;
    while (active() && synthPos < items.length && inflight < 2) {
      const i = synthPos;
      if (urls.has(i)) { synthPos++; continue; }  // 已有结果，跳过
      synthPos++;
      inflight++;
      synthesize(items[i].slice(0, 4000), opts)
        .then((r) => { if (active()) { urls.set(i, r.url); wakePlay(); } })
        .catch(() => { if (active()) { urls.set(i, null); wakePlay(); } });
    }
  };

  const wakePlay = () => {
    if (!active()) { streamWakers.delete(wakePlay); return; }  // 已被新朗读作废 → 自清
    if (wake) return; wake = true;
    Promise.resolve().then(() => { wake = false; pumpPlay(); pumpSynth(); });
  };
  streamWakers.add(wakePlay);  // 供 resumeSpeak 唤醒本流式朗读器

  const _finishSession = () => {
    if (ended) return;
    ended = true;
    streamWakers.delete(wakePlay);
    _setPhase("idle");
    if (pausedGen === gen) pausedGen = -1;
    emit();
  };

  return {
    /** 流式到达一个「已定型」句子：立即排入并触发后台合成+播放推进 */
    feed(sentence) {
      if (ended || finished || !active()) return;
      const s = String(sentence || "").trim();
      if (!s) return;
      items.push(s);
      wakePlay();
    },
    /** 流式结束：标记无更多句；把最后已入队但未合成的句子也合完并播完 */
    finish() {
      finished = true;
      wakePlay();
    },
    /** 手动/新消息停止 */
    cancel() { stopSpeak(); },
    gen,
  };
}

export { createStreamSpeaker };

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
