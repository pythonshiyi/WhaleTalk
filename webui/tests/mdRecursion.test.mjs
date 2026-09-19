// 递归深度防护回归（node 直接运行：node tests/mdRecursion.test.mjs）
// 深嵌套公式/引用/列表曾会爆栈（RangeError）打崩整页渲染。超限时应降级而非崩溃。
import assert from "node:assert";
import { parseMath } from "../src/mdMath.js";
import { parseMarkdown } from "../src/mdParser.js";

const cases = [
  ["parseMath 深花括号", () => parseMath("{".repeat(5000))],
  ["parseMath 深 frac", () => parseMath("\\frac".repeat(5000))],
  ["parseMath 深 left(", () => parseMath("\\left(".repeat(3000))],
  ["parseMarkdown 深引用", () => parseMarkdown(">".repeat(5000))],
  ["parseMarkdown 深列表", () => {
    let s = "";
    for (let i = 0; i < 5000; i++) s += "  ".repeat(Math.min(i, 40)) + "- x\n";
    return parseMarkdown(s);
  }],
];
for (const [name, fn] of cases) {
  assert.doesNotThrow(fn, `${name} 不应爆栈`);
}
console.log("PASS: 深嵌套公式/引用/列表不爆栈");

// 正常解析仍工作（防护不得误伤）
const math = parseMath("x^2 + \\frac{a}{b} + \\alpha");
assert.ok(Array.isArray(math) && math.length > 0, "正常公式应产出节点");
const md = parseMarkdown("# 标题\n\n- a\n- b\n\n> quote\n\n```js\nconst x=1;\n```");
assert.ok(md.blocks.length >= 4, "正常 Markdown 应产出多个块");
console.log("PASS: 正常公式/Markdown 解析不受影响");
