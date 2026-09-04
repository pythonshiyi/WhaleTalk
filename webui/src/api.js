// @ts-check
// ── 后端 API 封装 ─────────────────────────────────────
// 同源访问（生产：api_server 服务静态文件；开发：vite proxy 代理到后端 8745，
// 见 vite.config.js）。全部请求走相对路径，无端口硬编码（P2-2）。
// token 自动获取：1) localStorage → 2) URL ?token= → 3) 本机端点 /v1/token 自取。
// 后端不可用时一律抛出带中文描述的异常，由界面明确提示（不提供任何假数据兜底）。
//
// ── 类型契约（P1-4）──────────────────────────────────
// 本文件开启 @ts-check，所有类型以 JSDoc typedef 声明，是「前端字段 ↔ 后端字段」
// 对齐的唯一依据。后端改动字段时**必须**同步更新下方 typedef，否则
// `npm run typecheck`（tsc --noEmit）会直接报错——从根上杜绝 web_search 漏传
// 这类静默失效（v3.8.2 曾发生：streamChat 解构/请求体漏掉该字段）。

const TOKEN_KEY = "whaletalk.api.token";
const REQUEST_TIMEOUT = 15000;

// ── 类型定义 ─────────────────────────────────────────

/**
 * 聊天消息（DeepSeek API 消息链；tools 模式必须完整回传 assistant→tool 对，
 * 缺一环后端 `_sanitize_messages` 会清洗掉，模型将看不到工具结果）。
 * @typedef {Object} ChatMessage
 * @property {"system"|"user"|"assistant"|"tool"} role 消息角色
 * @property {string|null} [content] 正文；assistant 可仅携带 tool_calls（content 为 null）
 * @property {string} [reasoning_content] assistant 的思考增量（前端须一并回传）
 * @property {Array<{id:string, type:"function", function:{name:string, arguments?:string}}>} [tool_calls]
 *   assistant 发起的工具调用
 * @property {string} [tool_call_id] tool 消息回执指向的调用 id（格式 call_0 / call_1 …）
 * @property {string} [name] tool 消息的工具名
 * @property {Array<string>} [images] 会话图片路径/URL（当前模型非视觉时后端自动切视觉模型）
 */

/**
 * SSE 流式事件（POST /v1/chat/stream 的 data 帧）。
 * 字段按事件类型取用：reasoning/content→text；tool_start/tool→name+args(+result)；
 * tool_duration→name+duration；usage→usage 对象；compressed→removed_turns 等；
 * ask/approval→rid/kind/提示语；error→message。
 * @typedef {Object} SSEEvent
 * @property {"reasoning"|"content"|"tool_start"|"tool"|"tool_duration"|"usage"|"compressed"|"ask_request"|"approval_request"|"done"|"error"} type 事件类型
 * @property {string} [text] 增量文本
 * @property {string} [name] 工具名
 * @property {Object} [args] 工具参数
 * @property {Object} [result] 工具结果
 * @property {number} [duration] 工具耗时（秒）
 * @property {{prompt:number, completion:number, cache_hit:number, cache_miss:number}} [usage] token 用量与缓存命中
 * @property {{removed_turns:number, mode:string, archived_path?:string}} [compressed] 上下文压缩信息
 * @property {string} [rid] 审批/询问请求 id（回传 /v1/respond）
 * @property {string} [kind] 审批类别（ask/approval）
 * @property {string} [message] 错误信息
 */

/**
 * streamChat 事件处理器集合。
 * @typedef {Object} StreamHandlers
 * @property {(text:string)=>void} [onReasoning] 思考增量
 * @property {(text:string)=>void} [onContent] 正文增量
 * @property {(ev:SSEEvent)=>void} [onToolStart] 工具开始
 * @property {(ev:SSEEvent)=>void} [onTool] 工具完成（同时后端已写 tasklog/审计）
 * @property {(ev:SSEEvent)=>void} [onToolDuration] 工具耗时
 * @property {(ev:SSEEvent)=>void} [onUsage] 用量与缓存命中（同时后端已累计统计）
 * @property {(ev:SSEEvent)=>void} [onCompressed] 上下文已压缩
 * @property {(ev:SSEEvent)=>void} [onAskRequest] 询问（需 POST /v1/respond 回传）
 * @property {(ev:SSEEvent)=>void} [onApprovalRequest] 审批请求
 * @property {()=>void} [onDone] 正常结束（后端已自动落盘会话）
 * @property {(message:string)=>void} [onError] 错误
 */

