// 回归哨兵：外部服务表单字段名不能再用 React 保留属性 key。
//
// 历史缺陷：EmRow 通过 `key="smtp_host"` 之类传字段名。React 会把 key 当作
// 列表标识并从 props 中剥离，组件里拿到的 key 恒为 undefined → 同一分组内
// 所有输入框都读写同一个 "undefined" 字段，填模型/端点/名称时三个文本框一起变。
// 字段名改用 name 传，key 只留给真正的列表项使用。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const JSX = readFileSync(join(HERE, "..", "src", "components", "SettingsPage.jsx"), "utf8");

describe("外部服务表单字段独立绑定", () => {
  it("EmRow 用 name 而非 key 传字段名", () => {
    assert.match(JSX, /const EmRow = \(\{ label, placeholder, group, name, desc, type \}\)/, "EmRow 必须解构 name");
    assert.match(JSX, /field\(group, name\)/, "EmRow 取值必须用 name");
    assert.match(JSX, /setField\(group, name, e\.target\.value\)/, "EmRow 写值必须用 name");
  });

  it("所有 EmRow 调用点都使用 name= 且不出现 key=", () => {
    const tags = JSX.match(/<EmRow[\s\S]*?\/>/g) || [];
    assert.ok(tags.length >= 10, `EmRow 调用点过少（${tags.length}），检查是否被重构`);
    for (const tag of tags) {
      assert.match(tag, /\bname="/, `EmRow 缺少 name 字段：${tag.slice(0, 120)}`);
      assert.doesNotMatch(tag, /\bkey="/, `EmRow 不能用 key 传字段名（React 不会传入 props）：${tag.slice(0, 120)}`);
    }
  });
});
