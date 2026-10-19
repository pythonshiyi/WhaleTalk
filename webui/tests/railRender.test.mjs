// ── 执行过程面板的「真实渲染」回归（SSR）──
// toolRail.test.mjs 只测判据函数；这里是**组件真的渲染出来长什么样**：
// 折叠态必须带 rail-collapsed、带工具名摘要、且不含步骤明细（信息确实被收起）；
// 失败态必须渲染成展开并带失败标记（异常不能藏）。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { renderStepGroup } from "./ssrRender.mjs";

const done = (tool, extra = {}) => ({ tool, status: "done", duration: "1.2s", args: { q: "x" }, result: "ok", ...extra });

describe("StepGroup 渲染", () => {
  it("历史（全部完成）→ 折叠，且头部带步数与工具名摘要", async () => {
    const html = await renderStepGroup({
      steps: [done("read_file"), done("web_search"), done("write_file")],
      streaming: false,
      showMore: false,
    });
    assert.match(html, /rail-collapsed/, "应折叠渲染");
    assert.doesNotMatch(html, /class="step /, "折叠时不应渲染步骤明细");
    assert.match(html, /3 步/, "折叠态必须仍有步数摘要");
    assert.match(html, /read_file/, "折叠态必须显示工具名摘要（不能是信息黑洞）");
    assert.match(html, /aria-expanded="false"/, "展开状态要暴露给无障碍工具");
  });

  it("有失败 → 展开，并带失败标记", async () => {
    const html = await renderStepGroup({
      steps: [done("a"), { tool: "b", status: "failed", duration: "0.3s", result: "错误：boom" }],
      streaming: false,
    });
    assert.match(html, /rail-open/, "失败必须展开");
    assert.match(html, /rail-has-fail/, "失败组要有可见标记");
    assert.match(html, /1 失败/, "失败数量要写出来");
  });

  it("挂载时正在执行 → 展开", async () => {
    const html = await renderStepGroup({
      steps: [done("a"), { tool: "b", status: "running" }],
      streaming: true,
    });
    assert.match(html, /rail-open/);
    assert.match(html, /rail-live/);
  });

  it("空步骤组 → 不渲染任何东西", async () => {
    const html = await renderStepGroup({ steps: [], streaming: false });
    assert.equal(html, "");
  });
});