/**
 * streamChat 请求参数（与后端 /v1/chat/stream 请求体字段一一对应）。
 * @typedef {Object} StreamChatParams
 * @property {ChatMessage[]} messages 消息链（含历史，tools 模式须完整回传 assistant/tool 对）
 * @property {string} [model] 模型名
 * @property {string} [thinking] 思考档位 none/low/medium/high/max/auto
 * @property {boolean} [toolsEnabled] 是否启用工具（后端 tools_enabled）
 * @property {string} [mode] task / dialog（对话模式）
 * @property {boolean} [web_search] 联网搜索开关（v3.8.1 起对话模式可用）
 * @property {boolean} [quiet_mode] 纯净对话总开关（关闭记忆/自我/大脑注入）
 * @property {string} [continue_prefix] 续写前缀（从该文本继续）
 * @property {string} [session_id] 已有会话 id（携带则后端生成后自动落盘）
 */

/**
 * 指令库条目。
 * @typedef {Object} PromptEntry
 * @property {string} id 唯一 id
 * @property {string} title 标题
 * @property {string} content 指令内容（支持 {{TEXT}}/{{DATE}}/{ASK:} 变量）
 * @property {string} [category] 分类
 * @property {Array<string>} [tags] 标签
 * @property {string} [shortcut] 短命令（输入框 / 触发）
 * @property {boolean} [disabled] 禁用
 */

/**
 * 会话元信息（sessions_index.json 结构）。
 * @typedef {Object} SessionMeta
 * @property {string} id
 * @property {string} name 会话标题
 * @property {string} model 模型名
 * @property {number} [mtime] 修改时间戳
 * @property {number} [size] 文件大小
 * @property {boolean} [pinned] 置顶
 * @property {Array<string>} [tags] 标签
 * @property {boolean} [starred] 收藏
 */

// ── 通用请求 ─────────────────────────────────────────

import { silentWarn } from "./quiet.js";

// ── SSR/测试安全层（P2-3）─────────────────────────────
// api.js 被组件 import，node/SSR 环境没有 localStorage/location/history：
// 一切浏览器全局访问收敛到下方 helper，模块 import 零副作用、函数级降级。
// localStorage 不可用时退化为内存 Map——token 仍在请求期有效，仅不跨刷新持久化。
const _memStore = new Map();

/** @param {string} k @returns {string} */
function _lsGet(k) {
  try {
    if (typeof localStorage === "undefined") return _memStore.get(k) || "";
    return localStorage.getItem(k) || "";
  } catch {
    return _memStore.get(k) || "";
  }
}

/** @param {string} k @param {string} v */
function _lsSet(k, v) {
  try {
    if (typeof localStorage === "undefined") {
      _memStore.set(k, v);
      return;
    }
    localStorage.setItem(k, v);
  } catch {
    _memStore.set(k, v);
  }
}

/** @param {string} k */
function _lsDel(k) {
  try {
    if (typeof localStorage === "undefined") {
      _memStore.delete(k);
      return;
    }
    localStorage.removeItem(k);
  } catch {
    _memStore.delete(k);
  }
}

/** @returns {{search:string, pathname:string}|null} 非浏览器环境返回 null */
function _winLoc() {
  try {
    if (typeof location === "undefined") return null;
    return { search: location.search, pathname: location.pathname };
  } catch {
    return null;
  }
}

/** @param {string} url 静默 replaceState（SSR/隐私模式下 noop） */
function _histReplace(url) {
  try {
    if (typeof history !== "undefined") history.replaceState(null, "", url);
  } catch {
    /* noop */
  }
}

function getBase() {
  // 生产（api_server 同源）与开发（vite proxy → 8745）统一相对路径（P2-2），
  // 无端口硬编码：dev/prod 拓扑一致。
  return "";
}

/** @returns {string} 本地缓存的访问令牌（无则空串） */
function getToken() {
  let t = "";
  try {
    t = _lsGet(TOKEN_KEY);
    if (!t) {
      const loc = _winLoc();
      const m = loc && loc.search.match(/[?&]token=([^&]+)/);
      if (m) {
        t = decodeURIComponent(m[1]);
        _lsSet(TOKEN_KEY, t);
        if (loc) _histReplace(loc.pathname);
      }
    }
  } catch (e) { silentWarn(e, "api"); }
  return t;
}

/** @type {boolean | null} */
let backendOk = null;

async function _selfFetchToken() {
  try {
    const tr = await fetch(`${getBase()}/v1/token`, { signal: AbortSignal.timeout(2500) });
    if (tr.ok) {
      const tj = await tr.json();
      if (tj.token) {
        try {
          _lsSet(TOKEN_KEY, tj.token);
        } catch (e) { silentWarn(e, "api"); }
        return tj.token;
      }
    }
  } catch (e) { silentWarn(e, "api"); }
  return "";
}

/** @returns {Promise<boolean>} 后端是否可用（结果缓存，可用后自动失效重探） */
export async function checkBackend() {
  if (backendOk !== null) return backendOk;
  const tryHealth = async (/** @type {string} */ token) => {
    const r = await fetch(`${getBase()}/health`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(2500),
    });
    return r;
  };
  try {
    let token = getToken();
    let r = await tryHealth(token);
    if (r.status === 401) {
      // token 缺失或失效：自取新 token 重试
      try {
        _lsDel(TOKEN_KEY);
      } catch (e) { silentWarn(e, "api"); }
      token = await _selfFetchToken();
      r = await tryHealth(token);
    }
    backendOk = r.ok;
  } catch {
    backendOk = false;
  }
  return backendOk;
}

