import React from "react";
import * as api from "../api.js";

// ── U3 实体图谱：把记忆里的实体(entities/relations)画成可读的节点-边图 ──
// 零依赖：用稳定的圆形布局（无动画循环），节点尺寸随连接度，边为关系。
const W = 640, H = 420, CX = W / 2, CY = H / 2, R = 150;

function BrainGraph() {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState("");
  const [active, setActive] = React.useState(null); // {x,y,text} 悬停提示

  React.useEffect(() => {
    (async () => {
      const d = await api.brainAction({ action: "graph-data" }).catch(() => null);
      if (d && d.ok && d.data) setData(d.data);
      else setErr("图谱加载失败");
    })();
  }, []);

  const layout = React.useMemo(() => {
    if (!data) return { nodes: [], edges: [] };
    const ents = data.entities || [];
    const rels = data.relations || [];
    // 连接度（出现于边的次数）
    const deg = {};
    for (const en of ents) deg[en.name] = 0;
    for (const e of rels) {
      if (deg[e.from] !== undefined) deg[e.from]++;
      if (deg[e.to] !== undefined) deg[e.to]++;
    }
    const maxDeg = Math.max(1, ...Object.values(deg));
    const n = Math.max(ents.length, 1);
    const nodes = ents.map((en, i) => {
      const ang = (i / n) * Math.PI * 2 - Math.PI / 2;
      const dd = deg[en.name] || 0;
      return {
        ...en,
        x: CX + R * Math.cos(ang),
        y: CY + R * Math.sin(ang),
        r: 8 + (dd / maxDeg) * 18,
        deg: dd,
      };
    });
    const byName = {};
    for (const nd of nodes) byName[nd.name] = nd;
    const edges = [];
    for (const e of rels) {
      const a = byName[e.from], b = byName[e.to];
      if (a && b) edges.push({ from: a, to: b, rel: e.rel });
    }
    return { nodes, edges };
  }, [data]);

  if (err) return <div className="sched-text" style={{ color: "var(--danger)" }}>⚠ {err}</div>;
  if (!data) return <div className="sched-text" style={{ opacity: 0.7 }}>图谱生成中…</div>;
  if (layout.nodes.length === 0)
    return <div className="sched-text" style={{ opacity: 0.7 }}>还没有实体节点——给记忆标注实体/关系后会在这里出现知识图谱。</div>;

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ background: "transparent", display: "block" }}
        onMouseLeave={() => setActive(null)}>
        {/* 边 */}
        {layout.edges.map((e, i) => (
          <line key={i} x1={e.from.x} y1={e.from.y} x2={e.to.x} y2={e.to.y}
            stroke="var(--border-strong)" strokeWidth={1.5} opacity={0.5} />
        ))}
        {/* 节点 */}
        {layout.nodes.map((nd) => (
          <g key={nd.id}
            onMouseEnter={() => setActive({ x: nd.x, y: nd.y, name: nd.name, types: (nd.types || []).join("、"), deg: nd.deg })}
            onMouseMove={(ev) => {
              const b = ev.currentTarget.ownerSVGElement.getBoundingClientRect();
              setActive({ x: nd.x, y: nd.y, name: nd.name, types: (nd.types || []).join("、"), deg: nd.deg });
            }}
            style={{ cursor: "pointer" }}>
            <circle cx={nd.x} cy={nd.y} r={nd.r} fill="var(--accent, #4a8cf7)" opacity={0.85} stroke="var(--bg, #fff)" strokeWidth={1} />
            <text x={nd.x} y={nd.y + 4} textAnchor="middle" fontSize={nd.r > 14 ? 11 : 10} fontWeight={600}
              fill="var(--text-1)" style={{ pointerEvents: "none" }}>{nd.name.length > 10 ? nd.name.slice(0, 10) + "…" : nd.name}</text>
          </g>
        ))}
        {active && (
          <g transform={`translate(${Math.min(Math.max(active.x, 70), W - 150)}, ${Math.max(active.y - 48, 10)})`}>
            <rect x="-6" y="-6" width={150} height={38} rx={6} fill="var(--panel, #222)" opacity={0.95} stroke="var(--border-strong)" strokeWidth={0.5} />
            <text x="0" y="6" fontSize={11} fontWeight={600} fill="var(--text-1)">{active.name}</text>
            <text x="0" y="21" fontSize={10} fill="var(--text-2)">{active.types || "无类型"}{active.deg ? ` · 关联 ${active.deg}` : ""}</text>
          </g>
        )}
      </svg>
      <div style={{ fontSize: 11, opacity: 0.6, marginTop: 4 }}>
        {layout.nodes.length} 个实体 · {layout.edges.length} 条关系 —— 悬停查看节点；在记忆里标注 entities/relations 会在此生长图谱。
      </div>
    </div>
  );
}

export default BrainGraph;
