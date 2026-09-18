import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";

// ②b 记忆洞察：置信度排序（重要度 × 时效衰减 × 命中反馈）+ 疑似冲突候选。
// 数据来自统一端点 insights=1，只读展示，不改动任何记忆。
export default function BrainInsights() {
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState("");

  const load = async () => {
    if (open) { setOpen(false); return; }
    setBusy(true); setErr(""); setOpen(true);
    const d = await api.memoryInsights("", 12).catch(() => null);
    if (d && d.items) setData(d);
    else { setErr("洞察加载失败（后端未连接或版本过旧？）"); setData(null); }
    setBusy(false);
  };

  const items = (data && data.items) || [];
  const conflicts = (data && data.conflicts) || [];
  const pct = (c) => `${Math.round((Number(c) || 0) * 100)}%`;

  return (
    <div className="brain-search-wrap">
      <button className="confirm-btn" style={{ padding: "4px 12px" }} onClick={load}>
        <Icon name="activity" size={13} /> {open ? "收起洞察" : "记忆洞察"}
      </button>
      {open && (
        <div className="brain-search-panel" role="region" aria-label="记忆洞察">
          <div className="brain-search-head">
            <span>{busy ? "计算中…" : err ? err : `高置信记忆 ${items.length} · 疑似冲突 ${conflicts.length}`}</span>
            <button className="msg-op" onClick={() => setOpen(false)} aria-label="关闭洞察"><Icon name="x" size={13} /></button>
          </div>
          {!busy && !err && items.length === 0 && <div className="empty-tip">暂无可展示的记忆。</div>}
          {items.length > 0 && (
            <div className="brain-search-group">
              <div className="brain-search-group-title"><Icon name="book" size={13} /> 按置信度</div>
              {items.map((m) => (
                <div key={m.id} className="brain-search-item" title={`置信度 ${pct(m.confidence)}`}>
                  <span className="bs-kind">{m.type || "记忆"} · {pct(m.confidence)}</span>
                  <span className="bs-text">{m.text}</span>
                </div>
              ))}
            </div>
          )}
          {conflicts.length > 0 && (
            <div className="brain-search-group">
              <div className="brain-search-group-title"><Icon name="target" size={13} /> 疑似冲突（待仲裁）</div>
              {conflicts.map((c, i) => (
                <div key={i} className="brain-search-item" title={`主题：${c.topic} · 相似度 ${c.similarity}`}>
                  <span className="bs-kind">{c.topic}</span>
                  <span className="bs-text">{c.a_text} ⇄ {c.b_text}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