/** @returns {Promise<boolean>} 后端当前是否健康（不缓存结果） */
export async function probeBackendHealth() {
  const tryHealth = async (/** @type {string} */ token) => {
    const r = await fetch(`${getBase()}/health`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(2500),
    });
    return r;
  };
  try {
    let token = getToken();
    let r = await tryHealth(token);
    if (r.status === 401) {
      try { _lsDel(TOKEN_KEY); } catch (e) { silentWarn(e, "api"); }
      token = await _selfFetchToken();
      r = await tryHealth(token);
    }
    return r.ok;
  } catch {
    return false;
  }
}

/**
 * 持续心跳：每 interval 秒探测一次，连接状态翻转时回调。
 * @param {number} [interval] 探测间隔毫秒
 * @param {(ok:boolean)=>void} [onChange] 状态翻转回调
 * @returns {()=>void} 停止探测函数
 */
export function watchBackend(interval = 5000, onChange) {
  let alive = true;
  /** @type {boolean|null} */
  let reported = null;   // 最近一次回调给 onChange 的状态(null=未定)
  let fails = 0;         // 连续失败数
  /** @param {boolean} ok */
  const set = (ok) => {
    if (reported === ok) return;
    reported = ok;
    try { onChange(ok); } catch (e) { silentWarn(e, "api"); }
    if (ok) backendOk = null;  // 恢复连接时重置缓存
  };
  const tick = async () => {
    if (!alive) return;
    let ok = false;
    try { ok = await probeBackendHealth(); } catch { ok = false; }
    if (ok) { fails = 0; }
    else { fails++; }
    // 在线基线：首次即定。
    if (reported === null) { set(ok); return; }
    if (ok && reported === false) { set(true); return; }         // 恢复：1 次成功即翻回
    if (!ok && reported !== false && fails >= 2) { set(false); } // 断开：需连续 2 次失败(防瞬断误报)
  };
  tick();
  const iv = setInterval(tick, interval);
  return () => { alive = false; clearInterval(iv); };
}

// 从错误响应里取出服务端的人话（{error} / {detail} / {error:{message}}），取不到给兜底
/** @param {Response} r @param {string} fallback @returns {Promise<string>} */
async function _errMessage(r, fallback) {
  try {
    const j = await r.json();
    if (j && typeof j === "object") {
      if (typeof j.detail === "string" && j.detail) return j.detail;
      if (typeof j.error === "string" && j.error) return j.error;
      if (j.error && typeof j.error === "object" && typeof j.error.message === "string") return j.error.message;
    }
  } catch (e) { silentWarn(e, "api"); }
  return fallback;
}

/**
 * 通用请求（GET 默认；带 body 时须 method:"POST"）。401 自动自取 token 重试一次。
 * @param {string} path
 * @param {RequestInit & {body?: string}} [opts]
 * @returns {Promise<any>} 后端 JSON 响应体
 */
async function api(path, opts = {}) {
  const attempt = async (/** @type {string} */ token) => {
    const r = await fetch(`${getBase()}${path}`, {
      ...opts,
      signal: opts.signal || AbortSignal.timeout(REQUEST_TIMEOUT),
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(opts.headers || {}),
      },
    });
    return r;
  };
  let r;
  try {
    r = await attempt(getToken());
  } catch (e) {
    const why = e && e.name === "TimeoutError" ? "请求超时" : "后端服务未连接";
    throw new Error(why);
  }
  if (r.status === 401) {
    // token 缺失/失效（如启动时预取早于 checkBackend 拿到 token）：自取后重试一次
    try {
      _lsDel(TOKEN_KEY);
    } catch (e) { silentWarn(e, "api"); }
    const token = await _selfFetchToken();
    if (token) r = await attempt(token);
  }
  if (!r.ok) throw new Error(await _errMessage(r, `请求失败（${r.status}）`));
  return r.json();
}

// ── SSE 流式对话 ─────────────────────────────────────

/**
 * 流式对话（POST /v1/chat/stream）。
 * @param {StreamChatParams} params 请求参数（字段与后端请求体一一对应，改动必须同步 typedef）
 * @param {StreamHandlers} handlers 事件回调
 * @param {AbortSignal} [signal] 停止生成信号（前端 AbortController）
 * @returns {Promise<void>} 流结束后 resolve；HTTP 错误时 throw
 */
