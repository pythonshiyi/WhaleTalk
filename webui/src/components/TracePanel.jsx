import React from "react";
import { Icon } from "./icons.jsx";
import { filterTurns, fmtSeconds, stepsSummary, traceSummary } from "../traceModel.js";

// ── 执行时间轴（「过程」页签）──
// 为什么存在：执行过程过去长在正文里，一轮 5–15 步会把回答挤出屏幕；把它折叠
// 又只是把信息藏起来。正确的形态是「仪表」：常驻一侧、可回放、可筛选、可定位。
//
// 交互约定：
//  · 正文里的锚点（「6 步 · 12.3s」）点击后，本面板切到对应轮次并高亮；
//  · 轮次之间用竖线相连表达时间顺序；同轮内步骤按发生顺序排列；
//  · 失败步骤默认展开（异常不该需要二次点击才发现），其余默认收起。
const FILTERS = [
  { id: "all", label: "全部" },
  { id: "failed", label: "仅失败" },
  { id: "active", label: "执行中" },
];

function StatusDot({ status }) {
  const cls = status === "failed" ? "fail" : status === "running" ? "run" : "done";
  return <span className={`tl-dot tl-dot-${cls}`} aria-hidden="true" />;
}

function StepRow({ step, index }) {
  const summary = React.useMemo(() => {
    const a = step.args || {};
    return Object.entries(a).slice(0, 2)
      .map(([k, v]) => `${k}=${String(v).length > 22 ? `${String(v).slice(0, 22)}…` : String(v)}`)
      .join("  ");
  }, [step.args]);
  const [open, setOpen] = React.useState(step.status === "failed");
  const failed = step.status === "failed";
  const running = step.status === "running";
  return (
    <div className={`tl-step${failed ? " tl-step-fail" : ""}${open ? " tl-step-open" : ""}`}>
      <button
        type="button"
        className="tl-step-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="tl-step-idx">{running ? index : failed ? "!" : index}</span>
        <StatusDot status={step.status} />
        <span className="tl-step-name">{step.tool}</span>
        {summary && <span className="tl-step-args">{summary}</span>}
        <span className="tl-step-right">
          {running ? <span className="tl-running">执行中</span>
            : step.duration && step.duration !== "—" ? <span className="tl-dur">{step.duration}s</span> : null}
        </span>
      </button>
      {open && (
        <div className="tl-step-body">
          {step.argsText || (step.args && Object.keys(step.args).length > 0) ? (
            <div className="tl-kv"><span className="tl-k">参数</span>
              <code className="tl-v">{step.argsText || JSON.stringify(step.args)}</code>
            </div>
          ) : null}
          {step.result ? (
            <div className="tl-kv"><span className="tl-k">结果</span>
              <pre className="tl-pre">{String(step.result).slice(0, 4000)}</pre>
            </div>
          ) : running ? (
            <div className="tl-kv"><span className="tl-k">结果</span><span className="tl-v">等待中…</span></div>
          ) : null}
        </div>
      )}
    </div>
  );
}

export default function TracePanel({ activity, focusTurn = -1, active = true }) {
  const turns = (activity && activity.turns) || [];
  const [mode, setMode] = React.useState("all");
  const listRef = React.useRef(null);
  // 统计始终按**全部**轮次算：筛选后汇总跟着变会让人以为任务总量变小了
  const sum = React.useMemo(() => traceSummary(turns), [turns]);
  const shown = React.useMemo(() => filterTurns(turns, mode), [turns, mode]);

  // 锚点定位：滚到对应轮次并短暂高亮（不改变筛选，避免「点了却看不到」）
  // focusTurn 允许是数字，或 {turn, at}——后者让「连续点同一轮」也能重新触发滚动。
  const focusId = focusTurn && typeof focusTurn === "object" ? focusTurn.turn : focusTurn;
  const focusAt = focusTurn && typeof focusTurn === "object" ? focusTurn.at : focusTurn;
  const [flash, setFlash] = React.useState(-1);
  React.useEffect(() => {
    if (focusId == null || focusId < 0) return undefined;
    const box = listRef.current;
    if (!box) return undefined;
    // 筛选成「仅失败」时目标轮次可能不在列表里：临时切回全部，保证点了就能看到
    let el = box.querySelector(`[data-turn="${focusId}"]`);
    if (!el && mode !== "all") {
      setMode("all");
      return undefined;
    }
    try { el.scrollIntoView({ block: "center", behavior: "smooth" }); } catch { el && el.scrollIntoView(); }
    setFlash(focusId);
    const t = setTimeout(() => setFlash(-1), 1200);
    return () => clearTimeout(t);
  }, [focusId, focusAt, mode]);

  if (!turns.length) {
    return (
      <div className="tl-empty">
        <Icon name="activity" size={20} />
        <p>还没有执行记录。让鲸语跑一次带工具的任务，过程会出现在这里。</p>
      </div>
    );
  }

  return (
    <div className="tl-wrap">
      <div className="tl-sum">
        <div className="tl-metric"><b>{sum.turns}</b><span>轮</span></div>
        <div className="tl-metric"><b>{sum.count}</b><span>步</span></div>
        <div className={`tl-metric${sum.failed ? " is-bad" : ""}`}><b>{sum.failed}</b><span>失败</span></div>
        <div className="tl-metric"><b>{fmtSeconds(sum.seconds) || "—"}</b><span>耗时</span></div>
      </div>

      <div className="tl-filter" role="tablist" aria-label="过程筛选">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            role="tab"
            aria-selected={mode === f.id}
            className={`tl-fbtn${mode === f.id ? " is-on" : ""}`}
            onClick={() => setMode(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="tl-list" ref={listRef}>
        {shown.length === 0 && (
          <div className="tl-empty-inline">
            {mode === "failed" ? "没有失败的步骤。" : "当前没有正在执行的任务。"}
          </div>
        )}
        {shown.map((turn) => {
          const s = stepsSummary(turn.steps);
          const isFocus = flash === turn.taskId;
          return (
            <section
              key={turn.taskId}
              data-turn={turn.taskId}
              className={`tl-turn${isFocus ? " tl-turn-focus" : ""}${turn.failed ? " tl-turn-bad" : ""}`}
            >
              <header className="tl-turn-head">
                <span className="tl-turn-time">{turn.time || ""}</span>
                <span className="tl-turn-label">{turn.label}</span>
                <span className="tl-turn-meta">
                  {s.count} 步{s.failed ? ` · ${s.failed} 失败` : ""}
                  {s.seconds > 0 ? ` · ${fmtSeconds(s.seconds)}` : ""}
                </span>
              </header>
              <div className="tl-rail">
                {(turn.steps || []).map((step, i) => (
                  <StepRow key={step.id || `${turn.taskId}-${i}`} step={step} index={i + 1} />
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
