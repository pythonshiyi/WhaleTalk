// ── 流式渲染预算回归 ──
// 锁定的承诺：
// 1. 非流式文本永远立刻解析（终态只解析一次，不该被节流拖慢）；
// 2. 便宜的解析（短文本）不节流——保持逐帧刷新，视觉上不能变卡；
// 3. 解析变贵时按耗时自适应拉长间隔，且有上限，不会「越慢越慢到卡死」；
// 4. 间隔单调：耗时越大 → 等得越久（这条保证节流方向不会写反）。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { streamGateDelay, shouldReparse, MIN_PARSE_MS, MAX_GAP_MS } from "../src/renderBudget.js";

describe("streamGateDelay 自适应节流", () => {
  it("便宜解析（< 2ms）不节流，任何时刻都返回 0", () => {
    assert.equal(streamGateDelay(0, 0), 0);
    assert.equal(streamGateDelay(1.9, 0), 0);
    assert.equal(streamGateDelay(MIN_PARSE_MS - 0.1, 5), 0);
  });

  it("已超过预算 → 立刻可解析", () => {
    // 8ms 耗时 → 预算 48ms；已过 60ms 就该放行
    assert.equal(streamGateDelay(8, 60), 0);
  });

  it("未到预算 → 返回剩余等待时间（且小于预算本身）", () => {
    const wait = streamGateDelay(8, 10);
    assert.ok(wait > 0, "应该要求等待");
    assert.ok(wait < 48, `等待时间应小于预算 48ms，实际 ${wait}`);
  });

  it("解析越慢等待越久（上限 MAX_GAP_MS）", () => {
    const a = streamGateDelay(5, 0);
    const b = streamGateDelay(20, 0);
    const c = streamGateDelay(500, 0);          // 极端：单次解析半秒
    assert.ok(a < b, "20ms 的解析应比 5ms 等得久");
    assert.equal(c, MAX_GAP_MS, "极端耗时应被上限截住，否则长文本会卡成 PPT");
  });

  it("非法/负数耗时按 0 处理，不产生 NaN 或负等待", () => {
    for (const v of [undefined, null, NaN, -5, "x"]) {
      const w = streamGateDelay(v, 0);
      assert.ok(Number.isFinite(w) && w >= 0, `耗时 ${String(v)} 产生了非法等待 ${w}`);
    }
  });
});

describe("shouldReparse 判定", () => {
  it("非流式：永远立刻解析", () => {
    assert.equal(shouldReparse({ streaming: false, costMs: 999, elapsedMs: 0 }), true);
  });

  it("流式 + 便宜解析：立刻解析", () => {
    assert.equal(shouldReparse({ streaming: true, costMs: 0.5, elapsedMs: 0 }), true);
  });

  it("流式 + 昂贵解析且刚解析过：等一下再解析", () => {
    assert.equal(shouldReparse({ streaming: true, costMs: 30, elapsedMs: 2 }), false);
  });

  it("流式 + 昂贵解析但已等待足够：放行", () => {
    assert.equal(shouldReparse({ streaming: true, costMs: 30, elapsedMs: 500 }), true);
  });
});
