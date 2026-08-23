import React from "react";

// ── 全局提示基础设施（对齐原程序 _flash_status / _toast）──
// Flash：状态栏位置短暂显示（默认 1600ms）后恢复
// Toast：右下角非模态轻提示（3s 自动消失）

export const FlashContext = React.createContext({ flash: () => {} });
export const ToastContext = React.createContext({ toast: () => {} });

export function FlashProvider({ children }) {
  const [msg, setMsg] = React.useState(null);
  const genRef = React.useRef(0);
  const timerRef = React.useRef(null);

  const flash = React.useCallback((text, ms = 1600) => {
    const gen = ++genRef.current;
    setMsg({ text, gen });
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      if (genRef.current === gen) setMsg(null);
    }, ms);
  }, []);

  React.useEffect(() => () => clearTimeout(timerRef.current), []);

  return (
    <FlashContext.Provider value={{ flash, flashMsg: msg }}>
      {children}
    </FlashContext.Provider>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = React.useState([]);

  const toast = React.useCallback((text, ms = 3000) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), ms);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="toast-stack">
        {toasts.map((t) => (
          <div className="toast-item" key={t.id}>{t.text}</div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}