// ── 块级 Markdown 解析器（手写 · 零依赖 · 流式安全）────────────────
// 产出纯数据 AST，渲染与测试分离。流式安全：未闭合代码围栏产出
// { t:'code-open' } 由组件决定延迟/占位；其余未闭合结构自然闭合。
//
// block 类型：
//   {t:'heading', level, text}
//   {t:'p', lines:[...]}
//   {t:'ul'|'ol', items:[{num?, task?, checked?, text, children:[blocks]}]}
//   {t:'quote', children:[blocks]}
//   {t:'table', align:['l'|'c'|'r'|null], head:[cells], rows:[[cells]]}
//   {t:'code', lang, code} | {t:'code-open', lang}
//   {t:'math', expr}
//   {t:'hr'} | {t:'details', summary, children:[blocks]}
//   {t:'footnotes', defs:[{id, text}]}（由解析器汇总，渲染时置于末尾）

const LIST_RE = /^(\s*)([-*+]|\d+\.)\s+(\[[ xX]\]\s+)?(.*)$/;
const SEP_RE = /^\s*\|?[\s:|-]+\|?\s*$/;
const HEAD_RE = /^(#{1,6})\s+(.*)$/;
const HR_RE = /^\s*([-*_])\s*(\1\s*){2,}$/;
const FENCE_RE = /^\s*(```|~~~)\s*([\w+#.-]*)\s*$/;
const QUOTE_RE = /^\s*>\s?/;
const TABLE_LINE_RE = /^\s*\|.*\|\s*$/;
const FN_DEF_RE = /^\s*\[\^([^\]]+)\]:\s*(.*)$/;
const DETAILS_OPEN_RE = /^\s*<details>\s*$/i;
const DETAILS_CLOSE_RE = /^\s*<\/details>\s*$/i;
const SUMMARY_RE = /^\s*<summary>\s*(.*?)\s*<\/summary>\s*$/i;

function trimLine(l) {
  return l.replace(/\s+$/, "");
}

// ── 表格 ──────────────────────────────
function parseTableRow(line) {
  const t = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return t.split("|").map((c) => c.trim());
}

function tryCollectTable(lines, i) {
  if (!TABLE_LINE_RE.test(lines[i])) return null;
  const sep = lines[i + 1];
  if (!sep || !SEP_RE.test(sep) || !sep.includes("-")) return null;
  const head = parseTableRow(lines[i]);
  const align = parseTableRow(sep).map((c) => {
    if (/^:.*:$/.test(c)) return "c";
    if (/^:/.test(c)) return "l";
    if (/:$/.test(c)) return "r";
    return null;
  });
  const rows = [];
  let j = i + 2;
  while (j < lines.length && TABLE_LINE_RE.test(lines[j])) {
    rows.push(parseTableRow(lines[j]));
    j++;
  }
  return { block: { t: "table", align, head, rows }, next: j };
}

// ── 代码围栏 ─────────────────────────
function tryCollectFence(lines, i) {
  const m = lines[i].match(FENCE_RE);
  if (!m) return null;
  const lang = m[2].split(/\s+/)[0] || "";
  const fence = m[1];
  let j = i + 1;
  const buf = [];
  let closed = false;
  for (; j < lines.length; j++) {
    const l = lines[j];
    if (l.trim() === fence || (fence === "```" && l.trim() === "~~~") || (fence === "~~~" && l.trim() === "```")) {
      closed = true;
      j++;
      break;
    }
    buf.push(l);
  }
  if (!closed) {
    return { block: { t: "code-open", lang, code: buf.join("\n") }, next: j };
  }
  return { block: { t: "code", lang, code: buf.join("\n") }, next: j };
}

// ── 列表（缩进树 + 任务 + 续行）─────────
function tryCollectList(lines, i) {
  const first = lines[i].match(LIST_RE);
  if (!first) return null;
  const baseIndent = first[1].length;
  const rows = []; // {indent, ordered, marker, num, task, checked, text, line, raw}
  let j = i;
  const raw = [];
  while (j < lines.length) {
    const line = lines[j];
    const lm = line.match(LIST_RE);
    if (lm && lm[1].length >= baseIndent) {
      const ordered = lm[2].endsWith(".");
      const taskM = lm[3];
      let text = lm[4];
      let checked = false;
      let task = false;
      if (taskM) {
        task = true;
        checked = /x/i.test(taskM.trim());
      }
      rows.push({
        indent: lm[1].length,
        ordered,
        marker: lm[2],
        num: ordered ? parseInt(lm[2], 10) : null,
        task,
        checked,
        text,
        line,
      });
      raw.push(line);
      j++;
      continue;
    }
    if (line.trim() === "") {
      // 空行：若下一行仍是列表行（松散列表），跳过空行继续
      if (j + 1 < lines.length && LIST_RE.test(lines[j + 1]) && lines[j + 1].match(LIST_RE)[1].length >= baseIndent) {
        raw.push(line);
        j++;
        continue;
      }
      break;
    }
    // 缩进的续行：属于上一个列表项（段落续行/子块），缩进必须大于 baseIndent
    const indentOf = (s) => s.match(/^\s*/)[0].length;
    if (indentOf(line) > baseIndent) {
      // 归类到上一个 item 的 body（可能为子代码块/子引用/普通续行）
      rows.push({ indent: indentOf(line), cont: true, text: line, line });
      raw.push(line);
      j++;
      continue;
    }
    break;
  }

  // 构建树：root 为顶层列表块数组；item.children 存子块（子列表块/子段落等）
  const root = []; // [{t:'ul'|'ol', items:[...]}]
  const stack = []; // {indent, item}
  const listBlockOf = (parent, kind) => {
    if (!parent) {
      let lb = root.length && root[root.length - 1].t === kind ? root[root.length - 1] : null;
      if (!lb) {
        lb = { t: kind, items: [] };
        root.push(lb);
      }
      return lb;
    }
    let lb = parent.children.length && parent.children[parent.children.length - 1].t === kind
      ? parent.children[parent.children.length - 1]
      : null;
    if (!lb) {
      lb = { t: kind, items: [] };
      parent.children.push(lb);
    }
    return lb;
  };
  for (const row of rows) {
    if (row.cont) {
      if (stack.length) stack[stack.length - 1].item.body.push(trimLine(row.text));
      else {
        const last = root.length && root[root.length - 1].t === "p" ? root[root.length - 1] : null;
        if (last) last.lines.push(trimLine(row.text));
        else root.push({ t: "p", lines: [trimLine(row.text)] });
      }
      continue;
    }
    const node = {
      marker: row.marker,
      ordered: row.ordered,
      num: row.num,
      task: row.task,
      checked: row.checked,
      text: row.text,
      body: [],
      children: [],
    };
    while (stack.length && row.indent <= stack[stack.length - 1].indent) stack.pop();
    const parent = stack.length ? stack[stack.length - 1].item : null;
    const lb = listBlockOf(parent, row.ordered ? "ol" : "ul");
    lb.items.push(node);
    stack.push({ indent: row.indent, item: node });
  }

  // body 续行再解析为段落/子块
  for (const lb of root) {
    if (lb.t === "ul" || lb.t === "ol") for (const item of lb.items) finalizeItem(item);
  }
  return { blocks: root, next: j };
}

function finalizeItem(item) {
  if (!item.body.length) return;
  // 递归把 body 行解析为子块（支持子段落/子代码/子引用/子列表）
  const sub = parseMarkdown(item.body.join("\n"));
  item.children.push(...sub.blocks);
  item.body = [];
  for (const c of item.children) {
    if (c.t === "ul" || c.t === "ol") for (const it of c.items) finalizeItem(it);
  }
}

// ── 引用 ─────────────────────────────
function tryCollectQuote(lines, i) {
  if (!QUOTE_RE.test(lines[i])) return null;
  const qlines = [];
  let j = i;
  while (j < lines.length) {
    const l = lines[j];
    if (QUOTE_RE.test(l)) {
      qlines.push(l.replace(QUOTE_RE, ""));
      j++;
    } else if (l.trim() === "" && j + 1 < lines.length && QUOTE_RE.test(lines[j + 1])) {
      qlines.push("");
      j++;
    } else break;
  }
  const sub = parseMarkdown(qlines.join("\n"));
  return { block: { t: "quote", children: sub.blocks }, next: j };
}

// ── details 折叠 ─────────────────────
function tryCollectDetails(lines, i) {
  if (!DETAILS_OPEN_RE.test(lines[i])) return null;
  let j = i + 1;
  let summary = "";
  const inner = [];
  for (; j < lines.length; j++) {
    const l = lines[j];
    if (DETAILS_CLOSE_RE.test(l)) {
      j++;
      break;
    }
    const sm = l.match(SUMMARY_RE);
    if (sm && !summary) summary = sm[1];
    else inner.push(l);
  }
  const sub = parseMarkdown(inner.join("\n"));
  return { block: { t: "details", summary, children: sub.blocks }, next: j };
}

// ── 主入口 ───────────────────────────
export function parseMarkdown(text, opts = {}) {
  const lines = String(text == null ? "" : text).split("\n");
  const blocks = [];
  const defs = new Map();
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // 代码围栏
    const fence = tryCollectFence(lines, i);
    if (fence) {
      if (fence.block.t === "code" && fence.block.lang === "math") {
        blocks.push({ t: "math", expr: fence.block.code });
      } else {
        blocks.push(fence.block);
      }
      i = fence.next;
      continue;
    }

    // 表格
    const table = tryCollectTable(lines, i);
    if (table) {
      blocks.push(table.block);
      i = table.next;
      continue;
    }

    // 标题
    const hm = line.match(HEAD_RE);
    if (hm) {
      blocks.push({ t: "heading", level: hm[1].length, text: hm[2].trim() });
      i++;
      continue;
    }

    // 分隔线
    if (HR_RE.test(line)) {
      blocks.push({ t: "hr" });
      i++;
      continue;
    }

    // 引用
    const quote = tryCollectQuote(lines, i);
    if (quote) {
      blocks.push(quote.block);
      i = quote.next;
      continue;
    }

    // 列表
    const list = tryCollectList(lines, i);
    if (list) {
      blocks.push(...list.blocks);
      i = list.next;
      continue;
    }

    // details
    const details = tryCollectDetails(lines, i);
    if (details) {
      blocks.push(details.block);
      i = details.next;
      continue;
    }

    // 脚注定义
    const fm = line.match(FN_DEF_RE);
    if (fm) {
      defs.set(fm[1], trimLine(fm[2]));
      i++;
      continue;
    }

    // 空行
    if (line.trim() === "") {
      i++;
      continue;
    }

    // 普通段落：收集连续非空、非块起始行
    const buf = [trimLine(line)];
    i++;
    while (i < lines.length) {
      const l = lines[i];
      const t = l.trim();
      if (t === "") break;
      if (FENCE_RE.test(l) || TABLE_LINE_RE.test(l) || HEAD_RE.test(l) || HR_RE.test(l) || QUOTE_RE.test(l) || LIST_RE.test(l) || DETAILS_OPEN_RE.test(l) || FN_DEF_RE.test(l)) break;
      buf.push(trimLine(l));
      i++;
    }
    blocks.push({ t: "p", lines: buf });
  }

  if (defs.size) {
    blocks.push({ t: "footnotes", defs: [...defs.entries()].map(([id, text]) => ({ id, text })) });
  }
  return { blocks };
}
