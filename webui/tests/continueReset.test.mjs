// 回归门禁：续写标记（continueRef）必须在本轮结束时复位。
//
// 历史缺陷：continueRef.current.active 只在「续写成功保存」分支里复位。一旦续写被
// 停止 / 报错 / 切换会话打断，标记永久残留 active=true，之后每次正常发送都被当成
// 续写——不追加用户消息、请求沿用旧 continue 前缀，表现为「输入什么都不显示、
// AI 也收不到，只能点继续」。本用例锁定所有复位点不被删。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(__dirname, "../src");
const jsx = fs.readFileSync(path.join(src, "components/ChatPage.jsx"), "utf8");

const RESET = "continueRef.current = { active: false, idx: -1 }";

function windowFrom(anchor, len = 1600) {
  const i = jsx.indexOf(anchor);
  assert.ok(i >= 0, `未找到锚点：${anchor}`);
  return jsx.slice(i, i + len);
}

describe("continueRef 复位点", () => {
  it("存在续写标记复位语句", () => {
    assert.ok(jsx.includes(RESET), "缺少 continueRef 复位");
  });

  it("onSend（正常发送）复位续写标记", () => {
    assert.ok(windowFrom("const onSend =", 900).includes(RESET), "onSend 未复位续写标记");
  });

  it("onStop（停止生成）复位续写标记", () => {
    assert.ok(windowFrom("const onStop = ()", 1600).includes(RESET), "onStop 未复位续写标记");
  });

  it("onPickSession（切换会话）复位续写标记", () => {
    assert.ok(windowFrom("const onPickSession = async", 700).includes(RESET), "onPickSession 未复位续写标记");
  });

  it("finish/错误/中止路径均复位续写标记", () => {
    const n = jsx.split(RESET).length - 1;
    assert.ok(n >= 6, `复位点过少（${n}），可能被误删`);
    assert.ok(jsx.includes("AbortError") && windowFrom("AbortError", 300).includes(RESET), "AbortError 路径未复位");
  });

  it("effect 启动即一次性消费续写意图（读后清零）", () => {
    const w = windowFrom("const _cont =", 400);
    assert.ok(w.includes("continueRef.current = { active: false, idx: -1 }"), "effect 未一次性消费续写标记");
    assert.ok(w.includes("Number.isInteger(_cont.idx)"), "缺少目标 idx 有效性校验");
  });
});