export async function streamChat({ messages, model, thinking, toolsEnabled, mode, web_search, quiet_mode, continue_prefix, session_id }, handlers, signal) {
  const r = await fetch(`${getBase()}/v1/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify({ messages, model, thinking, tools_enabled: toolsEnabled, mode, web_search, quiet_mode, continue_prefix, session_id }),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`chat/stream → ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, nl);
      buf = buf.slice(nl + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        /** @type {SSEEvent|null} */
        let ev;
        try {
          ev = JSON.parse(line.slice(6));
        } catch {
          continue;
        }
        if (ev.type === "reasoning") handlers.onReasoning?.(ev.text);
        else if (ev.type === "content") handlers.onContent?.(ev.text);
        else if (ev.type === "tool_start") handlers.onToolStart?.(ev);
        else if (ev.type === "tool") handlers.onTool?.(ev);
        else if (ev.type === "tool_duration") handlers.onToolDuration?.(ev);
        else if (ev.type === "usage") handlers.onUsage?.(ev);
        else if (ev.type === "compressed") handlers.onCompressed?.(ev);
        else if (ev.type === "ask_request") handlers.onAskRequest?.(ev);
        else if (ev.type === "approval_request") handlers.onApprovalRequest?.(ev);
        else if (ev.type === "done") handlers.onDone?.();
        else if (ev.type === "error") handlers.onError?.(ev.message);
      }
    }
  }
}

// ── 会话 ─────────────────────────────────────────────

/** @returns {Promise<SessionMeta[]>} 会话列表 */
export async function listSessions() {
  const d = await api("/v1/sessions");
  return d.sessions || [];
}

/**
 * 保存/追加会话（带 session_id 的流式对话结束后后端也会自动落盘）。
 * @param {{id?:string, name?:string, messages:ChatMessage[], model?:string, append?:boolean, stars?:number, pinned?:boolean, tags?:string[]}} p
 * @returns {Promise<string>} 会话 id
 */
export async function saveSession({ id, name, messages, model, append, stars, pinned, tags }) {
  const d = await api("/v1/sessions", {
    method: "POST",
    body: JSON.stringify({
      id,
      name,
      messages,
      model,
      append,
      ...(stars !== undefined ? { stars } : {}),
      ...(pinned !== undefined ? { pinned } : {}),
      ...(tags !== undefined ? { tags } : {}),
    }),
  });
  return d.id;
}

/** @param {string} id @returns {Promise<any>} */
export async function deleteSession(id) {
  return api("/v1/sessions/delete", { method: "POST", body: JSON.stringify({ id }) });
}

/** @param {string[]} ids @returns {Promise<any>} */
export async function deleteSessionsBatch(ids) {
  return api("/v1/sessions/delete_batch", { method: "POST", body: JSON.stringify({ ids }) });
}

/** @param {string} id @param {boolean} pinned @returns {Promise<any>} */
export async function pinSession(id, pinned) {
  return api("/v1/sessions/pin", { method: "POST", body: JSON.stringify({ id, pinned }) });
}

/** @param {string} id @param {string} name @returns {Promise<any>} */
export async function renameSession(id, name) {
  return api("/v1/sessions/rename", { method: "POST", body: JSON.stringify({ id, name }) });
}

/** @param {string} id @param {string[]} tags @returns {Promise<any>} */
export async function tagSession(id, tags) {
  return api("/v1/sessions/tags", { method: "POST", body: JSON.stringify({ id, tags }) });
}

/**
 * 读取会话完整消息。
 * @param {string} id
 * @returns {Promise<{id:string, name:string, messages:ChatMessage[], model:string}>}
 */
export async function getSession(id) {
  return api(`/v1/sessions/${encodeURIComponent(id)}/messages`);
}

/** @returns {Promise<{dir:string, workspace?:string, data?:string}>} 上下文信息 */
export async function getContext() {
  return api("/v1/context");
}

/** @returns {Promise<Object>} 配置全量 */
export async function getConfig() {
  return api("/v1/config");
}

/** @returns {Promise<{mode:string, version:string, model?:string}>} 运行状态 */
export async function getStatus() {
  return api("/v1/status");
}

/** @param {string} mode task | dialog @returns {Promise<any>} */
export async function setMode(mode) {
  return api("/v1/mode", { method: "POST", body: JSON.stringify({ mode }) });
}

/** @returns {Promise<Object>} 能力清单 */
export async function listAbilities() {
  return api("/v1/abilities");
}

/**
 * 回传审批/询问的答案（配合 SSE 的 ask/approval_request）。
 * @param {{rid:string, kind?:string, answer?:any, action?:string, reason?:string}} payload
 * @returns {Promise<any>}
 */
export async function respond(payload) {
  return api("/v1/respond", { method: "POST", body: JSON.stringify(payload) });
}

// ── 指令库 ───────────────────────────────────────────

/** @returns {Promise<PromptEntry[]>} */
export async function getPrompts() {
  const d = await api("/v1/prompts");
  return d.prompts || [];
}

/** @param {PromptEntry} prompt @returns {Promise<any>} */
export async function savePrompt(prompt) {
  return api("/v1/prompts/save", { method: "POST", body: JSON.stringify({ prompt }) });
}

