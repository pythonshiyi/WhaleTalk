// ── 消息不可变更新回归（流式热路径）──
// 背景：ChatPage 曾直接修改已进入 state 的消息对象（msg.think += …、
// msg.tools.push(…)、card.status = "done"），再靠复制数组触发重渲染——既违反
// React 不可变约定（加 React.memo 后流式内容会静默停更），又让长会话每帧全量
// 重渲染。现统一走 msgUpdates.js 的不可变更新。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { findLastToolCard, makePatchLast } from "../src/msgUpdates.js";

// 最小 updateMsgs 替身：同步执行变换并记录，模拟 ChatPage 的 setMsgs + 实时镜像
function makeUpdateMsgs() {
  const calls = [];
  let mirror = [];
  const updateMsgs = (fn) => {
    calls.push(fn);
    mirror = typeof fn === "function" ? fn(mirror) : fn;
    return mirror;
  };
  return { updateMsgs, calls, get: () => mirror, set: (v) => { mirror = v; } };
}

describe("makePatchLast 不可变更新", () => {
  it("替换最后一条且不修改原数组/原对象", () => {
    const { updateMsgs, get, set } = makeUpdateMsgs();
    const m0 = { role: "user", text: "hi" };
    const m1 = { role: "assistant", text: "a", streaming: true };
    const arr = [m0, m1];
    set(arr);

    const patchLast = makePatchLast(updateMsgs);
    const out = patchLast((x) => ({ ...x, text: "ab" }));

    assert.notEqual(out, arr, "应产生新数组");
    assert.equal(out.length, 2);
    assert.equal(out[1].text, "ab");
    assert.equal(out[1].streaming, true);
    // 前一条保持同一引用（memo 才能跳过重渲染）
    assert.equal(out[0], m0);
    // 原对象未被修改
    assert.equal(m1.text, "a");
    assert.equal(arr[1].text, "a");
    // 实时镜像与返回值一致
    assert.equal(get(), out);
  });

  it("空列表安全返回", () => {
    const { updateMsgs, set, get } = makeUpdateMsgs();
    set([]);
    const out = makePatchLast(updateMsgs)((x) => ({ ...x, text: "x" }));
    assert.deepEqual(out, []);
    assert.equal(get(), out);
  });

  it("patchFn 返回同一引用 → 不产生新数组（可完全跳过重渲染）", () => {
    const { updateMsgs, set, get } = makeUpdateMsgs();
    const arr = [{ role: "assistant", text: "a" }];
    set(arr);
    const out = makePatchLast(updateMsgs)((x) => x);
    assert.equal(out, arr, "应原样返回同一数组引用");
    assert.equal(get(), arr);
  });

  it("连续增量累积到同一条消息", () => {
    const { updateMsgs, set, get } = makeUpdateMsgs();
    set([{ role: "assistant", think: "", text: "", tools: [] }]);
    const patchLast = makePatchLast(updateMsgs);
    patchLast((x) => ({ ...x, text: x.text + "你" }));
    patchLast((x) => ({ ...x, text: x.text + "好" }));
    patchLast((x) => ({ ...x, think: x.think + "想" }));
    assert.equal(get()[0].text, "你好");
    assert.equal(get()[0].think, "想");
  });

  it("工具卡片以替换方式落地（不原地改旧卡片对象）", () => {
    const { updateMsgs, set, get } = makeUpdateMsgs();
    const card = { tool: "read_file", args: {}, status: "running" };
    set([{ role: "assistant", text: "", tools: [card] }]);
    const patchLast = makePatchLast(updateMsgs);
    patchLast((x) => ({
      ...x,
      tools: x.tools.map((t) => (t === card ? { ...t, status: "done", result: "ok" } : t)),
    }));
    assert.equal(get()[0].tools[0].status, "done");
    assert.equal(card.status, "running", "旧卡片对象不应被修改");
    assert.notEqual(get()[0].tools[0], card);
  });
});

describe("findLastToolCard 工具卡片定位", () => {
  const tools = [
    { tool: "search_web", status: "done" },
    { tool: "read_file", status: "running" },
    { tool: "search_web", status: "running" },
    { tool: "read_file", status: "done" },
  ];

  it("取最后一张匹配的卡片（同名并发时取最近）", () => {
    assert.equal(findLastToolCard(tools, "search_web", "running"), 2);
    assert.equal(findLastToolCard(tools, "read_file", "done"), 3);
  });

  it("无匹配返回 -1", () => {
    assert.equal(findLastToolCard(tools, "write_file", "running"), -1);
    assert.equal(findLastToolCard(tools, "search_web", "pending"), -1);
  });

  it("容忍空值", () => {
    assert.equal(findLastToolCard(null, "x", "running"), -1);
    assert.equal(findLastToolCard(undefined, "x", "running"), -1);
    assert.equal(findLastToolCard([], "x", "running"), -1);
    assert.equal(findLastToolCard([null, {}], "x", "running"), -1);
  });
});
