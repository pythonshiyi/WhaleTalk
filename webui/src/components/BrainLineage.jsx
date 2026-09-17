import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";

// ── 快照血缘图：把「时光备份」从平铺列表升级成一张 git-log 式 DAG ──
// 节点=快照版本/融合事件；边=链式延续 / 恢复来源 / 融合。数据来自 brain_action('lineage')，
// 只读元数据（快照清单 + .lineage.json + merge_log.json），无需解包快照。
const DX = 96, Y = 120, MY = 44, PAD = 44;

export default function BrainLineage() {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState("");

  React.useEffect(() => {
    (async () => {
      const d = await api.brainAction({ action: "lineage" }).catch(() => null);
      if (d && d.ok && d.data) setData(d.data);
      else setErr("血缘图加载失败（后端未连接？）");
    })();
  }, []);

  if (err) return <div className="sched-text" style={{ color: "var(--danger-text)" }}><Icon name="warning" size={12} /> {err}</div>;
  if (!data) return <div className="skeleton" style={{ width: "100%", height: 140, borderRadius: "var(--r-lg)" }} />;

  const nodes = data.nodes || [];
  const snaps = nodes.filter((n) => n.kind === "snapshot");
  const merges = nodes.filter((n) => n.kind === "merge");
  if (snaps.length === 0) {
    return <div className="sched-text" style={{ opacity: 0.7 }}>还没有快照——备份一次后，这里会长出大脑的时间线。</div>;
  }

  const idx = new Map(snaps.map((n, i) => [n.id, i]));
  const width = Math.max(360, PAD * 2 + (snaps.length - 1) * DX);
  const pos = {};
  snaps.forEach((n) => { pos[n.id] = { x: PAD + idx.get(n.id) * DX, y: Y }; });
  merges.forEach((n) => {
    const ax = n.a != null && idx.has(`v${n.a}`) ? pos[`v${n.a}`].x : PAD;
    const bx = n.b != null && idx.has(`v${n.b}`) ? pos[`v${n.b}`].x : PAD;
    pos[n.id] = { x: (ax + bx) / 2, y: MY };
  });

  const edges = (data.edges || []).filter((e) => pos[e.from] && pos[e.to]);

  return (
    <div className="brain-lineage-scroll">
      <svg viewBox={`0 0 ${width} 168`} width={width} height={168} style={{ display: "block", maxWidth: "none" }} aria-label="快照血缘图">
        {edges.map((e, i) => {
          const a = pos[e.from], b = pos[e.to];
          const dash = e.kind === "restore" ? "5 4" : undefined;
          const color = e.kind === "merge" ? "var(--ai)" : e.kind === "restore" ? "var(--warn)" : "var(--border-strong)";
          return (
            <path
              key={i}
              d={`M ${a.x} ${a.y} L ${a.x} ${(a.y + b.y) / 2} L ${b.x} ${(a.y + b.y) / 2} L ${b.x} ${b.y}`}
              fill="none" stroke={color} strokeWidth={e.kind === "chain" ? 1.6 : 2} strokeDasharray={dash}
            />
          );
        })}
        {snaps.map((n) => (
          <g key={n.id}>
            <circle cx={pos[n.id].x} cy={pos[n.id].y} r={n.current ? 13 : 10}
              fill={n.current ? "var(--brand)" : "var(--bg-1)"}
              stroke={n.current ? "var(--brand)" : "var(--border-strong)"} strokeWidth={2} />
            <text x={pos[n.id].x} y={pos[n.id].y + 4} textAnchor="middle" fontSize="10" fontWeight="700"
              fill={n.current ? "var(--text-on-brand)" : "var(--text-2)"}>v{n.version}</text>
            <text x={pos[n.id].x} y={pos[n.id].y + 26} textAnchor="middle" fontSize="9" fill="var(--text-3)">{n.mtime}</text>
          </g>
        ))}
        {merges.map((n) => (
          <g key={n.id}>
            <rect x={pos[n.id].x - 7} y={pos[n.id].y - 7} width="14" height="14" rx="3"
              transform={`rotate(45 ${pos[n.id].x} ${pos[n.id].y})`}
              fill="var(--ai-soft)" stroke="var(--ai)" strokeWidth="1.6" />
            <text x={pos[n.id].x} y={pos[n.id].y - 16} textAnchor="middle" fontSize="9" fill="var(--ai)">融合</text>
          </g>
        ))}
      </svg>
      <div className="brain-lineage-legend">
        <span><i className="ll-dot chain" /> 延续</span>
        <span><i className="ll-dot restore" /> 恢复来源</span>
        <span><i className="ll-dot merge" /> 融合事件</span>
        <span style={{ marginLeft: "auto", opacity: 0.7 }}>{snaps.length} 个快照 · 当前 v{data.current}</span>
      </div>
    </div>
  );
}
