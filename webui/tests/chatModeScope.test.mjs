// 回归门禁：ChatPage 组件层必须定义 chatMode。
//
// 历史缺陷：组件层（onSend/onForkMsg/onRegenerate/resendLastUser/onFinished）调用
// `buildHistory(base, chatMode)`，但组件作用域里的变量其实叫 `mode`、没有 `chatMode`
// —— `chatMode` 只是 useBackendChat 的入参。于是每次发送都在 onSend 里抛
// `ReferenceError: chatMode is not defined`，表现为「输入/回车/点建议都无任何反应」，
// 而「继续」不走组件层 buildHistory 所以正常。构建与 tsc 都不会报（未定义变量在
// 运行时才抛），故用本用例锁定。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const jsx = fs.readFileSync(path.join(__dirname, "../src/components/ChatPage.jsx"), "utf8");

describe("chatMode 作用域", () => {
  it("组件层定义了 chatMode（= mode）", () => {
    assert.match(jsx, /const chatMode = mode;/, "组件层缺少 const chatMode = mode;（会 ReferenceError）");
  });

  it("组件层不得仅引用未定义的 chatMode", () => {
    // useBackendChat 的入参里有 chatMode，这是允许的；组件层必须有定义
    const defs = (jsx.match(/const chatMode =/g) || []).length;
    assert.ok(defs >= 1, "chatMode 未定义");
  });
});
