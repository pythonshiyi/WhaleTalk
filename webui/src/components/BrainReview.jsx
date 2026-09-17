import React from "react";
import * as api from "../api.js";
import * as brainNav from "../brainNav.js";
import { Icon } from "./icons.jsx";

// ── 待复习（F4 间隔复习）：高价值记忆到达复习间隔时浮到眼前 ──
// 此前只注入对话上下文，用户看不到；此卡片把它显性化。无到期项时不渲染。
export default function BrainReview() {
  const [items, setItems] = React.useState(null);

  React.useEffect(() => {
    (async () => {
      const d = await api.brainAction({ action: "review-due", limit: 20 }).catch(() => null);
      setItems((d && d.ok ? d.data?.items : []) || []);
    })();
  }, []);

  if (!items || items.length === 0) return null;

  return (
    <div className="brain-card">
      <div className="brain-card-title">
        <Icon name="rotate" size={14} />
        <span>待复习</span>
        <i>高价值记忆已到复习间隔——趁记忆还新，回看一遍</i>
      </div>
      <div className="review-list">
        {items.map((e) => (
          <button key={e.id} className="review-item" onClick={() => brainNav.focusMemory(e.id)} title="在记忆库中查看">
            <span className="mem-tag">{e.type || "记忆"}</span>
            <span className="review-text">{e.text}</span>
            <span className="review-meta">重要度 {e.importance}{e.hit_count ? ` · 命中 ${e.hit_count}` : ""}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
