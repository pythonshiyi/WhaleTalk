// 产物路径提取回归：此前硬编码「上限 4」，导致活动栏「本会话产物」最多只显示 4 个，
// 且逐条消息各调一次时同样被截断；扩展名列表也偏窄。本测试锁定：不限条数 + 扩展名补全 + 去重 + 误报防护。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import extractProducts from "../src/extractProducts.js";

const P = (s) => `D:\\work\\out\\${s}`;

describe("extractProducts 产物提取", () => {
  it("单条消息可提取 4 个以上产物（不再截断）", () => {
    const text = Array.from({ length: 8 }, (_, i) => `已生成 ${P("f" + i + ".png")}`).join("\n");
    assert.equal(extractProducts(text).length, 8);
  });

  it("扩展名补全：html/md/pdf/svg/css/js/mp4", () => {
    const names = ["a.html", "b.md", "c.pdf", "d.svg", "e.css", "f.js", "g.mp4"];
    const text = names.map((n) => P(n)).join(" ");
    assert.equal(extractProducts(text).length, names.length);
  });

  it("去重", () => {
    const text = `${P("a.png")} 又 ${P("a.png")}`;
    assert.equal(extractProducts(text).length, 1);
  });

  it("非 Windows 盘符路径不误报", () => {
    assert.deepEqual(extractProducts("see ./a.png and http://x/a.png please"), []);
  });

  it("limit 参数生效（默认不限，可显式截断）", () => {
    const text = Array.from({ length: 5 }, (_, i) => P("f" + i + ".txt")).join("\n");
    assert.equal(extractProducts(text).length, 5);
    assert.equal(extractProducts(text, 2).length, 2);
  });

  it("行尾标点会被清理", () => {
    const r = extractProducts(`见 ${P("a.pdf")}。`);
    assert.equal(r[0], P("a.pdf"));
  });
});
