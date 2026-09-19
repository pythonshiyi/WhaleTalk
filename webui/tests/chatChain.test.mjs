// 送模型消息链构造（任务模式完整工具链 / 对话模式剔除工具链）。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { buildHistory, buildMessageChain, fileRefsBlock, withAttachRefs } from "../src/chatChain.js";

const MSGS = [
  { role: "user", text: "帮我做MV", files: [{ name: "a.txt", path: "D:/a.txt" }] },
  {
    role: "assistant",
    text: "先看一下素材",
    think: "思考",
    tools: [
      { tool: "list_dir", args: { path: "D:/p" }, result: "a b c" },
      { tool: "read_file", args: { path: "D:/p/x" }, result: "内容" },
    ],
  },
  { role: "user", text: "继续" },
  { role: "assistant", text: "", tools: [{ tool: "run_python", args: {}, result: "ok" }] },
  { role: "assistant", text: "完成" },
];

describe("buildMessageChain", () => {
  it("任务模式完整回传 tool_calls + tool 结果，且 id 与结果一一对应", () => {
    const out = buildMessageChain(MSGS, { includeTools: true });
    const asst = out.find((m) => m.role === "assistant" && m.tool_calls);
    assert.ok(asst, "应携带 tool_calls");
    assert.equal(asst.tool_calls.length, 2);
    assert.equal(asst.tool_calls[0].id, "call_0");
    const toolMsgs = out.filter((m) => m.role === "tool");
    assert.equal(toolMsgs.length, 3);
    assert.equal(toolMsgs[0].tool_call_id, "call_0");
    assert.equal(toolMsgs[0].content, "a b c");
  });

  it("对话模式剔除工具链，只留正文，纯工具轮整条跳过", () => {
    const out = buildMessageChain(MSGS, { includeTools: false });
    assert.equal(out.filter((m) => m.role === "tool").length, 0, "不应有 tool 消息");
    assert.ok(!out.some((m) => m.tool_calls), "不应有 tool_calls");
    // 纯工具轮（text 为空）被跳过；有正文的助手保留
    const texts = out.filter((m) => m.role === "assistant").map((m) => m.content);
    assert.deepEqual(texts, ["先看一下素材", "完成"]);
  });

  it("user 附件以路径清单追加；图片走 images", () => {
    const chain = buildMessageChain(
      [{ role: "user", text: "看这个", images: ["D:/i.png"], files: [{ name: "r.pdf", path: "D:/r.pdf" }] }],
      { includeTools: false }
    );
    assert.equal(chain[0].role, "user");
    assert.ok(chain[0].content.includes("看这个"));
    assert.deepEqual(chain[0].images, ["D:/i.png"]);
    assert.ok(chain[0].content.includes("[本次附件]"));
    assert.ok(chain[0].content.includes("D:/r.pdf"));
  });

  it("reasoning_content 透传", () => {
    const chain = buildMessageChain(
      [{ role: "assistant", text: "答", think: "推理" }],
      { includeTools: false }
    );
    assert.equal(chain[0].reasoning_content, "推理");
  });
});

describe("buildHistory 按模式分流", () => {
  it("task → 带工具链；dialog → 不带工具链", () => {
    assert.ok(buildHistory(MSGS, "task").some((m) => m.role === "tool"));
    assert.ok(!buildHistory(MSGS, "dialog").some((m) => m.role === "tool"));
  });
});

describe("附件引用工具", () => {
  it("fileRefsBlock / withAttachRefs", () => {
    assert.equal(fileRefsBlock([]), "");
    assert.ok(fileRefsBlock([{ name: "n", path: "p" }]).startsWith("\n\n[本次附件]\n- n → p"));
    assert.equal(withAttachRefs("hi", null), "hi");
  });
});
