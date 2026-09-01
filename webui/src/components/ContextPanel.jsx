import React from "react";

export default function ContextPanel({ data, onClose }) {
  const [tab, setTab] = React.useState("工具");
  return (
    <aside className="ctx-panel">
      <div className="ctx-head">
        <b>上下文</b>
        <button className="icon-btn" onClick={onClose}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="ctx-tabs">
        {["工具", "记忆", "用量"].map((t) => (
          <button key={t} className={`ctx-tab ${tab === t ? "ctx-tab-on" : ""}`} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      <div className="ctx-body">
        {tab === "工具" && (() => {
          const tools = (data && data.tools) || [];
          const on = tools.filter((t) => t.state === "on");
          const off = tools.filter((t) => t.state === "off");
          return (
            <>
              <div className="ctx-group-title">已激活（{on.length}）</div>
              {on.map((t) => (
                <div className="ctx-row" key={t.name}>
                  <span className="ctx-dot ctx-dot-on" />
                  <span className="ctx-name">{t.name}</span>
                  <span className="ctx-desc">{t.desc}</span>
                </div>
              ))}
              <div className="ctx-group-title">未激活（{off.length}）</div>
              {off.map((t) => (
                <div className="ctx-row" key={t.name}>
                  <span className="ctx-dot" />
                  <span className="ctx-name ctx-off">{t.name}</span>
                  <span className="ctx-desc">{t.desc}</span>
                </div>
              ))}
              <button className="ctx-action">+ 激活工具组</button>
            </>
          );
        })()}
        {tab === "记忆" && (
          <>
            {((data && data.memory) || []).map((m) => (
              <div className="mem-card" key={m.id}>
                <div className="mem-card-head">
                  <span className="mem-id">{m.id}</span>
                  <span className="mem-tag">{m.tag}</span>
                </div>
                <div className="mem-text">{m.text}</div>
              </div>
            ))}
            <button className="ctx-action">检索记忆库</button>
          </>
        )}
        {tab === "用量" && (() => {
          const usage = (data || {}).usage || {};
          const hitRate = Math.min(100, Math.max(0, parseFloat(String(usage.cached || "").replace("%", "")) || 0));
          return (
            <div className="usage">
              <div className="usage-row"><span>本月输入 tokens</span><b>{(usage.prompt || 0).toLocaleString()}</b></div>
              <div className="usage-row"><span>本月输出 tokens</span><b>{(usage.completion || 0).toLocaleString()}</b></div>
              <div className="usage-row"><span>前缀缓存命中率</span><b className="usage-ok">{usage.cached || "—"}</b></div>
              <div className="usage-row"><span>本月成本</span><b>¥{Number(usage.cost || 0).toFixed(2)}</b></div>
              <div className="usage-bar">
                <div className="usage-bar-fill" style={{ width: `${hitRate}%` }} />
              </div>
              <div className="usage-bar-label">前缀缓存命中率 {Math.round(hitRate)}%</div>
            </div>
          );
        })()}
      </div>
    </aside>
  );
}