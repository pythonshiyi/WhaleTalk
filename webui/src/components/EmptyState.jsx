import React from "react";

// 统一空状态：图标 + 标题 + 引导说明（替代纯文字 empty-tip）
export default function EmptyState({ icon = "🫧", title, hint, children, compact }) {
  return (
    <div className={`empty-state ${compact ? "empty-state-compact" : ""}`}>
      <div className="empty-state-icon">{icon}</div>
      {title && <div className="empty-state-title">{title}</div>}
      {hint && <div className="empty-state-hint">{hint}</div>}
      {children && <div className="empty-state-action">{children}</div>}
    </div>
  );
}
