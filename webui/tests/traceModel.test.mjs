// ── 时间轴数据整理回归 ──
// 过程面板的价值在于「可回放、可筛选、可定位」，这些都建立在数据整理的准确性上：
// 统计错了，用户据此判断「这次任务是不是出问题了」就会跟着错。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { stepSeconds, stepsSummary, traceSummary, filterTurns, fmtSeconds } from "../src/traceModel.js";

const step = (tool, extra = {}) => ({ tool, status: "done", duration: "1.0s", ...extra });

describe("stepSeconds", () => {
  it("解析 '1.2s' / 1200ms / 数字", () => {
    assert.equal(stepSeconds({ duration: "1.2s" }), 1.2);
    assert.equal(stepSeconds({ duration: "1200ms" }), 1200);   // 只取数字：单位由后端语义决定
    assert.equal(stepSeconds({ duration: 3 }), 3);
  });

  it("缺失/非法 → 0，不产生 NaN", () => {
    for (const v of [undefined, null, {}, { duration: "" }, { duration: "—" }, { duration: "abc" }]) {
      assert.equal(stepSeconds(v), 0);
    }
  });
});

describe("stepsSummary", () => {
  it("统计步数 / 完成 / 失败 / 执行中 / 总耗时", () => {
    const s = stepsSummary([
      step("a"), step("b", { status: "failed", duration: "0.5s" }),
      step("c", { status: "running", duration: "2.0s" }),
    ]);
    assert.deepEqual(s, { count: 3, done: 1, failed: 1, running: 1, seconds: 3.5 });
  });

  it("忽略空条目与缺 tool 的项（脏数据不参与统计）", () => {
    assert.equal(stepsSummary([null, {}, { tool: "" }, step("x")]).count, 1);
    assert.equal(stepsSummary(null).count, 0);
  });
});

describe("traceSummary", () => {
  it("跨轮汇总", () => {
    const sum = traceSummary([
      { taskId: 1, steps: [step("a"), step("b", { status: "failed" })] },
      { taskId: 2, steps: [step("c")] },
    ]);
    assert.equal(sum.turns, 2);
    assert.equal(sum.count, 3);
    assert.equal(sum.failed, 1);
    assert.equal(sum.seconds, 3);
  });
});

describe("filterTurns", () => {
  const turns = [
    { taskId: 1, steps: [step("a")] },
    { taskId: 2, steps: [step("b", { status: "failed" })] },
    { taskId: 3, steps: [step("c", { status: "running" })], streaming: true },
  ];

  it("all 返回全部（且是原对象，保证 taskId 仍可定位消息）", () => {
    const r = filterTurns(turns, "all");
    assert.equal(r.length, 3);
    assert.equal(r[0], turns[0]);
  });

  it("failed 只保留含失败的轮次", () => {
    assert.deepEqual(filterTurns(turns, "failed").map((t) => t.taskId), [2]);
  });

  it("active 只保留执行中的轮次（含 streaming 但暂无 running 步骤）", () => {
    assert.deepEqual(filterTurns(turns, "active").map((t) => t.taskId), [3]);
    assert.deepEqual(filterTurns([{ taskId: 9, steps: [], streaming: true }], "active").length, 1);
  });

  it("未知模式按全部处理（前端状态出错时不至于白屏）", () => {
    assert.equal(filterTurns(turns, "wat").length, 3);
    assert.equal(filterTurns(null).length, 0);
  });
});

describe("fmtSeconds", () => {
  it("60s 以下用秒，以上用 分m秒s", () => {
    assert.equal(fmtSeconds(0), "");
    assert.equal(fmtSeconds(12.34), "12.3s");
    assert.equal(fmtSeconds(83), "1m23s");
    assert.equal(fmtSeconds(600), "10m00s");
  });
});
