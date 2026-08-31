import React from "react";
import Markdown from "./Markdown.jsx";
import ToolCard from "./ToolCard.jsx";
import * as api from "../api.js";
import { unwrapLongText } from "../longTextUtil.js";
import { cleanForSpeech, speakText, stopSpeak, primeAudio } from "../ttsUtil.js";

// 表格内嵌预览（CSV/XLSX）：分页展示，不超过后端返回的 rows 上限
function TablePreview({ header = [], rows = [], total = 0, name = "" }) {
  const [pg, setPg] = React.useState(0);
  const PER = 25;
  const pages = Math.max(1, Math.ceil((rows.length || 1) / PER));
  const cur = rows.slice(pg * PER, pg * PER + PER);
  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ opacity: .8, marginBottom: 4 }}>📊 {name}（{total > 0 ? total + " 行" : rows.length + " 行"}）</div>
      <div style={{ overflow: "auto", maxHeight: 300, border: "1px solid var(--border)", borderRadius: 8 }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>{header.length > 0 && (
          <thead><tr>{(header || []).map((h, i) => (
            <th key={i} style={{ padding: "4px 8px", background: "rgba(128,140,160,.15)", textAlign: "left", fontWeight: 600, borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>{h}</th>
          ))}</tr></thead>
        )}
          <tbody>
            {(cur || []).map((r, ri) => (
              <tr key={ri}>
                {(r || []).map((c, ci) => (
                  <td key={ci} style={{ padding: "4px 8px", borderBottom: "1px solid rgba(128,140,160,.12)", whiteSpace: "nowrap", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div style={{ marginTop: 6, display: "flex", gap: 8, alignItems: "center", fontSize: 12 }}>
          <button className="msg-op" onClick={() => setPg(Math.max(0, pg - 1))} disabled={pg === 0}>‹</button>
          <span style={{ opacity: .8 }}>{pg + 1}/{pages}</span>
          <button className="msg-op" onClick={() => setPg(Math.min(pages - 1, pg + 1))} disabled={pg >= pages - 1}>›</button>
        </div>
      )}
    </div>
  );
}

// 后台任务进度条：AI 执行多工具任务时展示「进行中 / 已完成」计数与进度，感知"真在干活"
function TaskProgress({ tools, streaming }) {
  const list = tools || [];
  const total = list.filter((t) => t && (t.tool || t.status)).length;
  const running = list.filter((t) => t && t.status === "running").length;
  const done = list.filter((t) => t && t.status === "done").length;
  const failed = list.filter((t) => t && t.status === "failed").length;
  if (!total) return null;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const cur = list.find((t) => t && t.status === "running");
  const currentTool = cur ? cur.tool : null;
  return (
    <div className="task-progress" style={{
      margin: "6px 0 4px", padding: "8px 12px", borderRadius: 10,
      background: "var(--bg-2, rgba(128,140,160,.08))", fontSize: 12.5,
      color: "var(--text, #ddd)", display: "flex", alignItems: "center", gap: 10,
    }}>
      <span style={{ fontWeight: 600, whiteSpace: "nowrap" }}>
        {streaming && (!done || running) ? "⏳ 任务进行中" : done >= total && total ? "✅ 任务完成" : "🔄 任务"}
      </span>
      <span style={{ flex: 1 }}>
        <div style={{ height: 6, borderRadius: 3, background: "rgba(128,140,160,.18)", overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${pct}%`, background: "linear-gradient(90deg,#0ea5e9,#2563eb)", transition: "width .3s" }} />
        </div>
      </span>
      <span className="task-prog-count" style={{ whiteSpace: "nowrap", opacity: .85 }}>
        {done}/{total} 步
        {running ? ` · ${currentTool ? "▶ " + currentTool : "执行中"}…` : failed ? ` · ${failed} 失败` : ""}
      </span>
    </div>
  );
}

const Whale = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 12c1.5-4 4-6 7-6 3.5 0 5.5 2 9 2 1.6 0 2.8-.6 4-1.5-1 3-3 4.5-5 4.8.6 1.4.9 2.9.9 4.5 0 .8-.1 1.6-.3 2.3-1-.4-1.8-1-2.2-1.8-.9 1-2.4 1.7-4.2 1.7s-3.3-.7-4.2-1.7c-.4.8-1.2 1.4-2.2 1.8A11 11 0 015 15c0-1.6.3-3.1.9-4.5C4.7 10.2 3.3 8.7 3 12z" />
  </svg>
);

// 从助手回复/工具结果中提取产物路径（仅匹配以产物扩展名结尾的绝对路径，
// 避免目录段如 D:\workspace\ 误匹配）
const ABS_PATH_RE = /[A-Za-z]:[\\/][^\s"“”“<>|,，；;]*?[.](?:md|txt|json|csv|xlsx|docx|pptx|pdf|png|jpg|jpeg|html|htm|zip|py|log)\b/gi;

function extractProducts(text) {
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

function ThinkBlock({ text, streaming }) {
  // 对齐原程序：思考卡片默认折叠，生成结束后自动收起
  const [open, setOpen] = React.useState(false);
  React.useEffect(() => {
    if (!streaming) setOpen(false);
  }, [streaming]);
  return (
    <div className={`think-block ${open ? "think-open" : ""}`}>
      <div className="think-head" onClick={() => setOpen(!open)}>
        <span className="think-dot" />
        <span>思考过程</span>
        {streaming && <span className="think-streaming">进行中</span>}
        <svg className="think-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </div>
      {open && <div className="think-body">{text}</div>}
    </div>
  );
}

// 悬停操作条：收藏/固定/引用/分叉/编辑/重新生成（对齐原程序右键菜单）
export default function Message({ msg, onResend, onStar, onPin, onQuote, onFork, onEdit, onRegenerate, onContinue }) {
  const [copied, setCopied] = React.useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(unwrapLongText(msg.text || ""));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {}
  };

  const [speaking, setSpeaking] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState("");
  const [preview, setPreview] = React.useState({});  // {path: {loading, data, err}}

  const loadPreview = async (path) => {
    const cur = preview[path];
    if (cur && (cur.data || cur.loading)) return;
    setPreview((s) => ({ ...s, [path]: { loading: true } }));
    try {
      const d = await api.previewFile(path);
      setPreview((s) => ({ ...s, [path]: { loading: false, data: d } }));
    } catch (e) {
      setPreview((s) => ({ ...s, [path]: { loading: false, err: e && e.message ? e.message : "预览失败" } }));
    }
  };

  // 朗读/停止切换：点击立即 ⏳（合成中）→ 播放 ⏹ → 失败 ⚠ 并提示原因。
  // 用 speakText 分句逐句合成播放：长回复不超时、更快出第一句，也便于随时停止。
  const toggleSpeak = () => {
    if (speaking || loading) {
      stopSpeak();
      setLoading(false);
      setSpeaking(false);
      return;
    }
    primeAudio();  // 借本次点击手势解锁音频管线（防自动播放拦截）
    if (!cleanForSpeech(unwrapLongText(msg.text))) return;
    setErr("");
    setLoading(true);
    speakText(msg.text, {}, {
      onSpeak: () => { setLoading(false); setSpeaking(true); },
      onDone: () => { setLoading(false); setSpeaking(false); },
      onError: (e) => { setErr(e && e.message ? e.message : "朗读失败"); setTimeout(() => setErr(""), 4000); setLoading(false); setSpeaking(false); },
    });
  };

  const time = msg.time || "";
  const isStarred = msg.starred;
  const isPinned = msg.pinned;

  // 产物路径：助手回复文本 ∪ 工具结果
  const products = React.useMemo(() => {
    if (msg.role === "user" || msg.role === "system" ) return [];
    const text = [msg.text, ...(msg.tools || []).map((t) => t.result || "")].join("\n");
    return extractProducts(text);
  }, [msg]);

  const prodAct = async (path, act) => {
    try {
      await api.api(`/v1/files/${act}`, { method: "POST", body: JSON.stringify({ path }) });
    } catch {}
  };

  if (msg.role === "user") {
    return (
      <div className="msg msg-user">
        <div className="msg-user-bubble">
          {isPinned && <span className="msg-flag msg-flag-pin">📌</span>}
          {msg.text}
        </div>
        <div className="msg-user-avatar">我</div>
        <div className="msg-ops">
          {time && <span className="msg-time">{time}</span>}
          <button className="msg-op" title={isPinned ? "取消固定" : "固定（压缩时保留进摘要）"} onClick={() => onPin && onPin()}>
            {isPinned ? "📌" : "📌"}
          </button>
          <button className="msg-op" title="从此分叉为新会话" onClick={() => onFork && onFork()}>🔀</button>
          <button className="msg-op" title="编辑并重发" onClick={() => onEdit && onEdit()}>✏️</button>
          <button className="msg-op" title="引用此消息回复" onClick={() => onQuote && onQuote()}>💬</button>
          <button className="msg-op" title="重新发送" onClick={() => onResend && onResend(msg.text)}>↻</button>
        </div>
      </div>
    );
  }

  return (
    <div className="msg msg-assistant">
      <div className="msg-avatar">
        <Whale />
      </div>
      <div className="msg-body">
        <div className="msg-head">
          <span className="msg-role">助手</span>
          {isStarred && <span className="msg-flag-emoji" title="已收藏">⭐</span>}
          {isPinned && <span className="msg-flag-emoji" title="已固定">📌</span>}
          {time && <span className="msg-time">{time}</span>}
        </div>
        {msg.think && (
          <ThinkBlock text={msg.think} streaming={msg.streaming} />
        )}
        {(msg.tools && msg.tools.length > 0 || msg.streaming) && (
          <TaskProgress tools={msg.tools} streaming={msg.streaming} />
        )}
        {msg.tools && msg.tools.map((t, i) => (
          <ToolCard key={i} {...t} />
        ))}
        {!msg.streaming && products.length > 0 && (
          <div className="prod-bar">
            <span className="prod-label">📦 产物直达</span>
            {products.map((p) => {
              const pv = preview[p];
              const canPreview = !pv || (pv.data && pv.data.previewable !== false) || pv.data === undefined;
              return (
                <span className="prod-chip" key={p} title={p}>
                  <button className="prod-op" title="打开文件" onClick={() => prodAct(p, "open")}>打开</button>
                  <span className="prod-name" style={{ cursor: canPreview ? "pointer" : "default" }}
                    onClick={() => canPreview && loadPreview(p)}
                    title={pv ? (pv.data ? (pv.data.previewable ? "点击查看预览" : pv.data.reason || "") : "预览") : "点击查看预览"}>
                    {String(p).split(/[\\/]/).pop()}
                  </span>
                  <button className="prod-op" title="打开所在文件夹" onClick={() => prodAct(p, "opendir")}>⌖</button>
                  {pv && (pv.data || pv.loading || pv.err) && (
                    <button className="prod-op" title={pv.data && pv.data.previewable ? "收起预览" : "关闭"} onClick={() => setPreview((s) => ({ ...s, [p]: undefined }))}>✕</button>
                  )}
                  {pv && pv.loading && <span className="prod-op">⏳</span>}
                </span>
              );
            })}
          </div>
        )}
        {products.length > 0 && products.map((p) => {
          const pv = preview[p];
          if (!pv || !pv.data) return null;
          const d = pv.data;
          return (
            <div className="prod-preview" key={"pv_" + p} style={{
              margin: "4px 0 10px", padding: 10, borderRadius: 10,
              background: "var(--bg-2, rgba(128,140,160,.08))", fontSize: 13, overflow: "auto",
            }}>
              <div style={{ marginBottom: 6, opacity: .8 }}>{d.name}{d.truncated ? "（已截断）" : ""}</div>
              {d.kind === "image" && d.data_uri && (
                // eslint-disable-next-line jsx-a11y/alt-text
                <img src={d.data_uri} style={{ maxWidth: "100%", maxHeight: 360, borderRadius: 8 }} />
              )}
              {d.kind === "html" && (
                <iframe title={d.name} srcDoc={d.content} sandbox="" style={{ width: "100%", height: 260, border: "1px solid var(--border)", borderRadius: 8, background: "#fff" }} />
              )}
              {d.kind === "md" && <Markdown text={d.content} deferCode={false} />}
              {d.kind === "text" && (
                <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit", fontSize: 12.5 }}>{d.content}</pre>
              )}
              {d.kind === "table" && (
                <TablePreview header={d.header} rows={d.rows} total={d.total_rows} name={d.name} />
              )}
              {d.kind === "pdf" && (
                <div style={{ marginTop: 4, fontSize: 12.5 }}>
                  <div style={{ opacity: .8, marginBottom: 4 }}>📄 PDF（{d.page_count || "?"} 页）· 首页文本预览</div>
                  {d.content ? (
                    <pre style={{ whiteSpace: "pre-wrap", margin: 0, maxHeight: 240, overflow: "auto", fontFamily: "inherit", fontSize: 12.5 }}>
                      {(d.content || "").slice(0, 4000)}
                    </pre>
                  ) : (
                    <div style={{ opacity: .8 }}>（无文本层，可能是扫描件，可用系统程序打开）</div>
                  )}
                  <button className="msg-op" style={{ marginTop: 6 }} onClick={() => prodAct(p, "open")}>用系统程序打开</button>
                </div>
              )}
              {d.kind === "doc" && (
                <div style={{ marginTop: 4, fontSize: 12.5 }}>
                  <div style={{ opacity: .8 }}>📄 {d.name}（Office 文档）——用系统程序打开查看</div>
                  <button className="msg-op" style={{ marginTop: 6 }} onClick={() => prodAct(p, "open")}>用系统程序打开</button>
                </div>
              )}
              {!d.previewable && <div style={{ opacity: .8 }}>{d.reason || "该格式不支持内嵌预览"}</div>}
            </div>
          );
        })}
        {msg.text && <Markdown text={msg.text} deferCode={msg.streaming} />}
        {msg.streaming && <span className="caret" />}
        {!msg.streaming && msg.text && (
          <div className="msg-ops">
            <button className="msg-op" title="复制回复" onClick={copy}>
              {copied ? "✓ 已复制" : "📋"}
            </button>
            <button className="msg-op" title={err ? ("朗读失败：" + err) : loading ? "正在合成语音…" : speaking ? "⏹ 停止朗读" : "🔊 朗读回复（服务端合成，跟随语音设置）"} style={err ? { color: "var(--danger)" } : undefined} onClick={toggleSpeak}>
              {err ? "⚠" : loading ? "⏳" : speaking ? "⏹" : "🔊"}
            </button>
            <button className="msg-op" title={isStarred ? "取消收藏" : "收藏"} onClick={() => onStar && onStar()}>
              {isStarred ? "⭐" : "☆"}
            </button>
            <button className="msg-op" title="引用此消息回复" onClick={() => onQuote && onQuote()}>💬</button>
            <button className="msg-op" title="编辑此消息并继续" onClick={() => onEdit && onEdit()}>✏️</button>
            <button className="msg-op" title="重新生成（旧版存变体）" onClick={() => onRegenerate && onRegenerate()}>🔄</button>
            <button className="msg-op" title="继续生成（Beta 续写）" onClick={() => onContinue && onContinue()}>▶ 继续</button>
            <button className="msg-op" title="从此分叉为新会话" onClick={() => onFork && onFork()}>🔀</button>
          </div>
        )}
      </div>
    </div>
  );
}