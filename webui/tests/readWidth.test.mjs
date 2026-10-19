// ── 正文宽度：上限只能有一处 ──
// 背景：v3.23 给 `.md` 的段落加了 max-width: 72ch，而 `.msg-body` 早有 860px 的阅读栏，
// 两层叠加后回答只占正文栏的一半，右侧一片空白——用户看到的是"气泡宽度坏了"。
// 这条测试锁住结构约束：宽度上限只允许出现在**容器**上，不允许加在段落/列表上。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "..", "src", "app.css"), "utf8");

/** 取 `.md` 直接子元素选择器上是否声明了 max-width */
function paragraphWidthRules(css) {
  const hits = [];
  const re = /([^{}]*\.md\s*>\s*[^{}]+)\{([^}]*)\}/g;
  let m;
  while ((m = re.exec(css))) {
    if (/max-width\s*:/.test(m[2])) hits.push(m[1].trim());
  }
  return hits;
}

function msgBodyMaxWidth(css) {
  const m = /\.msg-body\s*\{([^}]*)\}/.exec(css);
  if (!m) return null;
  const w = /max-width\s*:\s*([^;]+);/.exec(m[1]);
  return w ? w[1].trim() : null;
}

describe("正文宽度约束", () => {
  it("段落/列表上不得再声明 max-width（宽度上限只能有一处）", () => {
    const bad = paragraphWidthRules(CSS);
    assert.deepEqual(bad, [], `这些规则在段落上加了宽度上限，会让回答只占半栏：${bad.join(" , ")}`);
  });

  it(".msg-body 有且仅有一个宽度上限（阅读栏）", () => {
    const w = msgBodyMaxWidth(CSS);
    assert.ok(w, ".msg-body 必须声明 max-width（否则宽屏上行长失控）");
    assert.match(w, /1100px/, `阅读栏宽度应保持在 1100px 上限，实际 ${w}`);
  });
});