/** @param {string} id @returns {Promise<any>} */
export async function deletePrompt(id) {
  return api("/v1/prompts/delete", { method: "POST", body: JSON.stringify({ id }) });
}

/** @param {string[]} ids @returns {Promise<any>} */
export async function reorderPrompts(ids) {
  return api("/v1/prompts/reorder", { method: "POST", body: JSON.stringify({ ids }) });
}

/** @param {PromptEntry[]} prompts @param {"merge"|"replace"} [mode] @returns {Promise<any>} */
export async function importPrompts(prompts, mode = "merge") {
  return api("/v1/prompts/import", { method: "POST", body: JSON.stringify({ prompts, mode }) });
}

/** @returns {Promise<PromptEntry[]>} */
export async function exportPrompts() {
  const d = await api("/v1/prompts/export");
  return d.prompts || [];
}

/** @param {string} id @returns {Promise<any>} */
export async function usePrompt(id) {
  return api("/v1/prompts/use", { method: "POST", body: JSON.stringify({ id }) });
}

/** @returns {Promise<any>} 恢复内置指令模板 */
export async function restoreBuiltinPrompts() {
  return api("/v1/prompts/restore_builtin", { method: "POST", body: JSON.stringify({}) });
}

// ── 插件 ─────────────────────────────────────────────

/** @returns {Promise<Array<{slug:string, title:string, description?:string}>>} 已装插件的技能提示词 */
export async function getPluginSkills() {
  const d = await api("/v1/plugin_skills");
  return d.skills || [];
}

// ── 插件中心（画廊 / 市场 / 工坊）─────────────────────

/** @returns {Promise<{installed?:Array<Object>, gallery?:Array<Object>}>} 已装插件与画廊 */
export async function getPlugins() {
  return api("/v1/plugins");
}

/** @param {string} name @returns {Promise<Object>} 插件详情 */
export async function getPluginDetail(name) {
  return api(`/v1/plugins/${encodeURIComponent(name)}`);
}

/** @param {string} name @param {"install"|"uninstall"|"enable"|"disable"} action
 * @returns {Promise<{ok?:boolean, error?:string}>} */
export async function pluginAction(name, action) {
  return api("/v1/plugins", { method: "POST", body: JSON.stringify({ name, action }) });
}

/** @returns {Promise<{plugins?:Array<Object>, source?:string, error?:string}>} 在线市场索引 */
export async function getPluginMarket() {
  return api("/v1/plugin_market");
}

/** @param {string} name @returns {Promise<{ok?:boolean, error?:string}>} 校验并安装市场插件 */
export async function installMarketPlugin(name) {
  return api("/v1/plugin_market/install", { method: "POST", body: JSON.stringify({ name }) });
}

/** AI 插件设计工坊：生成插件。
 * @param {{description:string, name?:string, type?:string}} spec
 * @returns {Promise<{plugin?:Object, error?:string}>} */
export async function pluginStudioGenerate(spec) {
  return api("/v1/plugin_studio/generate", { method: "POST", body: JSON.stringify(spec) });
}

/** AI 插件设计工坊：校验并安装。
 * @param {Object} plugin @returns {Promise<{ok?:boolean, error?:string}>} */
export async function pluginStudioInstall(plugin) {
  return api("/v1/plugin_studio/install", { method: "POST", body: JSON.stringify({ plugin }) });
}

// ── 目录与文件 ───────────────────────────────────────

/** @returns {Promise<{path:string, entries?:Array<{name:string, type:string, size?:number}>}>} 当前工作目录 */
export async function getDirs() {
  return api("/v1/dir");
}

/** @param {string} path @returns {Promise<any>} 切换工作目录 */
export async function setDir(path) {
  return api("/v1/dir", { method: "POST", body: JSON.stringify({ path }) });
}

/** @param {string} imageB64 @param {string} name @returns {Promise<{path?:string}>} 上传图片 */
export async function uploadImage(imageB64, name) {
  return api("/v1/upload", { method: "POST", body: JSON.stringify({ image: imageB64, name }) });
}

/** @param {string} path @returns {Promise<{content?:string, preview?:string, error?:string}>} 预览文件 */
export async function previewFile(path) {
  return api("/v1/files/preview", { method: "POST", body: JSON.stringify({ path }) });
}

/** @param {string} query @param {Object} [filters] @returns {Promise<Object>} 全局搜索会话/产物 */
export async function searchSessions(query, filters = {}) {
  return api("/v1/search", { method: "POST", body: JSON.stringify({ query, filters }) });
}

// ── 定时任务 ─────────────────────────────────────────

/** @returns {Promise<Object>} 定时任务列表 */
export async function getSchedules() {
  return api("/v1/schedules");
}

/** @param {Object} schedules @returns {Promise<any>} */
export async function saveSchedules(schedules) {
  return api("/v1/schedules", { method: "POST", body: JSON.stringify({ schedules }) });
}

