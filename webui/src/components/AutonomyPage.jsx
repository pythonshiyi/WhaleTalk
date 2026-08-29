import React from "react";
import * as api from "../api.js";

function EvTab({ onToast }) {
  const [evs, setEvs] = React.useState(null);
  const [branches, setBranches] = React.useState(null);
  const [detail, setDetail] = React.useState(null); // {name, stat, diff}
  const [loading, setLoading] = React.useState(false);

  const load = React.useCallback(async () => {
    const [e, b] = await Promise.all([api.getEvolutions().catch(() => null), api.getEvolveBranches().catch(() => null)]);
    setEvs(e && e.evolutions ? e.evolutions : []);
    setBranches(b && b.branches ? b.branches : []);
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const act = async (fn, okMsg) => {
    try {
      const r = await fn();
      if (r && r.ok) {
        onToast && onToast(okMsg);
        load();
      } else if (r && r.error) {
        onToast && onToast(`操作失败：${r.error}`);
      }
    } catch {
      onToast && onToast("操作失败：后端未响应");
    }
  };

  const showDiff = async (name) => {
    setDetail(null);
    try {
      const d = await api.getEvolveBranchDetail(name);
      if (d && (d.stat || d.diff)) {
        setDetail({ name, stat: d.stat || "", diff: d.diff || "" });
      } else if (d && d.error) {
        onToast && onToast(d.error);
      }
    } catch {
      onToast && onToast("读取 diff 失败");
    }
  };

  const confirmMerge = async (name) => {
    if (!window.confirm(`确认将分支 ${name} 合入当前分支？\n合并前请先查看 diff。`)) return;
    setLoading(true);
    try {
      const r = await api.mergeEvolveBranch(name);
      if (r && r.ok) {
        onToast && onToast(`已合并 ${name}（可 git log 查看，需要回滚可 git revert）`);
        load();
      } else {
        onToast && onToast(`合并失败：${(r && r.error) || "未知错误（可能有冲突）"}`);
      }
    } catch {
      onToast && onToast("合并失败：后端未响应");
    }
    setLoading(false);
  };

  const confirmDelete = async (name) => {
    if (!window.confirm(`确认删除分支 ${name}？删除后无法恢复。`)) return;
    try {
      const r = await api.deleteEvolveBranch(name);
      if (r && r.ok) {
        onToast && onToast(`已删除 ${name}`);
        load();
      } else {
        onToast && onToast(`删除失败：${(r && r.error) || "未知错误"}`);
      }
    } catch {
      onToast && onToast("删除失败：后端未响应");
    }
  };

  return (
    <div className="au-col">
      <div className="au-card">
        <div className="au-card-title">📋 进化提案（create_evolution）</div>
        {evs === null ? (
          <div className="empty-tip">加载中…</div>
        ) : evs.length === 0 ? (
          <div className="empty-tip">暂无提案 —— 让 AI 用 create_evolution 提改进方案（如"对 X 提出改进提案"），方案会出现在这里供你审阅采纳</div>
        ) : (
          <div className="au-list">
            {evs.map((e) => (
              <div className="au-item" key={e.name}>
                <div className="au-item-main">
                  <b>{e.name}</b>
                  <span className="pm-cat">
                    {e.mtime} · {e.files.length} 个文件{e.applied ? " · 已采纳" : ""}
                  </span>
                  {e.files.length > 0 && (
                    <div className="au-files">{e.files.slice(0, 10).map((f) => <code key={f}>{f}</code>)}</div>
                  )}
                </div>
                <div className="au-item-ops">
                  {!e.applied && (
                    <>
                      <button className="pm-op" onClick={() => act(() => api.applyEvolution(e.name), `已采纳 ${e.name}`)}>采纳</button>
                      <button className="pm-op pm-op-danger" onClick={() => act(() => api.ignoreEvolution(e.name), `已忽略 ${e.name}`)}>忽略</button>
                    </>
                  )}
                  {e.applied && <span className="pm-badge">已采纳</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="au-card">
        <div className="au-card-title">🔀 自我进化分支（self_evolve）</div>
        {branches === null ? (
          <div className="empty-tip">加载中…</div>
        ) : branches.length === 0 ? (
          <div className="empty-tip">暂无 evolve 分支 —— AI 用 self_evolve 提交的改进分支会出现在这里，可查看 diff 后合并</div>
        ) : (
          <div className="au-list">
            {branches.map((b) => (
              <div className="au-item" key={b.name}>
                <div className="au-item-main">
                  <b>{b.name}</b>
                  <span className="pm-cat">
                    {b.date} · {b.files_changed} 文件 +{b.insertions}/-{b.deletions}
                  </span>
                  <div className="au-subject">{b.subject}</div>
                </div>
                <div className="au-item-ops">
                  <button className="pm-op" onClick={() => showDiff(b.name)}>查看 diff</button>
                  <button className="pm-op" disabled={loading} onClick={() => confirmMerge(b.name)}>合并</button>
                  <button className="pm-op pm-op-danger" onClick={() => confirmDelete(b.name)}>删除</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {detail && (
        <div className="au-detail-overlay" onClick={() => setDetail(null)}>
          <div className="au-detail" onClick={(e) => e.stopPropagation()}>
            <div className="au-detail-head">
              <b>diff · {detail.name}</b>
              <button className="pm-op" onClick={() => setDetail(null)}>✕</button>
            </div>
            {detail.stat && <pre className="au-diff">{detail.stat}</pre>}
            {detail.diff && <pre className="au-diff">{detail.diff}</pre>}
          </div>
        </div>
      )}
    </div>
  );
}

function ApprovalTab({ onToast }) {
  const [items, setItems] = React.useState(null);
  React.useEffect(() => {
    api.getApprovals().then((d) => setItems(d && d.approvals ? d.approvals : [])).catch(() => setItems([]));
  }, []);
  const resultCls = (r) => (r === "允许" || r === "已回答" ? "ok-text" : r === "拒绝" ? "warn-text" : "pm-cat");
  return (
    <div className="au-card">
      <div className="au-card-title">🛡 审批与询问记录（最近 200 条）</div>
      {items === null ? (
        <div className="empty-tip">加载中…</div>
      ) : items.length === 0 ? (
        <div className="empty-tip">暂无记录 —— AI 请求权限或向你提问时会记录在这里</div>
      ) : (
        <div className="au-list">
          {items.map((a, i) => (
            <div className="au-item" key={i}>
              <div className="au-item-main">
                <b>{a.type === "ask" ? "🤔 询问" : "🛡 权限请求"} · {a.name || a.prompt || "—"}</b>
                <span className="pm-cat">{a.ts}{a.args ? ` · ${a.args}` : ""}</span>
                {a.reason && <div className="au-subject">理由：{a.reason}</div>}
              </div>
              <div className="au-item-ops">
                <span className={resultCls(a.result)}>{a.result}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ActivityTab({ onToast }) {
  const [tasks, setTasks] = React.useState(null);
  const [audit, setAudit] = React.useState(null);
  React.useEffect(() => {
    Promise.all([api.getTasklog().catch(() => null), api.getAudit().catch(() => null)]).then(([t, a]) => {
      setTasks(t && t.tasks ? t.tasks : []);
      setAudit(a && a.entries ? a.entries : []);
    });
  }, []);
  return (
    <div className="au-col">
      <div className="au-card">
        <div className="au-card-title">🗂 最近任务（tasklog · AI 干了什么）</div>
        {tasks === null ? (
          <div className="empty-tip">加载中…</div>
        ) : tasks.length === 0 ? (
          <div className="empty-tip">暂无任务记录</div>
        ) : (
          <div className="au-list">
            {[...tasks].reverse().map((t, i) => (
              <div className="au-item" key={i}>
                <div className="au-item-main">
                  <b>{t.title || "未命名任务"}</b>
                  <div className="au-chain">
                    {(t.chain || []).slice(0, 12).map((c, j) => <code key={j}>{c}</code>)}
                    {(t.chain || []).length > 12 && <span className="pm-cat">+{(t.chain || []).length - 12}</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="au-card">
        <div className="au-card-title">🧾 审计日志（最近 200 条）</div>
        {audit === null ? (
          <div className="empty-tip">加载中…</div>
        ) : audit.length === 0 ? (
          <div className="empty-tip">暂无审计记录（工具调用审计未开启或尚未产生）</div>
        ) : (
          <div className="au-audit">{audit.map((l, i) => <code key={i}>{l}</code>)}</div>
        )}
      </div>
    </div>
  );
}

function SelfTab({ onToast }) {
  const [profile, setProfile] = React.useState(null);
  const [failures, setFailures] = React.useState(null);
  React.useEffect(() => {
    Promise.all([api.getSelfProfile().catch(() => null), api.getFailures().catch(() => null)]).then(([p, f]) => {
      setProfile(p && p.text ? p.text : "（自我状态为空）");
      setFailures(f && f.failures ? f.failures : []);
    });
  }, []);
  return (
    <div className="au-col">
      <div className="au-card">
        <div className="au-card-title">🧠 核心自我状态（self_profile · 跨会话连续）</div>
        {profile === null ? (
          <div className="empty-tip">加载中…</div>
        ) : (
          <pre className="au-profile">{profile}</pre>
        )}
      </div>
      <div className="au-card">
        <div className="au-card-title">💥 失败模式库（AI 犯过的错 · 下次自动规避）</div>
        {failures === null ? (
          <div className="empty-tip">加载中…</div>
        ) : failures.length === 0 ? (
          <div className="empty-tip">暂无失败记录</div>
        ) : (
          <div className="au-list">
            {failures.map((f, i) => (
              <div className="au-item" key={i}>
                <div className="au-item-main">
                  <b>{f.tool || "工具"}</b>
                  <span className="pm-cat">{f.ts || ""}</span>
                  <div className="au-subject">{f.error || f}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function AutonomyPage() {
  const [tab, setTab] = React.useState("evolve");
  const [tip, setTip] = React.useState("");
  React.useEffect(() => {
    if (!tip) return;
    const t = setTimeout(() => setTip(""), 2600);
    return () => clearTimeout(t);
  }, [tip]);
  const tabs = [
    { id: "evolve", label: "🧬 进化管理" },
    { id: "approval", label: "🛡 审批记录" },
    { id: "activity", label: "📜 行为日志" },
    { id: "self", label: "🧠 自我状态" },
  ];
  return (
    <div className="page">
      <div className="page-head">
        <h1>自主</h1>
        <p>AI 自主能力的观察与管理窗口 · 进化 · 审批 · 行为 · 自我</p>
      </div>
      <div className="au-tabs">
        {tabs.map((t) => (
          <button key={t.id} className={tab === t.id ? "au-tab au-tab-on" : "au-tab"} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
        {tip && <span className="au-tip">{tip}</span>}
      </div>
      {tab === "evolve" && <EvTab onToast={setTip} />}
      {tab === "approval" && <ApprovalTab onToast={setTip} />}
      {tab === "activity" && <ActivityTab onToast={setTip} />}
      {tab === "self" && <SelfTab onToast={setTip} />}
    </div>
  );
}
