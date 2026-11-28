import React from "react";
import * as api from "../api.js";
import { usePoll } from "../usePoll.js";
import { Icon } from "./icons.jsx";
import Overlay from "./Overlay.jsx";

// 待处理收件箱：把散落在记忆 / 自主 / 工作台 / 信任内核里的「等人决定」
// 聚合到一个入口。只做汇总与跳转，具体操作仍由对应页面完成（记忆可直接裁决）。
export default function InboxButton({ onNavigate }) {
  const [open, setOpen] = React.useState(false);
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState("");
  const [loadErr, setLoadErr] = React.useState("");

  const load = React.useCallback(async () => {
    const [failed, sched, channels] = await Promise.allSettled([
      api.jobs({ status: "error", limit: 20 }),
      api.getSchedules(),
      api.listChannels(),
    ]);
    const out = [];
    if (failed.status === "fulfilled") {
      const jobs = failed.value?.jobs || [];
      for (const j of jobs.slice(0, 5)) {
        out.push({
          id: `job:${j.id}`, kind: "失败任务", icon: "warning",
          title: String(j.user_text || "未命名任务").slice(0, 110),
          detail: `${j.started_at || ""} · ${j.failed_count || 0} 步失败 · ${j.duration_s || 0}s`,
          dest: { page: "workbench", workbenchTab: "tasks" },
        });
      }
    }
    if (sched.status === "fulfilled") {
      for (const s of (sched.value?.schedules || [])) {
        if (s.last_status === "error") {
          out.push({
            id: `sch:${s.id || s.name}`, kind: "定时任务", icon: "clock",
            title: `定时任务失败：${s.name || s.text || "未命名"}`,
            detail: String(s.last_result || "").slice(0, 140),
            dest: { page: "workbench", workbenchTab: "overview" },
          });
        }
      }
    }
    if (channels.status === "fulfilled") {
      for (const c of (channels.value?.channels || [])) {
        if (c.last_error) {
          out.push({
            id: `ch:${c.name}`, kind: "接入通道", icon: "globe",
            title: `${c.display_name || c.name} 异常`,
            detail: String(c.last_error).slice(0, 140),
            dest: { page: "settings", settingsTab: "channels" },
          });
        }
      }
    }
    setItems(out);
    // 体验：某些汇总接口失败时给出可见提示，不再静默吞掉（此前待办数字会莫名偏少）
    const failedFetches = [failed, sched, channels]
      .filter((r) => r.status === "rejected").length;
    setLoadErr(failedFetches ? `${failedFetches} 项数据加载失败，部分待办可能未显示` : "");
    setLoading(false);
  }, []);

  usePoll(load, 20000);

  const count = items.length;
  return (
    <>
      <button
        type="button"
        className={`sb-inbox ${count ? "has-items" : ""}`}
        title={count ? `${count} 项待处理` : "待处理"}
        aria-label={count ? `${count} 项待处理` : "待处理"}
        onClick={() => setOpen(true)}
      >
        <Icon name="bell" size={18} />
        <span className="sb-item-label">待办</span>
        {count > 0 && <span className="sb-inbox-badge">{count > 9 ? "9+" : count}</span>}
      </button>

      {open && (
        <Overlay title={count ? `待处理 · ${count}` : "待处理"} onClose={() => setOpen(false)} wide>
          {loadErr && (
            <div className="empty-tip is-err">
              {loadErr}　<button className="msg-op" onClick={load}>重试</button>
            </div>
          )}
          {loading ? (
            <div className="empty-tip is-loading">正在汇总待处理事项…</div>
          ) : items.length === 0 ? (
            <div className="empty-tip">全部处理完了。新的待确认记忆、失败任务与进化提案会出现在这里。</div>
          ) : (
            <div className="inbox-list">
              {items.map((it) => (
                <div className="inbox-item" key={it.id}>
                  <span className="inbox-ic"><Icon name={it.icon} size={16} /></span>
                  <div className="inbox-main">
                    <div className="inbox-title">
                      <span className="inbox-kind">{it.kind}</span>
                      <b>{it.title}</b>
                      {it.count ? <span className="pm-badge">{it.count}</span> : null}
                    </div>
                    {it.detail && <div className="inbox-detail">{it.detail}</div>}
                  </div>
                  <div className="inbox-ops">
                    {it.memoryId ? (
                      <>
                        <button className="pm-op" disabled={busy === it.id} onClick={() => resolveMemory(it.memoryId, "accept")}>写入</button>
                        <button className="pm-op pm-op-danger" disabled={busy === it.id} onClick={() => resolveMemory(it.memoryId, "dismiss")}>忽略</button>
                      </>
                    ) : (
                      <button className="pm-op" onClick={() => { setOpen(false); onNavigate?.(it.dest); }}>查看 →</button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Overlay>
      )}
    </>
  );
}
