import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";

const STATUS_LABEL = {
  queued: "排队中",
  running: "进行中",
  paused: "已暂停",
  blocked: "已阻塞",
  completed: "已完成",
  cancelled: "已取消",
};

const STEP_LABEL = {
  pending: "待执行",
  waiting: "等待前置",
  running: "执行中",
  done: "已完成",
  failed: "失败",
  blocked: "阻塞",
  skipped: "已跳过",
};

const STRATEGY_LABEL = {
  code: "代码",
  docs: "文档",
  data: "数据",
  research: "调研",
  general: "通用",
};

function ArtifactItem({ plan, art, onRollback }) {
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const label = art.state === "created" ? "新增" : art.state === "modified" ? "修改" : "未变";
  const doRollback = async () => {
    setBusy(true);
    try {
      await onRollback(plan, art);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="tc-art">
      <div className="tc-art-head">
        <span className={`tc-art-state tc-art-${art.state || "unchanged"}`}>{label}</span>
        <button className="tc-art-name" title={art.path}
          onClick={() => api.openFile(art.path).catch(() => {})}>
          {art.relpath || art.path}
        </button>
        <span className="tc-art-step">#{art.step}</span>
        <button className="tc-art-toggle" onClick={() => setOpen((v) => !v)}>
          {open ? "收起" : "差异"}
        </button>
        {art.rollback_available && (
          <button className="tc-art-rollback" disabled={busy} onClick={doRollback}>
            {busy ? "回滚中…" : "回滚"}
          </button>
        )}
      </div>
      {open && (
        <div className="tc-art-body">
          {art.diff ? <pre className="tc-art-diff">{art.diff}</pre> : <div className="tc-art-empty">无文本差异（二进制或未变更）</div>}
          <div className="tc-art-hash">SHA256 {(art.after && art.after.sha256) || "—"}</div>
        </div>
      )}
    </li>
  );
}

function PlanCard({ plan, onControl, onResume, onSync, onRollback }) {
  const active = ["queued", "running", "paused", "blocked"].includes(plan.status);
  const pct = plan.total ? Math.round((plan.done / plan.total) * 100) : 0;
  const rt = plan.runtime || {};
  const artifacts = Array.isArray(plan.artifacts) ? plan.artifacts : [];
  return (
    <article className={`tc-card tc-${plan.status}`}>
      <div className="tc-card-head">
        <span className={`tc-status tc-status-${plan.status}`}>{STATUS_LABEL[plan.status] || plan.status}</span>
        <span className="tc-plan-id" title={plan.plan_id}>#{String(plan.plan_id).slice(0, 8)}</span>
        {plan.strategy && plan.strategy !== "general" && (
          <span className="tc-strategy">{STRATEGY_LABEL[plan.strategy] || plan.strategy}</span>
        )}
        {!!plan.priority && <span className="tc-priority">P{plan.priority}</span>}
        {rt.queue_position > 0 && <span className="tc-queue-pos">排队 {rt.queue_position}</span>}
        {rt.job_running && <span className="tc-live">运行中</span>}
        {!rt.job_running && rt.recoverable && <span className="tc-recover">可恢复</span>}
        <span className="tc-time">{plan.updated_at || plan.created_at || ""}</span>
      </div>
      <div className="tc-goal" title={plan.goal}>{plan.goal}</div>
      {rt.message && <div className="tc-runtime-note">{rt.kind || "运行时"}：{rt.message}</div>}
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
        {rt.stream_id && (
          <button onClick={() => onSync(plan)}>同步进度</button>
        )}
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
              {(s.depends_on || []).length > 0 && (
                <span className="tc-step-dep">依赖 #{s.depends_on.join(",#")}</span>
              )}
              {s.parallel_group && <span className="tc-step-group">并行 {s.parallel_group}</span>}
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
            {active && ["pending", "waiting", "failed", "blocked"].includes(s.status) && (
              <button className="tc-step-op" onClick={() => onControl(plan.plan_id, "skip", s.id)}>跳过此步</button>
            )}
          </li>
        ))}
      </ol>
      {artifacts.length > 0 && (
        <div className="tc-artifacts">
          <div className="tc-art-title">产物（{artifacts.length}）</div>
          <ul className="tc-art-list">
            {artifacts.map((art, i) => (
              <ArtifactItem key={art.path || i} plan={plan} art={art} onRollback={onRollback} />
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

function ExperiencePanel({ onDeleteRecipe }) {
  const [recipes, setRecipes] = React.useState([]);
  const [pitfalls, setPitfalls] = React.useState([]);
  const [busy, setBusy] = React.useState(false);
  const [loaded, setLoaded] = React.useState(false);
  const load = React.useCallback(async () => {
    setBusy(true);
    try {
      const [r, p] = await Promise.all([api.listTaskRecipes(50), api.listTaskPitfalls(50)]);
      setRecipes(Array.isArray(r && r.recipes) ? r.recipes : []);
      setPitfalls(Array.isArray(p && p.pitfalls) ? p.pitfalls : []);
      setLoaded(true);
    } catch (e) { silentWarnApi(e); }
    finally { setBusy(false); }
  }, []);
  React.useEffect(() => { load(); }, [load]);
  const doDelete = async (id) => {
    try {
      await api.deleteTaskRecipe(id);
      await load();
      if (onDeleteRecipe) onDeleteRecipe();
    } catch (e) { silentWarnApi(e); }
  };
  return (
    <div className="tc-exp">
      <div className="tc-exp-bar">
        <span className="tc-exp-note">完成过的任务会自动变成可复用配方；踩过的坑变成开工前避坑清单——都是为了让同类任务第一次就做对，不返工。</span>
        <button className="tc-icon-btn" title="刷新" onClick={load} disabled={busy}><Icon name="refresh" size={14} /></button>
      </div>
      <div className="tc-exp-title">可复用配方（{recipes.length}）</div>
      {recipes.length === 0 && <div className="tc-empty">还没有配方。任务完成后会自动沉淀。</div>}
      <ul className="tc-exp-list">
        {recipes.map((r) => (
          <li key={r.recipe_id} className="tc-exp-item">
            <div className="tc-exp-item-head">
              <span className="tc-exp-name">{r.name}</span>
              <span className="tc-exp-stat" title="成功次数">成功 {Number(r.successes) || 0}</span>
              <span className="tc-exp-stat" title="复用次数">复用 {Number(r.uses) || 0}</span>
              <button className="tc-exp-del" title="删除配方" onClick={() => doDelete(r.recipe_id)}>×</button>
            </div>
            <div className="tc-exp-steps">{(r.steps || []).map((s, i) => `${i + 1}. ${s.title}`).join("　")}</div>
          </li>
        ))}
      </ul>
      <div className="tc-exp-title">避坑经验（{pitfalls.length}）</div>
      {pitfalls.length === 0 && <div className="tc-empty">还没有失败经验。步骤失败会自动记录。</div>}
      <ul className="tc-exp-list">
        {pitfalls.map((p) => (
          <li key={p.pitfall_id} className="tc-exp-item tc-exp-pitfall">
            <div className="tc-exp-item-head">
              <span className="tc-exp-name">{p.step_title}</span>
              <span className="tc-exp-stat" title="出现次数">×{Number(p.occurrences) || 0}</span>
            </div>
            <div className="tc-exp-reason">上次失败：{p.reason}</div>
            {p.avoid && <div className="tc-exp-avoid">改用：{p.avoid}</div>}
          </li>
        ))}
      </ul>
      {loaded && recipes.length === 0 && pitfalls.length === 0 && (
        <div className="tc-exp-hint">先跑几个任务，这里就会自动积累经验。</div>
      )}
    </div>
  );
}

function silentWarnApi(e) { try { console.warn("[TaskCenter]", e); } catch (_) {} }

export default function TaskCenter({ open, onClose, plans = [], loading, queue = [], recoverable = 0, onRefresh, onControl, onResume, onSync, onRollback }) {
  const [filter, setFilter] = React.useState("all");
  const [tab, setTab] = React.useState("tasks");
  if (!open) return null;
  const shown = plans.filter((p) => {
    if (filter === "active") return ["queued", "running", "paused", "blocked"].includes(p.status);
    if (filter === "done") return p.status === "completed";
    if (filter === "problem") return ["blocked", "cancelled"].includes(p.status) || Number(p.failed) > 0;
    return true;
  });
  return (
    <>
      <div className="task-center-scrim" onClick={onClose} />
      <aside className="task-center-drawer" aria-label="任务中心">
        <div className="tc-head">
          <span className="tc-title"><Icon name="list" size={14} /> 任务中心</span>
          {recoverable > 0 && <span className="tc-recover-count">{recoverable} 个可恢复</span>}
          {queue.length > 0 && <span className="tc-queue-count">队列 {queue.length}</span>}
          <button className="tc-icon-btn" title="刷新" onClick={onRefresh}><Icon name="refresh" size={14} /></button>
          <button className="tc-icon-btn" title="关闭" onClick={onClose}>×</button>
        </div>
        <div className="tc-tabs" role="group" aria-label="任务中心视图">
          <button className={tab === "tasks" ? "tc-tab-on" : ""} onClick={() => setTab("tasks")}>任务</button>
          <button className={tab === "exp" ? "tc-tab-on" : ""} onClick={() => setTab("exp")}>经验库</button>
        </div>
        {tab === "tasks" ? (
          <>
            <div className="tc-filters" role="group" aria-label="任务筛选">
              {[["all", "全部"], ["active", "进行中"], ["problem", "失败/阻塞"], ["done", "已完成"]].map(([key, label]) => (
                <button key={key} className={filter === key ? "tc-filter-on" : ""} onClick={() => setFilter(key)}>{label}</button>
              ))}
            </div>
            <div className="tc-list">
              {loading && <div className="tc-empty">正在加载任务计划…</div>}
              {!loading && shown.length === 0 && <div className="tc-empty">这里还没有任务计划。任务模式下多步任务会自动生成计划。</div>}
              {shown.map((p) => <PlanCard key={p.plan_id} plan={p} onControl={onControl}
                onResume={onResume} onSync={onSync} onRollback={onRollback} />)}
            </div>
          </>
        ) : (
          <ExperiencePanel />
        )}
      </aside>
    </>
  );
}