/**
 * FIM 补全（代码中间补全）。
 * @param {string} prompt @param {string} [suffix] @returns {Promise<{text?:string}>}
 */
export async function fimComplete(prompt, suffix = "") {
  return api("/v1/fim", { method: "POST", body: JSON.stringify({ prompt, suffix }) });
}

// ── 自主能力（进化/审批/行为/自我） ──────────────────

/** @returns {Promise<Object>} 进化提案列表 */
export async function getEvolutions() {
  return api("/v1/evolutions");
}
/** @param {string} name @returns {Promise<any>} */
export async function applyEvolution(name) {
  return api("/v1/evolutions/apply", { method: "POST", body: JSON.stringify({ name }) });
}
/** @param {string} name @returns {Promise<any>} */
export async function ignoreEvolution(name) {
  return api("/v1/evolutions/ignore", { method: "POST", body: JSON.stringify({ name }) });
}
/** @param {string} name @returns {Promise<Object>} 单个进化提案详情（差异预览） */
export async function getEvolutionDetail(name) {
  return api(`/v1/evolutions/${encodeURIComponent(name)}`);
}
/** @returns {Promise<Object>} 审批历史 */
export async function getApprovals() {
  return api("/v1/approvals");
}
/** @returns {Promise<Object>} 进化分支列表 */
export async function getEvolveBranches() {
  return api("/v1/evolve_branches");
}
/** @param {string} name @returns {Promise<{diff?:string, files?:string[]}>} 分支详情 */
export async function getEvolveBranchDetail(name) {
  return api("/v1/evolve_branches/detail", { method: "POST", body: JSON.stringify({ name }) });
}
/** @param {string} name @returns {Promise<any>} 合并进化分支（合入权在用户） */
export async function mergeEvolveBranch(name) {
  return api("/v1/evolve_branches/merge", { method: "POST", body: JSON.stringify({ name }) });
}
/** @param {string} name @returns {Promise<any>} 删除进化分支 */
export async function deleteEvolveBranch(name) {
  return api("/v1/evolve_branches/delete", { method: "POST", body: JSON.stringify({ name }) });
}
/** @returns {Promise<Object>} 核心自我状态 */
export async function getSelfProfile() {
  return api("/v1/self_profile");
}
/** @returns {Promise<Object>} 失败模式库 */
export async function getFailures() {
  return api("/v1/failures");
}
/** @returns {Promise<Object>} 任务链日志 */
export async function getTasklog() {
  return api("/v1/tasklog");
}
/** @returns {Promise<Object>} 审计日志 */
export async function getAudit() {
  return api("/v1/audit");
}

// ── 鲸语大脑 ─────────────────────────────────────────

/** @returns {Promise<Object>} 大脑状态（brain_status 30+ 字段） */
export async function getBrain() {
  return api("/v1/brain");
}
/**
 * 大脑操作（mount/unmount/archive/restore/merge/export-key/import-key 等 24 个 action）。
 * @param {{action:string, [k:string]:any}} payload
 * @returns {Promise<{ok:boolean, message:string, data?:any}>}
 */
export async function brainAction(payload) {
  return api("/v1/brain", { method: "POST", body: JSON.stringify(payload) });
}

/** @param {string} [query] @param {number} [limit] @returns {Promise<{items:Array<any>}>} 大脑记忆列表（支持本地检索） */
export async function listBrainMemories(query = "", limit = 100) {
  const q = query ? `&query=${encodeURIComponent(query)}` : "";
  return api(`/v1/brain/memories?limit=${limit}${q}`);
}

/** 大脑记忆增删改（action: add/update/delete）。
 * @param {{action:"add"|"update"|"delete", id?:string, [k:string]:any}} payload
 * @returns {Promise<{ok?:boolean}>} */
export async function brainMemoryAction(payload) {
  return api("/v1/brain/memory", { method: "POST", body: JSON.stringify(payload) });
}

/** @returns {Promise<{facts?:Array<any>}>} 通用长期记忆（大脑未初始化时的回退源） */
export async function getMemory() {
  return api("/v1/memory");
}

/**
 * 统一记忆检索（B1/B2）：跨 memory.json + 大脑 jsonl + knowledge 文档。
 * @param {string} [query] 关键词
 * @param {"all"|"memory.json"|"brain"} [sources] 来源过滤，默认 all
 * @param {number} [limit]
 * @returns {Promise<{ok?:boolean, query?:string, items?:Array<any>, count?:number}>}
 */
export async function searchUnifiedMemories(query = "", sources = "all", limit = 8) {
  const p = new URLSearchParams({ limit: String(limit) });
  if (query) p.set("query", query);
  if (sources !== "all") p.set("sources", sources);
  return api(`/v1/brain/unified-memories?${p.toString()}`);
}

// ── 权限 / 任务 / 态势 ───────────────────────────────

/** @returns {Promise<Object>} 权限与黑名单配置 */
export async function getPermissions() {
  return api("/v1/permissions");
}

