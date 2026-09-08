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
  const timersRef = React.useRef({});   // id → timeout，卸载时统一清理
  const toastsRef = React.useRef([]);   // 旁路引用：去重判定不依赖 state updater 时序

  const dismiss = React.useCallback((id) => {
    clearTimeout(timersRef.current[id]);
    delete timersRef.current[id];
    toastsRef.current = toastsRef.current.filter((x) => x.id !== id);
    setToasts(toastsRef.current);
  }, []);

  const toast = React.useCallback((text, ms = 3000) => {
    const key = text;
    // 同文本去重：仅刷新该条并重置计时，避免快速重复操作刷屏。
    const dup = toastsRef.current.find((x) => x.text === key);
    const id = dup ? dup.id : Math.random().toString(36).slice(2);
    clearTimeout(timersRef.current[id]);
    timersRef.current[id] = setTimeout(() => dismiss(id), ms);
    if (dup) {
      // 复用已有条目，位置不变，只重置计时。
      toastsRef.current = toastsRef.current.slice();
      setToasts(toastsRef.current);
    } else {
      // 上限 3 条：超出丢弃最旧，防长时间多操作堆积。
      toastsRef.current = [...toastsRef.current, { id, text }].slice(-3);
      setToasts(toastsRef.current);
    }
  }, [dismiss]);

  React.useEffect(() => {
    const timers = timersRef.current;
    return () => Object.values(timers).forEach(clearTimeout);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div className="toast-item" key={t.id}>{t.text}</div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}