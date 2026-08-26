import React from "react";
import { ThemeContext, DisplayContext } from "../App.jsx";
import * as api from "../api.js";
import ToolTest from "./ToolTest.jsx";

const apiGet = async (path) => {
  try {
    return await api.api(path);
  } catch {
    return null;
  }
};

const apiPost = async (path, body) => {
  try {
    return await api.api(path, { method: "POST", body: JSON.stringify(body) });
  } catch {
    return null;
  }
};

const DOMAINS = []; // 能力域以后端 /v1/abilities 为准（未加载时无数据，明确提示错误）

export function AbilitiesPage() {
  const [open, setOpen] = React.useState(null);
  const [tab, setTab] = React.useState("tools");
  const [domains, setDomains] = React.useState(null);
  const [total, setTotal] = React.useState(0);
  const [testTool, setTestTool] = React.useState(null);
  const [err, setErr] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    (async () => {
      const d = await apiGet("/v1/abilities");
      if (!alive) return;
      if (d && d.domains) {
        setDomains(d.domains);
        setTotal(d.total || 0);
        setErr("");
      } else {
        setErr("能力清单加载失败：后端未连接，请启动服务后刷新");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const list = domains || [];
  return (
    <div className="page">
      <div className="page-head">
        <h1>能力中心</h1>
        <p>12 大能力域 · {total} 项能力 · 点击工具名可打开测试台直调</p>
      </div>
      <div className="ab-tabs">
        <button className={`ab-tab ${tab === "tools" ? "ab-tab-on" : ""}`} onClick={() => setTab("tools")}>
          🧰 工具库（{total}）
        </button>
        <button className={`ab-tab ${tab === "perms" ? "ab-tab-on" : ""}`} onClick={() => setTab("perms")}>
          🔓 权限 · 黑名单
        </button>
      </div>
      {tab === "tools" && (
        <div className="domain-grid">
          {list.map((d) => (
            <div
              key={d.name}
              className={`domain-card ${open === d.name ? "domain-open" : ""}`}
              onClick={() => setOpen(open === d.name ? null : d.name)}
            >
              <div className="domain-head">
                <span className="domain-icon" style={{ background: `${d.color || "#38bdf8"}22` }}>{d.icon || "🧩"}</span>
                <div className="domain-meta">
                  <b>{d.name}</b>
                  <span className="domain-count">{d.count} 项能力</span>
                </div>
                <span className="domain-status ds-stable">稳定</span>
              </div>
              <div className="domain-desc">{domains ? `${d.tools.length} 项 · ${d.tools.slice(0, 3).map((t) => t.name).join(" / ")}${d.tools.length > 3 ? " …" : ""}` : d.desc}</div>
              {open === d.name && (
                <div className="domain-actions">
                  {(d.tools || []).slice(0, 8).map((t) => (
                    <div
                      className="cap-row cap-clickable"
                      key={t.name}
                      title="打开测试台"
                      onClick={(e) => {
                        e.stopPropagation();
                        setTestTool(t.name);
                      }}
                    >
                      <span className="cap-dot" style={{ background: d.color || "#38bdf8" }} />
                      <span className="cap-name">{t.name}</span>
                      <span className="cap-desc">{String(t.description || "").slice(0, 34)}</span>
                      <span className={t.enabled ? "cap-on" : "cap-off"}>{t.enabled ? "开" : "关"}</span>
                    </div>
                  ))}
                  <button className="ctx-action">查看全部 {d.count} 项 →</button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {tab === "perms" && <PermissionsPage />}
      {testTool && <ToolTest name={testTool} onClose={() => setTestTool(null)} />}
      {err && <div className="empty-tip">{err}</div>}
    </div>
  );
}

export { default as PluginsPage } from "./PluginsPage.jsx";

export function MemoryPage() {
  const [q, setQ] = React.useState("");
  const [focused, setFocused] = React.useState(false);
  const [memories, setMemories] = React.useState(null);
  const [err, setErr] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    (async () => {
      const d = await apiGet("/v1/memory");
      if (!alive) return;
      if (d && d.facts) {
        setMemories(
          d.facts.map((f, i) => ({
            id: `MEM#${String(d.facts.length - i).padStart(4, "0")}`,
            text: f.text,
            tag: f.type || (f.tags ? f.tags.split(",")[0] : "记忆"),
            time: f.ts ? f.ts.slice(0, 16).replace("T", " ") : "",
          }))
        );
        setErr("");
      } else {
        setErr("记忆加载失败：后端未连接，请启动服务后刷新");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const items = (memories || []).filter((m) => m.text.includes(q) || m.tag.includes(q));
  return (
    <div className="page">
      <div className="page-head">
        <h1>记忆与知识</h1>
        <p>分层记忆 · 混合检索 · 每条记忆可溯源到原始会话{memories ? ` · 共 ${memories.length} 条` : ""}</p>
      </div>
      <div className={`mem-search ${focused ? "mem-search-focus" : ""}`}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4-4" />
        </svg>
        <input
          placeholder="检索长期记忆（混合检索：关键词 + 语义向量）"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
        <span className="mem-search-count">{items.length} 条</span>
      </div>
      <div className="mem-list">
        {items.map((m) => (
          <div className="mem-card mem-card-lg" key={m.id}>
            <div className="mem-card-head">
              <span className="mem-id">{m.id}</span>
              <span className="mem-tag">{m.tag}</span>
              {typeof m.score === "number" && <span className="mem-score">相似度 {m.score.toFixed(2)}</span>}
              {m.time && <span className="mem-time">{m.time}</span>}
            </div>
            <div className="mem-text">{m.text}</div>
          </div>
        ))}
        {items.length === 0 && (
          <div className="empty-tip">{err || "暂无记忆（对话中记录的事实会出现在这里）"}</div>
        )}
      </div>
    </div>
  );
}

// 可编辑下拉（下拉选择 + 自由输入，模型网关一切可自定）
function Combobox({ options, value, onChange, placeholder }) {
  return (
    <>
      <input
        className="set-select set-combo"
        list="wt-model-list"
        value={value || ""}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id="wt-model-list">
        {options.map((o) => <option key={o} value={o} />)}
      </datalist>
    </>
  );
}

// 设置页已独立为 SettingsPage.jsx（分类 tabs + 简单/高级模式 + 外部服务）
export { default as SettingsPage } from "./SettingsPage.jsx";

// ── 权限（黑名单管理 · 核心哲学：默认全放行，只按禁止拦截）──
function BlacklistGroup({ title, desc, items, color, onAdd, onRemove, placeholder }) {
  const [input, setInput] = React.useState("");
  return (
    <div className="perm-group">
      <div className="perm-group-head">
        <b>{title}</b>
        <span>{desc}</span>
      </div>
      <div className="perm-chips">
        {items.length === 0 ? (
          <span className="perm-empty">空 —— 默认放行（不拦截任何{title === "审批动作" ? "工具" : "目标"}）</span>
        ) : (
          items.map((it) => (
            <span className="perm-chip" key={it} style={{ borderColor: `${color}44`, color }}>
              {it}
              <button className="perm-chip-x" onClick={() => onRemove(it)}>×</button>
            </span>
          ))
        )}
      </div>
      <div className="perm-add">
        <input
          placeholder={placeholder}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && input.trim()) {
              onAdd(input.trim());
              setInput("");
            }
          }}
        />
        <button
          className="confirm-btn"
          disabled={!input.trim()}
          onClick={() => {
            onAdd(input.trim());
            setInput("");
          }}
        >
          添加
        </button>
      </div>
    </div>
  );
}

export function PermissionsPage() {
  const [perms, setPerms] = React.useState(null);
  const [savedTip, setSavedTip] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    (async () => {
      const d = await apiGet("/v1/permissions");
      if (alive && d) setPerms(d);
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (!perms) {
    return (
      <div className="page">
        <div className="page-head"><h1>权限</h1><p>加载中…</p></div>
      </div>
    );
  }

  const update = async (key, items) => {
    setBusy(true);
    try {
      const d = await apiPost("/v1/permissions", { [key]: items });
      if (d && d.ok) {
        setPerms({ ...perms, [key]: items });
        setSavedTip("已保存到 permissions.json");
        setTimeout(() => setSavedTip(""), 1800);
      }
    } catch {}
    setBusy(false);
  };

  const groups = [
    {
      key: "blocked_dirs",
      title: "禁目录（filesystem.blocked_dirs）",
      desc: "命中路径的文件操作直接拒绝",
      color: "var(--danger)",
      placeholder: "输入禁用的目录路径，Enter 添加",
    },
    {
      key: "shell_blocklist",
      title: "禁命令（shell.blocklist）",
      desc: "命中命令名的 run_command 拒绝",
      color: "var(--warn)",
      placeholder: "输入禁用的命令名，Enter 添加",
    },
    {
      key: "network_blocklist",
      title: "禁网络（network.blocklist）",
      desc: "命中的主机/网段请求拒绝",
      color: "var(--ai)",
      placeholder: "输入禁用的主机或网段，Enter 添加",
    },
    {
      key: "approval_actions",
      title: "审批动作（approval_actions）",
      desc: "仅这些工具执行前需要你确认（其余默认放行）",
      color: "var(--brand)",
      placeholder: "输入工具名，如 send_email，Enter 添加",
    },
  ];

  return (
    <div className="page">
      <div className="page-head">
        <h1>🔓 权限</h1>
        <p>自由优先：默认放行 + 黑名单。AI 拥有全部行动能力，只按你禁止的拦截。</p>
      </div>
      <div className={`perm-mode-banner ${perms.full_auto ? "perm-mode-task" : ""}`}>
        {perms.full_auto
          ? "🚀 任务模式已开启：零审批、零开关，AI 拥有全部工具（黑名单仍生效）"
          : "💬 对话模式：不调用任何工具，黑名单不生效"}
      </div>
      <div className="perm-groups">
        {groups.map((g) => (
          <BlacklistGroup
            key={g.key}
            title={g.title}
            desc={g.desc}
            color={g.color}
            items={perms[g.key] || []}
            placeholder={g.placeholder}
            onAdd={(v) => update(g.key, [...(perms[g.key] || []), v])}
            onRemove={(v) => update(g.key, (perms[g.key] || []).filter((x) => x !== v))}
          />
        ))}
      </div>
      <div className="perm-note">配置文件：permissions.json · 修改立即生效</div>
      {savedTip && <div className="set-saved-tip">{savedTip}</div>}
    </div>
  );
}

// ── 任务与模板 ────────────────────────────────────
export function TasksPage() {
  const [tasks, setTasks] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    (async () => {
      const d = await apiGet("/v1/tasks");
      if (alive && d) setTasks(d);
    })();
    return () => {
      alive = false;
    };
  }, []);
  const templates = tasks?.templates || [];
  const playground = tasks?.playground || [];
  return (
    <div className="page">
      <div className="page-head">
        <h1>任务与模板</h1>
        <p>Agent 任务模板 · 试玩任务 · 全部在任务模式下执行</p>
      </div>
      {templates.length > 0 && (
        <>
          <div className="wb-card-title">任务模板（{templates.length}）</div>
          <div className="task-grid">
            {templates.map((t, i) => (
              <div className="wb-card task-card" key={i}>
                <b>{t.name || `模板 ${i + 1}`}</b>
                <div className="task-sub">{t.desc || ""}</div>
              </div>
            ))}
          </div>
        </>
      )}
      {playground.length > 0 && (
        <>
          <div className="wb-card-title" style={{ marginTop: 16 }}>试玩任务</div>
          <div className="task-grid">
            {playground.map((p, i) => (
              <div className="wb-card task-card" key={i}>
                <b>{typeof p === "string" ? p : p.name || p.title || `试玩 ${i + 1}`}</b>
                <div className="task-sub">点击后在输入框发起（试玩由本界面提供）</div>
              </div>
            ))}
          </div>
        </>
      )}
      {!tasks && <div className="empty-tip">任务模板加载失败：后端未连接，请启动服务后刷新</div>}
    </div>
  );
}

// ── 文件与产物 ────────────────────────────────────
export function FilesPage() {
  const [files, setFiles] = React.useState(null);
  const [err, setErr] = React.useState("");
  React.useEffect(() => {
    let alive = true;
    (async () => {
      const d = await apiGet("/v1/files");
      if (!alive) return;
      if (d) {
        setFiles(d);
        setErr("");
      } else {
        setErr("文件列表加载失败：后端未连接，请启动服务后刷新");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);
  return (
    <div className="page">
      <div className="page-head">
        <h1>文件与产物</h1>
        <p>{files ? `工作目录：${files.active_dir}` : "最近产物 · 工作区文件"}</p>
      </div>
      <div className="wb-card-title">最近产物（{files?.recent?.length || 0}）</div>
      <div className="files-list">
        {err && <div className="empty-tip">{err}</div>}
        {(files ? files.recent || [] : []).map((r, i) => (
          <div className="files-row" key={i}>📦 {r}</div>
        ))}
        {!err && files && (files.recent || []).length === 0 && (
          <div className="empty-tip">暂无产物——工具生成文件后自动出现在这里</div>
        )}
      </div>
      <div className="wb-card-title" style={{ marginTop: 16 }}>工作区（{files?.entries?.length || 0} 项）</div>
      <div className="files-list">
        {(files?.entries || []).map((e, i) => (
          <div className="files-row" key={i}>
            <span>{e.is_dir ? "📁" : "📄"} {e.name}</span>
            <span className="files-meta">{e.is_dir ? "目录" : `${e.size} B`} · {e.mtime}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── 自我进化 ──────────────────────────────────────
export function EvolutionPage() {
  const [evos, setEvos] = React.useState(null);
  const [detail, setDetail] = React.useState(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    const d = await apiGet("/v1/evolutions");
    if (d) setEvos(d.evolutions);
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const apply = async (name) => {
    if (!window.confirm(`采纳提案「${name}」？原文件将备份为 .evobak`)) return;
    setBusy(true);
    const r = await apiPost("/v1/evolutions/apply", { name });
    setBusy(false);
    if (r && r.ok) {
      alert(`已采纳：${r.applied.length} 个文件（原文件备份 .evobak，重启后生效）`);
      setDetail(null);
      load();
    } else if (r && r.error) {
      alert(`失败：${r.error}`);
    }
  };

  const ignore = async (name) => {
    if (!window.confirm(`忽略并删除提案「${name}」？`)) return;
    setBusy(true);
    const r = await apiPost("/v1/evolutions/ignore", { name });
    setBusy(false);
    if (r && r.ok) {
      setDetail(null);
      load();
    } else if (r && r.error) {
      alert(`失败：${r.error}`);
    }
  };

  const showDetail = async (name) => {
    const d = await apiGet(`/v1/evolutions/${encodeURIComponent(name)}`);
    if (d && d.files) setDetail(d);
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>自我进化</h1>
        <p>鲸语阅读自己的代码 → 提交改进提案 → 由你决定是否采纳</p>
      </div>
      {detail && (
        <div className="evo-detail">
          <div className="evo-detail-head">
            <b>📄 {detail.name}</b>
            <button className="confirm-btn" onClick={() => setDetail(null)}>← 返回列表</button>
          </div>
          {detail.files.map((f) => (
            <div className="evo-file" key={f.name}>
              <div className="evo-file-head">
                <b>{f.name}</b>
                <span>{f.original_exists ? "（将覆盖原文件）" : "（新文件）"}</span>
              </div>
              <pre>{f.content.slice(0, 3000)}</pre>
            </div>
          ))}
        </div>
      )}
      {!detail && (
        <div className="evo-list">
          {(evos || []).map((e) => (
            <div className={`evo-card ${e.applied ? "evo-applied" : ""}`} key={e.name}>
              <div className="evo-line1">
                <b>{e.name}</b>
                {e.applied && <span className="evo-badge">已采纳</span>}
              </div>
              <div className="evo-line2">{e.files.length} 个文件 · {e.mtime}</div>
              {!e.applied && (
                <div className="evo-actions">
                  <button className="msg-op" onClick={() => showDetail(e.name)}>🔍 差异预览</button>
                  <button className="msg-op" disabled={busy} onClick={() => apply(e.name)}>✅ 采纳</button>
                  <button className="msg-op" style={{ color: "var(--danger)" }} disabled={busy} onClick={() => ignore(e.name)}>🗑 忽略</button>
                </div>
              )}
            </div>
          ))}
          {(!evos || evos.length === 0) && <div className="empty-tip">暂无进化提案（AI 自我审查后自动生成）</div>}
        </div>
      )}
    </div>
  );
}

// ── 系统 ──────────────────────────────────────────
export function SystemPage() {
  const [status, setStatus] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    (async () => {
      const s = await apiGet("/v1/status");
      if (alive && s) setStatus(s);
    })();
    return () => {
      alive = false;
    };
  }, []);
  const u = status?.usage_total || {};
  return (
    <div className="page">
      <div className="page-head">
        <h1>系统</h1>
        <p>用量 · 安全 · 依赖 · 工作目录</p>
      </div>
      <div className="wb-card">
        <div className="wb-card-title">账户与用量</div>
        <div className="sys-row"><span>累计输入</span><b>{(u.prompt || 0).toLocaleString()} tokens</b></div>
        <div className="sys-row"><span>累计输出</span><b>{(u.completion || 0).toLocaleString()} tokens</b></div>
        <div className="sys-row"><span>缓存命中</span><b className="ok-text">{(u.cache_hit || 0).toLocaleString()}</b></div>
        <div className="sys-row"><span>本月成本</span><b>¥{status?.monthly_cost || 0}</b></div>
        <div className="sys-row"><span>本月预算</span><b>¥{(status?.monthly_budget || 0).toFixed(2)}</b></div>
        {status?.peak_hour && <div className="sys-row"><span>高峰时段</span><b className="warn-text">⏰ 高峰</b></div>}
      </div>
      <div className="wb-card" style={{ marginTop: 12 }}>
        <div className="wb-card-title">工作模式与安全</div>
        <div className="sys-row"><span>工作模式</span><b>{status?.mode === "task" ? "🚀 任务模式：全部工具自动可用" : "💬 对话模式：不调用任何工具"}</b></div>
        <div className="sys-row"><span>隐私模式</span><b>{status?.privacy ? "🔒 开启" : "关闭"}</b></div>
        <div className="sys-row"><span>工作目录</span><b className="sys-path">{status?.active_dir || ""}</b></div>
      </div>
    </div>
  );
}

// ── 定时任务（A2）──────────────────────────────────
function SchedulesBlock() {
  const [schedules, setSchedules] = React.useState([]);
  const [newS, setNewS] = React.useState({ action: "message", time: "09:00", text: "", name: "", off_peak: false, every: "", cron: "" });

  React.useEffect(() => {
    api.getSchedules().then((d) => d && setSchedules(d.schedules || [])).catch(() => {});
  }, []);

  const save = async (items) => {
    setSchedules(items);
    try {
      await api.saveSchedules(items);
    } catch {}
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

  return (
    <div className="wb-card">
      <div className="wb-card-title">⏰ 定时任务（{schedules.length}）</div>
      <div className="sched-list">
        {schedules.map((s, i) => (
          <div className="sched-item" key={i}>
            <div className="sched-line1">
              <b>{s.name}</b>
              <span className={`sched-mode ${s.cron ? "sched-cron" : ""}`}>
                {s.cron ? `cron ${s.cron}` : s.every ? `每 ${s.every} 分钟` : s.time}
              </span>
              {s.off_peak && <span className="sched-badge">🌙 错峰</span>}
              <span className="sched-action">{s.action}</span>
              <button className="msg-op" onClick={() => del(i)}>✕</button>
            </div>
            {s.text && <div className="sched-text">{s.text}</div>}
          </div>
        ))}
        {schedules.length === 0 && <div className="empty-tip">暂无定时任务——AI 用 schedule_task 工具也能创建</div>}
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

export function WorkbenchPage({ onGoChat }) {
  const [status, setStatus] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    (async () => {
      const s = await apiGet("/v1/status");
      if (alive && s) setStatus(s);
    })();
    return () => {
      alive = false;
    };
  }, []);
  const u = status?.usage_total || {};
  return (
    <div className="page">
      <div className="page-head">
        <h1>工作台</h1>
        <p>快捷指令 · 最近会话 · 系统概览</p>
      </div>
      <div className="wb-grid">
        <div className="wb-card wb-quick">
          <div className="wb-card-title">快捷指令</div>
          {["📝 公众号写作", "✈️ 智能飞侠巡航", "📊 图表解读", "🔍 深度调研报告", "⏰ 定时巡检", "🧪 运行测试"].map((c) => (
            <button className="wb-chip" key={c} onClick={() => onGoChat && onGoChat()}>{c}</button>
          ))}
        </div>
        <div className="wb-card wb-sys">
          <div className="wb-card-title">系统概览</div>
          <div className="sys-row"><span>工作模式</span><b>{status?.mode === "task" ? "🚀 任务模式" : "💬 对话模式"}</b></div>
          <div className="sys-row"><span>模型</span><b>{status?.model || "…"}</b></div>
          <div className="sys-row"><span>角色</span><b>{status?.role || "…"}</b></div>
          <div className="sys-row"><span>累计输入</span><b>{(u.prompt || 0).toLocaleString()} tokens</b></div>
          <div className="sys-row"><span>累计输出</span><b>{(u.completion || 0).toLocaleString()} tokens</b></div>
          <div className="sys-row"><span>缓存命中</span><b className="ok-text">{(u.cache_hit || 0).toLocaleString()}</b></div>
          <div className="sys-row"><span>本月成本</span><b>¥{status?.monthly_cost || 0}{status?.monthly_budget ? ` / ¥${status.monthly_budget}` : ""}</b></div>
          <div className="sys-row"><span>工具数</span><b>115（12 域）</b></div>
          <div className="sys-row"><span>网络</span><b className="ok-text">● 正常</b></div>
        </div>
      </div>
      <div className="wb-card wb-recent">
        <div className="wb-card-title">最近会话</div>
        <div className="wb-recent-row" onClick={onGoChat}>💬 点击进入会话页开始对话</div>
      </div>
      <div style={{ marginTop: 12 }}>
        <SchedulesBlock />
      </div>
    </div>
  );
}