import React from "react";

// 骨架屏基础件（样式见 app.css 的 .skeleton / .skeleton-line / .skeleton-stack）。
// 用途：各页异步加载态复用，替代裸「加载中…」文字，减少布局跳动。
export function SkeletonLine({ w = "100%", h = 12, style }) {
  return <div className="skeleton skeleton-line" style={{ width: w, height: h, ...style }} />;
}

export function SkeletonCard({ lines = 3 }) {
  return (
    <div className="set-card">
      <SkeletonLine w="38%" h={16} style={{ marginBottom: 16 }} />
      {Array.from({ length: lines }).map((_, i) => (
        <SkeletonLine
          key={i}
          w={i === lines - 1 ? "72%" : "100%"}
          style={{ marginBottom: i === lines - 1 ? 0 : 10 }}
        />
      ))}
    </div>
  );
}

export function SkeletonList({ rows = 5 }) {
  return (
    <div className="skeleton-stack">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="skeleton" key={i} style={{ height: 52, borderRadius: "var(--r-lg)" }} />
      ))}
    </div>
  );
}

export function SkeletonPage({ title = "", hint = "正在加载…" }) {
  return (
    <div className="page">
      <div className="page-head">
        <h1>{title}</h1>
        <p>{hint}</p>
      </div>
      <div className="skeleton-stack" style={{ maxWidth: 720 }}>
        <SkeletonCard lines={3} />
        <SkeletonCard lines={2} />
      </div>
    </div>
  );
}
