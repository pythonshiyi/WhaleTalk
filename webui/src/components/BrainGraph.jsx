import React from "react";
import * as api from "../api.js";
import * as brainNav from "../brainNav.js";
import { Icon } from "./icons.jsx";

// ── U3 实体图谱：把记忆里的实体(entities/relations)画成可读的节点-边图 ──
// 零依赖：圆形布局（节点尺寸随连接度、半径随节点数自适应），支持缩放/拖拽。
// 点击节点 = 按实体筛选记忆（brainNav → 记忆库）。
const W = 640, H = 420, CX = W / 2, CY = H / 2;

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function BrainGraph() {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState("");
  const [active, setActive] = React.useState(null);
  const [view, setView] = React.useState({ scale: 1, tx: 0, ty: 0 });
  const drag = React.useRef(null);

  React.useEffect(() => {
    (async () => {
      const d = await api.brainAction({ action: "graph-data" }).catch(() => null);
      if (d && d.ok && d.data) setData(d.data);
      else setErr("图谱加载失败");
    })();
  }, []);

  const layout = React.useMemo(() => {
    if (!data) return { nodes: [], edges: [], R: 150 };
    const ents = data.entities || [];
    const rels = data.relations || [];
    const deg = {};
    for (const en of ents) deg[en.name] = 0;
    for (const e of rels) {
      if (deg[e.from] !== undefined) deg[e.from]++;
      if (deg[e.to] !== undefined) deg[e.to]++;
    }
    const maxDeg = Math.max(1, ...Object.values(deg));
    const n = Math.max(ents.length, 1);
    const R = clamp(34 * Math.sqrt(n), 90, 250);
    const nodes = ents.map((en, i) => {
      const ang = (i / n) * Math.PI * 2 - Math.PI / 2;
      const dd = deg[en.name] || 0;
      return {
        ...en,
        name: en.name || en.id || `ent-${i}`,
        x: CX + R * Math.cos(ang),
        y: CY + R * Math.sin(ang),
        r: 7 + (dd / maxDeg) * 19,
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
    return { nodes, edges, R };
  }, [data]);

  const zoom = (f) => setView((v) => ({ ...v, scale: clamp(v.scale * f, 0.5, 2.6) }));
  const reset = () => setView({ scale: 1, tx: 0, ty: 0 });

  const onPointerDown = (e) => {
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e) => {
    if (!drag.current) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const sx = rect.width ? W / rect.width : 1;
    const sy = rect.height ? H / rect.height : 1;
    setView((v) => ({ ...v, tx: drag.current.tx + (e.clientX - drag.current.x) * sx, ty: drag.current.ty + (e.clientY - drag.current.y) * sy }));
  };
  const onPointerUp = () => { drag.current = null; };

  if (err) return <div className="sched-text" style={{ color: "var(--danger-text)" }}><Icon name="warning" size={12} /> {err}</div>;
  if (!data) return <div className="skeleton" style={{ width: "100%", height: 220, borderRadius: "var(--r-lg)" }} />;
  if (layout.nodes.length === 0)
    return <div className="sched-text" style={{ opacity: 0.7 }}>还没有实体节点——在记忆里标注实体/关系后会在这里出现知识图谱。</div>;

  const t = `translate(${view.tx} ${view.ty}) translate(${CX} ${CY}) scale(${view.scale}) translate(${-CX} ${-CY})`;

  return (
    <div className="brain-graph-wrap">
      <div className="brain-graph-tools">
        <button className="msg-op" onClick={() => zoom(1.2)} aria-label="放大"><Icon name="plus" size={13} /></button>
        <button className="msg-op" onClick={() => zoom(0.83)} aria-label="缩小">－</button>
        <button className="msg-op" onClick={reset} aria-label="重置视图"><Icon name="refresh" size={13} /></button>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`} width="100%"
        style={{ background: "transparent", display: "block", cursor: "grab", touchAction: "none" }}
        onMouseLeave={() => setActive(null)}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
      >
        <g transform={t}>
          {layout.edges.map((e, i) => (
            <line key={i} x1={e.from.x} y1={e.from.y} x2={e.to.x} y2={e.to.y}
              stroke="var(--border-strong)" strokeWidth={1.5} opacity={0.5} />
          ))}
          {layout.nodes.map((nd, i) => (
            <g key={nd.id || nd.name || i}
              onMouseEnter={() => setActive({ x: nd.x, y: nd.y, name: nd.name, types: (nd.types || []).join("、"), deg: nd.deg })}
              onClick={(ev) => { ev.stopPropagation(); brainNav.setEntityFilter(nd.name); }}
              style={{ cursor: "pointer" }}>
              <circle cx={nd.x} cy={nd.y} r={nd.r} fill="var(--brand)" opacity={0.85} stroke="var(--bg-0)" strokeWidth={1} />
              <text x={nd.x} y={nd.y + 4} textAnchor="middle" fontSize={nd.r > 14 ? 11 : 10} fontWeight={600}
                fill="var(--text-on-brand)" style={{ pointerEvents: "none" }}>
                {String(nd.name).length > 12 ? String(nd.name).slice(0, 12) + "…" : nd.name}
              </text>
            </g>
          ))}
          {active && (
            <g transform={`translate(${Math.min(Math.max(active.x, 70), W - 150)}, ${Math.max(active.y - 48, 10)})`} style={{ pointerEvents: "none" }}>
              <rect x="-6" y="-6" width={150} height={38} rx={6} fill="var(--bg-2)" opacity={0.95} stroke="var(--border-strong)" strokeWidth={0.5} />
              <text x="0" y="6" fontSize={11} fontWeight={600} fill="var(--text-1)">{active.name}</text>
              <text x="0" y="21" fontSize={10} fill="var(--text-2)">{active.types || "无类型"}{active.deg ? ` · 关联 ${active.deg}` : ""}</text>
            </g>
          )}
        </g>
      </svg>
      <div style={{ fontSize: "var(--fs-2xs)", opacity: 0.6, marginTop: 4 }}>
        {layout.nodes.length} 个实体 · {layout.edges.length} 条关系 —— 点击节点按实体筛选记忆；拖拽平移、右上角缩放。
      </div>
    </div>
  );
}

export default BrainGraph;