/** @param {Object} patch 权限增量（blocked_dirs/shell_blocklist/network_blocklist/approval_actions/blocklist_enabled）
 * @returns {Promise<{ok?:boolean}>} */
export async function savePermissions(patch) {
  return api("/v1/permissions", { method: "POST", body: JSON.stringify(patch) });
}

/** @returns {Promise<{templates?:Array<Object>, playground?:Array<Object>}>} 任务模板与试玩 */
export async function getTasks() {
  return api("/v1/tasks");
}

/** @returns {Promise<Object>} 工作台态势（单一事实源：usage/system/recent/processes/health） */
export async function getSituation() {
  return api("/v1/situation");
}

/** @returns {Promise<Object>} 依赖安装状态（软核心静默安装进度） */
export async function getDeps() {
  return api("/v1/deps");
}

// ── 首次启动引导 ─────────────────────────────────────

/** @returns {Promise<{first_run?:boolean}>} 是否首次启动 */
export async function getFirstRun() {
  return api("/v1/first_run");
}

/** @returns {Promise<any>} 完成首次启动引导 */
export async function completeFirstRun() {
  return api("/v1/first_run/complete", { method: "POST", body: JSON.stringify({}) });
}

/**
 * 安装进度事件（NDJSON 每行一种）。
 * @typedef {{type:"batch_start", total:number} | {type:"item_start", index:number, label:string} | {type:"line", message:string} | {type:"item_done", ok:boolean, index:number, label:string} | {type:"batch_done", ok:boolean, failed:Array<string>} | {type:"error", message:string}} InstallEvent
 */

/**
 * 批量安装依赖（首次启动向导）：NDJSON 流式进度。
 * @typedef {Object} InstallHandlers
 * @property {(ev:{total:number})=>void} [onBatchStart]
 * @property {(ev:{index:number,label:string})=>void} [onItemStart]
 * @property {(message:string)=>void} [onLine]
 * @property {(ev:{ok:boolean,index:number,label:string})=>void} [onItemDone]
 * @property {(ev:{ok:boolean,failed:Array<string>})=>void} [onBatchDone]
 * @property {(message:string)=>void} [onError]
 * @param {string[]} keys
 * @param {InstallHandlers} handlers
 * @returns {Promise<void>}
 */
