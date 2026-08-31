// ── 行内 Markdown 解析器（手写 · 零依赖 · 流式安全）────────────────
// 支持：转义 / 行内 code（单/双反引号）/ 行内公式 $…$ / 图片 / 链接 /
// 自动链接（尖括号/裸 URL/邮箱）/ 粗体 / 粗斜体 / 斜体 / 删除线 /
// 下划线 / 高亮 ==…== / 上标 ^…^ / 下标 ~…~，且支持嵌套组合。
// 未闭合标记（流式半截）原样输出，绝不吞内容。
//
// 输出为纯数据 tokens，便于 node 直跑测试与 React 渲染分离：
//   { t:'txt', v } | { t:'b'|'i'|'bi'|'s'|'u'|'h'|'sup'|'sub'|'math', c:[tokens] }
//   { t:'code', v } | { t:'a', href, title, c:[tokens] } | { t:'img', src, alt, title }

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// 链接白名单：仅 http/https/mailto/ftp/锚点/相对路径；javascript: 等一律拒绝
export function safeUrl(url) {
  if (!url) return null;
  const u = String(url).trim();
  if (/^(https?:|mailto:|ftp:|#|\/|\.\/|\.\.\/|[a-zA-Z0-9_.\-/]+\.(?:png|jpe?g|gif|webp|svg|md|txt|pdf|zip))/i.test(u)) return u;
  return null;
}

// 标题锚点：中文保留、空白转连字符、去符号
export function slugify(text) {
  return String(text)
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "") || "sec";
}

// 组合 token 正则（顺序即优先级）。
// 组号对照：1 转义｜2 双反引号｜3 单反引号｜4 块公式｜5 行内公式｜
// 6=alt 7=src 8=title（图片）｜9=text 10=href 11=title（链接）｜
// 12 邮箱｜13 尖括号链接｜14=pre 15=url（裸链接）｜
// 16 粗斜体｜17 粗体*｜18 粗体_｜19 斜体*｜20 斜体_｜21 删除线｜22 高亮｜23 上标｜24 下标
const TOKEN_RE = new RegExp(
  [
    /\\([\\`*_~^=+<>()\[\]{}|#.!-])/.source,
    /``(.+?)``/.source,
    /`([^`\n]+)`/.source,
    /\$\$([^$]+)\$\$/.source,
    /\$([^$\n]+)\$/.source,
    /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/.source,
    /\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/.source,
    /<([a-zA-Z0-9_.+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>/.source,
    /<((?:https?|ftp):\/\/[^\s<>]+)>/.source,
    /(^|[\s(（【\[>])((?:https?|ftp):\/\/[^\s<>"'）)\]】]+)/.source,
    /\*\*\*(.+?)\*\*\*/.source,
    /\*\*(.+?)\*\*/.source,
    /__([^_\n]+)__/.source,
    /\*([^*\n]+)\*/.source,
    /_([^_\n]+)_/.source,
    /~~([^~\n]+)~~/.source,
    /==([^=\n]+)==/.source,
    /\^([^^\n]+)\^/.source,
    /~([^~\n]+)~/.source,
    /\[\^([^\]]+)\]/.source                                       // 25 脚注引用
  ].join("|"),
  "g"
);

const MAX_DEPTH = 5;

// 注意：parseInline 递归时每层必须持有独立 RegExp 实例——
// 共享同一全局正则会让内层 exec 破坏外层 lastIndex，导致死循环。
const TOKEN_RE_SRC = TOKEN_RE.source;

function parseInline(text, depth = 0) {
  const tokens = [];
  if (text == null) return tokens;
  text = String(text);
  const re = new RegExp(TOKEN_RE_SRC, "g");
  let pos = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > pos) tokens.push({ t: "txt", v: text.slice(pos, m.index) });
    const full = m[0];
    const sub = (s, pre = 1) => (depth < MAX_DEPTH ? parseInline(s, depth + 1) : [{ t: "txt", v: s }]);
    let tok = null;
    if (m[1] !== undefined) {
      tok = { t: "txt", v: m[1] }; // 转义：还原字面字符
    } else if (m[2] !== undefined) {
      tok = { t: "code", v: m[2] };
    } else if (m[3] !== undefined) {
      tok = { t: "code", v: m[3] };
    } else if (m[4] !== undefined) {
      tok = { t: "math", v: m[4], block: true };
    } else if (m[5] !== undefined) {
      tok = { t: "math", v: m[5], block: false };
    } else if (m[6] !== undefined) {
      const src = safeUrl(m[7]);
      tok = src ? { t: "img", src, alt: m[6], title: m[8] } : { t: "txt", v: full };
    } else if (m[9] !== undefined) {
      const href = safeUrl(m[10]);
      tok = href ? { t: "a", href, title: m[11], c: sub(m[9]) } : { t: "txt", v: full };
    } else if (m[12] !== undefined) {
      tok = { t: "a", href: "mailto:" + m[12], c: [{ t: "txt", v: m[12] }] };
    } else if (m[13] !== undefined) {
      const href = safeUrl(m[13]);
      tok = href ? { t: "a", href, c: [{ t: "txt", v: m[13] }] } : { t: "txt", v: full };
    } else if (m[14] !== undefined) {
      const pre = m[14] || "";
      const href = safeUrl(m[15]);
      if (href) {
        if (pre) tokens.push({ t: "txt", v: pre });
        tok = { t: "a", href, c: [{ t: "txt", v: m[15] }] };
      } else {
        tok = { t: "txt", v: full };
      }
    } else if (m[16] !== undefined) tok = { t: "bi", c: sub(m[16]) };
    else if (m[17] !== undefined) tok = { t: "b", c: sub(m[17]) };
    else if (m[18] !== undefined) tok = { t: "b", c: sub(m[18]) };
    else if (m[19] !== undefined) tok = { t: "i", c: sub(m[19]) };
    else if (m[20] !== undefined) tok = { t: "i", c: sub(m[20]) };
    else if (m[21] !== undefined) tok = { t: "s", c: sub(m[21]) };
    else if (m[22] !== undefined) tok = { t: "h", c: sub(m[22]) };
    else if (m[23] !== undefined) tok = { t: "sup", c: sub(m[23]) };
    else if (m[24] !== undefined) tok = { t: "sub", c: sub(m[24]) };
    else if (m[25] !== undefined) tok = { t: "fn", id: m[25] };
    if (tok) tokens.push(tok);
    pos = m.index + full.length;
  }
  if (pos < text.length) tokens.push({ t: "txt", v: text.slice(pos) });
  return tokens;
}

export { parseInline };
