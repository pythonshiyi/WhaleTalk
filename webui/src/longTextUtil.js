// ── 长文本包装解除（模型输出约定格式兜底）────────────────
// 现象：部分大模型（如 DeepSeek 长文本生成路径）对超长回复会回带
//   @long-text:"..." 元信息前缀 + <long_text_quote>...</long_text_quote>
// 包裹的 JSON 消息链（形如 {"role":"assistant","content":"..."} 或数组）。
// 若直接交给 Markdown 渲染，转义符 \n 无法还原为真实换行，导致
// 表格整段消失、列表退化为裸点、加粗失效——「部分内容渲染不出来」。
//
// 本函数在渲染前把这类包装还原为可渲染的 Markdown 正文：
//   1. 优先：提取 <long_text_quote> 内 JSON 消息链中「最后一条 assistant」的 content；
//   2. 兜底：剥掉包装标签与 @long-text 前缀，保留剩余文本；
//   3. 干净文本原样返回（零副作用）。

// 读取 JSON 字符串字面量（s[i] === '"'），正确处理 \n \" \\ \u 等转义。
// 返回 { value, end }；解析失败返回 null。
function readJsonString(s, i) {
  let out = "";
  i++;
  while (i < s.length) {
    const c = s[i];
    if (c === "\\") {
      const n = s[i + 1];
      if (n === "n") out += "\n";
      else if (n === "t") out += "\t";
      else if (n === "r") out += "\r";
      else if (n === '"') out += '"';
      else if (n === "\\") out += "\\";
      else if (n === "u") {
        out += String.fromCharCode(parseInt(s.slice(i + 2, i + 6), 16) || 0);
        i += 4;
      } else if (n != null) out += n;
      i += 2;
    } else if (c === '"') {
      return { value: out, end: i + 1 };
    } else {
      out += c;
      i++;
    }
  }
  return null;
}

function extractAssistantContent(str) {
  // 1) 标准 JSON 路径：数组 / 单对象 / {messages:[...]}
  try {
    const data = JSON.parse(str);
    const arr = Array.isArray(data) ? data : (data && data.messages) || [data];
    for (let i = arr.length - 1; i >= 0; i--) {
      const m = arr[i];
      if (m && (m.role === "assistant" || m.role === "model") && typeof m.content === "string" && m.content.trim()) {
        return m.content;
      }
    }
  } catch {}

  // 2) 退化路径：非规范 JSON（如多个对象裸拼/被截断）——
  //    定位最后一个 "role":"assistant" 的 content 字符串并读取。
  const key = '"role":"assistant"';
  let idx = -1;
  const pos = [];
  while ((idx = str.indexOf(key, idx + 1)) !== -1) pos.push(idx);
  for (let i = pos.length - 1; i >= 0; i--) {
    const ci = str.indexOf('"content"', pos[i] + key.length);
    if (ci === -1) continue;
    const qi = str.indexOf('"', ci + '"content"'.length);
    if (qi === -1) continue;
    const r = readJsonString(str, qi);
    if (r && r.value && r.value.trim()) return r.value;
  }
  return null;
}

export function unwrapLongText(text) {
  if (!text || typeof text !== "string") return text;

  // 1) 有 <long_text_quote> 包裹：优先从中提取
  const qm = text.match(/<long_text_quote>([\s\S]*?)<\/long_text_quote>/);
  if (qm) {
    const inner = qm[1].trim();
    const content = extractAssistantContent(inner);
    if (content != null) return content;
  }

  // 2) 无包裹标签但整段形如 JSON 消息链（模型只输出裸 JSON）
  if (!qm) {
    const maybe = text.trim();
    if (maybe.startsWith("[") || maybe.startsWith("{")) {
      const content = extractAssistantContent(maybe);
      if (content != null) return content;
    }
  }

  // 3) 兜底：剥掉包装标签与 @long-text 前缀，保留正文
  return text
    .replace(/<\/?long_text_quote>/gi, "")
    .replace(/@long-text\s*:\s*"[^"]*"/g, "")
    .replace(/@long-text\s*:\s*'[^']*'/g, "")
    .replace(/@long-text\s*:\s*[^\s<]*/g, "")
    .replace(/^[\s"']+/, "")
    .trim();
}
