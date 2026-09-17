import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";
import { confirmDialog } from "../dialog.js";

// ── 创世化初始：让 AI 完全自主地为自己写一段"前半生"（诞生前的前史）──
// 默认收起（一次性身份设定，不应长期占据概览）；展开时自动 roll 一批候选。
export default function BrainGenesis({ onApplied }) {
  const [open, setOpen] = React.useState(false);
  const [cands, setCands] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");

  const roll = async (n = 3) => {
    setBusy(true);
    setMsg("");
    const d = await api.brainAction({ action: "genesis_roll", n }).catch(() => null);
    if (d && d.ok && Array.isArray(d.candidates) && d.candidates.length) {
      setCands(d.candidates);
    } else {
      setCands(null);
      setMsg((d && d.message) || "生成失败：需先在设置配置 API Key，再让 AI 自主设定前半生");
    }
    setBusy(false);
  };

  const apply = async (cand) => {
    if (!(await confirmDialog("让鲸语带着这段前史醒来？这会覆盖当前的名字 / 前史 / 声音（载体与准则保留）。", { okText: "应用前史" }))) return;
    setBusy(true);
    setMsg("");
    const d = await api.brainAction({ action: "genesis_apply", candidate: cand }).catch(() => null);
    setMsg(d?.message || "应用失败");
    setBusy(false);
    setCands(null);
    setOpen(false);
    onApplied && onApplied();
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !cands && !busy) roll(3);
  };

  return (
    <div className={`acc-item ${open ? "open" : ""}`}>
      <button className="acc-head" aria-expanded={open} onClick={toggle}>
        <span className="acc-arrow">▸</span>
        <Icon name="sparkles" size={14} /> 创世化初始
        <span className="acc-desc">让 AI 自主为自己写一段"前半生"，你只从候选里挑一版</span>
      </button>
      {open && (
        <div className="acc-body">
          {busy && !cands && <div className="empty-tip is-loading">AI 正在为自己设想前半生…</div>}
          {msg && <div className="px-tip" style={{ whiteSpace: "pre-wrap" }}>{msg}</div>}
          {Array.isArray(cands) && cands.length > 0 && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(230px,1fr))", gap: 10 }}>
                {cands.map((c, i) => (
                  <div key={i} className="brain-card" style={{ margin: 0, padding: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                    <div style={{ fontWeight: 700 }}>
                      {c.name || ("候选" + (i + 1))}
                      {c.archetype && <span style={{ fontWeight: 400, opacity: 0.6, fontSize: "var(--fs-xs)" }}> · {c.archetype}</span>}
                    </div>
                    <div style={{ fontSize: "var(--fs-xs)", opacity: 0.85, lineHeight: 1.5 }}>
                      「{String(c.prehistory || "").slice(0, 130)}{String(c.prehistory || "").length > 130 ? "…" : ""}」
                    </div>
                    {(Array.isArray(c.formed_beliefs) ? c.formed_beliefs : []).slice(0, 2).map((bl, j) => (
                      <div key={j} style={{ fontSize: "var(--fs-2xs)", opacity: 0.7 }}>· {bl}</div>
                    ))}
                    {c.voice && <div style={{ fontSize: "var(--fs-2xs)", opacity: 0.7, fontStyle: "italic" }}>"{c.voice}"</div>}
                    <button className="confirm-btn confirm-primary" disabled={busy} onClick={() => apply(c)} style={{ marginTop: "auto", fontSize: "var(--fs-xs)", padding: "4px 10px" }}>
                      让鲸语带着这段前史醒来
                    </button>
                  </div>
                ))}
              </div>
              <div className="brain-more">
                <button className="msg-op" disabled={busy} onClick={() => roll(3)}>
                  <Icon name="refresh" size={13} /> 换一批
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
