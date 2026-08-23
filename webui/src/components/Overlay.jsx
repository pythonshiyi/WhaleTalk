import React from "react";

// ── 菜单弹出的 overlay 面板（对话框式）────────────────
export default function Overlay({ title, onClose, children, wide }) {
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay-mask" onClick={onClose}>
      <div className={`overlay-panel ${wide ? "overlay-wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="overlay-head">
          <b>{title}</b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="overlay-body">{children}</div>
      </div>
    </div>
  );
}