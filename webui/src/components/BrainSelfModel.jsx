import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";

// ── 自我认知（self_model.json）+ 大脑演化账本（evolution.json）──
// 两者每轮对话都会被注入/影响 AI，此前完全不可见。此组件把它们摊开给用户看，
// 并提供「重新校准自我认知」（LLM 动态校准）。
function List({ title, items, tone }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="sm-list">
      <div className="sm-list-title" style={{ color: tone }}>{title}</div>
      <ul>
        {items.map((x, i) => <li key={i}>{x}</li>)}
      </ul>
    </div>
  );
}

export function BrainSelfModel() {
  const [open, setOpen] = React.useState(false);
  const [sm, setSm] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");

  const load = async () => {
    const d = await api.brainAction({ action: "self-model" }).catch(() => null);
    if (d && d.ok) setSm(d.data?.self_model || {});
  };
  React.useEffect(() => { if (open && !sm) load(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = async () => {
    setBusy(true); setMsg("");
    const d = await api.brainAction({ action: "self-refresh" }).catch(() => null);
    setMsg(d?.message || "校准请求失败");
    await load();
    setBusy(false);
  };

  const src = sm?.source === "llm" ? "LLM 动态校准" : "静态模板";

  return (
    <div className={`acc-item ${open ? "open" : ""}`}>
      <button className="acc-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="acc-arrow">▸</span>
        <Icon name="eye" size={14} /> 自我认知
        <span className="acc-desc">{open && sm ? src : "我知道 / 我不确定 / 我的局限"}</span>
      </button>
      {open && (
        <div className="acc-body">
          {!sm && <div className="empty-tip is-loading">正在读取自我认知…</div>}
          {sm && (
            <>
              <div className="sm-grid">
                <List title="我知道" items={sm.knows} tone="var(--ok-text)" />
                <List title="我不确定" items={sm.unknowns} tone="var(--warn-text)" />
                <List title="我的局限" items={sm.limits} tone="var(--danger-text)" />
              </div>
              <div className="brain-more">
                {msg && <span className="sched-text" style={{ fontSize: "var(--fs-2xs)", marginRight: "auto", opacity: 0.8 }}>{msg}</span>}
                <button className="msg-op" disabled={busy} onClick={refresh}>
                  <Icon name="refresh" size={13} /> {busy ? "校准中…" : "重新校准"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function BrainEvolution() {
  const [open, setOpen] = React.useState(false);
  const [evo, setEvo] = React.useState(null);

  React.useEffect(() => {
    if (!open || evo) return;
    (async () => {
      const d = await api.brainAction({ action: "evolution-list" }).catch(() => null);
      setEvo((d && d.ok ? d.data : null) || { proposals: [], adopted: [] });
    })();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const proposals = evo?.proposals || [];
  const adopted = evo?.adopted || [];

  return (
    <div className={`acc-item ${open ? "open" : ""}`}>
      <button className="acc-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="acc-arrow">▸</span>
        <Icon name="sparkles" size={14} /> 大脑演化账本
        <span className="acc-desc">大脑自身的能力演化史（提案 → 采纳）</span>
      </button>
      {open && (
        <div className="acc-body">
          {!evo && <div className="empty-tip is-loading">正在读取演化账本…</div>}
          {evo && proposals.length === 0 && adopted.length === 0 && (
            <div className="sched-text" style={{ opacity: 0.7 }}>暂无演化记录。</div>
          )}
          {proposals.length > 0 && (
            <div className="sm-list">
              <div className="sm-list-title">提案</div>
              <ul>{proposals.slice(-12).reverse().map((p, i) => (
                <li key={i}><b>{p.id}</b> · {p.title} <span style={{ opacity: 0.6 }}>[{p.status || "proposed"}]</span></li>
              ))}</ul>
            </div>
          )}
          {adopted.length > 0 && (
            <div className="sm-list">
              <div className="sm-list-title" style={{ color: "var(--ok-text)" }}>已采纳</div>
              <ul>{adopted.slice(-12).reverse().map((a, i) => (
                <li key={i}><b>{a.id}</b> · {a.title || a.implemented || ""}</li>
              ))}</ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
