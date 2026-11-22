import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";
import EmptyState from "./EmptyState.jsx";
import { SkeletonList } from "./Skeleton.jsx";
import Overlay from "./Overlay.jsx";
import { usePoll } from "../usePoll.js";

// ── 任务（执行历史）──
// 一轮生成 = 一条作业记录：有状态、有步数、有耗时、有失败，可筛、可看详情、
// 可跳回会话，也可用原输入重新发起。
const STATUS = {
  done: { label: "完成", cls: "is-ok" },
  error: { label: "出错", cls: "is-bad" },
  stopped: { label: "已停止", cls: "is-warn" },
};

const FILTERS = [
  { id: "", label: "全部" },
  { id: "error", label: "出错" },
  { id: "done", label: "完成" },
  { id: "stopped", label: "已停止" },
];

function fmtDur(s) {
  const v = Number(s) || 0;
  if (v <= 0) return "—";
  if (v < 60) return `${v.toFixed(1)}s`;
  const m = Math.floor(v / 60);
  return `${m}m${String(Math.round(v - m * 60)).padStart(2, "0")}s`;
}

function JobRow({ job, onDetail }) {
  const st = STATUS[job.status] || { label: job.status || "—", cls: "" };
  const hasFail = (job.failed_count || 0) > 0;
  return (
    <button
      type="button"
      className="job card-hover"
      onClick={() => onDetail(job)}
      title="查看任务详情"
    >
      <span className={`job-dot ${st.cls}`} aria-hidden="true" />
      <span className="job-main">
        <span className="job-title">{job.user_text || "（无输入文本）"}</span>
        <span className="job-meta">
          <span>{job.started_at || ""}</span>
          <span className="job-sep">·</span>
          <span>{job.step_count || 0} 步</span>
          {hasFail ? <span className="job-fail">· {job.failed_count} 失败</span> : null}
          <span className="job-sep">·</span>
          <span>{fmtDur(job.duration_s)}</span>
        </span>
      </span>
      <span className="job-right">
        <span className={`job-tag ${st.cls}`}>{st.label}</span>
        <Icon name="chevron-right" size={13} />
      </span>
    </button>
  );
}

function StepRow({ s, i }) {
  return (
    <div className={`job-step ${s.failed ? "is-failed" : ""}`}>
      <span className="job-step-i">{i + 1}</span>
      <span className="job-step-main">
        <b>{s.tool || "未知工具"}</b>
        {s.args_preview && <code>{s.args_preview}</code>}
        {s.error_preview && <span className="job-step-err">{s.error_preview}</span>}
      </span>
      <span className="job-step-meta">
        {s.duration ? `${Number(s.duration).toFixed(1)}s` : ""}
        <span className={s.failed ? "is-bad" : "is-ok"}>{s.failed ? "失败" : "完成"}</span>
      </span>
    </div>
  );
}

