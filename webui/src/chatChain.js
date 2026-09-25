// 送模型的消息链构造（纯函数，从 ChatPage 抽出便于单测）。
//
// 关键区别：
// - 任务模式（includeTools=true）：必须完整回传 assistant(reasoning + tool_calls) → tool 结果，
//   否则模型丢失工具上下文 / 官方接口因悬空 tool_calls 报 400。
// - 对话模式（includeTools=false）：纯对话本就无工具，携带历史 tool_calls 既徒增 token，
//   又可能触发「请求未带 tools 参数却出现 tool 消息」的接口 400；只保留 user/assistant 正文。
import { unwrapLongText } from "./longTextUtil.js";

// 非图片附件：以「路径清单」追加进送给模型的正文——图片走 images 视觉链路，
// 其余文件（pdf/docx/csv…）让 AI 按路径用工具读取。展示层只用 msg.text，不显示这段。
export function fileRefsBlock(files) {
  if (!files || !files.length) return "";
  const lines = files
    .filter((f) => f && f.path)
    .map((f) => `- ${f.name || ""} → ${f.path}`);
  return lines.length ? `\n\n[本次附件]\n${lines.join("\n")}` : "";
}

export function withAttachRefs(text, files) {
  return `${text || ""}${fileRefsBlock(files)}`;
}

// ── 多轮消息链构造（官方规范）────────────────────────
// tools 模式必须完整回传：assistant(reasoning_content + tool_calls) → tool 结果。
// includeTools=false（对话模式）时只保留正文；纯工具轮（无正文）整条跳过。
export function buildMessageChain(msgs, { includeTools = true } = {}) {
  const out = [];
  for (const m of msgs) {
    if (m.local) continue; // 本地提示消息（如插件执行结果）：仅 UI 展示，绝不送模型
    if (m.role === "user") {
      const um = { role: "user", content: withAttachRefs(unwrapLongText(m.text || ""), m.files) };
      if (m.images && m.images.length) um.images = m.images;
      out.push(um);
    } else if (m.role === "assistant") {
      const am = { role: "assistant", content: unwrapLongText(m.text || "") };
      if (m.think) am.reasoning_content = m.think;
      if (includeTools && m.tools && m.tools.length) {
        am.tool_calls = m.tools.map((t, i) => ({
          id: `call_${i}`,
          type: "function",
          function: { name: t.tool, arguments: JSON.stringify(t.args || {}) },
        }));
        out.push(am);
        m.tools.forEach((t, i) => {
          out.push({
            role: "tool",
            tool_call_id: `call_${i}`,
            name: t.tool,
            content: String(t.result || "").slice(0, 4000),
          });
        });
      } else {
        if (!includeTools && !am.content) continue; // 对话模式：纯工具轮不占位
        out.push(am);
      }
    }
  }
  return out;
}

// 历史链统一入口：对话模式剔除工具链，任务模式完整回传。
export function buildHistory(msgs, chatMode) {
  return buildMessageChain(msgs, { includeTools: chatMode !== "dialog" });
}

// 同一气泡内的「多段输出」分段：一轮里 AI 会跨多个工具轮次输出多段文字
// （每段本是一条独立 assistant 消息），合并进同一气泡时若不加分隔会粘成一整块。
// 规则：上一段之后发生过工具调用（pending=true）且已有正文时，在新段前补一个空行
// （Markdown 段落分隔）——观感分段，且随文本持久化、重载后仍分段。
export function withSegmentBreak(acc, chunk, pending) {
  const prev = String(acc || "");
  if (pending && prev.trim() && !/\n\s*$/.test(prev)) return "\n\n" + chunk;
  return chunk;
}
