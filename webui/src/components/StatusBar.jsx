import React from "react";
import { getStatus } from "../api.js";
import { FlashContext } from "./FlashToast.jsx";

import { silentWarn } from "../quiet.js";
// ── 底部状态栏（对齐原程序：模式/目录/累计/预算/高峰 | 模型/角色/场景/思考）──
// 生成中状态：🤔 思考中… / ⚙ 正在执行「工具」（第 N 个）… / ⏳ 等待模型响应…
export default function StatusBar({ mode, onSwitchMode, generating, generatingText }) {
  const { flashMsg } = React.useContext(FlashContext);
  const [status, setStatus] = React.useState(null);

  React.useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await getStatus();
        if (alive && s) setStatus(s);
      } catch (e) { silentWarn(e, "StatusBar"); }
    };
    load();
    const iv = setInterval(load, 8000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
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
      {status?.trust && status.trust.state === "unconfirmed" && (
        <span
          className="st-budget-over"
          title={
            "🛡 信任内核：检测到未声明的自我修改（" +
            [...(status.trust.files || [])].join("、") +
            "）。\n查看差异：python trust_kernel.py diff <文件>\n回滚：python trust_kernel.py restore <文件>\n确认保留：python trust_kernel.py accept --all"
          }
        >
          {` | 🛡 内核 ${status.trust.pending + status.trust.alerts} 项待确认`}
        </span>
      )}
      {status?.degrade && status.degrade.critical > 0 && (
        <span
          className="st-budget-over"
          title={
            `⚠ 本次会话有 ${status.degrade.critical} 项关键能力降级（共 ${status.degrade.count} 条降级记录）。\n` +
            "AI 的回答可能因此不完整；明细见「上下文」或 GET /v1/context 的 degradations。"
          }
        >
          {` | ⚠ 降级 ${status.degrade.critical}`}
        </span>
      )}
    </span>
  ));

  return (
    <div className="status-bar">
      <div className="status-left">
        <span className="status-sonar" aria-hidden="true">
          <span className="status-dot" />
          <span className="status-dot-core" />
        </span>
        <span className="status-whale">🐋</span>
        <span className="status-text-wrap">{display}</span>
      </div>
      <div className="status-right">
        <span className="st-right-text" title={status ? `模型 ${status.model} · 角色 ${status.role} · 场景 ${status.scenario} · 思考档 ${status.thinking}` : undefined}>
          {status ? `模型 ${status.model} · 🎭 ${status.role} · 场景 ${status.scenario} · 思考 ${status.thinking}` : "连接中…"}
        </span>
      </div>
    </div>
  );
}