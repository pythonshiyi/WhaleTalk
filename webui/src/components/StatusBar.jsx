import React from "react";
import { getStatus, openDir } from "../api.js";
import { FlashContext } from "./FlashToast.jsx";
import { Icon } from "./icons.jsx";

import { silentWarn } from "../quiet.js";

// ── 底部状态栏 ──────────────────────────────────────────────────────────
// 分区：连接/模式/目录 │ 用量（可展开）与生成态 │ 告警（仅在触发时出现） │ 模型（悬停展开）
// 设计原则：告警独立成芯片、常态计量收起、装饰性 emoji 收敛为线性图标。
function fmtNum(n) {
  const v = Number(n) || 0;
  if (v >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
  return String(v);
}

// 中部省略：保留头尾，长路径不再硬截尾导致盘符/末级目录同时丢失
function midTruncate(s, max = 40) {
  const str = String(s || "");
  if (str.length <= max) return str;
  const keep = max - 1;
  const head = Math.ceil(keep * 0.42);
  const tail = keep - head;
  return str.slice(0, head) + "…" + str.slice(-tail);
}

export default function StatusBar({ mode, onSwitchMode, generating, generatingText, tps = 0 }) {
  const { flashMsg, flash } = React.useContext(FlashContext);
  const [status, setStatus] = React.useState(null);
  const [panel, setPanel] = React.useState(null); // "trust" | "usage" | null
  const [trustCopied, setTrustCopied] = React.useState(false);
  const popRef = React.useRef(null);

  React.useEffect(() => {
    if (!panel) return undefined;
    const onDoc = (e) => {
      if (popRef.current && popRef.current.contains(e.target)) return;
      if (e.target && e.target.closest && e.target.closest("[data-st-toggle]")) return;
      setPanel(null);
    };
    const onKey = (e) => { if (e.key === "Escape") setPanel(null); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [panel]);

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
  const ratio = budget > 0 ? Math.min(1, cost / budget) : 0;
  const overBudget = budget > 0 && cost >= budget;
  const nearBudget = budget > 0 && !overBudget && cost / budget >= 0.9;
  const budgetState = overBudget ? "over" : nearBudget ? "near" : "";

  const dir = status?.active_dir || "";
  const isTask = mode === "task";

  const trust = status?.trust;
  const trustPending = !!(trust && trust.state === "unconfirmed");
  const trustCount = trust ? ((trust.files || []).length || (trust.pending || 0) + (trust.alerts || 0)) : 0;
  const degrade = status?.degrade;
  const egress = status?.egress;

  const totalTokens = (u.prompt || 0) + (u.completion || 0);
  const cacheRate = totalTokens > 0 && u.cache_hit ? Math.round((u.cache_hit / totalTokens) * 100) : 0;

  const flashText = flashMsg?.text || "";
  const busy = !!(flashText || generatingText);

  const togglePanel = (name) => setPanel((p) => (p === name ? null : name));

  return (
    <div className="status-bar">
      {/* ── 连接 · 模式 · 目录 ── */}
      <div className="st-zone st-zone-left">
        <span
          className={`st-live ${status ? "st-live-on" : "st-live-off"}`}
          role="img"
          aria-label={status ? "服务连接正常" : "正在连接服务"}
          title={status ? "服务连接正常" : "正在连接服务"}
        >
          <span className="st-live-dot" />
        </span>
        <button
          type="button"
          className={`st-mode ${isTask ? "st-mode-task" : "st-mode-dialog"}`}
          onClick={() => onSwitchMode && onSwitchMode(isTask ? "dialog" : "task")}
          title={isTask ? "任务模式 · 点击切换为对话" : "对话模式 · 点击切换为任务"}
          aria-label="切换工作模式"
        >
          <Icon name={isTask ? "zap" : "message"} size={11} />
          {isTask ? "任务" : "对话"}
        </button>
        {status?.privacy && (
          <span className="st-chip st-chip-mute" title="隐私模式已开启（不写入快照/会话/记忆/统计）">
            <Icon name="lock" size={11} /> 隐私
          </span>
        )}
        {dir && (
          <button
            type="button"
            className="st-dir"
            title={`打开当前目录：${dir}`}
            aria-label="打开当前目录"
            onClick={() => {
              openDir(dir)
                .then(() => flash && flash("已打开当前目录"))
                .catch(() => flash && flash("打开目录失败"));
            }}
          >
            <Icon name="folder" size={11} />
            <span className="st-dir-text">{midTruncate(dir)}</span>
          </button>
        )}
      </div>

      {/* ── 计量 / 生成态 ── */}
      <div className="st-zone st-zone-mid">
        {busy ? (
          <span className={`st-activity ${flashText ? "is-flash" : "is-live"}`} role="status" aria-live="polite">
            {!flashText && <span className="st-activity-spin" aria-hidden="true" />}
            <span className="st-activity-text">{flashText || generatingText}</span>
            {generating && !flashText && tps > 0 && <span className="st-tps">⚡ {tps} tok/s</span>}
          </span>
        ) : (
          <div className="st-metrics">
            {budget > 0 && (
              <span
                className={`st-meter st-meter-${budgetState || "ok"}`}
                title={`本月成本 ¥${cost.toFixed(2)} / 预算 ¥${budget.toFixed(2)}${overBudget ? "（已超限）" : nearBudget ? "（接近上限）" : ""}`}
              >
                <Icon name="yen" size={11} />
                <span className="st-meter-track"><i style={{ transform: `scaleX(${ratio})` }} /></span>
                <span className="st-meter-txt">¥{cost.toFixed(2)}<span className="st-meter-cap">/¥{budget.toFixed(0)}</span></span>
              </span>
            )}
            <button
              type="button"
              data-st-toggle
              className={`st-metric ${panel === "usage" ? "is-open" : ""}`}
              onClick={() => togglePanel("usage")}
              aria-expanded={panel === "usage"}
              title="累计用量明细"
            >
              <Icon name="chart" size={11} /> Σ {fmtNum(totalTokens)}
            </button>
            {status?.peak_hour && (
              <span className="st-chip st-chip-warn" title="当前为高峰时段，计费高于空闲时段">
                <Icon name="clock" size={11} /> 高峰
              </span>
            )}
          </div>
        )}
      </div>

      {/* ── 告警（仅在触发时出现） ── */}
      <div className="st-zone st-zone-alerts">
        {trustPending && (
          <button
            type="button"
            data-st-toggle
            className={`st-alert st-alert-warn ${panel === "trust" ? "is-open" : ""}`}
            onClick={() => togglePanel("trust")}
            aria-expanded={panel === "trust"}
            title="自我完整性：有代码改动待确认（点击查看，这不是错误）"
          >
            <Icon name="shield" size={11} /> {trustCount || "待确认"}
          </button>
        )}
        {degrade && degrade.critical > 0 && (
          <span
            className="st-alert st-alert-danger"
            title={
              `⚠ 本次会话有 ${degrade.critical} 项关键能力降级（共 ${degrade.count} 条降级记录）。\n` +
              "AI 的回答可能因此不完整；明细见「上下文」或 GET /v1/context 的 degradations。"
            }
          >
            <Icon name="warning" size={11} /> 降级 {degrade.critical}
          </span>
        )}
        {egress && egress.alert && (
          <span
            className="st-alert st-alert-warn"
            title={
              `⇡ 本次会话已向外部发送 ${egress.count} 次数据（累计 ${egress.bytes} 字节）。\n` +
              (egress.targets?.length ? `目的地：${egress.targets.join("、")}\n` : "") +
              "如非有意发送请立即检查；明细见 GET /v1/context 的 egress。"
            }
          >
            <Icon name="upload" size={11} /> 出网 {egress.count}
          </span>
        )}
      </div>

      {/* ── 模型（悬停展开角色/场景/思考） ── */}
      <div className="st-zone st-zone-right">
        <div className="st-model-wrap" tabIndex={status ? 0 : -1}>
          <span className="st-model" title={status ? `模型 ${status.model}` : "连接中…"}>
            <Icon name="cpu" size={11} />
            <span className="st-model-name">{status ? status.model : "连接中…"}</span>
          </span>
          {status && (
            <div className="st-meta" role="tooltip">
              <span className="st-meta-row"><span>角色</span><b>{status.role}</b></span>
              <span className="st-meta-row"><span>场景</span><b>{status.scenario}</b></span>
              <span className="st-meta-row"><span>思考</span><b>{status.thinking}</b></span>
            </div>
          )}
        </div>
      </div>

      {/* ── 用量明细弹层 ── */}
      {panel === "usage" && (
        <div className="st-pop st-usage-pop" role="dialog" aria-label="累计用量" ref={popRef}>
          <div className="st-pop-head">
            <b>累计用量</b>
            <button className="icon-btn" onClick={() => setPanel(null)} title="关闭" aria-label="关闭">
              <Icon name="x" size={13} />
            </button>
          </div>
          <div className="st-usage-grid">
            <div className="st-usage-row"><span>输入</span><b>{(u.prompt || 0).toLocaleString()}</b></div>
            <div className="st-usage-row"><span>输出</span><b>{(u.completion || 0).toLocaleString()}</b></div>
            <div className="st-usage-row">
              <span>缓存命中</span>
              <b>{(u.cache_hit || 0).toLocaleString()}{cacheRate > 0 ? ` · ${cacheRate}%` : ""}</b>
            </div>
            {budget > 0 && (
              <div className="st-usage-row">
                <span>本月成本</span>
                <b className={budgetState === "over" ? "st-budget-over" : budgetState === "near" ? "st-budget-near" : ""}>
                  ¥{cost.toFixed(2)} / ¥{budget.toFixed(2)}
                </b>
              </div>
            )}
            {status?.model && <div className="st-usage-row"><span>模型</span><b>{status.model}</b></div>}
          </div>
        </div>
      )}

      {/* ── 自我完整性（信任内核）弹层 ── */}
      {panel === "trust" && trustPending && (
        <div className="st-pop st-trust-pop" role="dialog" aria-label="自我完整性" ref={popRef}>
          <div className="st-pop-head">
            <b>🛡 自我完整性</b>
            <button className="icon-btn" onClick={() => setPanel(null)} title="关闭" aria-label="关闭">
              <Icon name="x" size={13} />
            </button>
          </div>
          <p className="st-trust-pop-desc">
            检测到 <b>{(trust.files || []).length}</b> 个「决定 AI 能做什么」的代码文件被改动、且未声明。
            <b>这不是错误</b>——是提醒你确认这次改动（例如仓库更新带来的功能变化）。
          </p>
          {(trust.files || []).length > 0 && (
            <ul className="st-trust-files">
              {(trust.files || []).map((f) => <li key={f}>{f}</li>)}
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
