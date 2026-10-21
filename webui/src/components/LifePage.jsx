import React from "react";
import * as api from "../api.js";
import usePoll from "../usePoll.js";
import { Icon } from "./icons.jsx";
import EmptyState from "./EmptyState.jsx";
import { SkeletonList } from "./Skeleton.jsx";

const DRIVE_LABELS = {
  curiosity: "好奇",
  maintenance: "维护",
  connection: "连接",
  mastery: "掌握",
  coherence: "一致",
};

const STATE_LABELS = {
  dormant: "休眠",
  sleeping: "睡眠",
  waking: "醒来",
  awake: "清醒",
  idle: "空闲",
  reflecting: "内省",
  acting: "行动",
  observer: "观察",
  paused: "暂停",
};

const KIND_LABELS = {
  wake: "醒来",
  sleep_begin: "入睡",
  sleep_start: "睡眠",
  sleep_end: "睡醒",
  reflect_begin: "开始内省",
  reflect: "内省",
  reflect_error: "内省失败",
  rest: "休息",
  intention: "意图",
  outcome: "结果",
  goal: "目标",
  thought: "思考",
  heartbeat: "心跳",
  pulse: "脉搏",
  consolidate: "整理",
  decision: "决策",
  decision_resolve: "回执",
  evolution: "进化",
  scar: "代价",
  epoch: "纪元",
  mount: "挂载",
  unmount: "卸载",
  shutdown: "停止",
  observer: "观察",
};

function fmtTime(ts) {
  if (!ts) return "—";
  const s = String(ts);
  return s.length >= 16 ? s.slice(0, 16).replace("T", " ") : s;
}

function pct(n) {
  const v = Number(n || 0);
  return Math.max(0, Math.min(100, Math.round(v * 100)));
}

