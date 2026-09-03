// 回归门禁（P2-3）：前端组件一律走 api.js 的 JSDoc 类型化具名导出，
// 禁止在 src 任何文件出现 api.api("...") 裸路径调用，也禁止文件级 apiGet/apiPost
// 本地包装——这两类写法绕过 @ts-check 类型校验（裸字符串路径不参与 typedef
// 对齐），且把"后端不可用"错误静默吞成 null，SSR/测试环境也不健壮。
// 一旦有组件改回裸路径或重新引入本地包装，本测试立即失败。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(__dirname, "../src");

function walk(dir) {
  const out = [];
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) out.push(...walk(p));
    else if (/\.(js|jsx|mjs)$/.test(ent.name)) out.push(p);
  }
  return out;
}

describe("前端 API 调用已收编为类型化导出（P2-3）", () => {
  const offenders = walk(src).filter((f) => {
    const code = fs.readFileSync(f, "utf8");
    return code.includes("api.api(") || /const apiGet|const apiPost/.test(code);
  });

  it("src 内不存在 api.api( 裸路径调用与文件级 apiGet/apiPost 包装", () => {
    assert.deepEqual(
      offenders.map((f) => path.relative(src, f)),
      [],
      "以下文件仍含裸路径调用/本地包装（应改走 api.js 类型化导出）：",
    );
  });
});