export default function TasksPage({ onOpenSession, onRetry, embedded = false }) {
  const [data, setData] = React.useState(null);
  const [running, setRunning] = React.useState([]);
  const [err, setErr] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [limit, setLimit] = React.useState(50);
  const [q, setQ] = React.useState("");
  const [onlyFailed, setOnlyFailed] = React.useState(false);
  const [detail, setDetail] = React.useState(null);

  const load = React.useCallback(async () => {
    try {
      const [r, run] = await Promise.all([
        api.jobs({ limit, status }),
        api.runningJobs().catch(() => ({ jobs: [] })),
      ]);
      if (r) { setData(r); setErr(""); }
      setRunning((run && run.jobs) || []);
    } catch {
      setErr("执行历史加载失败（后端未连接）");
    }
  }, [limit, status]);

  React.useEffect(() => { load(); }, [load]);
  usePoll(load, 10000);

  const openDetail = async (job) => {
    setDetail({ ...job, steps: job.steps || [], loading: true });
    try {
      const full = await api.jobDetail(job.id);
      if (full) setDetail({ ...full, loading: false });
    } catch {
      setDetail((d) => (d ? { ...d, loading: false } : d));
    }
  };

  const allJobs = (data && data.jobs) || [];
  const query = q.trim().toLowerCase();
  const jobs = allJobs.filter((j) => {
    if (onlyFailed && !(j.failed_count > 0 || j.status === "error")) return false;
    if (!query) return true;
    return `${j.user_text || ""} ${j.error || ""} ${(j.steps || []).map((s) => s.tool).join(" ")}`.toLowerCase().includes(query);
  });
  const sum = (data && data.summary) || { count: 0, failed: 0, seconds: 0 };

  return (
    <div className={embedded ? "page-embedded" : "page"}>
      {!embedded && (
        <div className="page-head">
          <h1>任务</h1>
          <p>每一次执行都是一条记录：跑了多少步、有没有失败、花了多久。点任意记录可查看每一步。</p>
        </div>
      )}

      {running.length > 0 && (
        <div className="job-running">
          <span className="bg-jobs-dot" aria-hidden="true" />
          <b>{running.length} 个任务正在运行</b>
          {running.slice(0, 3).map((j) => (
            <span key={j.id} className="job-running-item">
              {j.last_tool ? `执行 ${j.last_tool}` : "模型思考中"} · {Math.round(j.seconds || 0)}s
              {j.session_id && <button className="msg-op" onClick={() => onOpenSession?.(j.session_id)}>打开</button>}
            </span>
          ))}
        </div>
      )}

      <div className="jobs-sum">
        <div className="jobs-metric"><b>{sum.count}</b><span>记录</span></div>
        <div className={`jobs-metric${sum.failed ? " is-bad" : ""}`}><b>{sum.failed}</b><span>含失败</span></div>
        <div className="jobs-metric"><b>{fmtDur(sum.seconds)}</b><span>累计耗时</span></div>
      </div>

      <div className="jobs-bar">
        <div className="jobs-filter" role="tablist" aria-label="按状态筛选">
          {FILTERS.map((f) => (
            <button
              key={f.id || "all"}
              role="tab"
              aria-selected={status === f.id}
              className={`tl-fbtn${status === f.id ? " is-on" : ""}`}
              onClick={() => setStatus(f.id)}
            >{f.label}</button>
          ))}
          <button
            role="tab"
            aria-selected={onlyFailed}
            className={`tl-fbtn${onlyFailed ? " is-on" : ""}`}
            onClick={() => setOnlyFailed((v) => !v)}
          >有失败步骤</button>
        </div>
        <input
          className="set-select set-combo jobs-search"
          placeholder="搜索任务文本 / 工具名…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="msg-op" onClick={load}>刷新</button>
      </div>

      {err && <div className="empty-tip is-err">{err}</div>}
      {!data && !err && <SkeletonList rows={5} />}
      {data && jobs.length === 0 && (
        <EmptyState
          icon="🗂"
          title={query || onlyFailed || status ? "没有匹配的记录" : "还没有执行记录"}
          hint={query || onlyFailed || status ? "换个筛选条件看看" : "跑一次带工具的任务，结束后这里会出现一条记录"}
        />
      )}
      {jobs.length > 0 && (
        <div className="job-list">
          {jobs.map((j) => <JobRow key={j.id} job={j} onDetail={openDetail} />)}
        </div>
      )}
      {data && allJobs.length >= limit && (
        <div className="jobs-more">
          <button className="msg-op" onClick={() => setLimit((v) => Math.min(500, v + 50))}>加载更多</button>
        </div>
      )}

      {detail && (
        <Overlay title={`任务详情 · ${detail.started_at || ""}`} onClose={() => setDetail(null)} wide>
          <div className="job-detail">
            <div className="job-detail-head">
              <div>
                <b>{detail.user_text || "（无输入文本）"}</b>
                <div className="job-meta">
                  <span>{detail.status || "—"}</span>
                  <span className="job-sep">·</span>
                  <span>{detail.step_count || 0} 步</span>
                  <span className="job-sep">·</span>
                  <span>{fmtDur(detail.duration_s)}</span>
                  {detail.failed_count ? <span className="job-fail"> · {detail.failed_count} 失败</span> : null}
                </div>
              </div>
              <div className="job-detail-ops">
                {detail.session_id && <button className="confirm-btn" onClick={() => { setDetail(null); onOpenSession?.(detail.session_id); }}>打开会话</button>}
                {detail.user_text && <button className="confirm-btn confirm-primary" onClick={() => { setDetail(null); onRetry?.(detail); }}>带入输入框重跑</button>}
              </div>
            </div>
            {detail.error && <div className="empty-tip is-err">{detail.error}</div>}
            {detail.loading ? <div className="empty-tip is-loading">读取步骤…</div> : (
              <div className="job-steps">
                {(detail.steps || []).length === 0 && <div className="empty-tip">这次作业没有工具步骤。</div>}
                {(detail.steps || []).map((s, i) => <StepRow key={i} s={s} i={i} />)}
              </div>
            )}
          </div>
        </Overlay>
      )}
    </div>
  );
}
