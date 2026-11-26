import React from "react";
import { Icon } from "./icons.jsx";

const STATUS_LABEL = {
  running: "进行中",
  paused: "已暂停",
  blocked: "已阻塞",
  completed: "已完成",
  cancelled: "已取消",
};

const STEP_LABEL = {
  pending: "待执行",
  running: "执行中",
  done: "已完成",
  failed: "失败",
  blocked: "阻塞",
  skipped: "已跳过",
};

function PlanCard({ plan, onControl, onResume }) {
  const active = ["running", "paused", "blocked"].includes(plan.status);
  const pct = plan.total ? Math.round((plan.done / plan.total) * 100) : 0;
  return (
    <article className={`tc-card tc-${plan.status}`}>
      <div className="tc-card-head">
        <span className={`tc-status tc-status-${plan.status}`}>{STATUS_LABEL[plan.status] || plan.status}</span>
        <span className="tc-plan-id" title={plan.plan_id}>#{String(plan.plan_id).slice(0, 8)}</span>
        <span className="tc-time">{plan.updated_at || plan.created_at || ""}</span>
      </div>
      <div className="tc-goal" title={plan.goal}>{plan.goal}</div>
      <div className="tc-progress">
        <div className="tc-progress-bar"><i style={{ width: `${pct}%` }} /></div>
        <span>{plan.done}/{plan.total} · {pct}%</span>
        {plan.failed > 0 && <span className="tc-failed">{plan.failed} 步失败</span>}
      </div>
      <div className="tc-ctl">
        {active && plan.status === "running" && (
          <button onClick={() => onControl(plan.plan_id, "pause")}>暂停</button>
        )}
        {active && plan.status !== "running" && (
          <button onClick={() => onControl(plan.plan_id, "resume")}>恢复状态</button>
        )}
        {active && <button onClick={() => onControl(plan.plan_id, "cancel")}>取消</button>}
        {plan.status !== "cancelled" && (
          <button className="tc-primary" onClick={() => onResume(plan)}>续跑</button>
        )}
      </div>
      <ol className="tc-steps">
        {(plan.steps || []).map((s, i) => (
          <li key={s.id || i} className={`tc-step tc-step-${s.status}`}>
            <div className="tc-step-head">
              <span className="tc-step-node">
                {s.status === "done" ? "✓" : s.status === "skipped" ? "↷" : ["failed", "blocked"].includes(s.status) ? "✕" : i + 1}
              </span>
              <span className="tc-step-title">{s.title}</span>
              <span className="tc-step-state">{STEP_LABEL[s.status] || s.status}</span>
              {Number(s.attempts) > 0 && <span className="tc-step-attempts">×{s.attempts}</span>}
            </div>
            {(s.evidence || s.last_error || (s.verification && s.verification.checks && s.verification.checks.length)) && (
              <details className="tc-step-detail">
                <summary>验收详情</summary>
                {s.last_error && <div className="tc-error">失败原因：{s.last_error}</div>}
                {s.evidence && <div className="tc-evidence">{s.evidence}</div>}
                {(s.verification?.checks || []).map((c, ci) => <div key={ci}>· {c}</div>)}
              </details>
            )}
            {active && ["failed", "blocked", "done", "skipped"].includes(s.status) && (
              <button className="tc-step-op" onClick={() => onControl(plan.plan_id, "retry", s.id)}>重试此步</button>
            )}
            {active && ["pending", "failed", "blocked"].includes(s.status) && (
              <button className="tc-step-op" onClick={() => onControl(plan.plan_id, "skip", s.id)}>跳过此步</button>
            )}
          </li>
        ))}
      </ol>
    </article>
  );
}

export default function TaskCenter({ open, onClose, plans = [], loading, onRefresh, onControl, onResume }) {
  const [filter, setFilter] = React.useState("all");
  if (!open) return null;
  const shown = plans.filter((p) => {
    if (filter === "active") return ["running", "paused", "blocked"].includes(p.status);
    if (filter === "done") return p.status === "completed";
    if (filter === "problem") return ["failed", "blocked", "cancelled"].includes(p.status) || Number(p.failed) > 0;
    return true;
  });
  return (
    <>
      <div className="task-center-scrim" onClick={onClose} />
      <aside className="task-center-drawer" aria-label="任务中心">
        <div className="tc-head">
          <span className="tc-title"><Icon name="list" size={14} /> 任务中心</span>
          <button className="tc-icon-btn" title="刷新" onClick={onRefresh}><Icon name="refresh" size={14} /></button>
          <button className="tc-icon-btn" title="关闭" onClick={onClose}>×</button>
        </div>
        <div className="tc-filters" role="group" aria-label="任务筛选">
          {[["all", "全部"], ["active", "进行中"], ["problem", "失败/阻塞"], ["done", "已完成"]].map(([key, label]) => (
            <button key={key} className={filter === key ? "tc-filter-on" : ""} onClick={() => setFilter(key)}>{label}</button>
          ))}
        </div>
        <div className="tc-list">
          {loading && <div className="tc-empty">正在加载任务计划…</div>}
          {!loading && shown.length === 0 && <div className="tc-empty">这里还没有任务计划。任务模式下多步任务会自动生成计划。</div>}
          {shown.map((p) => <PlanCard key={p.plan_id} plan={p} onControl={onControl} onResume={onResume} />)}
        </div>
      </aside>
    </>
  );
}
