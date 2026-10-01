// ── 前端 node 测试统一跑测器 ──
// 为什么不用 package.json 里手写的 `node a.mjs && node b.mjs && ...` 链：
// 真实发生过两起套件被静默漏跑——`extractProducts.test.mjs` 与 `segmentNotes.test.mjs`
// 存在于 tests/ 却不在任何 runner 里，导致 extractProducts 的 URL 误报缺陷
// 长期没被门禁发现（该缺陷已修，回归测试现已纳入）。
// 改为**自动发现** tests/*.test.mjs：新增套件零配置即被 CI 覆盖，且不可能再漏。
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const TESTS_DIR = join(HERE, "tests");

// `--fast` 只跳过慢的重型渲染套件（Markdown 全量渲染），日常改前端用；
// 集合必须显式列名——「自动发现」保证新增套件默认进全量跑测。
const FAST_EXCLUDE = [
  "markdownRender.test.mjs",
  "mdRecursion.test.mjs",
];

const only = process.argv.includes("--fast") ? new Set(FAST_EXCLUDE) : null;

const files = readdirSync(TESTS_DIR)
  .filter((f) => f.endsWith(".test.mjs"))
  .filter((f) => !only || !only.has(f))
  .sort();

if (files.length === 0) {
  console.error("没有发现任何 tests/*.test.mjs 套件——runner 配置有误");
  process.exit(1);
}

const failed = [];
for (const f of files) {
  process.stdout.write(`\n──── ${f} ────\n`);
  const r = spawnSync(process.execPath, [join(TESTS_DIR, f)], { stdio: "inherit" });
  if (r.status !== 0) failed.push(f);
}

console.log(`\n════ 前端套件 ${files.length - failed.length}/${files.length} 通过 ════`);
if (failed.length) {
  console.error("失败套件：\n  - " + failed.join("\n  - "));
  process.exit(1);
}
