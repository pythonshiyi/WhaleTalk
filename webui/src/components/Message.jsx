import React from "react";
import Markdown from "./Markdown.jsx";
import ToolCard from "./ToolCard.jsx";
import * as api from "../api.js";
import { cleanForSpeech, enqueueSpeak, stopSpeak, primeAudio } from "../ttsUtil.js";

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
      await navigator.clipboard.writeText(msg.text || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {}
  };

  const [speaking, setSpeaking] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState("");

  // 朗读/停止切换：点击立即 ⏳（合成中）→ 播放 ⏹ → 失败 ⚠ 并提示原因
  const toggleSpeak = () => {
    if (speaking || loading) {
      stopSpeak();
      setLoading(false);
      setSpeaking(false);
      return;
    }
    primeAudio();  // 借本次点击手势解锁音频管线（防自动播放拦截）
    const clean = cleanForSpeech(msg.text).slice(0, 4000);
    if (!clean) return;
    setErr("");
    setLoading(true);
    enqueueSpeak(clean, {}, () => { setLoading(false); setSpeaking(true); })
      .catch((e) => { setErr(e && e.message ? e.message : "朗读失败"); setTimeout(() => setErr(""), 4000); })
      .finally(() => { setLoading(false); setSpeaking(false); });
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
        {msg.tools && msg.tools.map((t, i) => (
          <ToolCard key={i} {...t} />
        ))}
        {!msg.streaming && products.length > 0 && (
          <div className="prod-bar">
            <span className="prod-label">📦 产物直达</span>
            {products.map((p) => (
              <span className="prod-chip" key={p} title={p}>
                <button className="prod-op" title="打开文件" onClick={() => prodAct(p, "open")}>打开</button>
                <span className="prod-name">{String(p).split(/[\\/]/).pop()}</span>
                <button className="prod-op" title="打开所在文件夹" onClick={() => prodAct(p, "opendir")}>⌖</button>
              </span>
            ))}
          </div>
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