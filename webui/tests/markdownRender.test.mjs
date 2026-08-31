// ── Markdown 表格渲染回归测试（防「多表格共享数组引用 → 全部渲染为空」复发）──
// 用 vite ssrLoadModule 真实渲染 Markdown 组件，断言每个表格的行都被渲染出来。
// 运行：node tests/markdownRender.test.mjs（webui 目录下）
import { createServer } from "../node_modules/vite/dist/node/index.js";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const require = createRequire(path.join(root, "package.json"));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

const server = await createServer({
  root,
  logLevel: "error",
  server: { middlewareMode: true },
  appType: "custom",
  ssr: { external: ["react", "react-dom", "react-dom/server", "scheduler"] },
  optimizeDeps: { noDiscovery: true },
});

// 复现场景：多个表格组 + 标题 + 列表（模型长文本常见结构）
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
try {
  const { default: Markdown } = await server.ssrLoadModule("/src/components/Markdown.jsx");
  const html = renderToStaticMarkup(React.createElement(Markdown, { text: SAMPLE, deferCode: false }));

  const tables = (html.match(/<table>/g) || []).length;
  const rows = (html.match(/<tr>/g) || []).length;

  // 断言 1：两个表格都被渲染
  if (tables !== 2) {
    console.log(`FAIL: 期望 2 个 <table>，实际 ${tables}`);
    failed++;
  } else {
    console.log(`PASS: 表格数量 = 2`);
  }
  // 断言 2：表格行总数 = 表头1 + 3 + 表头1 + 2 = 7
  if (rows !== 7) {
    console.log(`FAIL: 期望 7 个 <tr>，实际 ${rows}`);
    failed++;
  } else {
    console.log(`PASS: 表格行数 = 7（两个表格均有内容，未共享引用被清空）`);
  }
  // 断言 3：两个表格的内容互不串组
  for (const name of ["browser_navigate", "fetch_url", "search_web", "run_python", "run_command"]) {
    if (!html.includes(`<code>${name}</code>`)) {
      console.log(`FAIL: 缺少表格行内容 <code>${name}</code>`);
      failed++;
    }
  }
  if (failed === 0) console.log(`PASS: 表格行内容完整（browser_navigate…run_command）`);
  // 断言 4：列表仍在
  if (!html.includes(`class="md-li"`) || (html.match(/class="md-li"/g) || []).length !== 2) {
    console.log(`FAIL: 列表项未渲染`);
    failed++;
  } else {
    console.log(`PASS: 列表项 = 2`);
  }
  // 断言 5：标题仍在
  if (!html.includes(`<h2`)) {
    console.log(`FAIL: 标题未渲染`);
    failed++;
  } else {
    console.log(`PASS: 标题已渲染`);
  }
} finally {
  await server.close();
}

if (failed) {
  console.log(`\n❌ markdownRender 测试 ${failed} 组失败`);
  process.exit(1);
} else {
  console.log(`\n✅ markdownRender 全部用例通过`);
}
