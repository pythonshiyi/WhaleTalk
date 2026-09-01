// 回归门禁：白名单请求 UI 已彻底废弃，黑名单主导架构下不再渲染"白名单请求"对话框。
// 覆盖三处关键文件：ConfirmGate.jsx / api.js / ChatPage.jsx，任何一处把"白名单请求"
// 或 permission 类型 SSE 事件加回来都会触发此测试失败。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(__dirname, "../src");

function read(rel) {
  return fs.readFileSync(path.join(src, rel), "utf8");
}

describe("白名单请求 UI 已废弃（黑名单主导）", () => {
  it("ConfirmGate.jsx 不再渲染白名单请求分支", () => {
    const jsx = read("components/ConfirmGate.jsx");
    assert.equal(jsx.includes("白名单请求"), false, "ConfirmGate 仍含'白名单请求'标题字符串");
    assert.equal(/type\s*===\s*["']permission["']/.test(jsx), false, "ConfirmGate 仍存在 permission 类型分支");
    assert.equal(jsx.includes("加入白名单"), false, "ConfirmGate 仍含'加入白名单'提示文案");
    assert.equal(jsx.includes("已加入白名单"), false, "ConfirmGate 仍含'已加入白名单'按钮文案");
    assert.equal(jsx.includes("白名单请求被拒绝"), false, "ConfirmGate 仍含'白名单请求被拒绝'按钮文案");
  });

  it("api.js 不再监听 permission_request SSE 事件", () => {
    const js = read("api.js");
    assert.equal(js.includes("permission_request"), false, "api.js 仍含 permission_request 事件名");
    assert.equal(/onPermissionRequest/.test(js), false, "api.js 仍定义 onPermissionRequest 处理器");
  });

  it("ChatPage.jsx 不再绑定 onPermissionRequest", () => {
    const jsx = read("components/ChatPage.jsx");
    assert.equal(/onPermissionRequest/.test(jsx), false, "ChatPage 仍绑定 onPermissionRequest");
    assert.equal(/type:\s*["']permission["']/.test(jsx), false, "ChatPage 仍触发 permission 类型弹窗");
  });
});