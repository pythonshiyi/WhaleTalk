import React from "react";
import { getStatus } from "../api.js";
import { FlashContext } from "./FlashToast.jsx";

import { silentWarn } from "../quiet.js";
// ── 底部状态栏（对齐原程序：模式/目录/累计/预算/高峰 | 模型/角色/场景/思考）──
// 生成中状态：🤔 思考中… / ⚙ 正在执行「工具」（第 N 个）… / ⏳ 等待模型响应…
export default function StatusBar({ mode, onSwitchMode, generating, generatingText, tps = 0 }) {
  const { flashMsg } = React.useContext(FlashContext);
  const [status, setStatus] = React.useState(null);
  const [trustOpen, setTrustOpen] = React.useState(false);
  const [trustCopied, setTrustCopied] = React.useState(false);
  const trustRef = React.useRef(null);
  React.useEffect(() => {
    if (!trustOpen) return undefined;
    const onDoc = (e) => {
      if (trustRef.current && trustRef.current.contains(e.target)) return;
      if (e.target && e.target.closest && e.target.closest(".st-trust")) return; // 触发按钮自己切换
      setTrustOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setTrustOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [trustOpen]);

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
    <span className="status-text st-gen">
      {generatingText}
      {generating && tps > 0 ? <span className="st-tps">⚡ {tps} tok/s</span> : null}
    </span>
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
        <button
          type="button"
          className="st-trust"
          aria-expanded={trustOpen}
          onClick={() => setTrustOpen((o) => !o)}
          title="点击查看详情：这是安全提醒，不是错误"
        >
          {` | 🛡 自我完整性：${(status.trust.files || []).length || (status.trust.pending + status.trust.alerts)} 处代码改动待确认`}
        </button>
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
      {status?.egress && status.egress.alert && (
        <span
          className="st-budget-over"
          title={
            `⇡ 本次会话已向外部发送 ${status.egress.count} 次数据（累计 ${status.egress.bytes} 字节）。\n` +
            (status.egress.targets?.length ? `目的地：${status.egress.targets.join("、")}\n` : "") +
            "如非有意发送请立即检查；明细见 GET /v1/context 的 egress。"
          }
        >
          {` | ⇡ 出网 ${status.egress.count}`}
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
        <span className="status-whale" aria-hidden="true">🐋</span>
        <span className="status-text-wrap" role="status" aria-live="polite">{display}</span>
      </div>
      <div className="status-right">
        <span className="st-right-text" title={status ? `模型 ${status.model} · 角色 ${status.role} · 场景 ${status.scenario} · 思考档 ${status.thinking}` : undefined}>
          {status ? `模型 ${status.model} · 🎭 ${status.role} · 场景 ${status.scenario} · 思考 ${status.thinking}` : "连接中…"}
        </span>
      </div>

      {trustOpen && status?.trust?.state === "unconfirmed" && (
        <div className="st-trust-pop" role="dialog" aria-label="自我完整性" ref={trustRef}>
          <div className="st-trust-pop-head">
            <b>🛡 自我完整性</b>
            <button className="icon-btn" onClick={() => setTrustOpen(false)} title="关闭" aria-label="关闭">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
            </button>
          </div>
          <p className="st-trust-pop-desc">
            检测到 <b>{(status.trust.files || []).length}</b> 个「决定 AI 能做什么」的代码文件被改动、且未声明。
            <b>这不是错误</b>——是提醒你确认这次改动（例如仓库更新带来的功能变化）。
          </p>
          {(status.trust.files || []).length > 0 && (
            <ul className="st-trust-files">
              {(status.trust.files || []).map((f) => <li key={f}>{f}</li>)}
            </ul>
          )}
          <div className="st-trust-cmds">
            <code>python trust_kernel.py diff &lt;文件&gt;</code>
            <code>python trust_kernel.py accept --all</code>
            <code>python trust_kernel.py restore &lt;文件&gt;</code>
          </div>
          <div className="st-trust-pop-foot">
            <button
              className="msg-op"
              onClick={() => {
                navigator.clipboard?.writeText("python trust_kernel.py accept --all").then(() => {
                  setTrustCopied(true);
                  setTimeout(() => setTrustCopied(false), 1500);
                }).catch(() => {});
              }}
            >
              {trustCopied ? "✓ 已复制" : "复制确认命令"}
            </button>
            <span className="st-trust-hint">确认保留后提示消失；改动可回滚</span>
          </div>
        </div>
      )}
    </div>
  );
}