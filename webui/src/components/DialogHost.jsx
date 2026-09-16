import React from "react";
import { _setDialogHandler } from "../dialog.js";
import { useFocusTrap } from "../useFocusTrap.js";

// 应用内对话框宿主：挂载一次（App 内），为 confirmDialog/promptDialog 提供渲染与 Promise 解析。
export default function DialogHost() {
  const [req, setReq] = React.useState(null);
  const [value, setValue] = React.useState("");
  const resolverRef = React.useRef(null);
  const cardRef = React.useRef(null);

  React.useEffect(() => {
    _setDialogHandler((r) => new Promise((resolve) => {
      resolverRef.current = resolve;
      setValue(r.defaultValue != null ? String(r.defaultValue) : "");
      setReq(r);
    }));
    return () => _setDialogHandler(null);
  }, []);

  const close = React.useCallback((result) => {
    const fn = resolverRef.current;
    resolverRef.current = null;
    setReq(null);
    if (fn) fn(result);
  }, []);

  const active = !!req;
  useFocusTrap(cardRef, active);

  React.useEffect(() => {
    if (!req) return;
    const onKey = (e) => {
      if (e.key === "Escape") close(req.kind === "prompt" ? null : false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [req, close]);

  if (!req) return null;
  const isPrompt = req.kind === "prompt";
  const cancel = () => close(isPrompt ? null : false);

  return (
    <div className="dialog-mask" onClick={cancel}>
      <div ref={cardRef} className="dialog-card" role="dialog" aria-modal="true"
        aria-label={req.title || (isPrompt ? "请输入" : "请确认")} onClick={(e) => e.stopPropagation()}>
        <div className="dialog-title">{req.title || (isPrompt ? "请输入" : "请确认")}</div>
        <div className="dialog-msg">{req.message}</div>
        {isPrompt && (
          <input
            className="dialog-input"
            autoFocus
            value={value}
            placeholder={req.placeholder || ""}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") close(value); }}
          />
        )}
        <div className="dialog-foot">
          <button className="confirm-btn" onClick={cancel}>{req.cancelText || "取消"}</button>
          <button
            className={`confirm-btn confirm-primary ${req.danger ? "dialog-danger" : ""}`}
            autoFocus={!isPrompt}
            onClick={() => close(isPrompt ? value : true)}
          >
            {req.okText || (isPrompt ? "确定" : "确认")}
          </button>
        </div>
      </div>
    </div>
  );
}
