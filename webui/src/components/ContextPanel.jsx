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
        {tab === "工具" && (
          <>
            <div className="ctx-group-title">已激活（4）</div>
            {data.tools.filter((t) => t.state === "on").map((t) => (
              <div className="ctx-row" key={t.name}>
                <span className="ctx-dot ctx-dot-on" />
                <span className="ctx-name">{t.name}</span>
                <span className="ctx-desc">{t.desc}</span>
              </div>
            ))}
            <div className="ctx-group-title">未激活（2）</div>
            {data.tools.filter((t) => t.state === "off").map((t) => (
              <div className="ctx-row" key={t.name}>
                <span className="ctx-dot" />
                <span className="ctx-name ctx-off">{t.name}</span>
                <span className="ctx-desc">{t.desc}</span>
              </div>
            ))}
            <button className="ctx-action">+ 激活工具组</button>
          </>
        )}
        {tab === "记忆" && (
          <>
            {data.memory.map((m) => (
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
        {tab === "用量" && (
          <div className="usage">
            <div className="usage-row"><span>输入 tokens</span><b>{data.usage.prompt.toLocaleString()}</b></div>
            <div className="usage-row"><span>输出 tokens</span><b>{data.usage.completion.toLocaleString()}</b></div>
            <div className="usage-row"><span>前缀缓存命中</span><b className="usage-ok">{data.usage.cached}</b></div>
            <div className="usage-row"><span>本会话成本</span><b>{data.usage.cost}</b></div>
            <div className="usage-bar">
              <div className="usage-bar-fill" style={{ width: "62%" }} />
            </div>
            <div className="usage-bar-label">上下文使用 62% · 31.2k / 50k tokens</div>
          </div>
        )}
      </div>
    </aside>
  );
}