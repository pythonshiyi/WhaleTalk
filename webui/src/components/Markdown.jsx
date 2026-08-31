import React, { useMemo } from "react";
import { unwrapLongText } from "../longTextUtil.js";
import { parseMarkdown } from "../mdParser.js";
import { parseInline } from "../mdInline.js";
import { highlight } from "../mdHighlight.js";

// ── 世界级 Markdown 渲染器（手写 · 零依赖 · 流式安全）────────────────
// 渲染链路：文本 → unwrapLongText(解除模型 @long-text 包装)
//        → parseMarkdown(块级 AST) → Block 分发
//        → parseInline(行内 tokens) → React DOM
//
// 能力清单：
//   块级：标题(1-6) / 段落 / 嵌套列表(缩进树) / 任务列表 / 嵌套引用 /
//         表格(带对齐) / 代码围栏(语法高亮 + 复制) / 数学块 /
//         details 折叠 / 脚注定义 / 分隔线
//   行内：粗体 / 斜体 / 粗斜体 / 删除线 / 高亮 ==…== / 上标 / 下标 /
//         行内 code / 行内公式 / 图片 / 链接 / 自动链接(URL/邮箱) /
//         脚注引用 / 转义字符，支持任意嵌套组合
//
// 安全性：所有文本先解析为纯数据 tokens 再渲染（无 HTML 注入）；
//         代码高亮输出在 mdHighlight 中整体转义后才注入。
// 流式安全：未闭合代码围栏由解析器产出 { t:'code-open' }，
//         deferCode=true 时暂不渲染，父组件在流结束后补渲染。

const LINK_OPTS = { onClick: (e) => e.preventDefault() };

// ── 行内 tokens 渲染 ─────────────────────────
function Tokens({ tokens }) {
  return tokens.map((p, i) => {
    switch (p.t) {
      case "txt":
        return <span key={i}>{p.v}</span>;
      case "b":
        return (
          <strong key={i}>
            <Tokens tokens={p.c} />
          </strong>
        );
      case "i":
        return (
          <em key={i}>
            <Tokens tokens={p.c} />
          </em>
        );
      case "bi":
        return (
          <strong key={i}>
            <em>
              <Tokens tokens={p.c} />
            </em>
          </strong>
        );
      case "s":
        return (
          <del key={i}>
            <Tokens tokens={p.c} />
          </del>
        );
      case "h":
        return (
          <mark key={i} className="md-mark">
            <Tokens tokens={p.c} />
          </mark>
        );
      case "sup":
        return (
          <sup key={i}>
            <Tokens tokens={p.c} />
          </sup>
        );
      case "sub":
        return (
          <sub key={i}>
            <Tokens tokens={p.c} />
          </sub>
        );
      case "code":
        return (
          <code key={i} className="md-ic">
            {p.v}
          </code>
        );
      case "math":
        return (
          <span key={i} className="md-math">
            {p.v}
          </span>
        );
      case "img":
        return <img key={i} src={p.src} alt={p.alt || ""} title={p.title} loading="lazy" className="md-img" />;
      case "a":
        return (
          <a key={i} href={p.href} title={p.title} {...LINK_OPTS}>
            <Tokens tokens={p.c} />
          </a>
        );
      case "fn":
        return (
          <sup key={i} className="md-fn-ref">
            <a href={`#fn-${p.id}`}>[{p.id}]</a>
          </sup>
        );
      default:
        return <span key={i}>{p.v}</span>;
    }
  });
}

const Inline = ({ text }) => <Tokens tokens={parseInline(text)} />;

