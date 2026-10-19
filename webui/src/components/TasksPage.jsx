import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";
import EmptyState from "./EmptyState.jsx";
import { SkeletonList } from "./Skeleton.jsx";
import { usePoll } from "../usePoll.js";

// ── 任务（执行历史）──
// 这是「执行作为一等对象」的第一个落地页：一轮生成 = 一条作业记录，
// 有状态、有步数、有耗时、有失败数，可以按状态筛、可以跳回会话。
//
// 明确不做的：伪造进行中的状态。作业记录在**结束**时写盘，正在跑的轮次
// 由会话页的「过程」时间轴负责——两处各管一段，不重叠、不矛盾。

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

function JobRow({ job, onOpen }) {
  const st = STATUS[job.status] || { label: job.status || "—", cls: "" };
  const hasFail = (job.failed_count || 0) > 0;
  const canOpen = !!job.session_id;
  return (
    <button
      type="button"
      className={`job card-hover${canOpen ? "" : " job-static"}`}
      onClick={canOpen ? () => onOpen(job.session_id) : undefined}
      title={canOpen ? "打开对应会话" : "该记录没有关联会话"}
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
        {canOpen && <Icon name="chevron-right" size={13} />}
      </span>
    </button>
  );
}

export default function TasksPage({ onOpenSession }) {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [limit, setLimit] = React.useState(50);

  const load = React.useCallback(async () => {
    try {
      const r = await api.jobs({ limit, status });
      if (r) {
        setData(r);
        setErr("");
      }
    } catch {
      setErr("执行历史加载失败（后端未连接）");
    }
  }, [limit, status]);

  React.useEffect(() => { load(); }, [load]);
  // 列表页每 20 秒刷新一次即可（作业在结束时落盘，不是实时流）
  usePoll(load, 20000);

  const jobs = (data && data.jobs) || [];
  const sum = (data && data.summary) || { count: 0, failed: 0, seconds: 0 };

  return (
    <div className="page">
      <div className="page-head">
        <h1>任务</h1>
        <p>
          每一次执行都是一条记录：跑了多少步、有没有失败、花了多久。
          记录只保存「发生了什么」，不保存完整参数与结果。
        </p>
      </div>

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
        </div>
        <button className="msg-op" onClick={load}>刷新</button>
      </div>

      {err && <div className="empty-tip is-err">{err}</div>}
      {!data && !err && <SkeletonList rows={5} />}
      {data && jobs.length === 0 && (
        <EmptyState
          icon="🗂"
          title={status ? "该状态下没有记录" : "还没有执行记录"}
          hint={status ? "换个筛选看看" : "跑一次带工具的任务，结束后这里会出现一条记录"}
        />
      )}
      {jobs.length > 0 && (
        <div className="job-list">
          {jobs.map((j) => <JobRow key={j.id} job={j} onOpen={onOpenSession} />)}
        </div>
      )}
      {data && jobs.length >= limit && (
        <div className="jobs-more">
          <button className="msg-op" onClick={() => setLimit((v) => Math.min(500, v + 50))}>加载更多</button>
        </div>
      )}
    </div>
  );
}
