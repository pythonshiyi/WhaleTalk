// ── 产物路径提取（共享）──
// 从助手回复/工具结果文本中提取产物绝对路径（仅匹配产物类扩展名）。
// 上限 4 条，去重。供右侧「📦 产物直达」使用——聊天里不显示。
const ABS_PATH_RE = /[A-Za-z]:[\\/][^\s"“”“<>|,，；;]*?[.](?:md|txt|json|csv|xlsx|docx|pptx|pdf|png|jpg|jpeg|html|htm|zip|py|log)\b/gi;

export default function extractProducts(text) {
  if (!text) return [];
  const found = [];
  const seen = new Set();
  for (const m of String(text).matchAll(ABS_PATH_RE)) {
    const raw = m[0].replace(/[),，;；。]+$/, "").trim();
    if (raw.length < 8 || seen.has(raw)) continue;
    seen.add(raw);
    found.push(raw);
    if (found.length >= 4) break;
  }
  return found;
}
