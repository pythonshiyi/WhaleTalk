// ── 首屏渲染冒烟（SSR）──
// 存在理由：v3.24.0 曾整页崩溃 —— 时间预算的 useEffect 被插到 `busy` 声明**之前**，
// 依赖数组在渲染期求值 → `Cannot access 'busy' before initialization`。
// 这类「渲染期就抛」的错误在纯函数测试里完全测不到，只有真渲染一次才暴露。
// 注意：SSR 不跑 effect，所以本测试能抓住的正是「渲染期求值」的错误（TDZ、undefined 调用等），
// 抓不到 effect 体内的运行时问题——那需要浏览器，见本仓 CI 的 webui 构建。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { renderChatPage } from "./ssrRender.mjs";

describe("ChatPage 首屏渲染", () => {
  it("最小 props 下不抛错，并渲染出输入框与意图条", async () => {
    const html = await renderChatPage({
      onGoWorkbench: () => {},
      onGoSettings: () => {},
      onApplyDone: () => {},
      onOpenSessionDone: () => {},
      quietMode: false,
      onToggleQuiet: () => {},
      active: true,
    });
    assert.match(html, /chat-scroll/, "会话滚动区应存在");
    assert.match(html, /composer-input/, "输入框应存在");
    assert.match(html, /intent-sum/, "意图条应存在（本轮预算/授权）");
  });

  it("渲染结果不是错误边界兜底页", async () => {
    const html = await renderChatPage({ active: true });
    assert.doesNotMatch(html, /页面出错了/);
  });
});
