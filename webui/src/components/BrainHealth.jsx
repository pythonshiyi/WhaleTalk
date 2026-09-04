import React from "react";
import * as api from "../api.js";

// ── U7 大脑健康盘：体检分 + 问题清单 + 一键修复（F6 doctor / B4 snapshot_index）────────
// 点按加载，调 brainAction doctor 拉结构化健康数据；避免把 O(n²) 体检塞进每次 status 轮询。
function BrainHealth() {
  const [health, setHealth] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [fixing, setFixing] = React.useState(false);
  const [err, setErr] = React.useState("");

  const runDoctor = async (fix = false) => {
    if (fix) setFixing(true); else { setBusy(true); setErr(""); }
    const d = await api
      .brainAction(fix ? { action: "doctor-fix" } : { action: "doctor" })
      .catch(() => null);
    if (fix) {
      setFixing(false);
      // 修复后重新体检，刷新问题清单
      const d2 = await api.brainAction({ action: "doctor" }).catch(() => null);
      if (d2 && d2.ok) setHealth(d2);
    } else {
      setBusy(false);
      if (d && d.ok) setHealth(d);
      else setErr("体检请求失败（后端是否运行？）");
    }
  };
  React.useEffect(() => { runDoctor(false); }, []);

  if (err) return <div className="sched-text" style={{ color: "var(--danger)" }}>⚠ {err}</div>;
  if (!health) return <div className="sched-text" style={{ opacity: 0.7 }}>体检中…</div>;

  const score = health.score ?? 0;
  const scoreColor = score >= 85 ? "var(--ok, #2e9e5b)" : score >= 60 ? "var(--warn, #d99a1b)" : "var(--danger)";

  return (
    <div style={{ padding: "6px 2px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
        <div style={{ position: "relative", width: 86, height: 86 }}>
          <svg viewBox="0 0 84 84" width="86" height="86">
            <circle cx="42" cy="42" r="36" fill="none" stroke="var(--border-strong)" strokeWidth="8" />
            <circle
              cx="42" cy="42" r="36" fill="none"
              stroke={scoreColor} strokeWidth="8" strokeLinecap="round"
              strokeDasharray={`${(score / 100) * 226} 226`}
              transform="rotate(-90 42 42)"
            />
          </svg>
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22, fontWeight: 700, color: scoreColor }}>
            {score}
          </div>
        </div>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>大脑健康度</div>
          <div style={{ fontSize: 12, opacity: 0.75, marginTop: 2 }}>
            记忆 {health.memories ?? 0} · 陈旧 {health.stale ?? 0} · 疑似重复 {health.dups ?? 0} ·
            未回执决策 {health.open_decisions ?? 0} · 快照 {health.snapshots ?? 0}
            {health.open_conflicts ? ` · 冲突 ${health.open_conflicts}` : ""}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className="msg-op" disabled={busy} onClick={() => runDoctor(false)}>🔄 复查</button>
            {(health.problems || []).length > 0 && (
              <button className="confirm-btn" style={{ padding: "3px 12px" }} disabled={fixing} onClick={() => runDoctor(true)}>
                {fixing ? "修复中…" : "🧹 一键修复陈旧记忆"}
              </button>
            )}
          </div>
        </div>
      </div>
      {(health.problems || []).length > 0 ? (
        <ul style={{ margin: "12px 0 0", paddingLeft: 18, fontSize: 12, opacity: 0.9 }}>
          {(health.problems || []).map((p, i) => (
            <li key={i} style={{ margin: "3px 0" }}>{p}</li>
          ))}
        </ul>
      ) : (
        <div className="sched-text" style={{ opacity: 0.7, marginTop: 10 }}>✓ 状态良好，无需处理。</div>
      )}
    </div>
  );
}

export default BrainHealth;
