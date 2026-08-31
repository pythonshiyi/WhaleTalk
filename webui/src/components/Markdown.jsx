import React, { useMemo } from "react";
import { unwrapLongText } from "../longTextUtil.js";

// ── 轻量 Markdown 渲染器（流式安全）──────────────────
// 规则：未闭合的代码块交给父组件延迟，其余即时渲染。
// 渲染前先解除模型长文本包装（@long-text / <long_text_quote>），
// 否则 JSON 消息链里的 \n 转义会让表格/列表/加粗全部失效。

function splitBlocks(text) {
  const blocks = [];
  let rest = text;
  while (rest.length) {
    const fence = rest.match(/^```(\w*)\n?/m);
    if (fence && fence.index === 0) {
      const close = rest.indexOf("```", fence[0].length);
      if (close === -1) {
        blocks.push({ type: "code-open", lang: fence[1] });
        rest = "";
      } else {
        blocks.push({ type: "code", lang: fence[1], code: rest.slice(fence[0].length, close) });
        rest = rest.slice(close + 3);
        if (rest.startsWith("\n")) rest = rest.slice(1);
      }
      continue;
    }
    const nextFence = rest.search(/^```/m);
    const part = nextFence === -1 ? rest : rest.slice(0, nextFence);
    rest = nextFence === -1 ? "" : rest.slice(nextFence);
    if (part.trim()) splitProse(part, blocks);
  }
  return blocks;
}

function splitProse(part, blocks) {
  const lines = part.split("\n");
  let buf = [];
  const flush = () => {
    if (buf.length) {
      blocks.push({ type: "prose", lines: buf });
      buf = [];
    }
  };
  for (const line of lines) {
    const t = line.trim();
    if (t.startsWith("#")) {
      flush();
      const level = t.match(/^#{1,6}/)[0].length;
      blocks.push({ type: "heading", level, text: t.slice(level).trim() });
    } else if (t.startsWith("|") && t.endsWith("|") && line.includes("|")) {
      flush();
      const cells = t.split("|").slice(1, -1).map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) {
        blocks.push({ type: "thead-marker" });
      } else {
        blocks.push({ type: "row", cells });
      }
    } else if (t.startsWith(">")) {
      flush();
      blocks.push({ type: "quote", text: t.slice(1).trim() });
    } else if (t.startsWith("- ") || t.startsWith("* ")) {
      flush();
      blocks.push({ type: "li", text: t.slice(2) });
    } else if (/^\d+\.\s/.test(t)) {
      flush();
      blocks.push({ type: "oli", num: t.match(/^\d+/)[0], text: t.slice(t.indexOf(".") + 1).trim() });
    } else if (/^-{3,}$/.test(t)) {
      flush();
      blocks.push({ type: "hr" });
    } else {
      buf.push(line);
    }
  }
  flush();
}

function inline(text) {
  const parts = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push({ t: "txt", v: text.slice(last, m.index) });
    const s = m[0];
    if (s.startsWith("**")) parts.push({ t: "b", v: s.slice(2, -2) });
    else if (s.startsWith("`")) parts.push({ t: "code", v: s.slice(1, -1) });
    else {
      const inner = s.slice(1, -1).split("](");
      parts.push({ t: "a", v: inner[0], href: inner[1] });
    }
    last = m.index + s.length;
  }
  if (last < text.length) parts.push({ t: "txt", v: text.slice(last) });
  return parts;
}

const Inline = ({ text }) => (
  <>
    {inline(text).map((p, i) => {
      if (p.t === "b") return <strong key={i}>{p.v}</strong>;
      if (p.t === "code") return <code key={i}>{p.v}</code>;
      if (p.t === "a")
        return (
          <a key={i} href={p.href} onClick={(e) => e.preventDefault()}>
            {p.v}
          </a>
        );
      return <span key={i}>{p.v}</span>;
    })}
  </>
);

function CodeBlock({ lang, code }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <div className="md-code-wrap">
      <pre className="md-code">
        {lang && <span className="md-code-lang">{lang}</span>}
        <code>{code}</code>
      </pre>
      <button
        className="md-code-copy"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          } catch {}
        }}
      >
        {copied ? "✓ 已复制" : "复制"}
      </button>
    </div>
  );
}

const Table = ({ rows }) => (
  <div className="md-table-wrap">
    <table>
      {rows.map((r, i) => (
        <tr key={i}>
          {r.cells.map((c, j) => (i === 0 ? <th key={j}>{c}</th> : <td key={j}><Inline text={c} /></td>))}
        </tr>
      ))}
    </table>
  </div>
);

export default function Markdown({ text, deferCode = false }) {
  const blocks = useMemo(() => splitBlocks(unwrapLongText(text)), [text]);
  const rows = [];
  const out = [];
  for (const b of blocks) {
    if (b.type === "row") rows.push(b);
    else if (b.type === "thead-marker") {
      // skip
    } else {
      if (rows.length) {
        // 快照复制：Table 必须持有独立数组，否则共享引用会在 rows.length=0 时
        // 被清空，导致所有表格渲染为空（历史 bug：多表格内容整段消失）。
        out.push(<Table key={out.length} rows={rows.slice()} />);
        rows.length = 0;
      }
      if (b.type === "heading") {
        const Tag = `h${Math.min(b.level, 4)}`;
        out.push(
          <Tag key={out.length} className={`md-h md-h${Math.min(b.level, 4)}`}>
            <Inline text={b.text} />
          </Tag>
        );
      } else if (b.type === "prose") {
        out.push(
          <p key={out.length}>
            {b.lines.map((ln, i) => (
              <span key={i}>
                <Inline text={ln} />
                {i < b.lines.length - 1 && <br />}
              </span>
            ))}
          </p>
        );
      } else if (b.type === "li") {
        out.push(
          <div className="md-li" key={out.length}>
            <span className="md-li-dot">•</span>
            <span>
              <Inline text={b.text} />
            </span>
          </div>
        );
      } else if (b.type === "oli") {
        out.push(
          <div className="md-li" key={out.length}>
            <span className="md-li-num">{b.num}.</span>
            <span>
              <Inline text={b.text} />
            </span>
          </div>
        );
      } else if (b.type === "quote") {
        out.push(
          <blockquote key={out.length}>
            <Inline text={b.text} />
          </blockquote>
        );
      } else if (b.type === "hr") {
        out.push(<div className="md-hr" key={out.length} />);
      } else if (b.type === "code") {
        out.push(
          <CodeBlock key={out.length} lang={b.lang} code={b.code} />
        );
      } else if (b.type === "code-open" && !deferCode) {
        out.push(
          <pre className="md-code md-code-open" key={out.length}>
            <code>…</code>
          </pre>
        );
      }
    }
  }
  if (rows.length) out.push(<Table key={out.length} rows={rows.slice()} />);
  return <div className="md">{out}</div>;
}