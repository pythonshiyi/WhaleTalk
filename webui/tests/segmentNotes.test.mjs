// 回归：多段输出按锚点切分（因果标注「↳ 基于第 N 步」的数据基础）。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { splitSegments, buildTurnFlow } from "../src/segmentNotes.js";

describe("splitSegments", () => {
  it("无锚点 → 单段、无标注", () => {
    assert.deepEqual(splitSegments("hello", []), [{ text: "hello", note: null }]);
    assert.deepEqual(splitSegments("hello", undefined), [{ text: "hello", note: null }]);
  });

  it("单锚点 → 前段无标注、后段带步数", () => {
    const text = "第一段\n\n第二段";
    const i = text.indexOf("第二段");
    const out = splitSegments(text, [{ i, n: 3 }]);
    assert.equal(out.length, 2);
    assert.equal(out[0].text, "第一段\n\n");
    assert.equal(out[0].note, null);
    assert.equal(out[1].text, "第二段");
    assert.equal(out[1].note, 3);
  });

  it("多锚点按位置递增切分；n=0 的锚点不产生标注", () => {
    const text = "A|B|C";
    const out = splitSegments(text, [{ i: 3, n: 2 }, { i: 1, n: 0 }]); // 乱序输入
    assert.deepEqual(out.map((x) => x.text), ["A", "|B", "|C"]); // 排序后 i=1,3
    assert.equal(out[0].note, null);   // 首段
    assert.equal(out[1].note, null);   // 锚点 n=0 → 无标注
    assert.equal(out[2].note, 2);
  });

  it("越界/非法锚点被忽略（不越界、不回退）", () => {
    const out = splitSegments("abcdef", [{ i: -1, n: 1 }, { i: 99, n: 2 }, { i: 3, n: 5 }, { i: 3, n: 9 }, { i: "x", n: 1 }]);
    assert.deepEqual(out.map((x) => x.text), ["abc", "def"]);
    assert.equal(out[1].note, 5); // 同位置重复锚点只取首个有效
  });

  it("空文本安全", () => {
    assert.deepEqual(splitSegments("", [{ i: 1, n: 1 }]), [{ text: "", note: null }]);
    assert.deepEqual(splitSegments(null, null), [{ text: "", note: null }]);
  });
});

describe("buildTurnFlow（步骤与正文按时间交错）", () => {
  const tools = [{ tool: "t1" }, { tool: "t2" }, { tool: "t3" }, { tool: "t4" }, { tool: "t5" }];

  it("有锚点：步骤插到正确的段间位置（不再全堆在顶部）", () => {
    const text = "开头\n\n结尾";
    const i = text.indexOf("结尾");
    const flow = buildTurnFlow(text, [{ i, n: 3 }], tools);
    assert.deepEqual(flow.map((b) => b.type), ["text", "steps", "text", "steps"]);
    assert.equal(flow[0].text, "开头\n\n");
    assert.deepEqual(flow[1].steps.map((t) => t.tool), ["t1", "t2", "t3"]);
    assert.equal(flow[1].start, 0);
    assert.equal(flow[2].text, "结尾");
    assert.equal(flow[2].note, 3);
    assert.deepEqual(flow[3].steps.map((t) => t.tool), ["t4", "t5"]);
    assert.equal(flow[3].start, 3);
  });

  it("无锚点：退化为「先步骤、后正文」", () => {
    const flow = buildTurnFlow("正文", [], tools);
    assert.deepEqual(flow.map((b) => b.type), ["steps", "text"]);
    assert.equal(flow[0].steps.length, 5);
    assert.equal(flow[0].start, 0);
  });

  it("无工具：仅正文；无正文：仅步骤", () => {
    assert.deepEqual(buildTurnFlow("hi", [], []).map((b) => b.type), ["text"]);
    assert.deepEqual(buildTurnFlow("", [], tools).map((b) => b.type), ["steps"]);
    assert.deepEqual(buildTurnFlow("", [], []), []);
  });
});
