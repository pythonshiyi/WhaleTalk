// 回归门禁：确认/输入类交互统一走应用内对话框（../dialog.js 的 confirmDialog/promptDialog），
// 禁止在组件里直接调用原生 window.confirm / window.prompt（不可样式化、阻塞、与设计割裂）。
// 唯一允许出现原生调用的文件是 dialog.js 自身（宿主缺失时的优雅回退）。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(__dirname, "../src");

function walk(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(p));
    else if (/\.(jsx?|mjs)$/.test(e.name)) out.push(p);
  }
  return out;
}

describe("统一应用内对话框（禁止原生 confirm/prompt）", () => {
  it("除 dialog.js 外，src 下不得出现 window.confirm / window.prompt 调用", () => {
    const offenders = [];
    for (const p of walk(src)) {
      if (path.basename(p) === "dialog.js") continue;
      const text = fs.readFileSync(p, "utf8");
      if (/window\.(confirm|prompt)\s*\(/.test(text)) offenders.push(path.relative(src, p));
    }
    assert.deepEqual(offenders, [], `以下文件仍使用原生对话框：${offenders.join(", ")}`);
  });
});
