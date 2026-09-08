// ── 面向用户选择器 / 询问弹窗 type 归一化回归测试 ──────
// 背景（v3.9.1 修复根因）：后端 SSE 发送 ask_request/approval_request 时，
// 帧内已含 type="ask_request"。而 ChatPage 拼 `{ type:"ask", ...ev }` 会被
// ...ev 里的 type 覆盖成 "ask_request" → ConfirmGate 的 `req.type === "ask"`
// 判 false → 误进 approval 分支 → 响应错发 {allow...} → 后端 _respond 对 ask
// 请求读到空 answer/option 返回"答案不能为空" 400 → AI 永远等不到回答（历史卡死）。
// 本测试锁定：① ChatPage 不得再用会被覆盖的 type 顺序；② ConfirmGate 须同时认
// 语义名与 SSE 原始名(ask_request/approval_request)。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(__dirname, "../src");
const read = (rel) => fs.readFileSync(path.join(src, rel), "utf8");

describe("询问/审批弹窗 type 归一化（防 AI 永久等待）", () => {
  it("ChatPage 不再让 ...ev 覆盖 type（type 必须放展开之后）", () => {
    const chat = read("components/ChatPage.jsx");
    // 禁止 { type:"ask", ...ev }（type 被 ev.type 覆盖）
    assert.equal(/onPrompt\s*\(\{\s*type:\s*["']ask["']\s*,\s*\.\.\.ev\s*\}/.test(chat), false,
      "onAskRequest 不得用 {type:'ask', ...ev}（会被覆盖成 ask_request）");
    // 正确写法：type 在展开之后，语义名恒生效
    assert.equal(/onPrompt\s*&&\s*onPrompt\(\{\s*\.\.\.ev\s*,\s*type:\s*["']ask["']\s*\}/.test(chat), true,
      "onAskRequest 应用 {...ev, type:'ask'}（type 恒为语义名）");
  });

  it("ConfirmGate 同时识别 ask/approval 与 SSE 原始名", () => {
    const cg = read("components/ConfirmGate.jsx");
    assert.equal(/isAsk\s*=\s*.*=== ["']ask["']\s*\|\|\s*.*=== ["']ask_request["']/.test(cg) ||
                  /rt\s*=\s*.*ask_request/.test(cg), true,
      "ConfirmGate 应归一 type 以同时认 ask_request");
    assert.equal(cg.includes("ask_request"), true, "ConfirmGate 应包含 ask_request 识别");
    assert.equal(cg.includes("approval_request"), true, "ConfirmGate 应包含 approval_request 识别");
  });

  it("选择器响应应携带 option（后端 _respond 据此唤醒 AI）", () => {
    const cg = read("components/ConfirmGate.jsx");
    assert.equal(/option:\s*opt/.test(cg), true, "选项点击应发 {id, option, answer}");
    assert.equal(/onRespond\(payload\)/.test(cg), true, "finish 应调用 onRespond");
  });

  it("多选模式：勾选 toggle + 确认才提交 selections（不一点即发）", () => {
    const cg = read("components/ConfirmGate.jsx");
    assert.equal(/multi/.test(cg), true, "ConfirmGate 应支持 multi 多选");
    assert.equal(/selections:\s*sel/.test(cg), true, "多选确认应发 {id, selections}，一次提交多项");
    assert.equal(/toggleSel/.test(cg), true, "多选选项应走 toggle（可勾选/取消勾选）");
    // 单选仍应一点即提交（option 路径保留）
    assert.equal(/option:\s*opt, answer:\s*opt/.test(cg), true, "单选仍保留点一下即提交");
  });
});
