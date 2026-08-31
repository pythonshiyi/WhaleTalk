// ── api.streamChat 请求体透传回归测试 ─────────────────
// 背景（v3.8.2 修复）：v3.8.1 对话模式联网开关的 web_search 参数在
// ChatPage.jsx 已传入 streamChat，但 api.js 的 streamChat 解构列表与
// JSON.stringify 遗漏了 web_search 字段 → 被静默丢弃，后端永远收不到
// 开关状态，模型始终回答「没有联网能力」。本测试锁定 streamChat 的
// 字段透传，防止再次静默丢字段。
import assert from "node:assert/strict";

// ── 浏览器环境 mock（api.js 顶层依赖 localStorage / location）──
const store = {};
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
globalThis.location = { search: "" };

let captured = null; // 最近一次 fetch 调用
const SSE_DONE = new TextEncoder().encode('data: {"type": "done"}\n\n');
const sseChunks = [
  new TextEncoder().encode('data: {"type": "reasoning", "text": "r1"}\n\n'),
  new TextEncoder().encode('data: {"type": "content", "text": "hi"}\n\ndata: {"type": "done"}\n\n'),
];
globalThis.fetch = async (url, opts) => {
  captured = { url, opts };
  const chunks = [...sseChunks];
  return {
    ok: true, status: 200,
    body: {
      getReader: () => ({ read: async () => (chunks.length ? { done: false, value: chunks.shift() } : { done: true, value: undefined }) }),
    },
  };
};

const { streamChat } = await import("../src/api.js");

const run = async (payload) => {
  captured = null;
  const done = [];
  await streamChat(payload, {
    onReasoning: (t) => done.push(["r", t]),
    onContent: (t) => done.push(["c", t]),
    onDone: () => done.push(["d"]),
  });
  return { body: JSON.parse(captured.opts.body), done };
};

let n = 0;
const ok = (cond, name) => {
  n += 1;
  if (!cond) {
    console.error(`✗ [${n}] ${name}`);
    process.exit(1);
  }
  console.log(`✓ [${n}] ${name}`);
};

// 1. 联网开关开启（对话模式）：web_search 必须原样进请求体
{
  const { body } = await run({ messages: [{ role: "user", content: "hi" }], mode: "dialog", web_search: true });
  ok(body.web_search === true, "web_search:true 透传到请求体");
  ok(body.mode === "dialog", "mode 透传");
  ok(body.tools_enabled === undefined, "未传 toolsEnabled 时不带 tools_enabled");
}

// 2. 开关关闭：web_search:false 透传
{
  const { body } = await run({ messages: [], mode: "dialog", web_search: false });
  ok(body.web_search === false, "web_search:false 透传到请求体");
}

// 3. 不传 web_search（任务模式/旧调用方）：保持 undefined 不进请求体（后端走默认 False）
{
  const { body } = await run({ messages: [], mode: "task" });
  ok(body.web_search === undefined, "未传 web_search 时请求体不带该字段");
}

// 4. tools_enabled 字段映射（camelCase → snake_case 历史行为不回归）
{
  const { body } = await run({ messages: [], toolsEnabled: true });
  ok(body.tools_enabled === true, "toolsEnabled → tools_enabled 映射保持");
}

// 5. 流式事件：reasoning/content/done 正常分发（SSE 解析不回退）
{
  const { done } = await run({ messages: [], mode: "dialog", web_search: true });
  ok(done.some((x) => x[0] === "r"), "reasoning 事件已分发");
  ok(done.some((x) => x[0] === "c"), "content 事件已分发");
  ok(done.some((x) => x[0] === "d"), "done 事件正常收尾");
}

// 6. 纯净对话开关：quiet_mode:true 原样进请求体（v3.8.3 防再次静默丢字段）
{
  const { body } = await run({ messages: [], mode: "dialog", quiet_mode: true });
  ok(body.quiet_mode === true, "quiet_mode:true 透传到请求体");
}

// 7. 纯净对话关闭：quiet_mode:false 透传
{
  const { body } = await run({ messages: [], mode: "dialog", quiet_mode: false });
  ok(body.quiet_mode === false, "quiet_mode:false 透传到请求体");
}

// 8. 未传 quiet_mode（旧调用方）：不带该字段，后端走默认 False
{
  const { body } = await run({ messages: [], mode: "task" });
  ok(body.quiet_mode === undefined, "未传 quiet_mode 时请求体不带该字段");
}

// 9. quiet_mode 与 web_search 共存互不影响
{
  const { body } = await run({ messages: [], mode: "dialog", quiet_mode: true, web_search: true });
  ok(body.quiet_mode === true && body.web_search === true, "quiet_mode 与 web_search 同时透传互不覆盖");
}

console.log(`\napi.streamChat 透传测试：${n} 组断言全绿`);
