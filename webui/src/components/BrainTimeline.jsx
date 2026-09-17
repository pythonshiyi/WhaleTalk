import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";

// ── U1 认知时间轴：把「记忆 / 决策 / 思考日志 / 时光备份」合成一条可按时间排序、按类别筛选的叙事线 ──
// 让散落面板变成"大脑的自我成长叙事"——契合"大脑是可延续的自我"的定位。
const KIND_META = {
  memory: { label: "记忆", icon: "book", color: "var(--brand)" },
  decision: { label: "决策", icon: "target", color: "var(--ok-text)" },
  thought: { label: "思考", icon: "activity", color: "var(--ai)" },
  snapshot: { label: "时光备份", icon: "archive", color: "var(--warn-text)" },
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

// 时间轴数据源缓存：概览（前 5 条）与成长（完整）切换时不再重复拉取（12s TTL）。
// 记忆/决策发生变更时调用 bustBrainFeed() 使缓存失效。
let _feed = { at: 0, data: null };
const FEED_TTL = 12000;
export function bustBrainFeed() { _feed = { at: 0, data: null }; }

function BrainTimeline({ snapshots = [], limit = 0, hideFilters = false }) {
  const [memories, setMemories] = React.useState([]);
  const [decisions, setDecisions] = React.useState([]);
  const [thoughts, setThoughts] = React.useState([]);
  const [err, setErr] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [kinds, setKinds] = React.useState({ memory: true, decision: true, thought: true, snapshot: true });
  const [typeFilter, setTypeFilter] = React.useState("");
  const [expand, setExpand] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    const apply = (f) => {
      if (!alive) return;
      if (f.memories) setMemories(f.memories);
      if (f.decisions) setDecisions(f.decisions);
      if (f.thoughts) setThoughts(f.thoughts);
      if (f.err) setErr(f.err);
    };
    (async () => {
      if (_feed.data && Date.now() - _feed.at < FEED_TTL) {
        apply(_feed.data);
        setLoading(false);
        return;
      }
      try {
        // 各自 catch 置 null 以便部分失败仍展示；但全失败要置 err，避免静默空白
        const [m, d, t] = await Promise.all([
          api.listBrainMemories("", 400).catch(() => null),
          api.brainAction({ action: "decisions-list", limit: 200 }).catch(() => null),
          api.brainAction({ action: "thinking-list", days: 90, limit: 400 }).catch(() => null),
        ]);
        const f = {
          memories: (m && Array.isArray(m.items)) ? m.items : null,
          decisions: (d && d.data?.decisions) ? d.data.decisions : null,
          thoughts: (t && t.data?.items) ? t.data.items : null,
        };
        if (!f.memories && !f.decisions && !f.thoughts) f.err = "加载失败（后端未连接或版本过旧？）";
        _feed = { at: Date.now(), data: f };
        apply(f);
      } catch (e) {
        if (alive) setErr("加载失败：" + String(e));
      }
      if (alive) setLoading(false);
    })();
    return () => { alive = false; };
  }, []);

  const events = React.useMemo(() => {
    const out = [];
    for (const m of memories) {
      if (!m || m.archived) continue;
      out.push({ kind: "memory", id: m.id, ts: m.ts, text: m.text, type: m.type, importance: m.importance });
    }
    for (const dc of decisions) {
      const st = dc.status === "kept" ? "已采纳" : dc.status === "reversed" ? "已反转" : "待回执";
      out.push({ kind: "decision", id: dc.id, ts: dc.ts, text: `${st} · ${dc.decision}`, type: "决策", importance: 0 });
    }
    thoughts.forEach((t, i) => {
      out.push({
        kind: "thought", id: `${t.date || ""}#${i}`, ts: t.ts, type: t.tag || "思考",
        text: (t.tag ? `【${t.tag}】` : "") + String(t.text || ""), importance: 0,
      });
    });
    for (const s of snapshots) {
      out.push({ kind: "snapshot", id: s.name, ts: s.mtime, text: `快照 v${s.version}`, type: "备份", importance: 0 });
    }
    return out.sort((a, b) => tsEpoch(b.ts) - tsEpoch(a.ts));
  }, [memories, decisions, thoughts, snapshots]);

  const filtered = events.filter((e) => {
    if (!kinds[e.kind]) return false;
    if (typeFilter && e.kind === "memory" && (e.type || "") !== typeFilter) return false;
    return true;
  });
  const visible = (limit > 0 && !expand) ? filtered.slice(0, limit) : filtered;

  const memTypes = React.useMemo(() => {
    const s = new Set();
    for (const m of memories) if (m && m.type) s.add(m.type);
    return [...s];
  }, [memories]);

  return (
    <div>
      {err && <div className="sched-text" style={{ color: "var(--danger-text)" }}><Icon name="warning" size={12} /> {err}</div>}
      {/* 类别筛选 + 类型筛选（精简模式隐藏，供概览一眼可读） */}
      {!hideFilters && (
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
        {Object.keys(KIND_META).map((k) => (
          <label key={k} className="msg-op" style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer", padding: "2px 8px", opacity: kinds[k] ? 1 : 0.45 }}>
            <input type="checkbox" checked={!!kinds[k]} onChange={(e) => setKinds((o) => ({ ...o, [k]: e.target.checked }))} />
            <Icon name={KIND_META[k].icon} size={12} /> {KIND_META[k].label}
          </label>
        ))}
        <select className="set-select" style={{ fontSize: "var(--fs-xs)", padding: "2px 6px" }} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">记忆·全部类型</option>
          {memTypes.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <span className="sched-text" style={{ marginLeft: "auto", fontSize: "var(--fs-xs)", opacity: 0.7 }}>
          {loading ? "加载中…" : `${filtered.length} 个节点 · 沿时间轴回溯大脑`}
        </span>
      </div>
      )}
      {!loading && filtered.length === 0 && (
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
                  <b style={{ color: meta.color, display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <Icon name={meta.icon} size={12} /> {meta.label}
                  </b>
                  <span className="tl-meta">
                    {normTs(e.ts)}
                    {e.kind === "memory" && e.type ? ` · ${e.type}` : ""}
                    {e.kind === "memory" && e.importance ? ` · 重要度 ${e.importance}` : ""}
                    {e.kind === "thought" && e.type ? ` · ${e.type}` : ""}
                  </span>
                </div>
                <div className="tl-text">{e.text || ""}</div>
              </div>
            </div>
          );
        })}
      </div>
      {limit > 0 && filtered.length > limit && (
        <div className="brain-more">
          <button className="msg-op" onClick={() => setExpand(!expand)}>
            {expand ? "收起" : `显示更多（共 ${filtered.length} 条）`}
          </button>
        </div>
      )}
    </div>
  );
}

export default BrainTimeline;
