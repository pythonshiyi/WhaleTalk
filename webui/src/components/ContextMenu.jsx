import React from "react";
import { Icon } from "./icons.jsx";

// ── 通用右键上下文菜单 ────────────────────────────────
// 用法：
//   const menu = useContextMenu();
//   <div onContextMenu={(e) => menu.open(e, [{ label, icon, danger, onClick }])} />
//   <ContextMenu menu={menu.menu} onClose={menu.close} />
// 设计目标：会话项 / 消息项共用同一套「右键 / 长按」入口，替代仅 hover 才可见的小按钮，
// 提升触屏与键盘用户的可发现性。菜单自动做视口内钳制，避免溢出屏幕。
export function useContextMenu() {
  const [menu, setMenu] = React.useState(null);
  const open = React.useCallback((e, items) => {
    if (e && typeof e.preventDefault === "function") e.preventDefault();
    const valid = (items || []).filter(Boolean);
    if (!valid.length) return;
    setMenu({ x: e.clientX, y: e.clientY, items: valid });
  }, []);
  const close = React.useCallback(() => setMenu(null), []);
  return { menu, open, close };
}

export default function ContextMenu({ menu, onClose }) {
  const ref = React.useRef(null);

  React.useEffect(() => {
    if (!menu) return undefined;
    const onDoc = (e) => {
      if (ref.current && ref.current.contains(e.target)) return;
      onClose();
    };
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("blur", onClose);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", onClose);
    };
  }, [menu, onClose]);

  if (!menu) return null;

  // 视口内钳制：估算尺寸，避免菜单贴边溢出。
  const vw = typeof window !== "undefined" ? window.innerWidth : 1024;
  const vh = typeof window !== "undefined" ? window.innerHeight : 768;
  const estW = 188;
  const estH = menu.items.length * 34 + 12;
  const x = Math.max(6, Math.min(menu.x, vw - estW - 6));
  const y = Math.max(6, Math.min(menu.y, vh - estH - 6));

  return (
    <div ref={ref} className="ctx-menu" role="menu" style={{ left: x, top: y }}>
      {menu.items.map((it, i) => (
        <button
          key={i}
          type="button"
          role="menuitem"
          className={`ctx-item ${it.danger ? "ctx-danger" : ""}`}
          disabled={!!it.disabled}
          onClick={() => { onClose(); it.onClick && it.onClick(); }}
        >
          <span className="ctx-ic">{it.icon ? <Icon name={it.icon} size={14} /> : null}</span>
          <span className="ctx-label">{it.label}</span>
        </button>
      ))}
    </div>
  );
}
