import React from "react";
import * as api from "../api.js";

// ── U5 决策看板（Kanban）：open → kept / reversed 三列；卡片含「预期 vs 实际」，可回执 ──
// 让决策日志从"只写不读"变成可执行的验证闭环。
const COLS = [
  { key: "open", title: "⏳ 待回执" },
  { key: "kept", title: "✓ 已采纳" },
  { key: "reversed", title: "↺ 已反转" },
];

function fmt(iso) {
  const s = String(iso || "");
  return s ? s.replace("T", " ").slice(0, 16) : "";
}

function BrainKanban() {
  const [decs, setDecs] = React.useState(null);
  const [err, setErr] = React.useState("");

  const load = React.useCallback(async () => {
    const d = await api.brainAction({ action: "decisions-list", limit: 200 }).catch(() => null);
    if (d && d.data?.decisions) setDecs(d.data.decisions);
    else setErr("决策加载失败");
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const resolve = async (id, outcome, status) => {
    const d = await api.brainAction({ action: "decision-resolve", id, outcome, status }).catch(() => null);
    if (d && !d.ok) setErr(d.message || "回执失败");
    else { setErr(""); load(); }
  };

  const grouped = { open: [], kept: [], reversed: [] };
  for (const dc of decs || []) {
    const k = dc.status === "kept" ? "kept" : dc.status === "reversed" ? "reversed" : "open";
    grouped[k].push(dc);
  }

  return (
    <div>
      {err && <div className="sched-text" style={{ color: "var(--danger)", marginBottom: 8 }}>⚠ {err}</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, alignItems: "start" }}>
        {COLS.map((c) => (
          <div key={c.key} style={{ background: "var(--panel, rgba(127,127,127,.06))", borderRadius: 10, padding: 8 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
              {c.title} <span style={{ opacity: 0.6 }}>({grouped[c.key].length})</span>
            </div>
            {grouped[c.key].length === 0 && (
              <div className="sched-text" style={{ opacity: 0.5, fontSize: 12, padding: "4px 2px" }}>空</div>
            )}
            {grouped[c.key].map((dc) => (
              <div key={dc.id} className="mem-card" style={{ marginBottom: 6, padding: 8 }}>
                <div style={{ fontSize: 12.5, fontWeight: 500 }}>{dc.decision}</div>
                {dc.reason && <div style={{ fontSize: 11, opacity: 0.7, marginTop: 2 }}>理由：{dc.reason}</div>}
                {dc.expected && <div style={{ fontSize: 11, opacity: 0.7 }}>预期：{dc.expected}</div>}
                {dc.outcome && <div style={{ fontSize: 11, opacity: 0.8, marginTop: 2 }}>实际：{dc.outcome}</div>}
                <div style={{ fontSize: 10, opacity: 0.5, marginTop: 3 }}>{fmt(dc.ts)}</div>
                {c.key === "open" && <OpenCardResolver onResolve={resolve} />}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

// 待回执卡片的独立回执表单（每个卡片各自维护 outcome/status，互不干扰）
function OpenCardResolver({ onResolve }) {
  const [outcome, setOutcome] = React.useState("");
  const [status, setStatus] = React.useState("kept");
  const [busy, setBusy] = React.useState(false);
  return (
    <div style={{ marginTop: 6 }}>
      <input
        className="set-select set-combo"
        placeholder="实际结果"
        value={outcome}
        onChange={(e) => setOutcome(e.target.value)}
        style={{ width: "100%", fontSize: 12, marginBottom: 4 }}
      />
      <div style={{ display: "flex", gap: 4 }}>
        <select className="set-select" style={{ flex: 1, fontSize: 12 }} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="kept">✓ 采纳</option>
          <option value="reversed">↺ 反转</option>
        </select>
        <button className="msg-op" disabled={busy || !outcome.trim()} onClick={async () => {
          setBusy(true);
          await onResolve(outcome.trim(), status);
          setOutcome("");
          setStatus("kept");
          setBusy(false);
        }}>回执</button>
      </div>
    </div>
  );
}

export default BrainKanban;