// ── 代码块（高亮 + 复制）────────────────────────
function CodeBlock({ lang, code }) {
  const [copied, setCopied] = React.useState(false);
  const html = useMemo(() => highlight(code, lang), [code, lang]);
  return (
    <div className="md-code-wrap">
      <pre className="md-code">
        {lang && <span className="md-code-lang">{lang}</span>}
        <code dangerouslySetInnerHTML={{ __html: html }} />
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

// ── 表格（对齐感知）────────────────────────
function Table({ b }) {
  const alignStyle = (a) =>
    a ? { textAlign: a === "c" ? "center" : a === "r" ? "right" : "left" } : undefined;
  return (
    <div className="md-table-wrap">
      <table>
        <thead>
          <tr>
            {b.head.map((c, j) => (
              <th key={j} style={alignStyle(b.align[j])}>
                <Inline text={c} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {b.rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} style={alignStyle(b.align[j])}>
                  <Inline text={c} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── 嵌套列表（任务框）────────────────────────
function ListBlock({ block }) {
  const Tag = block.t === "ol" ? "ol" : "ul";
  const start = block.t === "ol" && block.items[0] && block.items[0].num ? block.items[0].num : undefined;
  return (
    <Tag className={`md-list md-list-${block.t}`} start={start}>
      {block.items.map((it, i) => (
        <li key={i} className={it.task ? "md-task" : ""}>
          {it.task && <input type="checkbox" className="md-task-box" checked={!!it.checked} readOnly disabled />}
          <span className="md-li-text">
            <Inline text={it.text} />
          </span>
          {it.children.length > 0 && (
            <div className="md-li-body">
              {it.children.map((c, j) => (
                <Block key={j} b={c} />
              ))}
            </div>
          )}
        </li>
      ))}
    </Tag>
  );
}

// ── 引用 ───────────────────────────────
function Quote({ b }) {
  return (
    <blockquote className="md-quote">
      {b.children.map((c, i) => (
        <Block key={i} b={c} />
      ))}
    </blockquote>
  );
}

// ── details 折叠 ─────────────────────────
function Details({ b }) {
  return (
    <details className="md-details">
      {b.summary && <summary>{b.summary}</summary>}
      <div className="md-details-body">
        {b.children.map((c, i) => (
          <Block key={i} b={c} />
        ))}
      </div>
    </details>
  );
}

// ── 脚注定义 ─────────────────────────
function Footnotes({ b }) {
  return (
    <section className="md-footnotes">
      <div className="md-hr" />
      {b.defs.map((d) => (
        <div className="md-fn-def" id={`fn-${d.id}`} key={d.id}>
          <sup className="md-fn-ref">[{d.id}]</sup>
          <span className="md-fn-text">
            <Inline text={d.text} />
          </span>
        </div>
      ))}
    </section>
  );
}

// ── 块级分发 ─────────────────────────
function Block({ b }) {
  switch (b.t) {
    case "heading": {
      const level = Math.min(Math.max(b.level, 1), 6);
      const Tag = `h${level}`;
      return (
        <Tag className={`md-h md-h${Math.min(level, 4)}`}>
          <Inline text={b.text} />
        </Tag>
      );
    }
    case "p":
      return (
        <p className="md-p">
          {b.lines.map((ln, i) => (
            <span key={i}>
              <Inline text={ln} />
              {i < b.lines.length - 1 && <br />}
            </span>
          ))}
        </p>
      );
    case "ul":
    case "ol":
      return <ListBlock block={b} />;
    case "quote":
      return <Quote b={b} />;
    case "table":
      return <Table b={b} />;
    case "code":
      return <CodeBlock lang={b.lang} code={b.code} />;
    case "code-open":
      return (
        <pre className="md-code md-code-open">
          <code>…</code>
        </pre>
      );
    case "math":
      return <div className="md-math-block">{b.expr}</div>;
    case "hr":
      return <div className="md-hr" />;
    case "details":
      return <Details b={b} />;
    case "footnotes":
      return <Footnotes b={b} />;
    default:
      return null;
  }
}

// ── 主组件 ─────────────────────────
export default function Markdown({ text, deferCode = false }) {
  const blocks = useMemo(() => parseMarkdown(unwrapLongText(text)).blocks, [text]);
  return (
    <div className="md">
      {blocks.map((b, i) => {
        if (b.t === "code-open" && deferCode) return null;
        return <Block key={i} b={b} />;
      })}
    </div>
  );
}
