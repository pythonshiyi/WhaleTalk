// ── Markdown 渲染回归测试（vite 8 兼容 · SSR 真实渲染）────────────────
// 覆盖：多表格共享引用 bug 回归、全类型块/行内渲染、流式安全、注入防护。
// vite 8 的 ssrLoadModule 会内联求值 CJS 依赖，因此经由 vite ssr build
// 打包为 ESM 后再原生 import（见 ssrRender.mjs）。
// 运行：node tests/markdownRender.test.mjs（webui 目录下）
import { renderMarkdown } from "./ssrRender.mjs";

// ── 复现场景：多个表格组（历史 bug：共享 rows 数组引用 → 全部渲染为空）──
const SAMPLE = `## 🌐 浏览器与网页（11 项）

| 能力 | 说明 |
|------|------|
| \`browser_navigate\` | 控制浏览器（打开网页/点击/输入） |
| \`fetch_url\` | 抓取网页/接口的文本或 JSON |
| \`search_web\` | 联网搜索 |

## 💻 编程与执行（20 项）

| 能力 | 说明 |
|------|------|
| \`run_python\` | 执行 Python 代码 |
| \`run_command\` | 执行系统命令 |

总结：以上全部能力。

- 列表项 A
- 列表项 B`;

let failed = 0;
const check = (cond, name, extra = "") => {
  if (cond) console.log(`PASS: ${name}`);
  else { console.log(`FAIL: ${name}${extra ? " -> " + extra : ""}`); failed++; }
};

// ═══ 用例 1：多表格回归 ═════════════════
{
  const html = await renderMarkdown({ text: SAMPLE, deferCode: false });
  const tables = (html.match(/<table>/g) || []).length;
  const rows = (html.match(/<tr>/g) || []).length;

  check(tables === 2, `多表格渲染：期望 2 个 <table>，实际 ${tables}`);
  // 表头1 + 3 + 表头1 + 2 = 7
  check(rows === 7, `表格行数 = 7（两个表格均有内容，未共享引用被清空），实际 ${rows}`);
  let allCells = true;
  for (const name of ["browser_navigate", "fetch_url", "search_web", "run_python", "run_command"]) {
    if (!html.includes(`<code class="md-ic">${name}</code>`)) allCells = false;
  }
  check(allCells, "表格行内容完整（browser_navigate…run_command）");
  check(html.includes("🌐 浏览器与网页"), "标题 1 保留");
  check(html.includes("💻 编程与执行"), "标题 2 保留");
  check((html.match(/class="md-li-text"/g) || []).length === 2, "列表项 = 2");
  check(html.includes("总结：以上全部能力"), "结尾段落保留");
}

// ═══ 用例 2：全类型渲染 ═════════════════
{
  const html = await renderMarkdown({
    text: [
      "# 主标题",
      "",
      "段落 **粗体**、*斜体*、***粗斜体***、~~删除~~、==高亮==、`行内代码`、$E=mc^2$ 和 [链接](https://example.com)。",
      "",
      "| 左 | 中 | 右 |",
      "|:---|:---:|---:|",
      "| `code` | **bold** | 3 |",
      "",
      "- 一级 A",
      "  - 二级 A1（嵌套）",
      "- [x] 已完成",
      "",
      "> 引用内容",
      "> 引用第二层",
      "",
      "```js",
      "function greet(name) {",
      "  // 你好",
      "  return `Hello, ${name}!`;",
      "}",
      "```",
      "",
      "<details>",
      "<summary>点击展开</summary>",
      "",
      "详情内容",
      "</details>",
      "",
      "脚注引用[^1]在这里。",
      "",
      "[^1]: 脚注内容",
      "",
      "```math",
      "\\int_0^1 x^2 dx",
      "```",
      "",
      "---",
    ].join("\n"),
    deferCode: false,
  });

  check(html.includes('<h1 class="md-h md-h1">'), "h1 标题");
  check(html.includes("<strong><span>粗体</span></strong>"), "行内粗体");
  check(html.includes("<em><span>斜体</span></em>"), "行内斜体");
  check(html.includes("<del><span>删除</span></del>"), "删除线");
  check(html.includes('class="md-mark"'), "高亮 mark");
  check(html.includes('<code class="md-ic">行内代码</code>'), "行内 code");
  check(html.includes('class="md-math"'), "行内公式");
  check(html.includes('<a href="https://example.com"'), "链接");
  check((html.match(/<table>/g) || []).length === 1, "表格渲染");
  check((html.match(/<th[ >]/g) || []).length === 3, "表头 3 列");
  check(html.includes("text-align:center") && html.includes("text-align:right"), "表格对齐");
  check(html.includes("二级 A1（嵌套）"), "嵌套列表层级");
  check(html.includes('type="checkbox"'), "任务框");
  check(html.includes('<blockquote class="md-quote">'), "引用块");
  check(html.includes("引用第二层"), "嵌套引用");
  check(html.includes('<details class="md-details">'), "details 折叠");
  check(html.includes("<summary>点击展开</summary>"), "summary");
  check(html.includes('class="hl-kw"'), "语法高亮");
  check(html.includes('<span class="md-code-lang">js</span>'), "代码块语言标签");
  check(html.includes('class="md-math-block"'), "数学块");
  check(html.includes('class="md-footnotes"'), "脚注定义区");
  check(html.includes('href="#fn-1"') && html.includes('id="fn-1"'), "脚注引用↔定义锚点");
  check(html.includes('class="md-hr"'), "分隔线");
}

