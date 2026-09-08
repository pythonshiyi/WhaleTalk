// ── tagColorClass 标签语义配色回归测试 ──
// 背景：曾误用 `tag-临时` 使所有标签同色；现按稳定哈希映射调色板，
// 保证同一标签恒同色、内置语义标签用固定色。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { tagColorClass, TAG_PALETTE } from "../src/tagColor.js";

describe("tagColorClass 标签配色", () => {
  it("内置语义标签 → 固定语义色", () => {
    assert.equal(tagColorClass("调研"), "c-brand");
    assert.equal(tagColorClass("写作"), "c-danger");
    assert.equal(tagColorClass("数据"), "c-ok");
  });
  it("同一标签恒同色（稳定哈希）", () => {
    assert.equal(tagColorClass("客户A"), tagColorClass("客户A"));
    assert.equal(tagColorClass("灵感"), tagColorClass("灵感"));
  });
  it("任意标签落在调色板内", () => {
    for (const t of ["报价", "法务", "x", "长标签".repeat(5)]) {
      assert.ok(TAG_PALETTE.includes(tagColorClass(t)), `${t} 应在调色板`);
    }
  });
  it("空/无标签 → 调色板内默认", () => {
    assert.ok(TAG_PALETTE.includes(tagColorClass("")));
  });
});
