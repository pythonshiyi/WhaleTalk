// 语法高亮回归（node 直接运行：node tests/mdHighlight.test.mjs）
// 重点：重写为「预计算匹配 + k 路归并」后，输出文本必须与源码逐字一致（仅多出
// span 包裹与 HTML 转义），且超大代码块不得退化为 O(n²) 卡死主线程。
import assert from "node:assert";
import { highlight } from "../src/mdHighlight.js";

// span 标签剥离 + HTML 反转义，还原纯文本
function plain(html) {
  return html
    .replace(/<span class="hl-[a-z-]+">/g, "")
    .replace(/<\/span>/g, "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

const samples = [
  ["js", 'const x = "hi"; // 注释\nfunction f(a){ return a + 1; } /* 块 */'],
  ["ts", "interface A { a: string }\nconst n: number = 42;"],
  ["python", 'def f(x):\n    # 注释\n    return "值" + str(x)'],
  ["sql", "SELECT id, name FROM users WHERE age > 18 -- 注释"],
  ["html", '<div class="box"><a href="/x">链接</a></div>'],
  ["css", ".box { color: #fff; width: 10px; } /* c */"],
  ["json", '{"a": 1, "b": true, "c": null}'],
  ["bash", 'echo "hi" && cd /tmp # note'],
  ["plaintext", "<script>alert(1)</script> & <b>"],
  ["js", "a < b && c > d & e"],
];

for (const [lang, code] of samples) {
  const out = highlight(code, lang);
  assert.strictEqual(plain(out), code, `[${lang}] 高亮后文本应与源码逐字一致：${JSON.stringify(code)}`);
}
console.log("PASS: 文本保真（span 剥离后逐字一致）");

// 已知高亮类别
assert.ok(highlight("const a = 1", "js").includes('class="hl-kw"'), "关键字应标 hl-kw");
assert.ok(highlight('const a = "s"', "js").includes('class="hl-str"'), "字符串应标 hl-str");
assert.ok(highlight("const a = 1 // x", "js").includes('class="hl-com"'), "注释应标 hl-com");
assert.ok(highlight("const a = 123", "js").includes('class="hl-num"'), "数字应标 hl-num");
console.log("PASS: 关键字/字符串/注释/数字类别");

// 未闭合/怪异输入不得抛错
for (const s of ["```", "/* unclosed", '"unterminated', "\u0000\u0001", ""]) {
  assert.doesNotThrow(() => highlight(s, "js"), `不应抛错：${JSON.stringify(s)}`);
}
console.log("PASS: 怪异输入不抛错");

// 超大代码块不得 O(n²)（旧实现 64KB≈1.5s、200KB≈14s；新实现近线性）
const unit = "return if else for while const let var function true false null\n";
const big = unit.repeat(Math.ceil((200 * 1024) / unit.length));
const t0 = Date.now();
const bigOut = highlight(big, "js");
const ms = Date.now() - t0;
assert.ok(plain(bigOut) === big, "大块文本仍须保真");
assert.ok(ms < 4000, `200KB 高亮应近线性完成，实际 ${ms}ms（疑似回退 O(n²)）`);
console.log(`PASS: 200KB 高亮 ${ms}ms（近线性）`);
