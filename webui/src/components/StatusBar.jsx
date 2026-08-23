import React from "react";
import { getStatus } from "../api.js";
import { FlashContext } from "./FlashToast.jsx";

// ── 底部状态栏（对齐原程序：模式/目录/累计/预算/高峰 | 模型/角色/场景/思考）──
// 生成中状态：🤔 思考中… / ⚙ 正在执行「工具」（第 N 个）… / ⏳ 等待模型响应…
export default function StatusBar({ mode, onSwitchMode, generating, generatingText }) {
  const { flashMsg } = React.useContext(FlashContext);
  const [status, setStatus] = React.useState(null);
  const [sonar, setSonar] = React.useState(0);

  React.useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await getStatus();
        if (alive && s) setStatus(s);
      } catch {}
    };
    load();
    const iv = setInterval(load, 8000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  React.useEffect(() => {
    const iv = setInterval(() => setSonar((s) => (s + 1) % 40), 50);
    return () => clearInterval(iv);
  }, []);

  const u = status?.usage_total || {};
  const budget = status?.monthly_budget || 0;
  const cost = status?.monthly_cost || 0;
  const overBudget = budget > 0 && cost >= budget;
  const nearBudget = budget > 0 && cost / budget >= 0.9;

  const dir = status?.active_dir || "";
  const dirShort = dir.length > 30 ? "…" + dir.slice(-29) : dir;

  const display = flashMsg?.text || (generatingText ? (
    <span className="status-text st-gen">{generatingText}</span>
  ) : (
    <span className="status-text">
      {status?.privacy ? "🔒 " : ""}
      <b className={mode === "task" ? "st-task" : "st-dialog"}>
        {mode === "task" ? "🚀任务" : "💬对话"}
      </b>
      {" "}📁 {dirShort}
      {" | "}累计: 输入 {(u.prompt || 0).toLocaleString()} · 输出 {(u.completion || 0).toLocaleString()}
      {u.cache_hit ? ` (缓存命中 ${(u.cache_hit || 0).toLocaleString()})` : ""}
      {budget > 0 && (
        <span className={overBudget ? "st-budget-over" : nearBudget ? "st-budget-near" : ""}>
          {" "}| 本月 ¥{cost.toFixed(2)}/¥{budget.toFixed(2)}
          {overBudget ? " ⛔ 已超限" : nearBudget ? " ⚠ 接近上限" : ""}
        </span>
      )}
      {status?.peak_hour ? " ⏰ 高峰" : ""}
    </span>
  ));

  return (
    <div className="status-bar">
      <div className="status-left">
        <span className="status-sonar">
          <span className="status-dot" style={{ transform: `scale(${1 + sonar / 40})`, opacity: 0.4 - sonar / 100 }} />
          <span className="status-dot-core" />
        </span>
        <span className="status-whale">🐋</span>
        <span className="status-text-wrap">{display}</span>
      </div>
      <div className="status-right">
        <span className="st-right-text">
          {status ? `模型 ${status.model} · 🎭 ${status.role} · 场景 ${status.scenario} · 思考 ${status.thinking}` : "连接中…"}
        </span>
        <span className="st-ctx">上下文</span>
        <div className="st-ctx-bar">
          <div className="st-ctx-fill" style={{ width: `${Math.min(100, Math.max(3, Math.round(((u.prompt || 0) / 1000000) * 100)))}%` }} />
        </div>
      </div>
    </div>
  );
}