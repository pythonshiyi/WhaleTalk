import React from "react";
import { Icon, hasIcon } from "./icons.jsx";

// 统一空状态：图标 + 标题 + 引导说明（替代纯文字 empty-tip）
// icon 既可传统一图标名（如 "brain"），也兼容历史 emoji 字符串。
export default function EmptyState({ icon = "🫧", title, hint, children, compact }) {
  return (
    <div className={`empty-state ${compact ? "empty-state-compact" : ""}`}>
      <div className="empty-state-icon" aria-hidden="true">
        {hasIcon(icon) ? <Icon name={icon} size={26} /> : icon}
      </div>
      {title && <div className="empty-state-title">{title}</div>}
      {hint && <div className="empty-state-hint">{hint}</div>}
      {children && <div className="empty-state-action">{children}</div>}
    </div>
  );
}
