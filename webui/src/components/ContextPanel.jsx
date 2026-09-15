import React from "react";
import EmptyState from "./EmptyState.jsx";
import { Icon } from "./icons.jsx";

export default function ContextPanel({ data, onClose, loading = false, err = "" }) {
  const [tab, setTab] = React.useState("工具");
  return (
    <aside className="ctx-panel">
      <div className="ctx-head">
        <b>上下文</b>
        <button className="icon-btn" onClick={onClose} title="关闭" aria-label="关闭">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="ctx-tabs" role="tablist">
        {[{ id: "工具", icon: "grid" }, { id: "记忆", icon: "book" }, { id: "用量", icon: "chart" }].map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`ctx-tab ${tab === t.id ? "ctx-tab-on" : ""}`} onClick={() => setTab(t.id)}>
            <Icon name={t.icon} size={14} /> {t.id}
          </button>
        ))}
      </div>

      <div className="ctx-body">
        {loading ? (
          <div className="empty-tip is-loading">正在读取上下文…</div>
        ) : err ? (
          <div className="empty-tip is-err">{err}</div>
        ) : (
        <>
        {tab === "工具" && (() => {
          const tools = (data && data.tools) || [];
          if (tools.length === 0) {
            return <EmptyState compact icon="settings" title="上下文尚未装配" hint="发起一次对话后，这里会显示已激活/未激活的工具。" />;
          }
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
        {tab === "记忆" && (() => {
          const mem = (data && data.memory) || [];
          return mem.length > 0 ? (
            <>
              {mem.map((m) => (
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
          ) : (
            <EmptyState compact icon="brain" title="还没有长期记忆"
              hint="对话中要求记住的关键信息会沉淀到这里" />
          );
        })()}
        {tab === "用量" && (() => {
          const usage = (data || {}).usage || {};
          const s = (data || {}).session || null;
          const su = (s && s.usage) || {};
          const sessionCachePct = su.prompt > 0 ? Math.round(((su.cache_hit || 0) / su.prompt) * 100) : 0;
          const hitRate = Math.min(100, Math.max(0, parseFloat(String(usage.cached || "").replace("%", "")) || 0));
          if (!usage.prompt && !usage.completion && (!s || !s.turns)) {
            return <EmptyState compact icon="chart" title="暂无用量数据" hint="完成一次对话后，这里会显示 token 用量与缓存命中率。" />;
          }
          return (
            <div className="usage">
              {s && s.turns > 0 && (
                <>
                  <div className="ctx-group-title">本会话（{s.turns} 轮）</div>
                  <div className="usage-row"><span>输入 tokens</span><b>{(su.prompt || 0).toLocaleString()}</b></div>
                  <div className="usage-row"><span>输出 tokens</span><b>{(su.completion || 0).toLocaleString()}</b></div>
                  <div className="usage-row"><span>缓存命中</span><b className="usage-ok">{su.cache_hit ? `${sessionCachePct}%` : "—"}</b></div>
                  <div className="usage-row"><span>平均输出速率</span><b className={s.tps > 0 ? "usage-ok" : ""}>{s.tps > 0 ? `${s.tps} tok/s` : "—"}</b></div>
                  <div className="usage-row"><span>首字延迟</span><b>{s.ttft != null ? `${(s.ttft / 1000).toFixed(1)}s` : "—"}</b></div>
                  <div className="usage-row"><span>累计耗时</span><b>{(s.total_ms / 1000).toFixed(1)}s</b></div>
                </>
              )}
              <div className="ctx-group-title">本月累计</div>
              <div className="usage-row"><span>输入 tokens</span><b>{(usage.prompt || 0).toLocaleString()}</b></div>
              <div className="usage-row"><span>输出 tokens</span><b>{(usage.completion || 0).toLocaleString()}</b></div>
              <div className="usage-row"><span>前缀缓存命中率</span><b className="usage-ok">{usage.cached || "—"}</b></div>
              <div className="usage-row"><span>本月成本</span><b>¥{Number(usage.cost || 0).toFixed(2)}</b></div>
              <div className="usage-bar">
                <div className="usage-bar-fill" style={{ transform: `scaleX(${hitRate / 100})` }} />
              </div>
              <div className="usage-bar-label">前缀缓存命中率 {Math.round(hitRate)}%</div>
            </div>
          );
        })()}
        </>
        )}
      </div>
    </aside>
  );
}