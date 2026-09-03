import React from "react";
import * as api from "../api.js";

import { silentWarn } from "../quiet.js";
// ── 能力测试台：JSON Schema → 表单 → 直调工具 ────────
function Field({ name, prop, value, onChange, required }) {
  const type = prop.type || "string";
  const desc = prop.description || "";
  const placeholder = `${desc}${required ? "" : "（可选）"}`;

  if (type === "boolean") {
    return (
      <label className="tf-row tf-bool">
        <span className="tf-name">{name}{required && <em className="tf-req">*</em>}</span>
        <button className={`toggle ${value ? "toggle-on" : ""}`} onClick={() => onChange(!value)}>
          <span className="toggle-knob" />
        </button>
        <span className="tf-desc">{desc}</span>
      </label>
    );
  }
  if (Array.isArray(prop.enum)) {
    return (
      <div className="tf-row">
        <span className="tf-name">{name}{required && <em className="tf-req">*</em>}</span>
        <select className="set-select tf-input" value={value || ""} onChange={(e) => onChange(e.target.value)}>
          <option value="">{placeholder}</option>
          {prop.enum.map((e) => (
            <option key={String(e)} value={String(e)}>{String(e)}</option>
          ))}
        </select>
      </div>
    );
  }
  if (type === "integer" || type === "number") {
    return (
      <div className="tf-row">
        <span className="tf-name">{name}{required && <em className="tf-req">*</em>}</span>
        <input
          className="tf-input"
          type="number"
          placeholder={placeholder}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
        />
      </div>
    );
  }
  if (type === "array" || type === "object") {
    return (
      <div className="tf-row tf-stack">
        <span className="tf-name">{name}{required && <em className="tf-req">*</em>}</span>
        <textarea
          className="tf-input tf-json"
          placeholder={`${placeholder}（JSON ${type}）`}
          value={value === undefined ? "" : JSON.stringify(value)}
          onChange={(e) => {
            try {
              onChange(JSON.parse(e.target.value));
            } catch {
              onChange(e.target.value);
            }
          }}
        />
      </div>
    );
  }
  return (
    <div className="tf-row">
      <span className="tf-name">{name}{required && <em className="tf-req">*</em>}</span>
      <input
        className="tf-input"
        placeholder={placeholder}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

export default function ToolTest({ name, onClose }) {
  const [schema, setSchema] = React.useState(null);
  const [values, setValues] = React.useState({});
  const [result, setResult] = React.useState("");
  const [running, setRunning] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const s = await api.getToolSchema(name);
        if (alive && s) setSchema(s);
      } catch (e) { silentWarn(e, "ToolTest"); }
    })();
    return () => {
      alive = false;
    };
  }, [name]);

  React.useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!schema) return null;
  const props = schema.parameters?.properties || {};
  const required = schema.parameters?.required || [];

  const invoke = async () => {
    setRunning(true);
    setResult("");
    try {
      const r = await api.invokeTool(name, values);
      setResult(String(r.result || "（空结果）"));
    } catch (e) {
      setResult(`⚠️ 调用失败：${e.message}`);
    }
    setRunning(false);
  };

  return (
    <div className="confirm-mask" onClick={onClose}>
      <div className="tf-panel" onClick={(e) => e.stopPropagation()}>
        <div className="confirm-head">
          <b>
            🔧 {name}
            <span className="tf-custom">{schema.custom ? "（自定义/交互工具，请对话中触发）" : "（测试台直调）"}</span>
          </b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="tf-desc-line">{schema.description}</div>
        <div className="tf-fields">
          {Object.keys(props).length === 0 && <div className="empty-tip">该工具无参数，直接点击执行</div>}
          {Object.entries(props).map(([k, p]) => (
            <Field
              key={k}
              name={k}
              prop={p}
              required={required.includes(k)}
              value={values[k]}
              onChange={(v) => setValues((prev) => ({ ...prev, [k]: v }))}
            />
          ))}
        </div>
        <div className="tf-foot">
          <button className="confirm-btn" onClick={onClose}>关闭</button>
          <button className="confirm-btn confirm-primary" disabled={running || schema.custom} onClick={invoke}>
            {running ? "执行中…" : "▶ 执行"}
          </button>
        </div>
        {result && (
          <div className="tf-result">
            <div className="tf-result-head">
              <span>执行结果</span>
              <button
                className="msg-op"
                onClick={() => navigator.clipboard.writeText(result).catch(() => {})}
              >
                📋 复制
              </button>
            </div>
            <pre>{result}</pre>
          </div>
        )}
      </div>
    </div>
  );
}