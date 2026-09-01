import React from "react";

// ── ChatPage 的浮层面板集（P0-3 拆分）─────────────────
// 纯 JSX 机械提取：所有状态与回调仍在 ChatPage，经 props 传入；
// 渲染结构与行为与拆分前完全一致。共 7 个面板：批量任务 / 命令 / 轨迹 / FIM / 变体 / 搜索 / 收藏。

// 📦 批量任务：多文件 + 指令模板 → 组装为一条批量指令塞入输入框
export function BatchPanel({ open, onClose, files, onFiles, tpl, onTpl, onDo }) {
  if (!open) return null;
  return (
    <div className="overlay-mask" onClick={onClose}>
      <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="overlay-head">
          <b>📦 批量任务</b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="overlay-body">
          <div className="ctx-group-title">文件列表（每行一个路径）</div>
          <textarea className="fim-input" rows={5} placeholder={"C:/work/data1.csv\nC:/work/data2.csv"} value={files} onChange={(e) => onFiles(e.target.value)} />
          <div className="ctx-group-title">指令模板（{'{file}'} 占位）</div>
          <input className="set-select set-combo" value={tpl} onChange={(e) => onTpl(e.target.value)} />
          <div className="svc-actions">
            <button className="confirm-btn confirm-primary" onClick={onDo} disabled={!files.trim()}>生成批量指令到输入框</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ⌨ 命令面板：Ctrl+K 唤起，输入即过滤命令列表 / 回车全局搜索
export function CmdPanel({
  open, onClose, query, onQuery, onSearch,
  onNewChat, onOpenSearch, onGoWorkbench, onOpenTimeline, onOpenVariants, onOpenFim, onOpenStar, onExport, onGoSettings,
}) {
  if (!open) return null;
  return (
    <div className="confirm-mask" onClick={onClose}>
      <div className="cmd-panel" onClick={(e) => e.stopPropagation()}>
        <input
          className="cmd-input"
          placeholder="输入命令或搜索会话…"
          autoFocus
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "Enter") {
              if (query) onSearch(query);
            }
          }}
        />
        <div className="cmd-list">
          {[
            { icon: "💬", label: "新对话", act: () => { onClose(); onNewChat(); } },
            { icon: "🔍", label: "全局搜索", act: () => { onClose(); onOpenSearch(); } },
            { icon: "⏱", label: "定时任务（工作台）", act: () => { onClose(); onGoWorkbench && onGoWorkbench(); } },
            { icon: "🕐", label: "会话轨迹", act: () => { onClose(); onOpenTimeline(); } },
            { icon: "🔄", label: "回复变体", act: () => { onClose(); onOpenVariants(); } },
            { icon: "✂", label: "FIM 代码补全", act: () => { onClose(); onOpenFim(); } },
            { icon: "⭐", label: "收藏与固定", act: () => { onClose(); onOpenStar(); } },
            { icon: "📋", label: "导出当前会话", act: () => { onClose(); onExport(); } },
            { icon: "⚙", label: "设置", act: () => { onClose(); onGoSettings && onGoSettings(); } },
          ]
            .filter((c) => !query || c.label.includes(query))
            .map((c, i) => (
              <div className="cmd-item" key={i} onClick={c.act}>
                <span>{c.icon}</span>
                <span>{c.label}</span>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}

// 🕐 会话轨迹：全部消息一览，点击跳转
export function TimelinePanel({ open, onClose, msgs, onGoto }) {
  if (!open) return null;
  return (
    <div className="overlay-mask" onClick={onClose}>
      <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="overlay-head">
          <b>🕐 会话轨迹（{msgs.length} 条）</b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="overlay-body">
          <div className="timeline">
            {msgs.map((m, i) => (
              <div key={i} className="tl-item" onClick={() => { onClose(); onGoto(i); }}>
                <span className="tl-icon">{m.role === "user" ? "💬" : "🤖"}</span>
                <span className="tl-role">{m.role === "user" ? "我" : "助手"}</span>
                <span className="tl-text">
                  {String(m.text || m.think || "").slice(0, 50) || (m.tools && m.tools.length ? `🔧 ${m.tools.length} 个工具调用` : "")}
                </span>
                <span className="gs-time">{m.time || ""}</span>
              </div>
            ))}
            {msgs.length === 0 && <div className="empty-tip">暂无消息</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ✂ FIM 代码补全（Beta）：前后缀 → 补全结果，可一键插入输入框
export function FimPanel({ open, onClose, prompt, onPrompt, suffix, onSuffix, result, busy, onDo, onInsert }) {
  if (!open) return null;
  return (
    <div className="overlay-mask" onClick={onClose}>
      <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="overlay-head">
          <b>✂ FIM 代码补全（Beta）</b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="overlay-body">
          <div className="ctx-group-title">前缀（必填）</div>
          <textarea className="fim-input" rows={4} placeholder="代码前缀…" value={prompt} onChange={(e) => onPrompt(e.target.value)} />
          <div className="ctx-group-title">后缀（可选）</div>
          <textarea className="fim-input" rows={2} placeholder="代码后缀…" value={suffix} onChange={(e) => onSuffix(e.target.value)} />
          <div className="svc-actions">
            <button className="confirm-btn confirm-primary" onClick={onDo} disabled={busy || !prompt.trim()}>
              {busy ? "补全中…" : "▶ 补全"}
            </button>
          </div>
          {result && (
            <div className="evo-file">
              <div className="evo-file-head"><b>结果</b></div>
              <pre>{result}</pre>
              <button className="msg-op" style={{ marginTop: 6 }} onClick={() => onInsert(result)}>↩ 插入输入框</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// 🔄 回复变体：重新生成时旧回复自动存档，可浏览/恢复
export function VariantPanel({ open, onClose, variants, onRestore }) {
  if (!open) return null;
  return (
    <div className="overlay-mask" onClick={onClose}>
      <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="overlay-head">
          <b>🔄 回复变体（{variants.length} 版）</b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="overlay-body">
          {variants.length === 0 && <div className="empty-tip">暂无变体——重新生成时旧回复自动保存</div>}
          {variants.map((v, i) => (
            <div className="star-item" key={i} onClick={() => onRestore(v)}>
              <span className="star-role">第 {variants.length - i} 版</span>
              <span className="star-text">{String(v.text).slice(0, 60)}</span>
              <span className="gs-time">{new Date(v.ts).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// 🔍 全局搜索：跨全部会话搜消息 / 跨会话搜产物路径
export function SearchPanel({
  open, onClose, query, onQuery, type, onType, results, onResults, busy, onBusy, onSearch, onOpen,
}) {
  if (!open) return null;
  return (
    <div className="overlay-mask" onClick={onClose}>
      <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="overlay-head">
          <b>🔍 全局搜索</b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="overlay-body">
          <div className="global-search-bar">
            <button className={`confirm-btn ${type === "message" ? "confirm-primary" : ""}`} onClick={() => { onType("message"); onResults([]); }}>💬 消息</button>
            <button className={`confirm-btn ${type === "artifact" ? "confirm-primary" : ""}`} onClick={() => { onType("artifact"); onResults([]); }}>📦 产物</button>
            <input
              className="set-select set-combo"
              placeholder={type === "artifact" ? "跨会话搜产物文件路径…（回车搜索）" : "跨全部会话搜索…（回车搜索）"}
              value={query}
              autoFocus
              onChange={(e) => onQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onSearch()}
            />
            <button className="confirm-btn confirm-primary" onClick={onSearch} disabled={busy || !query.trim()}>
              {busy ? "搜索中…" : "搜索"}
            </button>
          </div>
          <div className="global-search-results">
            {results.map((r, i) => (
              <div className="gs-item" key={i} onClick={() => onOpen(r)}>
                <div className="gs-line1">
                  <b>{r.session_name}</b>
                  <span>{r.role === "user" ? "我" : "助手"}</span>
                  <span className="gs-time">{r.time}</span>
                </div>
                <div className="gs-snippet">{r.snippet}</div>
              </div>
            ))}
            {query && !busy && results.length === 0 && <div className="empty-tip">无匹配结果</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ⭐ 收藏与固定：查看/取消收藏与固定消息（压缩时固定内容保留进摘要）
export function StarPanel({ open, onClose, msgs, onStar, onPin, onGoto }) {
  if (!open) return null;
  return (
    <div className="overlay-mask" onClick={onClose}>
      <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="overlay-head">
          <b>⭐ 收藏与固定</b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="overlay-body">
          <div className="ctx-group-title">⭐ 已收藏消息（{msgs.filter((m) => m.starred).length}）</div>
          {msgs.filter((m) => m.starred).length === 0 && <div className="empty-tip">暂无收藏——hover 消息点 ⭐</div>}
          {msgs.map((m, i) => m.starred && (
            <div className="star-item" key={i} onClick={() => onGoto(i)}>
              <span className="star-role">{m.role === "user" ? "我" : "助手"}</span>
              <span className="star-text">{String(m.text || "").slice(0, 60)}</span>
              <button className="msg-op" onClick={(e) => { e.stopPropagation(); onStar(i); }}>取消</button>
            </div>
          ))}
          <div className="ctx-group-title">📌 已固定消息（{msgs.filter((m) => m.pinned).length}，压缩时保留进摘要）</div>
          {msgs.filter((m) => m.pinned).length === 0 && <div className="empty-tip">暂无固定——hover 消息点 📌</div>}
          {msgs.map((m, i) => m.pinned && (
            <div className="star-item" key={i} onClick={() => onGoto(i)}>
              <span className="star-role">{m.role === "user" ? "我" : "助手"}</span>
              <span className="star-text">{String(m.text || "").slice(0, 60)}</span>
              <button className="msg-op" onClick={(e) => { e.stopPropagation(); onPin(i); }}>取消</button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
