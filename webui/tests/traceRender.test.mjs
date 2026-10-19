// ── 过程时间轴的「真实渲染」回归（SSR）──
// 纯函数测试保证数字对；这里保证**渲染出来的东西**对：
// 汇总数字出现在页面上、失败步骤默认展开、空态有引导。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { renderTracePanel } from "./ssrRender.mjs";

const step = (tool, extra = {}) => ({ tool, status: "done", duration: "1.2s", ...extra });

const TURNS = [
  { taskId: 2, time: "10:02", steps: [step("read_file"), step("web_search")], streaming: false, failed: 0, label: "已完成" },
  { taskId: 5, time: "10:05", steps: [step("write_file"), step("run_python", { status: "failed", result: "错误：boom" })], streaming: false, failed: 1, label: "含失败步骤" },
];

describe("TracePanel 渲染", () => {
  it("无数据 → 空态引导（不是空白面板）", async () => {
    const html = await renderTracePanel({ activity: { turns: [] }, focusTurn: -1 });
    assert.match(html, /tl-empty/);
    assert.match(html, /还没有执行记录/);
  });

  it("有轮次 → 汇总 / 轮次头 / 步骤名都渲染出来", async () => {
    const html = await renderTracePanel({ activity: { turns: TURNS }, focusTurn: -1 });
    assert.match(html, /tl-sum/);
    assert.match(html, /2<\/b><span>轮/, "轮数应为 2");
    assert.match(html, /4<\/b><span>步/, "步数应为 4");
    assert.match(html, /1<\/b><span>失败/, "失败数应为 1");
    assert.match(html, /read_file/);
    assert.match(html, /write_file/);
    assert.match(html, /10:05/, "轮次时间要显示");
  });

  it("失败步骤默认展开：结果文本直接可见，不用二次点击", async () => {
    const html = await renderTracePanel({ activity: { turns: TURNS }, focusTurn: -1 });
    assert.match(html, /tl-step-fail/);
    assert.match(html, /错误：boom/, "失败原因必须直接可见");
  });

  it("正常步骤默认收起：结果不占版面", async () => {
    const html = await renderTracePanel({
      activity: { turns: [{ taskId: 1, steps: [step("read_file", { result: "OK-内部结果" })] }] },
      focusTurn: -1,
    });
    assert.doesNotMatch(html, /OK-内部结果/);
  });

  it("筛选按钮齐全（全部 / 仅失败 / 执行中）", async () => {
    const html = await renderTracePanel({ activity: { turns: TURNS }, focusTurn: -1 });
    for (const label of ["全部", "仅失败", "执行中"]) {
      assert.ok(html.includes(label), `缺少筛选项 ${label}`);
    }
  });
});
