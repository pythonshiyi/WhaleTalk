import React from "react";
import EmptyState from "./EmptyState.jsx";
import { Icon } from "./icons.jsx";
import { confirmDialog, promptDialog } from "../dialog.js";
import ContextMenu, { useContextMenu } from "./ContextMenu.jsx";

import { silentWarn } from "../quiet.js";
import { tagColorClass } from "../tagColor.js";

export default function SessionList({ sessions, activeId, onPick, onClose, onDelete, onPin, onRename, onEditTags, onExport, onImport, onBatchDelete, onBatchPin, onBatchTag, hasMore, total, onLoadMore }) {
  const [q, setQ] = React.useState("");
  const [tagFilter, setTagFilter] = React.useState(null);
  const [editingId, setEditingId] = React.useState(null);
  const [editValue, setEditValue] = React.useState("");
  const [taggingId, setTaggingId] = React.useState(null);
  const [tagValue, setTagValue] = React.useState("");
  const [multiMode, setMultiMode] = React.useState(false);
  const [selected, setSelected] = React.useState(new Set());
  const [loadingMore, setLoadingMore] = React.useState(false);
  const lastClickedRef = React.useRef(null);   // Shift 连选锚点
  const menu = useContextMenu();
  const [width, setWidth] = React.useState(() => {
    try {
      return Number(localStorage.getItem("whaletalk.slwidth")) || 264;
    } catch {
      return 264;
    }
  });
  const fileRef = React.useRef(null);
  const dragRef = React.useRef(null);

  // 拖拽调宽
  const onDragStart = (e) => {
    const startX = e.clientX;
    const startW = width;
    const onMove = (ev) => {
      const w = Math.max(200, Math.min(420, startW + (ev.clientX - startX)));
      setWidth(w);
      try {
        localStorage.setItem("whaletalk.slwidth", String(w));
      } catch (e) { silentWarn(e, "SessionList"); }
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    // 存句柄以便「拖拽中卸载」时也能移除监听（否则 window 监听 + body 样式残留）
    dragRef.current = { onMove, onUp };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  // 卸载兜底：拖拽中卸载时清理全局监听与 body 样式（防 setState 于已卸载组件 + 光标样式残留）
  React.useEffect(() => {
    return () => {
      const d = dragRef.current;
      if (d) {
        if (d.onMove) window.removeEventListener("mousemove", d.onMove);
        if (d.onUp) window.removeEventListener("mouseup", d.onUp);
        dragRef.current = null;
      }
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, []);

  const allTags = [...new Set(sessions.flatMap((s) => s.tags || []))].slice(0, 12);

  const filtered = sessions.filter((s) => {
    const ql = q.toLowerCase();
    const tagMatch = tagFilter ? (s.tags || []).includes(tagFilter) : true;
    const qMatch = !ql
      ? true
      : s.title.toLowerCase().includes(ql) ||
        (ql.startsWith("#") && (s.tags || []).some((t) => t.toLowerCase().includes(ql.slice(1)))) ||
        (s.brief || "").toLowerCase().includes(ql);
    return tagMatch && qMatch;
  });

  const onFile = (f) => {
    if (!f || !onImport) return;
    const reader = new FileReader();
    reader.onload = () => onImport(String(reader.result || ""));
    reader.readAsText(f);
  };

  // ── 多选批量操作 ──
  const toggleMulti = () => {
    setMultiMode(!multiMode);
    setSelected(new Set());
    lastClickedRef.current = null;
  };
  const toggleSel = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  // Shift 区间连选：以最近一次点击为锚点，选中两者之间（按当前筛选顺序）的全部会话。
  const selectRange = (id) => {
    const ids = filtered.map((s) => s.id);
    const a = ids.indexOf(lastClickedRef.current);
    const b = ids.indexOf(id);
    if (a < 0 || b < 0) { toggleSel(id); return; }
    const [lo, hi] = a < b ? [a, b] : [b, a];
    setSelected((prev) => {
      const next = new Set(prev);
      for (let i = lo; i <= hi; i++) next.add(ids[i]);
      return next;
    });
  };
  const onRowClick = (id, e) => {
    if (e && e.shiftKey && lastClickedRef.current != null) {
      selectRange(id);
    } else {
      toggleSel(id);
      lastClickedRef.current = id;
    }
  };
  const selectAll = () => {
    const allIds = filtered.map((s) => s.id);
    // 用「筛选后是否已全选」判断，而非 selected.size——后者可能含被筛掉的项而误判
    const allSelected = allIds.length > 0 && allIds.every((id) => selected.has(id));
    setSelected(allSelected ? new Set() : new Set(allIds));
  };
  const exitMulti = () => {
    setMultiMode(false);
    setSelected(new Set());
    lastClickedRef.current = null;
  };
  const batchDelete = async () => {
    if (!selected.size) return;
    if (!(await confirmDialog(`删除选中的 ${selected.size} 个会话？删除后可在提示中撤销。`, { danger: true, okText: "删除" }))) return;
    onBatchDelete && onBatchDelete([...selected]);
    exitMulti();
  };
  const batchPin = (pinned) => {
    if (!selected.size || !onBatchPin) return;
    onBatchPin([...selected], pinned);
    exitMulti();
  };
  const batchTag = async () => {
    if (!selected.size || !onBatchTag) return;
    const ans = await promptDialog(`为选中的 ${selected.size} 个会话添加标签（逗号分隔）`, "");
    if (ans == null) return;
    const tags = String(ans).split(/[,，]/).map((t) => t.trim()).filter(Boolean);
    if (!tags.length) return;
    onBatchTag([...selected], tags);
    exitMulti();
  };
  const loadMore = async () => {
    if (!onLoadMore || loadingMore) return;
    setLoadingMore(true);
    try { await onLoadMore(); } finally { setLoadingMore(false); }
  };

  // 会话项右键菜单（与 hover 小按钮等价，触屏/键盘更可达）
  const openItemMenu = (e, s) => {
    menu.open(e, [
      { label: "打开", icon: "message", onClick: () => onPick(s.id) },
      { label: s.pinned ? "取消置顶" : "置顶", icon: "pin", onClick: () => onPin && onPin(s.id, !s.pinned) },
      { label: "重命名", icon: "pencil", onClick: () => { setEditingId(s.id); setEditValue(s.title); } },
      { label: "编辑标签", icon: "filter", onClick: () => { setTaggingId(s.id); setTagValue((s.tags || []).join(",")); } },
      { label: "多选", icon: "list", onClick: () => { setMultiMode(true); lastClickedRef.current = s.id; setSelected((prev) => new Set(prev).add(s.id)); } },
      { label: "删除", icon: "trash", danger: true, onClick: async () => { if (await confirmDialog(`删除会话「${s.title}」？`, { danger: true, okText: "删除" })) onDelete && onDelete(s.id); } },
    ]);
  };

  return (
    <aside className="session-list" style={{ width }}>
      <div className="sl-resize" onMouseDown={onDragStart} title="拖拽调整宽度" />
      <div className="sl-top">
        <div className="sl-search">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4-4" />
          </svg>
          <input
            placeholder="搜索会话（#标签）"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <button className="icon-btn" title={multiMode ? "退出多选" : "多选会话"} aria-label={multiMode ? "退出多选" : "多选会话"} onClick={toggleMulti} style={{ color: multiMode ? "var(--brand-strong)" : undefined }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" />
          </svg>
        </button>
        <button className="icon-btn" title="收起" aria-label="收起" onClick={onClose}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M15 18l-6-6 6-6" />
          </svg>
        </button>
      </div>

      {multiMode && (
        <div className="sl-multi-bar">
          <span className="sl-multi-count">已选 {selected.size} 个</span>
          <button className="msg-op" onClick={selectAll}>全选</button>
          {selected.size > 0 && <button className="msg-op" onClick={() => setSelected(new Set())}>清空</button>}
          <button className="msg-op" disabled={!selected.size} onClick={() => batchPin(true)} title="置顶选中会话"><Icon name="pin" size={13} /> 置顶</button>
          <button className="msg-op" disabled={!selected.size} onClick={() => batchPin(false)} title="取消置顶选中会话"><Icon name="pin" size={13} /> 取消置顶</button>
          <button className="msg-op" disabled={!selected.size} onClick={batchTag} title="为选中会话添加标签"><Icon name="filter" size={13} /> 标签</button>
          <button className="msg-op" style={{ color: "var(--danger-text)" }} disabled={!selected.size} onClick={batchDelete}>
            <Icon name="trash" size={13} /> 删除（{selected.size}）
          </button>
          <button className="msg-op" onClick={exitMulti}><Icon name="x" size={13} /> 退出</button>
        </div>
      )}

      {allTags.length > 0 && (
        <div className="sl-tagbar">
          {allTags.map((t) => (
            <button
              key={t}
              className={`sl-tag-chip ${tagFilter === t ? "sl-tag-chip-on" : ""}`}
              onClick={() => setTagFilter(tagFilter === t ? null : t)}
            >
              {t}
              {tagFilter === t && <span className="sl-tag-x"><Icon name="x" size={10} /></span>}
            </button>
          ))}
        </div>
      )}

      <div className="sl-new">
        <button className="sl-new-btn" onClick={() => onPick(null)}>
          <Icon name="plus" size={15} /> 新对话
        </button>
      </div>

      <div className="sl-actions">
        <button className="sl-action" onClick={() => fileRef.current?.click()} title="导入会话（JSON/JSONL）">
          <Icon name="upload" size={13} /> 导入
        </button>
        <button className="sl-action" onClick={onExport} title="导出当前会话（MD/TXT/HTML/JSONL）">
          <Icon name="download" size={13} /> 导出
        </button>
        <input ref={fileRef} type="file" accept=".json,.jsonl,.txt,.md" style={{ display: "none" }} onChange={(e) => {
          const f = e.target.files && e.target.files[0];
          if (f) onFile(f);
          e.target.value = "";
        }} />
      </div>

      <div className="sl-items">
        {filtered.length === 0 && (
          <EmptyState
            icon="message"
            title={sessions.length === 0 ? "还没有会话" : "没有匹配的会话"}
            hint={sessions.length === 0 ? "点击上方「新对话」开始，或直接在下方向 AI 提问。" : "换个关键词或标签再试试。"}
            compact
          />
        )}
        {filtered.map((s) => {
          const isSel = selected.has(s.id);
          return (
            <div
              key={s.id}
              role="button"
              tabIndex={0}
              className={`sl-item ${activeId === s.id ? "sl-item-on" : ""} ${multiMode ? "sl-item-multi" : ""} ${isSel ? "sl-item-selected" : ""}`}
              onClick={(e) => (multiMode ? onRowClick(s.id, e) : onPick(s.id))}
              onContextMenu={(e) => {
                // 内联重命名/标签输入框上右键保留原生文本菜单
                if (e.target && e.target.closest && e.target.closest("input, textarea")) return;
                openItemMenu(e, s);
              }}
              onKeyDown={(e) => {
                if (e.target !== e.currentTarget) return;
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  multiMode ? onRowClick(s.id, e) : onPick(s.id);
                }
              }}
              onDoubleClick={() => {
                if (!multiMode) {
                  setEditingId(s.id);
                  setEditValue(s.title);
                }
              }}
            >
              <div className="sl-item-line1">
                {multiMode && <span className={`multi-check ${isSel ? "multi-check-on" : ""}`}>{isSel ? "✓" : ""}</span>}
                {editingId === s.id ? (
                  <input
                    className="sl-inline-input"
                    autoFocus
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    onBlur={() => {
                      if (editValue.trim() && editValue !== s.title) onRename && onRename(s.id, editValue.trim());
                      setEditingId(null);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") e.target.blur();
                      if (e.key === "Escape") setEditingId(null);
                    }}
                  />
                ) : (
                  <b>{s.title}</b>
                )}
                {s.pinned && <span className="sl-pin" aria-hidden="true"><Icon name="pin" size={11} fill="currentColor" /></span>}
                {!multiMode && (
                  <span className="sl-item-actions">
                    <button className="sl-act" title={s.pinned ? "取消置顶" : "置顶"} aria-label="置顶" onClick={(e) => { e.stopPropagation(); onPin && onPin(s.id, !s.pinned); }}><Icon name="pin" size={13} fill={s.pinned ? "currentColor" : "none"} /></button>
                    <button className="sl-act" title="重命名" aria-label="重命名" onClick={(e) => { e.stopPropagation(); setEditingId(s.id); setEditValue(s.title); }}><Icon name="pencil" size={13} /></button>
                    <button className="sl-act" title="编辑标签" aria-label="编辑标签" onClick={(e) => { e.stopPropagation(); setTaggingId(s.id); setTagValue((s.tags || []).join(",")); }}><Icon name="filter" size={13} /></button>
                    <button className="sl-act" title="删除会话" aria-label="删除会话" onClick={async (e) => { e.stopPropagation(); if (await confirmDialog(`删除会话「${s.title}」？`, { danger: true, okText: "删除" })) onDelete && onDelete(s.id); }}><Icon name="trash" size={13} /></button>
                  </span>
                )}
              </div>
              {taggingId === s.id ? (
                <input
                  className="sl-inline-input"
                  autoFocus
                  placeholder="标签，逗号分隔"
                  value={tagValue}
                  onChange={(e) => setTagValue(e.target.value)}
                  onBlur={() => {
                    const tags = tagValue.split(/[,，]/).map((t) => t.trim()).filter(Boolean);
                    onEditTags && onEditTags(s.id, tags);
                    setTaggingId(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") e.target.blur();
                    if (e.key === "Escape") setTaggingId(null);
                  }}
                />
              ) : (
                <div className="sl-item-line2">{s.brief}</div>
              )}
              <div className="sl-item-line3">
                {s.tags && s.tags.length > 0 ? (
                  s.tags.slice(0, 2).map((t) => <span key={t} className={`sl-tag ${tagColorClass(t)}`}>{t}</span>)
                ) : (
                  <span className={`sl-tag ${tagColorClass(s.tag)}`}>{s.tag || "会话"}</span>
                )}
                <span className="sl-time">{s.time}</span>
                <span className="sl-model">{s.model}</span>
              </div>
            </div>
          );
        })}
        {hasMore && (
          <button className="sl-loadmore" disabled={loadingMore} onClick={loadMore}>
            {loadingMore ? "加载中…" : `加载更多${total ? `（共 ${total} 个）` : ""}`}
          </button>
        )}
      </div>
      <ContextMenu menu={menu.menu} onClose={menu.close} />
    </aside>
  );
}