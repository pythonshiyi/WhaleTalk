import React from "react";
import { Icon } from "./icons.jsx";

// ── 通用可折叠分组（抽屉式）─────────────────────────────────────────────
// 提供 onToggle 时 header 变为整行按钮，点击展开/收起；未提供则退化为静态标题块。
// 默认收起时只露「大项」，标题右侧可放摘要（right），dirty 显示未保存脏点。
// 样式复用控制台设计语言（`app.css` 的 .px-group* 区段），控制台以外的页面可直接复用。
export default function CollapsibleGroup({
  id,
  icon,
  title,
  right,
  open = true,
  onToggle,
  dirty,
  className = "",
  children,
}) {
  const collapsible = typeof onToggle === "function";
  const header = (
    <>
      {collapsible && <Icon name={open ? "chevron-down" : "chevron-right"} size={13} className="px-group-chev" />}
      <Icon name={icon} size={14} className="px-group-ic" />
      <span>{title}</span>
      {dirty && <span className="px-dirty-dot" title="有未保存修改" aria-label="有未保存修改" />}
      {right && <span className="px-group-right">{right}</span>}
    </>
  );
  return (
    <section className={`px-group ${collapsible && !open ? "is-collapsed" : ""} ${className}`.trim()}>
      {collapsible ? (
        <button
          type="button"
          className="px-group-title px-group-btn"
          aria-expanded={!!open}
          onClick={() => onToggle(id)}
        >
          {header}
        </button>
      ) : (
        <header className="px-group-title">{header}</header>
      )}
      {open && <div className="px-group-body">{children}</div>}
    </section>
  );
}
