import React from "react";
import Markdown from "./Markdown.jsx";
import ToolCard from "./ToolCard.jsx";
import { EditableTable, DocxEditable, TextDocPreview } from "./OfficePreview.jsx";
import PixelDocViewer from "./PixelDocViewer.jsx";
import * as api from "../api.js";
import { unwrapLongText } from "../longTextUtil.js";
import { cleanForSpeech, speakText, stopSpeak, primeAudio } from "../ttsUtil.js";

import { silentWarn } from "../quiet.js";
// 表格内嵌预览（CSV/XLSX）：分页展示，不超过后端返回的 rows 上限
function TablePreview({ header = [], rows = [], total = 0, name = "" }) {
  const [pg, setPg] = React.useState(0);
  const PER = 25;
  const pages = Math.max(1, Math.ceil((rows.length || 1) / PER));
  const cur = rows.slice(pg * PER, pg * PER + PER);
  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ opacity: .8, marginBottom: 4 }}>📊 {name}（{total > 0 ? total + " 行" : rows.length + " 行"}）</div>
      <div style={{ overflow: "auto", maxHeight: 300, border: "1px solid var(--border)", borderRadius: "var(--r-md)" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--fs-sm)" }}>{header.length > 0 && (
          <thead><tr>{(header || []).map((h, i) => (
            <th key={i} style={{ padding: "4px 8px", background: "var(--bg-3)", textAlign: "left", fontWeight: 600, borderBottom: "1px solid var(--border)", whiteSpace: "nowrap", color: "var(--text-2)" }}>{h}</th>
          ))}</tr></thead>
        )}
          <tbody>
            {(cur || []).map((r, ri) => (
              <tr key={ri}>
                {(r || []).map((c, ci) => (
                  <td key={ci} style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", whiteSpace: "nowrap", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>{c}</td>
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
      margin: "6px 0 4px", padding: "8px 12px", borderRadius: "var(--r-md)",
      background: "var(--bg-2)", fontSize: "var(--fs-sm)",
      color: "var(--text-1)", display: "flex", alignItems: "center", gap: 10,
    }}>
      <span style={{ fontWeight: 600, whiteSpace: "nowrap" }}>
        {streaming && (!done || running) ? "⏳ 任务进行中" : done >= total && total ? "✅ 任务完成" : "🔄 任务"}
      </span>
      <span style={{ flex: 1 }}>
        <div style={{ height: 6, borderRadius: 3, background: "var(--bg-3)", overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${pct}%`, background: "linear-gradient(90deg, var(--brand), var(--ai))", transition: "width .3s var(--ease)" }} />
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

// 从助手回复/工具结果中提取产物路径（已抽到 extractProducts.js 单例）

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
// 长会话性能（P1-5）：只在 msg 对象变化时重渲染。
// 流式更新已改为不可变（见 ChatPage.makePatchLast），未变化的消息保持同一对象
// 引用，因此本 memo 能让每帧增量只重渲染真正变化的最后一条，而不是整条消息列表。
// 为什么用自定义比较器而非默认浅比较：下面这些回调都是渲染时新建的内联箭头函数，
// 浅比较会永远判定「变了」而使 memo 失效。只比 msg 是安全的——其余 props 要么由
// msg 派生（onRegenerate/onContinue 取决于 msg.role / msg.streaming），要么与渲染
// 无关；回调闭包捕获的索引就是列表 key，msg 引用变化即意味着位置或内容变化。
function Message({ msg, onResend, onStar, onPin, onQuote, onFork, onEdit, onRegenerate, onContinue }) {
  const [copied, setCopied] = React.useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(unwrapLongText(msg.text || ""));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch (e) { silentWarn(e, "Message"); }
  };

  const [speaking, setSpeaking] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState("");

  // 朗读/停止切换：点击立即 ⏳（合成中）→ 播放 ⏹ → 失败 ⚠ 并提示原因。
  // 用 speakText 整段合成朗读：V4 一次性合成(或≤3900字分大块)连续播放，无逐句间隙、可随时停止。
  const toggleSpeak = () => {
    if (speaking || loading) {
      stopSpeak();
      setLoading(false);
      setSpeaking(false);
      return;
    }
    primeAudio();  // 借本次点击手势解锁音频管线（防自动播放拦截）
    if (!cleanForSpeech(unwrapLongText(msg.text))) return;
    stopSpeak();   // 手动朗读接管：先停掉任何正在的自动/别条朗读，避免两路同时播
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

  // 产物已迁出聊天（在右侧「🔧 活动」标签顶部置顶），聊天只保留正文。

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
        {(msg.streaming && msg.tools && msg.tools.length > 0) && (
          <TaskProgress tools={msg.tools} streaming />
        )}
        {msg.tools && msg.tools.length > 0 && (
          <>
            {/* 工具/产物详情已从聊天流移除：完整每步细节在侧栏「🔧 活动」标签里
                实时列出（点击展开看参数与结果），产物直达也置顶在活动顶部。
                聊天只保留正文与流式光标——不刷屏。 */}
            <button
              className="tool-summary"
              onClick={() => onFocusActivity && onFocusActivity()}
              title="在右侧『活动』查看工具详情与产物"
            >
              <span className="tool-summary-icon">🔧</span>
              <span className="tool-summary-text">
                {msg.streaming
                  ? `AI 正在调用工具（${msg.tools.length} 步）…`
                  : `本次调用 ${msg.tools.length} 个工具${msg.tools.some((t) => t.status === "failed") ? " · 部分失败" : " · 完成"} · 查看详情 ▸`}
              </span>
              <span className="tool-summary-chev">›</span>
            </button>
          </>
        )}
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
export default React.memo(Message, (a, b) => a.msg === b.msg);
