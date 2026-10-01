// ── 产物路径提取（共享）──
// 从助手回复 / 工具结果 / 工具参数中提取产物绝对路径（Windows 盘符路径）。
// limit<=0 表示不限条数；默认给一个宽松上限，避免异常长文本失控。
const EXTS = [
  "md", "markdown", "txt", "json", "jsonl", "csv", "tsv",
  "xlsx", "xls", "docx", "doc", "pptx", "ppt", "pdf",
  "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico",
  "html", "htm", "css", "js", "mjs", "ts", "jsx", "tsx", "vue",
  "zip", "rar", "7z", "tar", "gz",
  "py", "log", "yaml", "yml", "toml", "ini", "sql", "db", "sqlite",
  "wav", "mp3", "mp4", "srt", "whl", "exe",
].join("|");
// 前缀断言 `(?<![A-Za-z0-9])`：盘符必须是「独立的盘符」，前面不能紧跟字母数字。
// 否则 URL 里的 scheme 会被当成盘符——实测 `http://x/a.png` 曾提取出伪路径
// `p://x/a.png`、`https://cdn.example.com/img/photo.png` 提取出 `s://cdn...`，
// 污染「本会话产物」列表（回归测试 tests/extractProducts.test.mjs 锁定）。
const ABS_PATH_RE = new RegExp(
  `(?<![A-Za-z0-9])[A-Za-z]:[\\\\/][^\\s"“”“<>|,，；;]*?\\.(?:${EXTS})\\b`, "gi");

export default function extractProducts(text, limit = 200) {
  if (!text) return [];
  const found = [];
  const seen = new Set();
  for (const m of String(text).matchAll(ABS_PATH_RE)) {
    const raw = m[0].replace(/[),，;；。]+$/, "").trim();
    if (raw.length < 8 || seen.has(raw)) continue;
    seen.add(raw);
    found.push(raw);
    if (limit > 0 && found.length >= limit) break;
  }
  return found;
}
