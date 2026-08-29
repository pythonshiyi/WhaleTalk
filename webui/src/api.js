// ── 后端 API 封装 ─────────────────────────────────────
// 同源访问（生产：api_server 服务静态文件；开发：dev server 上直连 8745）。
// token 自动获取：1) localStorage → 2) URL ?token= → 3) 本机端点 /v1/token 自取。
// 后端不可用时一律抛出带中文描述的异常，由界面明确提示（不提供任何假数据兜底）。

const API_PORT = 8745;
const TOKEN_KEY = "whaletalk.api.token";
const REQUEST_TIMEOUT = 15000;

function getBase() {
  // 生产构建由 api_server 同源服务（8745）；开发时跨域直连（CORS 已开）
  return `http://127.0.0.1:${API_PORT}`;
}

function getToken() {
  let t = "";
  try {
    t = localStorage.getItem(TOKEN_KEY) || "";
    if (!t) {
      const m = location.search.match(/[?&]token=([^&]+)/);
      if (m) {
        t = decodeURIComponent(m[1]);
        localStorage.setItem(TOKEN_KEY, t);
        history.replaceState(null, "", location.pathname);
      }
    }
  } catch {}
  return t;
}

let backendOk = null;

async function _selfFetchToken() {
  try {
    const tr = await fetch(`${getBase()}/v1/token`, { signal: AbortSignal.timeout(2500) });
    if (tr.ok) {
      const tj = await tr.json();
      if (tj.token) {
        try {
          localStorage.setItem(TOKEN_KEY, tj.token);
        } catch {}
        return tj.token;
      }
    }
  } catch {}
  return "";
}

export async function checkBackend() {
  if (backendOk !== null) return backendOk;
  const tryHealth = async (token) => {
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
        localStorage.removeItem(TOKEN_KEY);
      } catch {}
      token = await _selfFetchToken();
      r = await tryHealth(token);
    }
    backendOk = r.ok;
  } catch {
    backendOk = false;
  }
  return backendOk;
}

// 周期性断连探测（不缓存结果）。onChange(ok) 在状态翻转时触发；返回停止函数。
export async function probeBackendHealth() {
  const tryHealth = async (token) => {
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
      try { localStorage.removeItem(TOKEN_KEY); } catch {}
      token = await _selfFetchToken();
      r = await tryHealth(token);
    }
    return r.ok;
  } catch {
    return false;
  }
}

// 持续心跳：每 interval 秒探测一次，连接状态翻转时回调；返回取消函数。
export function watchBackend(interval = 5000, onChange) {
  let alive = true;
  let last = null;
  const tick = async () => {
    if (!alive) return;
    let ok = false;
    try { ok = await probeBackendHealth(); } catch { ok = false; }
    if (alive && ok !== last) {
      last = ok;
      try { onChange(ok); } catch {}
      // 恢复连接时重置缓存，让各页面重新拉取真实数据
      if (ok) backendOk = null;
    }
  };
  tick();
  const iv = setInterval(tick, interval);
  return () => { alive = false; clearInterval(iv); };
}

// 从错误响应里取出服务端的人话（{error} / {detail} / {error:{message}}），取不到给兜底
async function _errMessage(r, fallback) {
  try {
    const j = await r.json();
    if (j && typeof j === "object") {
      if (typeof j.detail === "string" && j.detail) return j.detail;
      if (typeof j.error === "string" && j.error) return j.error;
      if (j.error && typeof j.error === "object" && typeof j.error.message === "string") return j.error.message;
    }
  } catch {}
  return fallback;
}

async function api(path, opts = {}) {
  const attempt = async (token) => {
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
      localStorage.removeItem(TOKEN_KEY);
    } catch {}
    const token = await _selfFetchToken();
    if (token) r = await attempt(token);
  }
  if (!r.ok) throw new Error(await _errMessage(r, `请求失败（${r.status}）`));
  return r.json();
}