export async function installMany(keys, handlers) {
  const r = await fetch(`${getBase()}/v1/deps/install_many`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
    body: JSON.stringify({ keys }),
    signal: AbortSignal.timeout(7200000),
  });
  if (!r.ok || !r.body) throw new Error(`install_many → ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      /** @type {InstallEvent} */
      let ev;
      try {
        ev = JSON.parse(line);
      } catch {
        continue;
      }
      if (ev.type === "batch_start") handlers.onBatchStart?.(ev);
      else if (ev.type === "item_start") handlers.onItemStart?.(ev);
      else if (ev.type === "line") handlers.onLine?.(ev.message);
      else if (ev.type === "item_done") handlers.onItemDone?.(ev);
      else if (ev.type === "batch_done") handlers.onBatchDone?.(ev);
      else if (ev.type === "error") handlers.onError?.(ev.message);
    }
  }
}

// ── 文件系统（树/产物操作）────────────────────────────

/** @param {string} [dir] 指定目录时列子目录，缺省返回根/最近产物
 * @returns {Promise<any>} 后端文件树原始结构 */
export async function listFiles(dir) {
  return api(dir ? `/v1/files?dir=${encodeURIComponent(dir)}` : "/v1/files");
}

/** @param {string} path @returns {Promise<any>} 用系统程序打开 */
export async function openFile(path) {
  return api("/v1/files/open", { method: "POST", body: JSON.stringify({ path }) });
}

/** @param {string} path @returns {Promise<any>} 打开所在文件夹 */
export async function openDir(path) {
  return api("/v1/files/opendir", { method: "POST", body: JSON.stringify({ path }) });
}

/** @param {string} path @returns {Promise<{content?:string, truncated?:boolean, error?:string}>} 读文件正文 */
export async function readFile(path) {
  return api("/v1/files/read", { method: "POST", body: JSON.stringify({ path }) });
}

// ── 进程管理 ─────────────────────────────────────────

/** @returns {Promise<Array<any>>} 进程列表 */
export async function listProcesses() {
  return api("/v1/processes");
}

/** @param {string} command @returns {Promise<any>} 启动进程 */
export async function startProcess(command) {
  return api("/v1/processes/start", { method: "POST", body: JSON.stringify({ command }) });
}

/** @param {string} name @returns {Promise<any>} 停止进程 */
export async function stopProcess(name) {
  return api("/v1/processes/stop", { method: "POST", body: JSON.stringify({ name }) });
}

// ── 工具 schema / 调试调用 ───────────────────────────

/** @param {string} name @returns {Promise<any>} 工具 schema（含 parameters） */
export async function getToolSchema(name) {
  return api(`/v1/tools/${encodeURIComponent(name)}`);
}

/** @param {string} name @param {Object} [args] @returns {Promise<{result?:any, error?:string}>} 直接调用工具 */
export async function invokeTool(name, args = {}) {
  return api(`/v1/tools/${encodeURIComponent(name)}/invoke`, { method: "POST", body: JSON.stringify({ args }) });
}

// ── 配置 ─────────────────────────────────────────────

/** @param {Object} patch 配置增量（与 GET /v1/config 同字段集）
 * @returns {Promise<{ok:boolean, error?:string}>} */
export async function saveConfig(patch) {
  return api("/v1/config", { method: "POST", body: JSON.stringify(patch) });
}

/** @returns {Promise<any>} 恢复默认配置（带副作用，仅 POST） */
export async function resetConfig() {
  return api("/v1/config/reset", { method: "POST", body: JSON.stringify({}) });
}

// ── 方案 / 角色 / 模型 ───────────────────────────────

/** @returns {Promise<Object>} 保存的方案列表 */
export async function getProfiles() {
  return api("/v1/profiles");
}

/** @param {string} action save | apply | delete @param {string} name @returns {Promise<any>} */
export async function profileAction(action, name) {
  return api("/v1/profiles", { method: "POST", body: JSON.stringify({ action, name }) });
}

/** @returns {Promise<Object>} 角色定义 */
export async function getRoles() {
  return api("/v1/roles");
}

/** @param {Object} roles @returns {Promise<any>} */
export async function saveRoles(roles) {
  return api("/v1/roles", { method: "POST", body: JSON.stringify({ roles }) });
}

/** @returns {Promise<Array<string>>} 可用模型名 */
export async function getModels() {
  return api("/v1/models");
}

// ── TTS / 声音 ───────────────────────────────────────

/** @returns {Promise<Object>} 可用声音列表 */
export async function getTtsVoices() {
  return api("/v1/tts/voices");
}

/** @param {string} voice @returns {Promise<{ok?:boolean, message?:string}>} 下载 Piper 语音模型 */
export async function downloadPiperVoice(voice) {
  return api("/v1/tts/download_piper", { method: "POST", body: JSON.stringify({ voice }) });
}

// ── 外部服务 / 备份 / 清理 ───────────────────────────

/** @returns {Promise<Object>} 外部服务配置（敏感字段后端加密） */
export async function getServices() {
  return api("/v1/services");
}

/** @param {Object} svc webhooks/im/db/email/agent_mail/image/inbound 分组
 * @returns {Promise<{ok?:boolean}>} */
export async function saveServices(svc) {
  return api("/v1/services", { method: "POST", body: JSON.stringify(svc) });
}

/** @returns {Promise<{backups:Array<any>}>} 备份列表 */
export async function listBackups() {
  return api("/v1/backup");
}

/** @returns {Promise<{ok?:boolean}>} 立即备份 */
export async function createBackup() {
  return api("/v1/backup", { method: "POST", body: JSON.stringify({ action: "create" }) });
}

/** @param {string} name @returns {Promise<{ok?:boolean}>} 删除备份 */
export async function deleteBackup(name) {
  return api("/v1/backup", { method: "POST", body: JSON.stringify({ action: "delete", name }) });
}

/** @returns {Promise<{update?:boolean, latest?:string}>} 版本更新检查 */
export async function getUpdateCheck() {
  return api("/v1/update/check");
}

/** @param {Array<string>} items @returns {Promise<{ok?:boolean, removed?:Array<string>}>} 清理数据 */
export async function cleanupItems(items) {
  return api("/v1/cleanup", { method: "POST", body: JSON.stringify({ items }) });
}

// ── 流程 / 检查点 / 知识 ─────────────────────────────

/** @returns {Promise<{workflows?:Object}>} 流程定义 */
export async function getWorkflows() {
  return api("/v1/workflows");
}

/** @param {Object} workflows @returns {Promise<{ok?:boolean}>} */
export async function saveWorkflows(workflows) {
  return api("/v1/workflows", { method: "POST", body: JSON.stringify({ workflows }) });
}

/** @returns {Promise<Object>} 检查点状态 */
export async function getCheckpoint() {
  return api("/v1/checkpoint");
}

/** @returns {Promise<{ok?:boolean}>} 保存检查点 */
export async function saveCheckpoint() {
  return api("/v1/checkpoint", { method: "POST", body: JSON.stringify({}) });
}

/** @returns {Promise<Object>} 知识库列表 */
export async function getKnowledge() {
  return api("/v1/knowledge");
}

/** @param {string} query @param {number} [top_k] @returns {Promise<{hits?:Array<any>}>} 知识库语义检索（带引用源） */
export async function searchKnowledge(query, top_k = 5) {
  return api("/v1/knowledge/search", { method: "POST", body: JSON.stringify({ query, top_k }) });
}

export { getToken, getBase, api };
