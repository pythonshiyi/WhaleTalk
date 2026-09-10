import React from "react";
import * as api from "../api.js";
import EmptyState from "./EmptyState.jsx";

function EvTab({ onToast }) {
  const [evs, setEvs] = React.useState(null);
  const [ignored, setIgnored] = React.useState([]);
  const [branches, setBranches] = React.useState(null);
  const [detail, setDetail] = React.useState(null); // {name, stat, diff}
  const [evoDetail, setEvoDetail] = React.useState(null); // 提案方案全文
  const [loading, setLoading] = React.useState(false);

  const load = React.useCallback(async () => {
    const [e, b] = await Promise.all([api.getEvolutions().catch(() => null), api.getEvolveBranches().catch(() => null)]);
    setEvs(e && e.evolutions ? e.evolutions : []);
    setIgnored(e && e.ignored ? e.ignored : []);
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

  const showProposal = async (name) => {
    setEvoDetail(null);
    try {
      const d = await api.getEvolutionDetail(name);
      if (d && d.files) {
        setEvoDetail({ ...d, name });
      } else if (d && d.error) {
        onToast && onToast(d.error);
      }
    } catch {
      onToast && onToast("读取方案失败");
    }
  };

  const confirmIgnore = async (name) => {
    if (!window.confirm(`忽略提案 ${name}？\n\n它会被归档到下方「已忽略的提案」，随时可以一键恢复——不会删除。`)) return;
    act(() => api.ignoreEvolution(name), `已忽略 ${name}（可在「已忽略的提案」恢复）`);
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
          <div className="empty-tip is-loading">加载中…</div>
        ) : evs.length === 0 ? (
          <EmptyState icon="💡" title="还没有进化提案" hint="让 AI 用 create_evolution 提出改进方案（如：对 X 提出改进提案），方案会出现在这里供你审阅采纳。" compact />
        ) : (
          <div className="au-list">
            {evs.map((e) => (
              <div className="au-item" key={e.name}>
                <div className="au-item-main">
                  <b>{e.title || e.name}</b>
                  <span className="pm-cat">
                    {e.mtime} · {e.files.length} 个文件
                    {(e.body_files || []).length > 0 ? ` + ${e.body_files.length} 篇正文` : ""}
                    {e.applied ? " · 已采纳" : ""}
                  </span>
                  {/* 摘要直接展示在列表层：入口页空着等于方案不存在（G17） */}
                  {e.summary ? (
                    <div className="au-subject">📄 {e.summary}</div>
                  ) : (
                    <div className="au-subject">
                      ⚠️ 未读到方案摘要（入口页可能是占位模板或尚无正文）——点「查看方案」确认，必要时让 AI 重写入口页。
                    </div>
                  )}
                  {(e.body_files || []).length > 0 && (
                    <div className="au-files">
                      {(e.body_files || []).slice(0, 6).map((f) => <code key={f}>{f}</code>)}
                    </div>
                  )}
                  <div className="au-files">
                    {e.files.slice(0, 10).map((f) => <code key={f}>{f}</code>)}
                  </div>
                </div>
                <div className="au-item-ops">
                  <button className="pm-op" onClick={() => showProposal(e.name)}>查看方案</button>
                  {!e.applied && (
                    <>
                      <button className="pm-op" onClick={() => act(() => api.applyEvolution(e.name), `已采纳 ${e.name}`)}>采纳</button>
                      <button className="pm-op pm-op-danger" onClick={() => confirmIgnore(e.name)}>忽略</button>
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
        <div className="au-card-title">🗄 已忽略的提案（软删除 · 可恢复）</div>
        {ignored.length === 0 ? (
          <div className="empty-tip">
            没有被忽略的提案。忽略只会归档到「已忽略」，不再删除——历史上一次「忽略」曾永久吃掉 4 份提案。
          </div>
        ) : (
          <div className="au-list">
            {ignored.map((g) => (
              <div className="au-item" key={g.archived}>
                <div className="au-item-main">
                  <b>{g.origin}</b>
                  <span className="pm-cat">忽略于 {g.mtime} · 内容完整保留</span>
                </div>
                <div className="au-item-ops">
                  <button className="pm-op" onClick={() => act(() => api.restoreEvolution(g.archived), `已恢复 ${g.origin}`)}>恢复</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="au-card">
        <div className="au-card-title">🔀 自我进化分支（self_evolve）</div>
        {branches === null ? (
          <div className="empty-tip is-loading">加载中…</div>
        ) : branches.length === 0 ? (
          <EmptyState icon="🌿" title="还没有改进分支" hint="AI 用 self_evolve 提交的改进分支会出现在这里，可查看 diff 后合并。" compact />
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

      {evoDetail && (
        <div className="au-detail-overlay" onClick={() => setEvoDetail(null)}>
          <div className="au-detail" onClick={(e) => e.stopPropagation()}>
            <div className="au-detail-head">
              <b>方案 · {evoDetail.title || evoDetail.name}</b>
              <button className="pm-op" onClick={() => setEvoDetail(null)}>✕</button>
            </div>
            {evoDetail.placeholder && (
              <div className="au-subject">
                ⚠️ 这份提案的入口页仍是占位模板，尚无实质方案内容——可让 AI 用 create_evolution 重写。
              </div>
            )}
            {(evoDetail.files || []).map((f) => (
              <div key={f.rel || f.name}>
                <div className="au-subject">
                  <code>{f.rel || f.name}</code>
                  {f.nested ? "（正文/附件，采纳时不覆盖工程文件）" : ""}
                </div>
                <pre className="au-diff">{f.content}</pre>
              </div>
            ))}
          </div>
        </div>
      )}

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
        <div className="empty-tip is-loading">加载中…</div>
      ) : items.length === 0 ? (
        <EmptyState icon="📋" title="还没有审批记录" hint="AI 请求权限或向你提问时，记录会出现在这里。" compact />
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
          <div className="empty-tip is-loading">加载中…</div>
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
          <div className="empty-tip is-loading">加载中…</div>
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
  const [fstats, setFstats] = React.useState(null);
  const [showResolved, setShowResolved] = React.useState(false);

  const loadFailures = React.useCallback(async () => {
    try {
      const f = await api.getFailures();
      setFailures(f && f.failures ? f.failures : []);
      setFstats(f && f.stats ? f.stats : null);
    } catch {
      setFailures([]);
    }
  }, []);

  React.useEffect(() => {
    Promise.all([api.getSelfProfile().catch(() => null), api.getFailures().catch(() => null)]).then(([p, f]) => {
      setProfile(p && p.text ? p.text : "（自我状态为空）");
      setFailures(f && f.failures ? f.failures : []);
      setFstats(f && f.stats ? f.stats : null);
    });
  }, []);

  const resolveOne = async (fp) => {
    try {
      const r = await api.resolveFailure({ fingerprint: fp });
      if (r && r.ok) await loadFailures();
    } catch {
      /* 失败静默：下拉刷新即可重试 */
    }
  };

  const shown = (failures || []).filter((f) => showResolved || !f.resolved);
  return (
    <div className="au-col">
      <div className="au-card">
        <div className="au-card-title">🧠 核心自我状态（self_profile · 跨会话连续）</div>
        {profile === null ? (
          <div className="empty-tip is-loading">加载中…</div>
        ) : (
          <pre className="au-profile">{profile}</pre>
        )}
      </div>
      <div className="au-card">
        <div className="au-card-title">💥 失败模式库（AI 犯过的错 · 下次自动规避）</div>
        {fstats && (
          <div className="pm-cat" style={{ marginBottom: 8 }}>
            未消解 {fstats.unresolved} · 已消解 {fstats.resolved} · 复现多次 {fstats.recurring}
            （同一工具连续成功 2 次即自动消解；已消解项不再注入 AI 上下文）
            <button className="pm-op" style={{ marginLeft: 8 }} onClick={() => setShowResolved((v) => !v)}>
              {showResolved ? "只看未消解" : "显示已消解"}
            </button>
          </div>
        )}
        {failures === null ? (
          <div className="empty-tip is-loading">加载中…</div>
        ) : shown.length === 0 ? (
          <div className="empty-tip">暂无{showResolved ? "" : "未消解的"}失败记录</div>
        ) : (
          <div className="au-list">
            {shown.map((f, i) => (
              <div className="au-item" key={f.fingerprint || i}>
                <div className="au-item-main">
                  <b>{f.tool || "工具"}</b>
                  <span className="pm-cat">
                    {f.resolved ? "已消解 · " : ""}
                    复现 {f.hits || 1} 次 · 最近 {String(f.last_ts || "").slice(0, 16)}
                    {f.resolved_by ? ` · ${f.resolved_by}` : ""}
                  </span>
                  <div className="au-subject">{f.error || ""}</div>
                  {f.note && <div className="au-files"><code>{f.note}</code></div>}
                </div>
                {!f.resolved && f.fingerprint && (
                  <div className="au-item-ops">
                    <button className="pm-op" onClick={() => resolveOne(f.fingerprint)}>标记已修复</button>
                  </div>
                )}
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
