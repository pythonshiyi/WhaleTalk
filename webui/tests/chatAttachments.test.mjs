// 回归门禁：对话附件链路（粘贴图片 / 拖拽文件到对话区）不可回退。
// 历史：输入区只支持「点按钮选单张图片」，既不能粘贴截图，也不能拖拽文件——
// 用户把文件拖进对话区会被浏览器直接打开（默认行为），体验割裂。本用例锁定三处
// 关键接线：Composer（录入+上传）、ChatPage（全局粘贴/拖拽 + 消息链附件字段）、
// Message（用户气泡回显），任何一处被删都会失败。
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

describe("对话附件链路（粘贴 / 拖拽）", () => {
  it("api.js 暴露 uploadFile（任意文件）与 uploadImage（图片）", () => {
    const js = read("api.js");
    assert.match(js, /export async function uploadFile\(/);
    assert.match(js, /export async function uploadImage\(/);
    assert.ok(js.includes("/v1/files/upload"), "缺少通用文件上传端点");
  });

  it("Composer 支持多选、区分图片/文件、缩略图与 addFiles 入口", () => {
    const jsx = read("components/Composer.jsx");
    assert.ok(jsx.includes("addFiles"), "Composer 未暴露 addFiles（粘贴/拖拽统一入口）");
    assert.ok(jsx.includes("multiple"), "文件选择缺少 multiple（多选）");
    assert.ok(jsx.includes("api.uploadFile"), "非图片文件未走 uploadFile");
    assert.ok(jsx.includes('kind === "image"'), "附件未区分图片/文件类型");
    assert.ok(jsx.includes("att-thumb"), "图片附件缺少缩略图");
  });

  it("ChatPage 处理全局粘贴 + 拖拽，附件进入消息链且随会话落盘", () => {
    const jsx = read("components/ChatPage.jsx");
    assert.ok(jsx.includes("onPaste={onPasteFiles}"), "chat-main 未绑定粘贴处理");
    assert.ok(jsx.includes("addEventListener(\"drop\""), "缺少全局拖拽处理");
    assert.ok(jsx.includes("drop-overlay"), "缺少拖拽高亮遮罩");
    assert.ok(jsx.includes("fileRefsBlock"), "非图片附件未生成给模型的路径清单");
    assert.ok(jsx.includes("withAttachRefs"), "消息链未拼接附件引用");
    assert.ok(jsx.includes("files"), "pendingRef/会话保存未携带 files");
  });

  it("Message 回显用户图片缩略图与文件 chip", () => {
    const jsx = read("components/Message.jsx");
    assert.ok(jsx.includes("UserAttachments"), "用户气泡未渲染附件组件");
    assert.ok(jsx.includes("msg.images"), "未读取消息图片路径");
    assert.ok(jsx.includes("msg.files"), "未读取消息文件附件");
  });
});
