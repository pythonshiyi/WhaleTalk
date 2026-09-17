import React from "react";
import * as api from "../api.js";
import * as brainNav from "../brainNav.js";
import { Icon } from "./icons.jsx";

// ── 大脑全局检索：一次搜索横跨 记忆 / 决策 / 快照 / 思考日志 ──
// 结果按来源分组；点击可深链到对应分区（记忆→记忆库并高亮，其余→指挥舱对应分区）。
const EMPTY = { memories: [], decisions: [], snapshots: [], thoughts: [] };

export default function BrainSearch() {
  const [q, setQ] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [res, setRes] = React.useState(null);
  const [err, setErr] = React.useState("");

  const run = async () => {
    const query = q.trim();
    if (!query) { setRes(null); setOpen(false); return; }
    setBusy(true); setErr(""); setOpen(true);
    const d = await api.brainAction({ action: "brain-search", q: query, limit: 20 }).catch(() => null);
    if (d && d.ok) setRes(d.data || EMPTY);
    else { setErr("检索失败（后端未连接或版本过旧？）"); setRes(EMPTY); }
    setBusy(false);
  };

  const total = res ? (res.memories.length + res.decisions.length + res.snapshots.length + res.thoughts.length) : 0;
  const close = () => { setOpen(false); };
  const clear = () => { setQ(""); setRes(null); setOpen(false); };

  return (
    <div className="brain-search-wrap">
      <div className="mem-search brain-search">
        <Icon name="search" size={15} />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          onFocus={() => res && setOpen(true)}
          placeholder="全局检索：记忆 / 决策 / 快照 / 思考日志（回车）"
          aria-label="大脑全局检索"
        />
        {q && <button className="msg-op" onClick={clear} aria-label="清空检索"><Icon name="x" size={13} /></button>}
        <button className="confirm-btn" style={{ padding: "3px 10px" }} disabled={busy || !q.trim()} onClick={run}>
          {busy ? "检索中…" : "检索"}
        </button>
      </div>
      {open && (
        <div className="brain-search-panel" role="region" aria-label="大脑检索结果">
          <div className="brain-search-head">
            <span>{busy ? "检索中…" : err ? err : `命中 ${total} 条`}</span>
            <button className="msg-op" onClick={close} aria-label="关闭结果"><Icon name="x" size={13} /></button>
          </div>
          {!busy && !err && total === 0 && <div className="empty-tip">没有匹配的记忆 / 决策 / 快照 / 思考。</div>}

          {res && res.memories.length > 0 && (
            <div className="brain-search-group">
              <div className="brain-search-group-title"><Icon name="book" size={13} /> 记忆 · {res.memories.length}</div>
              {res.memories.map((m) => (
                <button key={m.id} className="brain-search-item" onClick={() => { brainNav.focusMemory(m.id); close(); }}>
                  <span className="bs-kind">{m.type || "记忆"}</span>
                  <span className="bs-text">{m.text}</span>
                </button>
              ))}
            </div>
          )}

          {res && res.decisions.length > 0 && (
            <div className="brain-search-group">
              <div className="brain-search-group-title"><Icon name="target" size={13} /> 决策 · {res.decisions.length}</div>
              {res.decisions.map((d) => (
                <button key={d.id} className="brain-search-item" onClick={() => { brainNav.focusZone("growth"); close(); }}>
                  <span className="bs-kind">{d.status || "open"}</span>
                  <span className="bs-text">{d.decision}</span>
                </button>
              ))}
            </div>
          )}

          {res && res.thoughts.length > 0 && (
            <div className="brain-search-group">
              <div className="brain-search-group-title"><Icon name="activity" size={13} /> 思考日志 · {res.thoughts.length}</div>
              {res.thoughts.map((t, i) => (
                <button key={i} className="brain-search-item" onClick={() => { brainNav.focusZone("growth"); close(); }}>
                  <span className="bs-kind">{t.tag || t.date}</span>
                  <span className="bs-text">{String(t.text).slice(0, 140)}</span>
                </button>
              ))}
            </div>
          )}

          {res && res.snapshots.length > 0 && (
            <div className="brain-search-group">
              <div className="brain-search-group-title"><Icon name="archive" size={13} /> 时光备份 · {res.snapshots.length}</div>
              {res.snapshots.map((s) => (
                <button key={s.name} className="brain-search-item" onClick={() => { brainNav.focusZone("continuity"); close(); }}>
                  <span className="bs-kind">v{s.version}</span>
                  <span className="bs-text">{s.mtime} · {s.size_kb} KB</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
