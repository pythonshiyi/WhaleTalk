import React from "react";
import EmptyState from "./EmptyState.jsx";
import { Icon } from "./icons.jsx";
import * as api from "../api.js";

import { silentWarn } from "../quiet.js";

export default function ContextPanel({ data, onClose, loading = false, err = "" }) {
  const [tab, setTab] = React.useState("工具");
  const [pending, setPending] = React.useState([]);
  const [injection, setInjection] = React.useState((data && data.injection) || null);
  const [tip, setTip] = React.useState("");

  const loadPending = React.useCallback(() => {
    api.listPendingMemory()
      .then((d) => setPending((d && d.items) || []))
      .catch((e) => silentWarn(e, "ContextPanel"));
  }, []);

  // 注入回执是「最近一次装配」的服务端快照：打开面板/切到注入页时拉一次，
  // 否则只会停留在页面加载时的旧值（对话后不刷新 → 一直显示"暂无记录"）。
  const loadInjection = React.useCallback(() => {
    api.getContext()
      .then((d) => setInjection((d && d.last_injection) || null))
      .catch((e) => silentWarn(e, "ContextPanel"));
  }, []);

  React.useEffect(() => { loadPending(); loadInjection(); }, [loadPending, loadInjection]);
  React.useEffect(() => { if (tab === "记忆") loadPending(); }, [tab, loadPending]);
  React.useEffect(() => { if (tab === "注入") loadInjection(); }, [tab, loadInjection]);

  const resolve = async (ids, action) => {
    try {
      const d = await api.resolvePendingMemory({ action, ids });
      // 后端失败走 _fail_soft（HTTP 200 + ok:false），必须显式判 ok，否则误报成功
      if (d && d.ok === false) throw new Error(d.error || "操作失败");
      setPending((prev) => prev.filter((x) => !ids.includes(x.id)));
      setTip(action === "accept" ? "已写入长期记忆" : "已忽略");
    } catch {
      setTip("操作失败：后端未响应");
    }
    setTimeout(() => setTip(""), 2500);
  };

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
        {[{ id: "工具", icon: "grid" }, { id: "记忆", icon: "book" }, { id: "注入", icon: "layers" }, { id: "用量", icon: "chart" }].map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`ctx-tab ${tab === t.id ? "ctx-tab-on" : ""}`} onClick={() => setTab(t.id)}>
            <Icon name={t.icon} size={14} /> {t.id}
          </button>
        ))}
      </div>

      <div className="ctx-body">
        {tip && <div className="empty-tip">{tip}</div>}
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
            </>
          );
        })()}
        {tab === "记忆" && (() => {
          const mem = (data && data.memory) || [];
          return (
            <>
              {pending.length > 0 && (
                <>
                  <div className="ctx-group-title">待确认（{pending.length}）· 自动提炼，确认后才写入</div>
                  {pending.map((p) => (
                    <div className="mem-card mem-pending" key={p.id}>
                      <div className="mem-card-head">
                        <span className="mem-tag">{p.origin === "web" ? "来自外部" : "推断"}</span>
                        <span className="mem-id">{p.ts}</span>
                      </div>
                      <div className="mem-text">{p.text}</div>
                      <div className="mem-pending-ops">
                        <button className="ctx-action" onClick={() => resolve([p.id], "accept")}><Icon name="check" size={12} /> 记住</button>
                        <button className="ctx-action" onClick={() => resolve([p.id], "dismiss")}><Icon name="x" size={12} /> 忽略</button>
                      </div>
                    </div>
                  ))}
                </>
              )}
              <div className="ctx-group-title">长期记忆（{mem.length}）</div>
              {mem.length > 0 ? (
                mem.map((m) => (
                  <div className="mem-card" key={m.id}>
                    <div className="mem-card-head">
                      <span className="mem-id">{m.id}</span>
                      <span className="mem-tag">{m.tag}</span>
                    </div>
                    <div className="mem-text">{m.text}</div>
                  </div>
                ))
              ) : (
                <EmptyState compact icon="brain" title="还没有长期记忆"
                  hint="对话中要求记住的关键信息会沉淀到这里" />
              )}
            </>
          );
        })()}
        {tab === "注入" && (() => {
          const inj = injection;
          if (!inj || !(inj.injected || []).length) {
            return <EmptyState compact icon="layers" title="暂无注入记录"
              hint="发起一次对话后，这里显示本轮注入了哪些上下文、各占多少字符、谁被跳过或失败。" />;
          }
          return (
            <>
              <div className="ctx-group-title">已注入（{inj.injected.length} · 共 {inj.total_chars || 0} 字符）</div>
              {inj.injected.map((i) => (
                <div className="ctx-row" key={i.name}>
                  <span className="ctx-dot ctx-dot-on" />
                  <span className="ctx-name">{i.name}</span>
                  <span className="ctx-desc">{i.chars} 字{i.truncated ? " · 已截断" : ""}</span>
                </div>
              ))}
              {(inj.skipped || []).length > 0 && <div className="ctx-group-title">未注入（{inj.skipped.length}）</div>}
              {(inj.skipped || []).map((s, idx) => (
                <div className="ctx-row" key={`${s.name}-${idx}`}>
                  <span className="ctx-dot" />
                  <span className="ctx-name ctx-off">{s.name}</span>
                  <span className="ctx-desc">{s.reason}</span>
                </div>
              ))}
              {(inj.failed || []).length > 0 && <div className="ctx-group-title">失败（{inj.failed.length}）</div>}
              {(inj.failed || []).map((f, idx) => (
                <div className="ctx-row" key={`${f.name}-${idx}`}>
                  <span className="ctx-dot" style={{ background: "var(--danger)" }} />
                  <span className="ctx-name">{f.name}</span>
                  <span className="ctx-desc">{f.error}</span>
                </div>
              ))}
              {inj.quiet_mode && <div className="empty-tip">本轮为纯净对话：个性上下文（记忆/自我/大脑）已全部停注入。</div>}
            </>
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
