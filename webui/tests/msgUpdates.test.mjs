// ── 消息不可变更新回归（流式热路径）──
// 背景：ChatPage 曾直接修改已进入 state 的消息对象（msg.think += …、
// msg.tools.push(…)、card.status = "done"），再靠复制数组触发重渲染——既违反
// React 不可变约定（加 React.memo 后流式内容会静默停更），又让长会话每帧全量
// 重渲染。现统一走 msgUpdates.js 的不可变更新。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { findLastToolCard, findToolCard, makePatchLast, trimHistory, capLiveWindow } from "../src/msgUpdates.js";

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

describe("findToolCard 按 tool_call_id 精确配对", () => {
  it("优先按 id 命中（同名并发不再串卡片）", () => {
    const tools = [
      { id: "call_a", tool: "read_file", status: "running" },
      { id: "call_b", tool: "read_file", status: "running" },
    ];
    assert.equal(findToolCard(tools, { id: "call_a", name: "read_file", status: "running" }), 0);
    assert.equal(findToolCard(tools, { id: "call_b", name: "read_file", status: "running" }), 1);
  });

  it("无 id 或缺配时退化为名字+状态", () => {
    const tools = [{ tool: "read_file", status: "running" }];
    assert.equal(findToolCard(tools, { name: "read_file", status: "running" }), 0);
    assert.equal(findToolCard(tools, { id: "nope", name: "read_file", status: "running" }), 0);
    assert.equal(findToolCard(tools, { id: "nope", name: "write_file", status: "running" }), -1);
  });
});

describe("trimHistory 回合边界裁剪（防长任务失忆）", () => {
  // 构造一轮工具密集的回合：1 user + 1 assistant(tool_calls) + 89 tool
  function heavyTurn(tag, toolCount) {
    const tcs = [];
    const tools = [];
    for (let i = 0; i < toolCount; i++) {
      tcs.push({ id: `call_${i}`, type: "function", function: { name: "read_file", arguments: "{}" } });
      tools.push({ role: "tool", tool_call_id: `call_${i}`, name: "read_file", content: `${tag}-${i}` });
    }
    return [
      { role: "user", content: `任务${tag}` },
      { role: "assistant", content: "", tool_calls: tcs },
      ...tools,
    ];
  }

  it("单回合超过条数上限时仍完整保留（不腰斩）", () => {
    const chain = heavyTurn("A", 89);
    const out = trimHistory(chain, { maxMessages: 80, maxChars: 1e9 });
    assert.equal(out.length, 91, "整轮（含 user 指令）必须完整保留");
    assert.equal(out[0].role, "user");
    assert.equal(out[0].content, "任务A");
    assert.equal(out[out.length - 1].role, "tool");
  });

  it("绝不从 tool/assistant 片段中间开头，且保留原始任务锚点", () => {
    const old = heavyTurn("OLD", 10);
    const cur = heavyTurn("CUR", 10);
    const out = trimHistory([...old, ...cur], { maxMessages: 12, maxChars: 1e9 });
    // 首条是补回的原始任务锚点，其后是完整回合（都以 user 开头）
    assert.equal(out[0].role, "user");
    assert.equal(out[0].content, "任务OLD", "原始任务应作为锚点保留");
    assert.equal(out[1].role, "user", "窗口必须以 user 回合起点开头");
    assert.equal(out[1].content, "任务CUR");
    assert.equal(out.length, 13);
  });

  it("预算充足时纳入更早的完整回合", () => {
    const old = heavyTurn("OLD", 2);
    const cur = heavyTurn("CUR", 2);
    const out = trimHistory([...old, ...cur], { maxMessages: 100, maxChars: 1e9 });
    assert.equal(out.length, 8);
    assert.equal(out[0].content, "任务OLD");
  });

  it("字符预算超限时同样按回合边界回退，并保留原始任务锚点", () => {
    const old = heavyTurn("OLD", 5);
    const cur = heavyTurn("CUR", 5);
    const out = trimHistory([...old, ...cur], { maxMessages: 10000, maxChars: 20 });
    assert.equal(out[0].role, "user");
    assert.equal(out[0].content, "任务OLD", "原始任务锚点");
    assert.equal(out[1].role, "user");
    assert.equal(out[1].content, "任务CUR");
  });

  it("最后一个回合超预算也保底保留（宁可超也丢不得指令）", () => {
    const chain = heavyTurn("BIG", 50).map((m) => ({ ...m, content: "x".repeat(100) }));
    const out = trimHistory(chain, { maxMessages: 1, maxChars: 1 });
    assert.equal(out[0].role, "user");
    assert.ok(out.length > 1);
  });
});

// ── capLiveWindow：长会话活动消息上界（只在回合边界切，不改对象）──
describe("capLiveWindow 活动消息上界", () => {
  const mk = (n) => {
    // n 个回合，每回合 1 user + 1 assistant
    const out = [];
    for (let i = 0; i < n; i++) {
      out.push({ role: "user", text: "u" + i });
      out.push({ role: "assistant", text: "a" + i });
    }
    return out;
  };

  it("回合数未超上限时原样返回（dropped=0）", () => {
    const arr = mk(3);
    const r = capLiveWindow(arr, { maxTurns: 10 });
    assert.equal(r.dropped, 0);
    assert.equal(r.msgs, arr, "未超限应返回同一引用");
  });

  it("超上限只保留最近 N 回合，且从 user 开始", () => {
    const arr = mk(10);
    const r = capLiveWindow(arr, { maxTurns: 3 });
    assert.ok(r.dropped > 0);
    assert.equal(r.msgs[0].role, "user", "必须从回合起点开始");
    assert.equal(r.msgs.length, 6, "3 回合 × 2 条");
    assert.equal(r.msgs[0].text, "u7");
    assert.equal(r.msgs[r.msgs.length - 1].text, "a9");
  });

  it("保留正文对象引用不变（不破坏 memo）", () => {
    const arr = mk(5);
    const r = capLiveWindow(arr, { maxTurns: 2 });
    const last = arr[arr.length - 1];
    assert.equal(r.msgs[r.msgs.length - 1], last, "同一对象引用");
  });

  it("空数组 / maxTurns<=0 安全", () => {
    assert.deepEqual(capLiveWindow([], { maxTurns: 3 }), { msgs: [], dropped: 0 });
    const arr = mk(5);
    assert.equal(capLiveWindow(arr, { maxTurns: 0 }).dropped, 0);
  });

  it("工具密集的单回合不会被腰斩（同回合内多条 tool 全保留）", () => {
    const mkHeavy = () => {
      const out = [];
      for (let t = 0; t < 5; t++) {
        out.push({ role: "user", text: "u" + t });
        out.push({ role: "assistant", text: "a" + t, tools: [] });
        for (let k = 0; k < 30; k++) out.push({ role: "tool", content: "r" + k });
      }
      return out;
    };
    const arr = mkHeavy();
    const r = capLiveWindow(arr, { maxTurns: 1 });
    assert.equal(r.msgs[0].role, "user");
    // 最后一回合：1 user + 1 assistant + 30 tool = 32 条
    assert.equal(r.msgs.length, 32);
  });
});
