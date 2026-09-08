// ── formatToolResult 前端工具结果归一回归测试 ──
// 背景：工具结果若以 JS 对象漏到前端，直接渲染/拼接会变成 "[object Object]"
// （get_status 等历史问题）。formatToolResult 兜底把对象转成 JSON 文本。
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import formatToolResult from "../src/formatToolResult.js";

describe("formatToolResult 工具结果归一", () => {
  it("字符串原样返回", () => {
    assert.equal(formatToolResult("完成"), "完成");
    assert.equal(formatToolResult(""), "");
  });
  it("对象 → 缩进 JSON（不再 [object Object]）", () => {
    const out = formatToolResult({ mode: "task", budget: "¥12.5" });
    assert.ok(out.includes('"mode": "task"'), "对象应转 JSON 文本");
    assert.ok(out.includes("¥12.5"), "中文/符号应保留");
    assert.ok(!out.includes("[object Object]"), "不得出现 [object Object]");
  });
  it("数组 / 数字 / 空值处理", () => {
    assert.ok(formatToolResult([1, "a"]).includes("1"));
    assert.equal(formatToolResult(42), "42");
    assert.equal(formatToolResult(null), "");
    assert.equal(formatToolResult(undefined), "");
  });
});
