import React from "react";
import { ThemeContext, DisplayContext } from "../App.jsx";
import * as api from "../api.js";
import { usePoll } from "../usePoll.js";
import ToolTest from "./ToolTest.jsx";
import EmptyState from "./EmptyState.jsx";
import { Icon } from "./icons.jsx";
import CapabilitiesPage from "./CapabilitiesPage.jsx";
import PermissionsPage from "./PermissionsPage.jsx";
import TasksPage from "./TasksPage.jsx";
import { confirmDialog } from "../dialog.js";

import { silentWarn } from "../quiet.js";

export function AbilitiesPage({ initialQuery = "", onAskTool }) {
  const [open, setOpen] = React.useState(null);
  const [expanded, setExpanded] = React.useState({});
  const [tab, setTab] = React.useState("tools");
  const [domains, setDomains] = React.useState(null);
  const [health, setHealth] = React.useState({});
  const [total, setTotal] = React.useState(0);
  const [testTool, setTestTool] = React.useState(null);
  const [err, setErr] = React.useState("");
  const [query, setQuery] = React.useState(initialQuery || "");
  const [filter, setFilter] = React.useState("all");
  const [favs, setFavs] = React.useState(() => {
    try {
      const raw = JSON.parse(localStorage.getItem("whaletalk.toolFavs") || "[]");
      return new Set(Array.isArray(raw) ? raw : []);
    } catch { return new Set(); }
  });

  React.useEffect(() => {
    if (initialQuery) setQuery(String(initialQuery));
  }, [initialQuery]);

  React.useEffect(() => {
    let alive = true;
    (async () => {
      const [d, h] = await Promise.all([
        api.listAbilities().catch(() => null),
        api.capabilities().catch(() => null),
      ]);
      if (!alive) return;
      if (d && d.domains) {
        setDomains(d.domains);
        setTotal(d.total || 0);
        setErr("");
      } else {
        setErr("能力清单加载失败：后端未连接，请启动服务后刷新");
      }
      const hm = {};
      for (const it of (h?.health?.items || [])) hm[it.name] = it;
      setHealth(hm);
    })();
    return () => {
      alive = false;
    };
  }, []);

  const toggleFav = (name) => {
    setFavs((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      try { localStorage.setItem("whaletalk.toolFavs", JSON.stringify([...next])); } catch (e) { silentWarn(e, "AbilitiesPage"); }
      return next;
    });
  };

  const list = domains || [];
  const domainCount = list.filter((d) => d.name !== "其他").length;
  const allTools = React.useMemo(() => list.flatMap((d) => (d.tools || []).map((t) => ({
    ...t,
    domain: d.name,
    color: d.color,
    health: health[t.name] || null,
    fav: favs.has(t.name),
  }))), [list, health, favs]);
  const q = query.trim().toLowerCase();
  const filteredTools = allTools.filter((t) => {
    if (filter === "fav" && !t.fav) return false;
    if (filter === "failed" && !(t.health?.fail_rate > 0 || t.health?.breaker_open)) return false;
    if (!q) return true;
    return `${t.name} ${t.description || ""} ${t.domain}`.toLowerCase().includes(q);
  });
  const flatMode = !!q || filter !== "all";
  return (
    <div className="page">
      <div className="page-head">
        <h1>能力</h1>
        <p>{domainCount || 12} 个能力域 · {total} 项能力 · 点击工具名可打开测试台直调</p>
      </div>
      <div className="ab-tabs" role="tablist" aria-label="能力分区">
        <button role="tab" aria-selected={tab === "tools"} className={`ab-tab ${tab === "tools" ? "ab-tab-on" : ""}`} onClick={() => setTab("tools")}>
          <Icon name="grid" size={15} /> 工具库（{total}）
        </button>
        <button role="tab" aria-selected={tab === "packs"} className={`ab-tab ${tab === "packs" ? "ab-tab-on" : ""}`} onClick={() => setTab("packs")}>
          <Icon name="package" size={15} /> 能力包
        </button>
        <button role="tab" aria-selected={tab === "perms"} className={`ab-tab ${tab === "perms" ? "ab-tab-on" : ""}`} onClick={() => setTab("perms")}>
          <Icon name="shield" size={15} /> 权限 · 黑名单
        </button>
      </div>
      {tab === "tools" && (
        <>
          <div className="tools-bar">
            <input
              className="set-select set-combo tools-search"
              placeholder="搜索工具名 / 描述 / 能力域…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="搜索工具"
            />
            <div className="tools-filters" role="group" aria-label="工具筛选">
              {[
                { id: "all", label: `全部 ${allTools.length}` },
                { id: "fav", label: `收藏 ${favs.size}` },
                { id: "failed", label: "最近失败" },
              ].map((f) => (
                <button
                  key={f.id}
                  className={`ab-tab ${filter === f.id ? "ab-tab-on" : ""}`}
                  aria-pressed={filter === f.id}
                  onClick={() => setFilter(f.id)}
                >{f.label}</button>
              ))}
            </div>
          </div>

          {flatMode ? (
            <div className="tool-flat">
              {filteredTools.length === 0 && (
                <div className="empty-tip tools-empty">
                  <div className="tools-empty-title">
                    {q ? `没有匹配「${query.trim()}」的工具`
                      : filter === "fav" ? "还没有收藏的工具"
                        : filter === "failed" ? "最近没有失败的工具（这是好事）"
                          : "没有匹配的工具"}
                  </div>
                  <div className="tools-empty-hint">
                    {filter === "fav" ? "在工具库里点 ☆ 收藏常用工具，下次就能在这里快速找到。"
                      : filter === "failed" ? "工具调用失败会记录在这里，方便排查。"
                        : "换个关键词，或回到全部工具列表看看。"}
                  </div>
                  <button className="msg-op tools-empty-action"
                    onClick={() => { setQuery(""); setFilter("all"); }}>
                    查看全部 {allTools.length} 个工具
                  </button>
                </div>
              )}
              {filteredTools.map((t) => (
                <div className="tool-flat-row" key={t.name}>
                  <button className="tool-fav" title={t.fav ? "取消收藏" : "收藏"} onClick={() => toggleFav(t.name)}>{t.fav ? "★" : "☆"}</button>
                  <button className="tool-flat-main" onClick={() => setTestTool(t.name)} title="打开测试台">
                    <b>{t.name}</b>
                    <span>{t.domain} · {String(t.description || "").slice(0, 90)}</span>
                  </button>
                  <span className="tool-health">
                    {t.health?.calls ? `${t.health.calls} 次 · 失败 ${Math.round((t.health.fail_rate || 0) * 100)}%` : "未调用"}
                    {t.health?.breaker_open ? <em className="bad">熔断</em> : null}
                  </span>
                  {onAskTool && <button className="pm-op" onClick={() => onAskTool(t.name)}>问 AI</button>}
                  <span className={t.enabled ? "cap-on" : "cap-off"}>{t.enabled ? "开" : "关"}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="domain-grid">
              {list.map((d) => (
                <div
                  key={d.name}
                  className={`domain-card ${open === d.name ? "domain-open" : ""}`}
                  onClick={() => setOpen(open === d.name ? null : d.name)}
                >
                  <div className="domain-head">
                    <span className="domain-icon" style={{ background: d.color ? `${d.color}22` : "var(--brand-soft)" }}>{d.icon || "🧩"}</span>
                    <div className="domain-meta">
                      <b>{d.name}</b>
                      <span className="domain-count">{d.count} 项能力</span>
                    </div>
                    {(() => {
                      const tools = d.tools || [];
                      const on = tools.filter((t) => t.enabled).length;
                      return (
                        <span className={`domain-status ${on === tools.length ? "ds-stable" : "ds-muted"}`}>
                          {on === tools.length ? "全部启用" : `${on}/${tools.length} 启用`}
                        </span>
                      );
                    })()}
                  </div>
                  <div className="domain-desc">{domains ? `${(d.tools || []).length} 项 · ${(d.tools || []).slice(0, 3).map((t) => t.name).join(" / ")}${(d.tools || []).length > 3 ? " …" : ""}` : d.desc}</div>
                  {open === d.name && (
                    <div className="domain-actions">
                      {(expanded[d.name] ? (d.tools || []) : (d.tools || []).slice(0, 8)).map((t) => {
                        const h = health[t.name] || {};
                        return (
                          <div
                            className="cap-row cap-clickable"
                            key={t.name}
                            title="打开测试台"
                            onClick={(e) => {
                              e.stopPropagation();
                              setTestTool(t.name);
                            }}
                          >
                            <button className="tool-fav" onClick={(e) => { e.stopPropagation(); toggleFav(t.name); }}>{favs.has(t.name) ? "★" : "☆"}</button>
                            <span className="cap-dot" style={{ background: d.color || "var(--brand)" }} />
                            <span className="cap-name">{t.name}</span>
                            <span className="cap-desc">{String(t.description || "").slice(0, 34)}</span>
                            {h.fail_rate > 0 && <span className="tool-fail">{Math.round(h.fail_rate * 100)}%</span>}
                            <span className={t.enabled ? "cap-on" : "cap-off"}>{t.enabled ? "开" : "关"}</span>
                          </div>
                        );
                      })}
                      {(d.tools || []).length > 8 && (
                        <button
                          className="ctx-action"
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpanded((prev) => ({ ...prev, [d.name]: !prev[d.name] }));
                          }}
                        >
                          {expanded[d.name] ? "收起" : `查看全部 ${d.count} 项 →`}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
      {tab === "packs" && <CapabilitiesPage embedded />}
      {tab === "perms" && <PermissionsPage embedded />}
      {testTool && <ToolTest name={testTool} onClose={() => setTestTool(null)} />}
      {err && <div className="empty-tip is-err">{err}</div>}
    </div>
  );
}

// ── 知识库 RAG（记忆与知识页）：文档建索引 → 语义问答带引用源 ──
function KnowledgeBaseBlock() {
  const [kb, setKb] = React.useState(null);
  const [kquery, setKquery] = React.useState("");
  const [khits, setKhits] = React.useState(null);
  const [kerr, setKerr] = React.useState("");
  const [kberr, setKberr] = React.useState("");
  const [kbusy, setKbusy] = React.useState(false);
  const [kopen, setKopen] = React.useState(false);
  React.useEffect(() => { api.getKnowledge().then((d) => d && setKb(d)).catch(() => setKberr("知识库状态读取失败（后端未连接）")); }, []);
  const doSearch = async () => {
    if (!kquery.trim() || kbusy) return;
    setKerr("");
    setKbusy(true);
    try {
      const d = await api.searchKnowledge(kquery, 5).catch(() => null);
      if (!d) {
        setKerr("检索失败：后端未响应");
        setKhits(null);
        return;
      }
      const hits = Array.isArray(d.hits) ? d.hits : [];
      setKhits(hits);
      if (hits.length === 0) setKerr("知识库未命中相关内容（可先建立索引）");
    } catch (e) {
      setKerr((e && e.message) || "检索失败");
      setKhits(null);
    } finally {
      setKbusy(false);
    }
  };
  return (
    <div className={`acc-item ${kopen ? "open" : ""}`}>
      <button className="acc-head" aria-expanded={kopen} onClick={() => setKopen(!kopen)}>
        <span className="acc-arrow">▸</span>
        <Icon name="book-open" size={14} /> 知识库 RAG（带引用源）
        <span className="acc-desc">{kb && kb.indexed ? `已索引 ${(kb.files || []).length} 个文件` : "文档语义检索"}</span>
      </button>
      {kopen && (
        <div className="acc-body">
          <div className="empty-tip" style={{ marginBottom: 6 }}>
            {kberr
              ? `状态读取失败：${kberr}`
              : `已建立索引：${kb ? (kb.indexed ? ` ${(kb.files || []).length} 个文件` : "未建立") : "查询中…"}`}
            {kb && kb.files && kb.files.length > 0 && <span style={{ opacity: .6 }}>（{kb.files.slice(0, 3).join(" · ")}…）</span>}
          </div>
          <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
            <input className="set-select set-combo" placeholder="输入问题，如「我们服务器的部署步骤」" value={kquery}
              onChange={(e) => setKquery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doSearch()} />
            <button className="confirm-btn confirm-primary" onClick={doSearch} disabled={kbusy}>
              {kbusy ? "检索中…" : <><Icon name="search" size={13} /> 检索</>}
            </button>
          </div>
          {kerr && <div className="empty-tip is-err">{kerr}</div>}
          {khits && khits.length > 0 && (
            <div className="mem-list">
              {khits.map((h, i) => (
                <div className="mem-card mem-card-lg" key={i}>
                  <div className="mem-card-head">
                    <span className="mem-id">{h.path ? String(h.path).split(/[\\/]/).pop() : "命中"}</span>
                    <span className="mem-tag">相似度 {h.score}</span>
                    {h.path && <span className="mem-time" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "var(--fs-2xs)" }} title={h.path}>{h.path}</span>}
                  </div>
                  <div className="mem-text">{h.snippet || h.text}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// 设置页已独立为 SettingsPage.jsx（分类 tabs + 简单/高级模式 + 外部服务）
export { default as SettingsPage } from "./SettingsPage.jsx";

// ── 定时任务（A2）──────────────────────────────────
function SchedulesBlock() {
  const [schedules, setSchedules] = React.useState([]);
  const [loaded, setLoaded] = React.useState(false);
  const [err, setErr] = React.useState("");
  const [newS, setNewS] = React.useState({ action: "message", time: "09:00", text: "", name: "", off_peak: false, every: "", cron: "" });

  const refetch = () => api.getSchedules()
    .then((d) => { setSchedules((d && d.schedules) || []); setErr(""); })
    .catch(() => setErr("定时任务加载失败（后端未连接）"))
    .finally(() => setLoaded(true));
  React.useEffect(() => {
    refetch();
  }, []);
  // 运行中状态 / 上次结果由后端写回，15s 轮询一次即可（任务本身可能跑几分钟）
  usePoll(refetch, 15000);

  const save = async (items) => {
    setSchedules(items);
    try {
      await api.saveSchedules(items);
      refetch();  // 重新拉取，拿到后端计算的 next_run
    } catch (e) { silentWarn(e, "Pages"); setErr("保存失败：后端未连接"); }
  };

  const add = () => {
    if (!newS.text && newS.action !== "notify") return;
    const item = {
      name: newS.name || "定时任务",
      enabled: true,
      action: newS.action,
      off_peak: newS.off_peak,
      text: newS.text || (newS.action === "notify" ? "定时提醒" : ""),
    };
    if (newS.cron) item.cron = newS.cron;
    else if (newS.every) item.every = newS.every;
    else item.time = newS.time;
    save([...schedules, item]);
    setNewS({ action: "message", time: "09:00", text: "", name: "", off_peak: false, every: "", cron: "" });
  };

  const del = (idx) => save(schedules.filter((_, i) => i !== idx));
  const toggle = (idx) => save(schedules.map((s, i) => (i === idx ? { ...s, enabled: s.enabled === false } : s)));
  const runNow = async (s) => {
    if (!s.id) { refetch(); return; }
    try {
      await api.runSchedule(s.id);
      setSchedules((prev) => prev.map((x) => (x.id === s.id ? { ...x, last_status: "running", last_result: "" } : x)));
      setTimeout(refetch, 800);
    } catch (e) { silentWarn(e, "Pages"); }
  };

  return (
    <div className="wb-card">
      <div className="wb-card-title"><Icon name="list" size={14} /> ⏰ 计划与定时任务（{schedules.filter((s) => s.enabled !== false).length} 启用）</div>
      <div className="sched-list">
        {!loaded && <div className="empty-tip is-loading">正在加载定时任务…</div>}
        {loaded && err && <div className="empty-tip is-err">{err}　<button className="msg-op" onClick={refetch}>重试</button></div>}
        {schedules.map((s, i) => (
          <div className="sched-item" key={i}>
            <div className="sched-line1">
              <b>{s.name}</b>
              <span className={`sched-mode ${s.cron ? "sched-cron" : ""} ${s.enabled === false ? "sched-off" : ""}`}>
                {s.cron ? `cron ${s.cron}` : s.every ? `每 ${s.every} 分钟` : s.time}
                {s.enabled === false ? "（已停用）" : s.next_run ? ` · 下次 ${s.next_run}` : ""}
              </span>
              {s.off_peak && <span className="sched-badge">🌙 错峰</span>}
              <span className="sched-action">{s.action}</span>
              {s.last && <span className="sched-last" title="上次运行">上次 {s.last}</span>}
              <button className="msg-op" onClick={() => toggle(i)}>{s.enabled === false ? "启用" : "停用"}</button>
              <button className="msg-op" disabled={s.last_status === "running"} onClick={() => runNow(s)}>
                {s.last_status === "running" ? "运行中…" : "立即运行"}
              </button>
              <button className="msg-op" onClick={() => del(i)}>✕</button>
            </div>
            {s.text && <div className="sched-text">{s.text}</div>}
            {s.last_status && s.last_status !== "running" && (
              <div className={`sched-text ${s.last_status === "error" ? "is-err" : ""}`}>
                上次结果：{s.last_status === "ok" ? "成功" : "失败"}
                {s.last_duration ? ` · ${s.last_duration}s` : ""}
                {s.last_result ? ` · ${s.last_result}` : ""}
              </div>
            )}
          </div>
        ))}
        {loaded && !err && schedules.length === 0 && <EmptyState icon="⏰" title="还没有定时任务" hint="AI 用 schedule_task 工具也能创建定时任务。" compact />}
      </div>
      <div className="sched-form">
        <input className="set-select set-combo" placeholder="名称（可选）" value={newS.name} onChange={(e) => setNewS({ ...newS, name: e.target.value })} />
        <select className="set-select" value={newS.action} onChange={(e) => setNewS({ ...newS, action: e.target.value })}>
          <option value="message">消息任务</option>
          <option value="notify">推送提醒</option>
          <option value="backup">备份</option>
          <option value="workflow">流程</option>
        </select>
        <select className="set-select" value={newS.every ? "every" : newS.cron ? "cron" : "time"} onChange={(e) => setNewS({ ...newS, every: "", cron: "", time: e.target.value === "time" ? "09:00" : newS.time })}>
          <option value="time">每日时间</option>
          <option value="every">间隔分钟</option>
          <option value="cron">Cron 表达式</option>
        </select>
        {!newS.every && !newS.cron && <input className="set-select set-combo" value={newS.time} onChange={(e) => setNewS({ ...newS, time: e.target.value })} />}
        {newS.every !== "" && <input className="set-select set-combo" type="number" placeholder="分钟" value={newS.every} onChange={(e) => setNewS({ ...newS, every: e.target.value })} />}
        {newS.cron && <input className="set-select set-combo" placeholder="分 时 日 月 周，如 30 9 * * 1" value={newS.cron} onChange={(e) => setNewS({ ...newS, cron: e.target.value })} />}
        <input className="set-select set-combo" placeholder="内容" value={newS.text} onChange={(e) => setNewS({ ...newS, text: e.target.value })} />
        <button className="msg-op" onClick={() => setNewS({ ...newS, off_peak: !newS.off_peak })}>{newS.off_peak ? "🌙 错峰 ✓" : "🌙 高峰错峰"}</button>
        <button className="confirm-btn confirm-primary" onClick={add}>＋ 添加</button>
      </div>
    </div>
  );
}

// 产物文件类型图标
function fileIcon(p) {
  const ext = String(p).split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "🖼";
  if (["xlsx", "xls", "csv"].includes(ext)) return "📊";
  if (["docx", "doc"].includes(ext)) return "📄";
  if (["pdf"].includes(ext)) return "📕";
  if (["pptx", "ppt"].includes(ext)) return "📽";
  if (["py", "js", "ts", "html", "css", "json"].includes(ext)) return "🧩";
  if (["zip", "rar", "7z", "gz"].includes(ext)) return "🗜";
  if (["mp3", "wav", "mp4", "mov"].includes(ext)) return "🎵";
  if (["md", "txt", "log"].includes(ext)) return "📝";
  return "📦";
}

function greet() {
  const h = new Date().getHours();
  return h < 6 ? "夜深了" : h < 9 ? "早上好" : h < 12 ? "上午好" : h < 14 ? "中午好" : h < 18 ? "下午好" : h < 23 ? "晚上好" : "夜深了";
}

export function WorkbenchPage({ onApply, onPickSession, tab: tabProp, onTab }) {
  const [status, setStatus] = React.useState(null);
  const [sessions, setSessions] = React.useState([]);
  const [files, setFiles] = React.useState(null);
  const [procs, setProcs] = React.useState({});
  const [checkpoint, setCheckpoint] = React.useState(null);
  const [deps, setDeps] = React.useState([]);
  const [backups, setBackups] = React.useState([]);
  const [prompts, setPrompts] = React.useState([]);
  const [templates, setTemplates] = React.useState({});
  const [updatedAt, setUpdatedAt] = React.useState("");
  const [tabState, setTabState] = React.useState("overview");
  const tab = tabProp ?? tabState;
  const setTab = React.useCallback((next) => {
    setTabState(next);
    onTab?.(next);
  }, [onTab]);

  // 全量刷新：态势走 /v1/situation 单一事实源（人+AI 同源），快捷行动资产单独拉，30s 轮询
  const refresh = React.useCallback(async () => {
    const [sit, pm, tk] = await Promise.all([
      api.getSituation().catch(() => null), api.getPrompts().catch(() => null), api.getTasks().catch(() => null),
    ]);
    if (sit) {
      setStatus({
        monthly_cost: (sit.usage && sit.usage.month_cost) || 0,
        usage_total: {
          cache_hit: (sit.usage && sit.usage.cache_hit) || 0,
          prompt: (sit.usage && sit.usage.prompt_tokens) || 0,
          completion: (sit.usage && sit.usage.completion_tokens) || 0,
        },
        mode: (sit.system && sit.system.mode) || "dialog",
        model: (sit.system && sit.system.model) || "",
      });
      setSessions(((sit.recent && sit.recent.sessions) || []).slice(0, 6));
      setFiles({ recent: (sit.recent && sit.recent.files) || [] });
      setProcs(sit.processes || {});
      setCheckpoint(sit.checkpoint && (sit.checkpoint.name || sit.checkpoint.status || sit.checkpoint.pending || sit.checkpoint.notes) ? sit.checkpoint : null);
      setDeps(((sit.health && sit.health.missing_deps) || []).map((n) => ({ name: n, ok: false })));
      setBackups(sit.health && sit.health.last_backup ? [{ mtime: sit.health.last_backup }] : []);
    }
    if (pm) setPrompts(pm);
    if (tk && tk.templates) setTemplates(tk.templates);
    setUpdatedAt(new Date().toTimeString().slice(0, 5));
  }, []);

  // 工作台 30s 轮询：不可见时暂停（4 个接口一起拉，后台空转代价更高）
  usePoll(refresh, 30000);

  const u = (status && status.usage_total) || {};
  const running = Object.values(procs || {}).filter((p) => !p.exited);
  const missingDeps = (deps || []).filter((d) => !d.ok);
  // /v1/situation 的 recent.files 是带元数据的对象；兼容旧字符串形式。
  const recentFiles = ((files && files.recent) || []).map((f) => {
    if (f && typeof f === "object") {
      const p = f.path || "";
      return { ...f, path: p, name: f.name || String(p).split(/[\\/]/).pop() || "未命名文件" };
    }
    const p = String(f || "");
    return { path: p, name: p.split(/[\\/]/).pop() || "未命名文件" };
  }).filter((f) => f.path);
  // 快捷行动：高频（use_count）优先，不足用零次使用的补齐
  const used = (prompts || []).filter((p) => p.enabled !== false && p.use_count > 0)
    .sort((a, b) => (b.use_count || 0) - (a.use_count || 0));
  const unused = (prompts || []).filter((p) => p.enabled !== false && !p.use_count);
  const quickActs = used.slice(0, 6).concat(unused.slice(0, Math.max(0, 6 - used.length)));
  const tplEntries = Object.entries(templates || {}).slice(0, 4);
  const today = new Date();

  const stopProc = async (name) => {
    try {
      await api.stopProcess(name).catch(() => null);
      refresh();
    } catch (e) { silentWarn(e, "Pages"); }
  };
  const openFile = async (path, dir = false) => {
    try {
      await (dir ? api.openDir(path) : api.openFile(path)).catch(() => null);
    } catch (e) { silentWarn(e, "Pages"); }
  };
  const resumeCheckpoint = () => {
    if (!checkpoint) return;
    const lines = [
      `任务：${checkpoint.name || "未命名任务"}`,
      checkpoint.status ? `状态：${checkpoint.status}` : "",
      checkpoint.pending && checkpoint.pending.length ? `剩余待办：${checkpoint.pending.join("；")}` : "",
      checkpoint.notes ? `备注：${checkpoint.notes}` : "",
    ].filter(Boolean);
    onApply && onApply(`请继续完成以下任务（从检查点恢复）：\n${lines.join("\n")}`);
  };

  return (
    <div className="page">
      {/* 顶栏：标题 + 问候 + 刷新 */}
      <div className="page-head wb-head">
        <div>
          <h1>工作台</h1>
          <p>
            {greet()} · {today.getMonth() + 1}月{today.getDate()}日{" "}
            {["日", "一", "二", "三", "四", "五", "六"][today.getDay()]}{" "}
            <span className="wb-head-tip">一切尽在掌握</span>
          </p>
        </div>
        {tab === "overview" && (
          <button className="confirm-btn wb-refresh" onClick={refresh} title="手动刷新">
            ⟳ 刷新{updatedAt ? ` · ${updatedAt}` : ""}
          </button>
        )}
      </div>

      <div className="ab-tabs" role="tablist" aria-label="工作台分区">
        <button role="tab" aria-selected={tab === "overview"} className={`ab-tab ${tab === "overview" ? "ab-tab-on" : ""}`} onClick={() => setTab("overview")}>
          <Icon name="layout" size={15} /> 概览
        </button>
        <button role="tab" aria-selected={tab === "tasks"} className={`ab-tab ${tab === "tasks" ? "ab-tab-on" : ""}`} onClick={() => setTab("tasks")}>
          <Icon name="activity" size={15} /> 任务记录
        </button>
      </div>

      {tab === "tasks" ? (
        <TasksPage embedded onOpenSession={onPickSession} onRetry={(job) => onApply?.(job.user_text)} />
      ) : (
        <>
      {/* ① 态势横幅：现在发生了什么 */}
      <div className="wb-hero">
        <div className="wb-hero-item" title="运行中的后台进程">
          <span className="wb-hero-ic">🖥</span>
          <div className="wb-hero-txt">
            <b className={running.length ? "ok-text" : ""}>{running.length} 运行中</b>
            <span>后台进程</span>
          </div>
        </div>
        <div className="wb-hero-item" title="本月累计成本">
          <span className="wb-hero-ic">💰</span>
          <div className="wb-hero-txt">
            <b>¥{status ? status.monthly_cost || 0 : "…"}</b>
            <span>本月成本</span>
          </div>
        </div>
        <div className="wb-hero-item" title="累计缓存命中 token">
          <span className="wb-hero-ic">⚡</span>
          <div className="wb-hero-txt">
            <b className="ok-text">{(u.cache_hit || 0).toLocaleString()}</b>
            <span>缓存命中</span>
          </div>
        </div>
        <div className="wb-hero-item" title="可选依赖状态">
          <span className="wb-hero-ic">🧩</span>
          <div className="wb-hero-txt">
            <b className={missingDeps.length ? "warn-text" : "ok-text"}>
              {missingDeps.length ? `${missingDeps.length} 缺失` : "完整"}
            </b>
            <span>依赖</span>
          </div>
        </div>
        <div className="wb-hero-item" title="最近备份">
          <span className="wb-hero-ic">💾</span>
          <div className="wb-hero-txt">
            <b>{backups.length ? backups[0].mtime : "未备份"}</b>
            <span>备份</span>
          </div>
        </div>
        <div className="wb-hero-item" title="当前工作模式">
          <span className="wb-hero-ic">🚀</span>
          <div className="wb-hero-txt">
            <b>{status && status.mode === "task" ? "任务" : "对话"}</b>
            <span>模式</span>
          </div>
        </div>
      </div>

      {/* 主网格：左 快捷行动 + 右 进行中 */}
      <div className="wb-main-grid">
        <div className="wb-card wb-act">
          <div className="wb-card-title"><Icon name="list" size={14} /> 快捷行动</div>
          <div className="wb-act-grid">
            {quickActs.map((p) => (
              <button
                className="wb-act-card"
                key={p.id}
                title={p.desc || p.text}
                onClick={() => onApply && onApply(p.text)}
              >
                <span className="wb-act-ic">{p.icon || "⚡"}</span>
                <span className="wb-act-name">{p.name}</span>
                <span className="wb-act-desc">{p.desc || String(p.text || "").slice(0, 18)}</span>
                {p.use_count > 0 && <span className="wb-act-use">×{p.use_count}</span>}
              </button>
            ))}
            {tplEntries.map(([n, txt]) => (
              <button
                className="wb-act-card wb-act-tpl"
                key={n}
                title={String(txt).slice(0, 60)}
                onClick={() => onApply && onApply(txt)}
              >
                <span className="wb-act-ic">🧩</span>
                <span className="wb-act-name">{n}</span>
                <span className="wb-act-desc">任务模板</span>
              </button>
            ))}
          </div>
          {quickActs.length === 0 && tplEntries.length === 0 && (
            <EmptyState icon="zap" title="还没有快捷行动" hint="在「指令库」添加指令，或使用后高频指令会自动出现在这里。" compact />
          )}
        </div>

        <div className="wb-side">
          <div className="wb-card">
            <div className="wb-card-title"><Icon name="list" size={14} /> ⏳ 进行中</div>
            {checkpoint ? (
              <div className="wb-checkpoint">
                <div className="wb-cp-head">
                  <b>{checkpoint.name || "未命名任务"}</b>
                  {checkpoint.status && <span className="pm-cat"> · {checkpoint.status}</span>}
                </div>
                {checkpoint.pending && checkpoint.pending.length > 0 && (
                  <div className="pm-text">待办：{checkpoint.pending.join("；")}</div>
                )}
                {checkpoint.notes && <div className="pm-text">{checkpoint.notes}</div>}
                <button className="confirm-btn confirm-primary" onClick={resumeCheckpoint}>▶ 恢复任务</button>
              </div>
            ) : (
              <div className="empty-tip">无进行中的任务检查点</div>
            )}
            {Object.keys(procs || {}).length > 0 && (
              <div className="wb-proc-list">
                {Object.entries(procs).map(([name, p]) => (
                  <div className="wb-proc-item" key={name}>
                    <span className={`wb-dot ${p.exited ? "wb-dot-off" : ""}`} />
                    <b>{name}</b>
                    {!p.exited && <button className="pm-op" onClick={() => stopProc(name)}>停止</button>}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="wb-card">
            <div className="wb-card-title"><Icon name="list" size={14} /> ℹ 快速了解</div>
            <div className="wb-facts">
              <div className="wb-fact"><span>模型</span><b>{status ? status.model || "…" : "…"}</b></div>
              <div className="wb-fact"><span>累计输入</span><b>{(u.prompt || 0).toLocaleString()}</b></div>
              <div className="wb-fact"><span>累计输出</span><b>{(u.completion || 0).toLocaleString()}</b></div>
            </div>
          </div>
        </div>
      </div>

      {/* 底部双栏：最近会话 + 最近产物 */}
      <div className="wb-bottom-grid">
        <div className="wb-card">
          <div className="wb-card-title"><Icon name="list" size={14} /> 最近会话（{sessions.length}）</div>
          <div className="wb-sess-list">
            {sessions.map((s) => (
              <div className="wb-sess-item" key={s.id} onClick={() => onPickSession && onPickSession(s.id)}>
                <span className="wb-sess-ic">💬</span>
                <b>{s.name || "未命名会话"}</b>
                <span className="wb-sess-meta">
                  {s.msg_count} 条
                  {s.saved_at ? ` · ${String(s.saved_at).slice(5, 16).replace("T", " ")}` : ""}
                </span>
              </div>
            ))}
            {sessions.length === 0 && <div className="empty-tip">暂无历史会话</div>}
          </div>
        </div>

        <div className="wb-card">
          <div className="wb-card-title"><Icon name="package" size={14} /> 最近产物（{recentFiles.length}）</div>
          <div className="wb-file-list">
            {recentFiles.slice(0, 6).map((f) => (
              <div className="wb-file-item" key={f.path}>
                <span className="wb-file-ic">{fileIcon(f.name || f.path)}</span>
                <span className="wb-file-name" title={f.path}>{f.name}</span>
                <div className="wb-file-ops">
                  <button className="pm-op" onClick={() => openFile(f.path)}>打开</button>
                  <button className="pm-op" onClick={() => openFile(f.path, true)}>定位</button>
                </div>
              </div>
            ))}
            {recentFiles.length === 0 && <EmptyState icon="package" title="还没有产物" hint="AI 生成的文件会出现在这里。" compact />}
          </div>
        </div>
      </div>

      {/* 计划区 */}
      <div style={{ marginTop: 12 }}>
        <SchedulesBlock />
      </div>
        </>
      )}
    </div>
  );
}