// ═══ 用例 3：公式排版（P2-1 mdMath.js）═════════════
{
  const inline = await renderMarkdown({ text: "公式 $x^2 + \\frac{a}{b}$ 结束", deferCode: false });
  check(inline.includes('class="md-math"'), "行内公式容器");
  check(inline.includes('class="mx-sup"'), "上标渲染");
  check(inline.includes('class="mx-frac"') && inline.includes('class="mx-num"') && inline.includes('class="mx-den"'), "分数渲染（分子/分母）");
  check(inline.includes('class="mx-text">a<') && inline.includes('class="mx-text">b<'), "分数内容保留");

  const greek = await renderMarkdown({ text: "$\\alpha + \\beta = \\gamma$", deferCode: false });
  check(greek.includes("α") && greek.includes("β") && greek.includes("γ"), "希腊字母转写");

  const ops = await renderMarkdown({ text: "$a \\times b \\leq c \\approx \\infty$", deferCode: false });
  check(ops.includes("×") && ops.includes("≤") && ops.includes("≈") && ops.includes("∞"), "运算符转写");

  const paren = await renderMarkdown({ text: "$\\left( \\frac{a}{b} \\right)$", deferCode: false });
  check(paren.includes('class="mx-paren"') && paren.includes('class="mx-paren-open"'), "\\left\\right 括号配对");

  const block = await renderMarkdown({
    text: "```math\n\\sqrt[3]{y} = \\sum_{i=1}^{n} x_i\n```",
    deferCode: false,
  });
  check(block.includes('class="md-math-block"'), "数学块容器");
  check(block.includes('class="mx-sqrt"') && block.includes('class="mx-sqrt-idx"'), "根号渲染（含开方次数）");
  check(block.includes("∑"), "求和符号");

  const txt = await renderMarkdown({ text: "$E = mc^2 \\text{（质能方程）}$", deferCode: false });
  check(txt.includes("质能方程"), "\\text{} 中文文本");

  const evil = await renderMarkdown({ text: "$\\frac{<script>alert(1)</script>}{x}$", deferCode: false });
  check(!evil.includes("<script>alert"), "公式内容 HTML 注入防护");

  const stream = await renderMarkdown({ text: "半截 $\\frac{a", deferCode: false });
  check(stream.includes("半截") && !stream.includes("mx-frac"), "未闭合公式不崩、不吞正文");
}

// ═══ 用例 4：流式安全 ═════════════════
{
  const open = await renderMarkdown({ text: "前文\n```py\nx = 1", deferCode: false });
  check(open.includes("md-code-open"), "未闭合围栏占位（deferCode=false）");
  const deferred = await renderMarkdown({ text: "前文\n```py\nx = 1", deferCode: true });
  check(!deferred.includes("md-code-open") && deferred.includes("前文"), "未闭合围栏跳过（deferCode=true）");
}

// ═══ 用例 5：安全边界 ═════════════════
{
  const empty = await renderMarkdown({ text: "", deferCode: false });
  check(empty === '<div class="md"></div>', "空输入安全");
  const evil = await renderMarkdown({ text: "正文 <script>alert(1)</script> 之后", deferCode: false });
  check(!evil.includes("<script>alert"), "HTML 注入防护");
  check(evil.includes("&lt;script&gt;"), "HTML 转义可见");
  const jsHref = await renderMarkdown({ text: "[x](javascript:alert(1))", deferCode: false });
  check(!jsHref.includes('href="javascript:'), "javascript: 链接拒绝");
}

if (failed) {
  console.log(`\n❌ markdownRender 测试 ${failed} 组失败`);
  process.exit(1);
}
console.log(`\n✅ markdownRender 全部用例通过`);
