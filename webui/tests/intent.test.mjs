// ── 「意图」回归：预算与授权必须可预测且不污染全局 ──
// 锁定的承诺：
// 1. 非法/负数/超大预算一律归一，UI 永远拿到可渲染的数字；
// 2. 只有设了步数上限才发请求字段——不设就是不设，不能变成 0（0 = 不限）；
// 3. 存储损坏或不可用（隐私模式）时退化为默认，绝不影响发送；
// 4. 授权文案与 mode 一致（task=可执行 / dialog=只回答）。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_INTENT,
  normalizeIntent,
  readIntent,
  writeIntent,
  intentSummary,
  intentToRequest,
} from "../src/intent.js";

function fakeStorage({ throwOnGet = false, throwOnSet = false } = {}) {
  const map = new Map();
  return {
    getItem(k) { if (throwOnGet) throw new Error("SecurityError"); return map.has(k) ? map.get(k) : null; },
    setItem(k, v) { if (throwOnSet) throw new Error("QuotaExceededError"); map.set(k, String(v)); },
    removeItem(k) { map.delete(k); },
    _dump: () => Object.fromEntries(map),
  };
}

describe("normalizeIntent", () => {
  it("非数字/负数 → 0（不限）", () => {
    assert.deepEqual(normalizeIntent({ maxRounds: "abc", timeBudgetMin: -5 }), DEFAULT_INTENT);
    assert.deepEqual(normalizeIntent(null), DEFAULT_INTENT);
  });

  it("小数取整，超大值封顶（防手滑输入 999999999）", () => {
    assert.equal(normalizeIntent({ maxRounds: 7.6 }).maxRounds, 8);
    assert.equal(normalizeIntent({ maxRounds: 1e9 }).maxRounds, 10000);
  });
});

describe("读写", () => {
  it("写入后可读回", () => {
    const st = fakeStorage();
    writeIntent({ maxRounds: 20, timeBudgetMin: 5 }, st);
    assert.deepEqual(readIntent(st), { maxRounds: 20, timeBudgetMin: 5 });
  });

  it("局部更新不丢另一项", () => {
    const st = fakeStorage();
    writeIntent({ maxRounds: 20 }, st);
    writeIntent({ timeBudgetMin: 3 }, st);
    assert.deepEqual(readIntent(st), { maxRounds: 20, timeBudgetMin: 3 });
  });

  it("损坏的 JSON → 默认值，不抛错", () => {
    const st = fakeStorage();
    st.setItem("whaletalk.intent", "{oops");
    assert.deepEqual(readIntent(st), DEFAULT_INTENT);
  });

  it("存储被禁用（读取/写入抛错）时安静退化", () => {
    assert.deepEqual(readIntent(fakeStorage({ throwOnGet: true })), DEFAULT_INTENT);
    assert.doesNotThrow(() => writeIntent({ maxRounds: 5 }, fakeStorage({ throwOnSet: true })));
  });
});

describe("intentToRequest", () => {
  it("未设预算 → 空对象（不能带 0，0 在后端含义是「不限」）", () => {
    assert.deepEqual(intentToRequest({ maxRounds: 0 }), {});
    assert.deepEqual(intentToRequest(DEFAULT_INTENT), {});
  });

  it("设了预算 → 带 camelCase 键（由 api 层转 snake_case）", () => {
    assert.deepEqual(intentToRequest({ maxRounds: 12 }), { maxToolRounds: 12 });
  });
});

describe("intentSummary", () => {
  it("可执行 + 不限步数", () => {
    assert.equal(intentSummary({}, { taskMode: true }), "可执行 · 步数不限");
  });

  it("只回答 + 预算 + 时间提醒", () => {
    assert.equal(
      intentSummary({ maxRounds: 20, timeBudgetMin: 5 }, { taskMode: false }),
      "只回答 · ≤20 步 · 提醒 5 分钟",
    );
  });
});