function Bar({ value, tone = "" }) {
  return (
    <div className="life-bar" role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
      <span className={`life-bar-fill ${tone}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

function Stat({ label, value, hint }) {
  return (
    <div className="life-stat">
      <div className="life-stat-label">{label}</div>
      <div className="life-stat-value">{value}</div>
      {hint ? <div className="life-stat-hint">{hint}</div> : null}
    </div>
  );
}

export default function LifePage() {
  const [data, setData] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");

  const load = React.useCallback(async () => {
    try {
      const r = await api.getLife();
      setData(r && r.life ? r.life : null);
      setErr("");
    } catch (e) {
      setErr(String((e && e.message) || e));
    }
  }, []);

  usePoll(load, 5000);

  const act = async (action, extra = {}) => {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.lifeAction({ action, ...extra });
      if (r && r.ok === false) setErr(r.message || "操作失败");
      await load();
    } catch (e) {
      setErr(String((e && e.message) || e));
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>生命</h1>
          <p>生命循环尚未返回状态；大脑初始化后会自动醒来。</p>
        </div>
        <SkeletonList rows={3} />
      </div>
    );
  }

  const st = data.state || {};
  const met = data.metabolism || {};
  const dr = data.drives || {};
  const report = data.report || {};
  const active = (data.intentions && data.intentions.active) || [];
  const counts = (data.intentions && data.intentions.counts) || {};
  const events = data.timeline || [];
  const stateName = st.state || "dormant";
  const paused = !!st.paused;
  const ops = (data.metrics && data.metrics.metrics) || {};
  const storage = data.storage || {};
  const op = (name) => ops[name] || {};

  return (
    <div className="page life-page">
      <div className="page-head">
        <h1>生命</h1>
        <p>连续时间、内驱、代谢与睡眠。不是聊天窗口的另一个标签，而是它自己的节奏。</p>
      </div>

      {err ? (
        <div className="life-error" role="alert">
          <Icon name="warning" size={15} /> {err}
        </div>
      ) : null}

      <div className="au-card life-hero">
        <div className="life-hero-main">
          <div className={`life-orb ${stateName}`} aria-hidden="true" />
          <div>
            <div className="life-state">
              {STATE_LABELS[stateName] || stateName}
              {paused ? " · 已暂停" : ""}
            </div>
            <div className="life-sub">
              纪元 {st.epoch || 1} · 第 {st.boot_count || 0} 次醒来 · tick {st.tick_count || 0} · 上次心跳 {fmtTime(st.last_tick)}
              {st.next_wake_at ? ` · 下次醒来 ${fmtTime(st.next_wake_at)}` : ""}
            </div>
          </div>
        </div>
        <div className="life-actions">
          {paused ? (
            <button className="pm-op" disabled={busy} onClick={() => act("resume")}>
              <Icon name="play" size={13} /> 恢复
            </button>
          ) : (
            <button className="pm-op" disabled={busy} onClick={() => act("pause")}>
              <Icon name="stop" size={13} /> 暂停
            </button>
          )}
          <button className="pm-op" disabled={busy} onClick={() => act("wake")}>
            <Icon name="activity" size={13} /> 唤醒
          </button>
          <button className="pm-op" disabled={busy} onClick={() => act("sleep")}>
            <Icon name="moon" size={13} /> 睡眠
          </button>
          <button className="pm-op" disabled={busy} onClick={() => act("reflect")}>
            <Icon name="sparkles" size={13} /> 内省
          </button>
          <button className="pm-op" disabled={busy} onClick={() => act("tick", { force: true })}>
            <Icon name="refresh" size={13} /> 手动 tick
          </button>
        </div>
      </div>

      <div className="life-grid">
        <div className="au-card life-card">
          <div className="au-card-title"><Icon name="thermometer" size={14} /> 代谢</div>
          <div className="life-meters">
            <div>
              <div className="life-meter-head"><span>能量</span><b>{Math.round(met.energy || 0)} / 100</b></div>
              <Bar value={Number(met.energy || 0)} tone="energy" />
            </div>
            <div>
              <div className="life-meter-head"><span>今日 token</span><b>{met.used_tokens || 0} / {met.budget_tokens || 0}</b></div>
              <Bar value={pct(met.tokens_ratio)} tone="tokens" />
            </div>
            <div>
              <div className="life-meter-head"><span>今日费用</span><b>¥{met.used_cost || 0} / ¥{met.budget_cost || 0}</b></div>
              <Bar value={pct(met.cost_ratio)} tone="cost" />
            </div>
          </div>
          <div className="life-meta">
            疲劳 {met.fatigue || 0} · 动作 {met.actions || 0} · 反思 {met.reflections || 0}
            {met.peak ? " · 高峰时段" : ""}
            {met.can_think === false ? ` · ${met.reason || "暂不思考"}` : ""}
          </div>
        </div>

        <div className="au-card life-card">
          <div className="au-card-title"><Icon name="target" size={14} /> 内驱</div>
          {(dr.drives ? Object.entries(dr.drives) : []).map(([k, v]) => (
            <div className="life-drive" key={k}>
              <div className="life-meter-head">
                <span>{DRIVE_LABELS[k] || k}</span>
                <b>{v}</b>
              </div>
              <Bar value={Number(v || 0)} tone={k === dr.top ? "top" : ""} />
              <div className="life-drive-reason">{dr.reasons ? dr.reasons[k] : ""}</div>
            </div>
          ))}
          {!dr.drives ? <EmptyState icon="target" title="还没有内驱信号" hint="大脑有目标、决策或记忆后会自动出现。" compact /> : null}
        </div>

        <div className="au-card life-card">
          <div className="au-card-title"><Icon name="chart" size={14} /> 健康度</div>
          <div className="life-health">
            <div className="life-health-num">{report.health != null ? report.health : "—"}</div>
            <div className="life-health-unit">/ 100</div>
          </div>
          <div className="life-kv">
            <span>连续性</span><b>{report.continuity ? report.continuity.score : "—"}</b>
            <span>自主性</span><b>{report.autonomy ? report.autonomy.score : "—"}</b>
            <span>完成率</span><b>{report.competence ? report.competence.score : "—"}</b>
            <span>记忆</span><b>{report.memory ? `${report.memory.total} 条` : "—"}</b>
            <span>上次睡眠</span><b>{fmtTime(report.sleep && report.sleep.last_sleep)}</b>
            <span>tick 平均</span><b>{op("tick_ms").avg != null ? `${op("tick_ms").avg} ms` : "—"}</b>
            <span>反思 / 睡眠</span><b>{op("reflect_ms").count || 0} / {op("sleep_ms").count || 0}</b>
            <span>待折命中</span><b>{storage.hits_pending || 0}</b>
            <span>归档意图</span><b>{storage.intentions_archived || 0}</b>
          </div>
        </div>

        <div className="au-card life-card life-intentions">
          <div className="au-card-title"><Icon name="bolt" size={14} /> 活跃意图</div>
          <div className="life-meta">
            活跃 {counts.active || 0} · 完成 {counts.done || 0} · 失败 {counts.failed || 0}
          </div>
          {active.length === 0 ? (
            <EmptyState icon="sparkles" title="现在没有活跃意图" hint="空闲到阈值后，它会自己提出一个；也可以点上方「内省」。" compact />
          ) : (
            <div className="life-intent-list">
              {active.map((x) => (
                <div className="life-intent" key={x.id}>
                  <div className="life-intent-main">
                    <b>{x.title}</b>
                    <div className="life-intent-meta">
                      {DRIVE_LABELS[x.drive] || x.drive || x.kind} · {x.status} · {fmtTime(x.created_at)}
                    </div>
                    {x.why ? <div className="life-intent-why">{x.why}</div> : null}
                    {x.expected ? <div className="life-intent-expected">预期：{x.expected}</div> : null}
                  </div>
                  <div className="life-intent-ops">
                    <button className="pm-op" disabled={busy} onClick={() => act("complete-intention", { id: x.id, outcome: "手动完成" })}>完成</button>
                    <button className="pm-op pm-op-danger" disabled={busy} onClick={() => act("abandon-intention", { id: x.id, reason: "手动放弃" })}>放弃</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="au-card life-card life-timeline">
        <div className="au-card-title"><Icon name="clock" size={14} /> 时间线</div>
        {events.length === 0 ? (
          <EmptyState icon="clock" title="时间线还是空的" hint="醒来、思考、意图与睡眠都会写在这里。" compact />
        ) : (
          <div className="life-timeline-list">
            {events.slice().reverse().map((ev) => (
              <div className="life-event" key={ev.id || `${ev.ts}-${ev.kind}`}>
                <span className={`life-event-kind kind-${ev.kind}`}>{KIND_LABELS[ev.kind] || ev.kind}</span>
                <span className="life-event-time">{fmtTime(ev.ts)}</span>
                <span className="life-event-text">{ev.summary || "—"}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
