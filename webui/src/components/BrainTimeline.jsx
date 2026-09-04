import React from "react";
import * as api from "../api.js";

// ── U1 认知时间轴：把「记忆 / 决策 / 时光备份」合成一条可按时间排序、按类别筛选的叙事线 ──
// 让散落面板变成"大脑的自我成长叙事"——契合"大脑是可延续的自我"的定位。
const KIND_META = {
  memory: { label: "记忆", emoji: "📝", color: "var(--accent, #4a8cf7)" },
  decision: { label: "决策", emoji: "🎯", color: "var(--ok, #2e9e5b)" },
  snapshot: { label: "时光备份", emoji: "📦", color: "var(--warn, #d99a1b)" },
};

function normTs(ts) {
  const s = String(ts || "");
  return s ? s.replace("T", " ").replace(/\.\d+Z$/, "").replace(/\+08:00.*$/, "").slice(0, 16) : "";
}
function tsEpoch(ts) {
  const s = String(ts || "");
  if (!s) return 0;
  const m = s.match(/(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
  if (!m) return 0;
  const [_, y, mo, d, h, mi] = m;
  return Date.UTC(+y, +mo - 1, +d, +h, +mi);
}

function BrainTimeline({ snapshots = [] }) {
  const [memories, setMemories] = React.useState([]);
  const [decisions, setDecisions] = React.useState([]);
  const [err, setErr] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [kinds, setKinds] = React.useState({ memory: true, decision: true, snapshot: true });
  const [typeFilter, setTypeFilter] = React.useState("");

  React.useEffect(() => {
    (async () => {
      try {
        // 各自 catch 置 null 以便部分失败仍展示；但全失败要置 err，避免静默空白
        const [m, d] = await Promise.all([
          api.listBrainMemories("", 400).catch(() => null),
          api.brainAction({ action: "decisions-list", limit: 200 }).catch(() => null),
        ]);
        if (m && Array.isArray(m.items)) setMemories(m.items);
        if (d && d.data?.decisions) setDecisions(d.data.decisions);
        const bothFailed = !(m && Array.isArray(m.items)) && !(d && d.data?.decisions);
        if (bothFailed) setErr("加载失败（后端未连接或版本过旧？）");
      } catch (e) {
        setErr("加载失败：" + String(e));
      }
      setLoading(false);
    })();
  }, []);

  const events = React.useMemo(() => {
    const out = [];
    for (const m of memories) {
      if (!m || m.archived) continue;
      out.push({ kind: "memory", id: m.id, ts: m.ts, text: m.text, type: m.type, importance: m.importance });
    }
    for (const dc of decisions) {
      const st = dc.status === "kept" ? "✓" : dc.status === "reversed" ? "↺" : "·";
      out.push({ kind: "decision", id: dc.id, ts: dc.ts, text: `${st} ${dc.decision}`, type: "决策", importance: 0 });
    }
    for (const s of snapshots) {
      out.push({ kind: "snapshot", id: s.name, ts: s.mtime, text: `快照 v${s.version}`, type: "备份", importance: 0 });
    }
    return out.sort((a, b) => tsEpoch(b.ts) - tsEpoch(a.ts));
  }, [memories, decisions, snapshots]);

  const visible = events.filter((e) => {
    if (!kinds[e.kind]) return false;
    if (typeFilter && e.kind === "memory" && (e.type || "") !== typeFilter) return false;
    return true;
  });

  const memTypes = React.useMemo(() => {
    const s = new Set();
    for (const m of memories) if (m && m.type) s.add(m.type);
    return [...s];
  }, [memories]);

  return (
    <div>
      {err && <div className="sched-text" style={{ color: "var(--danger)" }}>⚠ {err}</div>}
      {/* 类别筛选 + 类型筛选 */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
        {Object.keys(KIND_META).map((k) => (
          <label key={k} className="msg-op" style={{ display: "inline-flex", gap: 4, cursor: "pointer", padding: "2px 8px", opacity: kinds[k] ? 1 : 0.45 }}>
            <input type="checkbox" checked={!!kinds[k]} onChange={(e) => setKinds((o) => ({ ...o, [k]: e.target.checked }))} />
            {KIND_META[k].emoji} {KIND_META[k].label}
          </label>
        ))}
        <select className="set-select" style={{ fontSize: 12, padding: "2px 6px" }} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">记忆·全部类型</option>
          {memTypes.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <span className="sched-text" style={{ marginLeft: "auto", fontSize: 12, opacity: 0.7 }}>
          {loading ? "加载中…" : `${visible.length} 个节点 · 沿时间轴回溯大脑`}
        </span>
      </div>
      {!loading && visible.length === 0 && (
        <div className="sched-text" style={{ opacity: 0.7, padding: "8px 0" }}>当前筛选下没有节点。</div>
      )}
      <div className="brain-timeline">
        {visible.map((e) => {
          const meta = KIND_META[e.kind];
          return (
            <div className="tl-item" key={e.kind + ":" + e.id}>
              <span className="tl-dot" style={{ background: meta.color }} />
              <div className="tl-body">
                <div className="tl-main">
                  <b style={{ color: meta.color }}>{meta.emoji} {meta.label}</b>
                  <span className="tl-meta">
                    {normTs(e.ts)}
                    {e.kind === "memory" && e.type ? ` · ${e.type}` : ""}
                    {e.kind === "memory" && e.importance ? ` · 重要度 ${e.importance}` : ""}
                  </span>
                </div>
                <div className="tl-text">{e.text || ""}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default BrainTimeline;
