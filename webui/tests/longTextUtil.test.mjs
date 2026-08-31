// 长文本包装解除工具测试（node 直接运行：node tests/longTextUtil.test.mjs）
import assert from "node:assert";
import { unwrapLongText } from "../src/longTextUtil.js";

// ── 用例 1：用户报告的包装格式（@long-text 前缀 + <long_text_quote> 包裹 JSON 消息链）──
// 程序化构造：JSON.stringify 会像模型输出那样把真实换行转义成 \n、把引号原样保留
const chain = [
  { role: "user", content: "列举你全部的能力" },
  {
    role: "assistant",
    content:
      "好的，让我为您列举我的全部能力。\n\n我拥有 **134 项能力**，分布在以下 13 个能力组中：\n\n## 🌐 浏览器与网页（11 项）\n\n| 能力 | 说明 |\n|------|------|\n| `browser_navigate` | 控制浏览器 |",
  },
];
const innerRaw = chain.map((m) => JSON.stringify(m)).join("\n");
const prefix = `@long-text:"${JSON.stringify(chain[0])}"`;
const wrapped = `${prefix} <long_text_quote>${innerRaw}</long_text_quote>`;
assert.ok(wrapped.includes("<long_text_quote>") && wrapped.includes("\\n\\n"), "测试样本必须含包装标签与转义换行");

const r1 = unwrapLongText(wrapped);
assert.ok(r1.includes("我拥有 **134 项能力**"), "应提取到 assistant 正文");
assert.ok(r1.includes("\n\n## 🌐 浏览器与网页（11 项）"), "\\n 应还原为真实换行，标题可被渲染器识别");
assert.ok(r1.includes("\n| 能力 | 说明 |\n|------|------|\n"), "表格行应还原为真实换行（行首|行尾|）");
assert.ok(!r1.includes("long_text_quote"), "不应残留包装标签");
assert.ok(!r1.includes("@long-text"), "不应残留 @long-text 前缀");
console.log("PASS 1: 单行 JSON 包装 → 提取 assistant 正文，转义换行还原");

// ── 用例 2：美化打印的 JSON 数组消息链 ──
const pretty = `<long_text_quote>[
  {"role": "user", "content": "hi"},
  {"role": "assistant", "content": "你好！\\n\\n- 第一点\\n- 第二点"}
]</long_text_quote>`;
const r2 = unwrapLongText(pretty);
assert.ok(r2.includes("你好！") && r2.includes("- 第一点"), "应从数组中取最后一条 assistant");
assert.ok(!r2.includes("long_text_quote"), "不应残留标签");
console.log("PASS 2: 美化 JSON 数组 → 取最后 assistant");

// ── 用例 3：干净 Markdown 原样返回（零副作用）──
const clean = "好的，让我为您列举我的全部能力。\n\n| 能力 | 说明 |\n|------|------|\n| `read_file` | 读取文件 |";
assert.strictEqual(unwrapLongText(clean), clean, "干净文本必须原样返回");
console.log("PASS 3: 干净 Markdown 零副作用");

// ── 用例 4：包装内是非 JSON 文本 → 剥标签 + 去前缀，保留正文 ──
const notJson = `@long-text:"meta" <long_text_quote>这是普通的长文本回复，没有 JSON 结构。</long_text_quote>`;
const r4 = unwrapLongText(notJson);
assert.ok(r4.includes("这是普通的长文本回复"), "应保留正文");
assert.ok(!r4.includes("long_text_quote") && !r4.includes("@long-text"), "应剥掉包装");
console.log("PASS 4: 非 JSON 包装 → 剥壳保留正文");

// ── 用例 5：无包装标签的裸 JSON 消息链 ──
const bareJson = `[{"role":"user","content":"q"},{"role":"assistant","content":"裸 JSON 回复正文"}]`;
assert.strictEqual(unwrapLongText(bareJson), "裸 JSON 回复正文", "应提取 assistant content");
console.log("PASS 5: 裸 JSON 数组 → 提取 assistant");

// ── 用例 6：流式中途（只有 @long-text 前缀，标签未闭合）→ 剥前缀 ──
const partial = `@long-text:"{\\"role\\":\\"user\\" 好的，让我为您列举我的全部能力。`;
const r6 = unwrapLongText(partial);
assert.ok(!r6.includes("@long-text"), "应剥掉前缀");
assert.ok(r6.includes("好的，让我为您列举我的全部能力"), "保留正文");
console.log("PASS 6: 流式半截 → 去前缀保留正文");

// ── 用例 7：空值/非字符串透传 ──
assert.strictEqual(unwrapLongText(null), null);
assert.strictEqual(unwrapLongText(""), "");
assert.strictEqual(unwrapLongText(undefined), undefined);
console.log("PASS 7: 空值透传");

// ── 用例 8：单对象 JSON（无数组包裹）──
const singleObj = `{"role":"assistant","content":"单对象回复正文"}`;
assert.strictEqual(unwrapLongText(singleObj), "单对象回复正文", "单对象也应提取");
console.log("PASS 8: 单对象 JSON → 提取 assistant");

console.log("\n✅ longTextUtil 全部 8 组用例通过");
