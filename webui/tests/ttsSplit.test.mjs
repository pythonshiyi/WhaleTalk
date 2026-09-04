// 语音分句(splitSentences)回归测试：node tests/ttsSplit.test.mjs
// 修复：sentence 自动朗读首段反复、后段不播 —— splitSentences 会把"已完句"与后续段
// 粘进同一 buf，导致流式里首句不断随后续内容增长 → 每次增长都是新文本 → 被反复重播。
import assert from "node:assert";
import { splitSentences } from "../src/ttsUtil.js";

// 完整文本两句分离
{
  const out = splitSentences("第一句：用户偏好中文。第二句这里讲的是很长的一段内容。");
  assert.strictEqual(out.length, 2, "两句应各自独立");
  assert.ok(out[0].includes("第一句"), "首句含首句内容");
  assert.ok(!out[0].includes("第二句"), "首句不应吞并第二句");
  assert.ok(out[1].includes("第二句"));
}

// 流式增长下，首句一旦以句号定型即稳定（不再随后续增长变化 → 不会反复重播）
{
  const stream = "第一句：用户偏好中文。第二句这里讲的是很长的一段内容需要被朗读出来以便验证是否正常。";
  let acc = "";
  const stableFirsts = new Set();
  for (const ch of stream) {
    acc += ch;
    const parts = splitSentences(acc);
    // 只观察"以句末标点结尾"的稳定句（feedAuto 只入队这类）
    for (const p of parts) if (/[。！？；!?，、\n]$/.test(p) && p.includes("第一句")) stableFirsts.add(p);
  }
  assert.strictEqual(stableFirsts.size, 1, "定型后的首句应只有一种稳定形态（去重只播一次）");
  assert.strictEqual([...stableFirsts][0], "第一句：用户偏好中文。");
}

// 多个短句 + 末尾未完片段：未完片段可被识别（不含句末分隔），由调用方决定是否收尾
{
  const out = splitSentences("你好。请介绍一下你的能力。正在生成中");
  assert.strictEqual(out.length, 3);
  assert.ok(/[。]$/.test(out[0]));
  assert.ok(!/[。！？；!?，、\n]$/.test(out[2]), "末尾未完片段不应带分隔符");
}

// 长句逗号软切：切出的每段以逗号/句号收尾（可尽早开播且各自稳定）
{
  const out = splitSentences("这是一个很长很长的句子，中间有逗号，用来验证软切是否正确。");
  assert.ok(out.length >= 1);
  assert.ok(out.every((s) => /[。，]$/.test(s) || out.indexOf(s) === out.length - 1), "软切片应以逗号或句号收尾");
}

console.log("✅ ttsSplit 语音分句回归全部通过");
