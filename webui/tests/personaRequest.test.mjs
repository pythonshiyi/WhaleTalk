import assert from "node:assert/strict";

const store = {};
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
globalThis.location = { search: "" };
store["whaletalk.api.token"] = "test-token";

let captured = null;
globalThis.fetch = async (url, opts) => {
  captured = { url, opts };
  if (String(url).includes("/v1/sessions")) {
    return {
      ok: true,
      status: 200,
      json: async () => ({ id: "s1" }),
    };
  }
  if (String(url).includes("/v1/personas/memory")) {
    return {
      ok: true,
      status: 200,
      json: async () => (opts?.method === "POST"
        ? { ok: true, count: 0 }
        : { persona_id: "p_xiaojing", count: 1, facts: [{ id: "m1", text: "喜欢海边" }] }),
    };
  }
  const done = new TextEncoder().encode('data: {"type": "done"}\n\n');
  let sent = false;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined };
          sent = true;
          return { done: false, value: done };
        },
      }),
    },
  };
};

const { streamChat, saveSession, getPersonaMemory, clearPersonaMemory } = await import("../src/api.js");

const run = async (payload) => {
  captured = null;
  await streamChat(payload, {}, undefined);
  return JSON.parse(captured.opts.body);
};

const body = await run({ messages: [], mode: "dialog", persona_id: "p_xiaojing" });
assert.equal(body.persona_id, "p_xiaojing", "persona_id 应透传到 /v1/chat/stream");

const empty = await run({ messages: [], mode: "dialog" });
assert.equal(empty.persona_id, undefined, "未传 persona_id 时不应写入字段");

const task = await run({ messages: [], mode: "task", persona_id: "" });
assert.equal(task.persona_id, "", "空字符串由调用方决定，api 层不擅自改写");

captured = null;
await saveSession({ messages: [], persona_id: "p_xiaojing" });
const saved = JSON.parse(captured.opts.body);
assert.equal(saved.persona_id, "p_xiaojing", "saveSession 应携带 persona_id");

captured = null;
const mem = await getPersonaMemory("p_xiaojing");
assert.equal(mem.count, 1, "getPersonaMemory 应返回角色记忆");
assert.ok(String(captured.url).includes("/v1/personas/memory?id=p_xiaojing"), "角色记忆查询应带 id");

captured = null;
const cleared = await clearPersonaMemory("p_xiaojing");
assert.equal(cleared.count, 0, "clearPersonaMemory 应清空角色记忆");
assert.equal(JSON.parse(captured.opts.body).action, "clear", "清空动作应透传");

console.log("persona request regression passed");