// ── SSE 流式对话 ─────────────────────────────────────
// handlers: { onReasoning(text), onContent(text), onToolStart({name,args}),
//             onTool({name,args,result}), onUsage(usage), onDone(), onError(msg) }
// signal: AbortController.signal（停止生成）
// session_id: 已有会话继续对话时携带，后端生成完成后自动落盘（前端卸载/断连不丢结果）
export async function streamChat({ messages, model, thinking, toolsEnabled, mode, continue_prefix, session_id }, handlers, signal) {
  const r = await fetch(`${getBase()}/v1/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify({ messages, model, thinking, tools_enabled: toolsEnabled, mode, continue_prefix, session_id }),
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
        else if (ev.type === "permission_request") handlers.onPermissionRequest?.(ev);
        else if (ev.type === "done") handlers.onDone?.();
        else if (ev.type === "error") handlers.onError?.(ev.message);
      }
    }
  }
}

export async function listSessions() {
  const d = await api("/v1/sessions");
  return d.sessions || [];
}

export async function saveSession({ id, name, messages, model, append }) {
  const d = await api("/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ id, name, messages, model, append }),
  });
  return d.id;
}

export async function deleteSession(id) {
  return api("/v1/sessions/delete", { method: "POST", body: JSON.stringify({ id }) });
}

export async function deleteSessionsBatch(ids) {
  return api("/v1/sessions/delete_batch", { method: "POST", body: JSON.stringify({ ids }) });
}

export async function pinSession(id, pinned) {
  return api("/v1/sessions/pin", { method: "POST", body: JSON.stringify({ id, pinned }) });
}

export async function renameSession(id, name) {
  return api("/v1/sessions/rename", { method: "POST", body: JSON.stringify({ id, name }) });
}

export async function tagSession(id, tags) {
  return api("/v1/sessions/tags", { method: "POST", body: JSON.stringify({ id, tags }) });
}

export async function getSession(id) {
  return api(`/v1/sessions/${encodeURIComponent(id)}/messages`);
}

export async function getContext() {
  return api("/v1/context");
}

export async function getConfig() {
  return api("/v1/config");
}

export async function getStatus() {
  return api("/v1/status");
}

export async function setMode(mode) {
  return api("/v1/mode", { method: "POST", body: JSON.stringify({ mode }) });
}

export async function listAbilities() {
  return api("/v1/abilities");
}

export async function respond(payload) {
  return api("/v1/respond", { method: "POST", body: JSON.stringify(payload) });
}

export async function getPrompts() {
  const d = await api("/v1/prompts");
  return d.prompts || [];
}

export async function savePrompt(prompt) {
  return api("/v1/prompts/save", { method: "POST", body: JSON.stringify({ prompt }) });
}

export async function deletePrompt(id) {
  return api("/v1/prompts/delete", { method: "POST", body: JSON.stringify({ id }) });
}

export async function reorderPrompts(ids) {
  return api("/v1/prompts/reorder", { method: "POST", body: JSON.stringify({ ids }) });
}

export async function importPrompts(prompts, mode = "merge") {
  return api("/v1/prompts/import", { method: "POST", body: JSON.stringify({ prompts, mode }) });
}

export async function exportPrompts() {
  const d = await api("/v1/prompts/export");
  return d.prompts || [];
}

export async function usePrompt(id) {
  return api("/v1/prompts/use", { method: "POST", body: JSON.stringify({ id }) });
}

export async function restoreBuiltinPrompts() {
  return api("/v1/prompts/restore_builtin", { method: "POST", body: JSON.stringify({}) });
}

export async function getPluginSkills() {
  const d = await api("/v1/plugin_skills");
  return d.skills || [];
}

export async function getDirs() {
  return api("/v1/dir");
}

export async function setDir(path) {
  return api("/v1/dir", { method: "POST", body: JSON.stringify({ path }) });
}

export async function uploadImage(imageB64, name) {
  return api("/v1/upload", { method: "POST", body: JSON.stringify({ image: imageB64, name }) });
}

export async function previewFile(path) {
  return api("/v1/files/preview", { method: "POST", body: JSON.stringify({ path }) });
}

export async function searchSessions(query, filters = {}) {
  return api("/v1/search", { method: "POST", body: JSON.stringify({ query, filters }) });
}

export async function getSchedules() {
  return api("/v1/schedules");
}

export async function saveSchedules(schedules) {
  return api("/v1/schedules", { method: "POST", body: JSON.stringify({ schedules }) });
}

export async function fimComplete(prompt, suffix = "") {
  return api("/v1/fim", { method: "POST", body: JSON.stringify({ prompt, suffix }) });
}

export { getToken, getBase, api };