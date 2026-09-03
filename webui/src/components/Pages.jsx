import React from "react";
import { ThemeContext, DisplayContext } from "../App.jsx";
import * as api from "../api.js";
import ToolTest from "./ToolTest.jsx";
import EmptyState from "./EmptyState.jsx";

import { silentWarn } from "../quiet.js";

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
      const d = await api.listAbilities().catch(() => null);
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
              <div className="domain-desc">{domains ? `${(d.tools || []).length} 项 · ${(d.tools || []).slice(0, 3).map((t) => t.name).join(" / ")}${(d.tools || []).length > 3 ? " …" : ""}` : d.desc}</div>
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

// ── 知识库 RAG（记忆与知识页）：文档建索引 → 语义问答带引用源 ──
function KnowledgeBaseBlock() {
  const [kb, setKb] = React.useState(null);
  const [kquery, setKquery] = React.useState("");
  const [khits, setKhits] = React.useState(null);
  const [kerr, setKerr] = React.useState("");
  React.useEffect(() => { api.getKnowledge().then((d) => d && setKb(d)).catch(() => {}); }, []);
  const doSearch = async () => {
    if (!kquery.trim()) return;
    setKerr("");
    try {
      const d = await api.searchKnowledge(kquery, 5).catch(() => null);
      setKhits(d.hits || []);
      if (!d.hits || d.hits.length === 0) setKerr("知识库未命中相关内容（可先建立索引）");
    } catch (e) {
      setKerr((e && e.message) || "检索失败");
      setKhits(null);
    }
  };
  return (
    <div className="wb-card" style={{ marginTop: 16 }}>
      <div className="wb-card-title">📚 知识库 RAG（带引用源）</div>
      <div className="empty-tip" style={{ marginBottom: 6 }}>
        已建立索引：{kb ? (kb.indexed ? ` ${(kb.files || []).length} 个文件` : "未建立") : "查询中…"}
        {kb && kb.files && kb.files.length > 0 && <span style={{ opacity: .6 }}>（{kb.files.slice(0, 3).join(" · ")}…）</span>}
      </div>
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <input className="set-select set-combo" placeholder="输入问题，如「我们服务器的部署步骤」" value={kquery}
          onChange={(e) => setKquery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doSearch()} />
        <button className="confirm-btn confirm-primary" onClick={doSearch}>🔍 检索</button>
      </div>
      {kerr && <div className="empty-tip">{kerr}</div>}
      {khits && khits.length > 0 && (
        <div className="mem-list">
          {khits.map((h, i) => (
            <div className="mem-card mem-card-lg" key={i}>
              <div className="mem-card-head">
                <span className="mem-id">{h.path ? String(h.path).split(/[\\/]/).pop() : "命中"}</span>
                <span className="mem-tag">相似度 {h.score}</span>
                {h.path && <span className="mem-time" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11 }} title={h.path}>{h.path}</span>}
              </div>
              <div className="mem-text">{h.snippet || h.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function MemoryPage({ embedded }) {
  const [q, setQ] = React.useState("");
  const [focused, setFocused] = React.useState(false);
  const [memories, setMemories] = React.useState(null);
  const [err, setErr] = React.useState("");
  const [editingId, setEditingId] = React.useState(null);
  const [editText, setEditText] = React.useState("");
  const [editType, setEditType] = React.useState("");
  const [editImportance, setEditImportance] = React.useState(3);
  const [adding, setAdding] = React.useState(false);
  const [addText, setAddText] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async (query) => {
    try {
      const d = await api.listBrainMemories(query).catch(() => null);
      if (d && Array.isArray(d.items)) {
        setMemories(d.items);
        setErr("");
        return;
      }
      throw new Error(d?.error || "加载失败");
    } catch (e) {
      // 大脑未初始化时回退到通用长期记忆
      try {
        const d2 = await api.getMemory().catch(() => null);
        if (d2 && d2.facts) {
          setMemories(
            d2.facts.map((f) => ({
              id: `MEM#${f.ts || f.key || ""}`,
              text: f.text || f.value || "",
              type: f.type || (f.tags ? String(f.tags).split(",")[0] : "记忆"),
              importance: 3,
              ts: f.ts || "",
            }))
          );
          setErr("");
          return;
        }
      } catch (e) { silentWarn(e, "Pages"); }
      setErr(e.message || "记忆加载失败：后端未连接");
    }
  }, []);

  React.useEffect(() => {
    load("");
  }, [load]);

  const items = (memories || []).filter(
    (m) => !q || (m.text || "").includes(q) || (m.type || "").includes(q)
  );

  const doUpdate = async (id, patch) => {
    setBusy(true);
    try {
      await api.brainMemoryAction({ action: "update", id, ...patch }).catch(() => null);
      setEditingId(null);
      load(q);
    } catch (e) {
      setErr(e.message);
    }
    setBusy(false);
  };

  const doDelete = async (id) => {
    if (!window.confirm("删除这条记忆？")) return;
    setBusy(true);
    try {
      await api.brainMemoryAction({ action: "delete", id }).catch(() => null);
      load(q);
    } catch (e) {
      setErr(e.message);
    }
    setBusy(false);
  };

  const doAdd = async () => {
    if (!addText.trim()) return;
    setBusy(true);
    try {
      await api.brainMemoryAction({ action: "add", text: addText.trim(), type: "备忘", importance: 3 }).catch(() => null);
      setAddText("");
      setAdding(false);
      load(q);
    } catch (e) {
      setErr(e.message);
    }
    setBusy(false);
  };

  const star = (m) => {
    const imp = m.importance >= 5 ? 3 : 5;
    doUpdate(m.id, { importance: imp });
  };

  return (
    <div className={embedded ? "page-embedded" : "page"}>
      {!embedded && (
        <div className="page-head">
          <h1>记忆与知识</h1>
          <p>大脑记忆库 · 类型/重要度/实体标注 · 相关检索 · 可编辑管理{memories ? ` · 共 ${memories.length} 条` : ""}</p>
        </div>
      )}
      <div className={`mem-search ${focused ? "mem-search-focus" : ""}`}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4-4" />
        </svg>
        <input
          placeholder="检索大脑记忆（本地 IDF 相关度）"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            load(e.target.value);
          }}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
        <span className="mem-search-count">{items.length} 条</span>
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", margin: "6px 0 2px" }}>
        {!adding ? (
          <button className="confirm-btn" onClick={() => setAdding(true)}>＋ 记录一条记忆</button>
        ) : (
          <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              style={{ width: 320, padding: "6px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-1)", color: "var(--text-1)", fontSize: 13 }}
              placeholder="要记住的内容…"
              value={addText}
              onChange={(e) => setAddText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doAdd()}
              autoFocus
            />
            <button className="confirm-btn confirm-primary" onClick={doAdd} disabled={busy || !addText.trim()}>保存</button>
            <button className="confirm-btn" onClick={() => { setAdding(false); setAddText(""); }}>取消</button>
          </span>
        )}
      </div>
      <BrainGoals />
      <div style={{ display: "flex", justifyContent: "flex-end", margin: "4px 0 8px" }}>
        <button
          className="confirm-btn"
          style={{ fontSize: 12 }}
          onClick={async () => {
            setBusy(true);
            try {
              const r = await api.brainAction({ action: "consolidate" }).catch(() => null);
              alert(r?.message || "巩固完成");
              load(q);
            } catch (e) {
              setErr(e.message);
            }
            setBusy(false);
          }}
        >
          🧹 睡眠巩固（归档旧记忆 + 合并相似 + LLM 提炼）
        </button>
      </div>
      <div className="mem-list">
        {items.map((m) => (
          <div className={`mem-card mem-card-lg ${m.importance >= 4 ? "mem-card-star" : ""}`} key={m.id}>
            <div className="mem-card-head">
              <span className="mem-id">{m.type || "记忆"}</span>
              {m.importance >= 4 && <span className="mem-tag" style={{ background: "var(--warn-soft)", color: "var(--warn)" }}>★ 重要</span>}
              <span className="mem-tag">{m.source || "手动"}</span>
              {m.ts && <span className="mem-time">{String(m.ts).slice(0, 16).replace("T", " ")}</span>}
            </div>
            {editingId === m.id ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "4px 0" }}>
                <textarea
                  style={{ width: "100%", minHeight: 60, padding: 8, borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-1)", color: "var(--text-1)", fontSize: 13 }}
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                />
                <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  类型：
                  <input style={{ width: 120, padding: "4px 8px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-1)", color: "var(--text-1)", fontSize: 12 }} value={editType} onChange={(e) => setEditType(e.target.value)} />
                  重要度：
                  <select className="set-select" style={{ fontSize: 12, padding: "4px 6px" }} value={editImportance} onChange={(e) => setEditImportance(Number(e.target.value))}>
                    <option value={1}>1 低</option>
                    <option value={2}>2</option>
                    <option value={3}>3 普通</option>
                    <option value={4}>4</option>
                    <option value={5}>5 高</option>
                  </select>
                  <button className="confirm-btn confirm-primary" disabled={busy} onClick={() => doUpdate(m.id, { text: editText, type: editType, importance: editImportance })}>保存</button>
                  <button className="confirm-btn" onClick={() => setEditingId(null)}>取消</button>
                </span>
              </div>
            ) : (
              <div className="mem-text">{m.text}</div>
            )}
            <div className="mem-card-foot" style={{ display: "flex", gap: 8, marginTop: 6 }}>
              <button className="msg-op" disabled={busy} onClick={() => star(m)}>{m.importance >= 4 ? "☆ 取消重要" : "★ 标记重要"}</button>
              <button className="msg-op" disabled={busy} onClick={() => { setEditingId(m.id); setEditText(m.text || ""); setEditType(m.type || ""); setEditImportance(Number(m.importance) || 3); }}>✏️ 编辑</button>
              <button className="msg-op" style={{ color: "var(--danger)" }} disabled={busy} onClick={() => doDelete(m.id)}>🗑 删除</button>
            </div>
          </div>
        ))}
        {items.length === 0 && (
          err ? (
            <EmptyState icon="⚠️" title="记忆加载失败" hint={err} compact />
          ) : (
            <EmptyState icon="🧠" title="大脑记忆库还是空的" hint="对话中记录的重要事实会自动同步到这里；点右上角「记录一条记忆」或与 AI 多聊几次，记忆就开始生长。" compact />
          )
        )}
      </div>
      <KnowledgeBaseBlock />
    </div>
  );
}

// ── 大脑进行中目标（goals.json，对话自动注入）──
function BrainGoals() {
  const [goals, setGoals] = React.useState([]);
  const [newTitle, setNewTitle] = React.useState("");
  const [showAdd, setShowAdd] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      const d = await api.brainAction({ action: "goals-list" }).catch(() => null);
      if (d && d.ok) setGoals((d.data?.goals || []).filter((g) => g.status === "active"));
    } catch (e) { silentWarn(e, "Pages"); }
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const act = async (action, payload) => {
    try {
      await api.brainAction({ action, ...payload }).catch(() => null);
      load();
    } catch (e) { silentWarn(e, "Pages"); }
  };

  const active = goals.filter((g) => g.status === "active");
  if (active.length === 0 && !showAdd) return null;
  return (
    <div style={{ margin: "4px 0 10px", padding: "10px 14px", borderRadius: 12, background: "var(--bg-1)", border: "1px solid var(--border)" }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-1)", marginBottom: 6 }}>🎯 进行中目标（对话中自动注入）</div>
      {active.map((g) => (
        <div key={g.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: 12.5, color: "var(--text-1)" }}>
          <span style={{ flex: 1 }}>{g.title}</span>
          {g.progress && <span style={{ color: "var(--brand)", fontSize: 11 }}>{g.progress}</span>}
          <button className="msg-op" style={{ color: "var(--ok)" }} onClick={() => act("goals-update", { id: g.id, status: "done" })}>✓ 完成</button>
          <button className="msg-op" style={{ color: "var(--danger)" }} onClick={() => act("goals-delete", { id: g.id })}>✕</button>
        </div>
      ))}
      {showAdd ? (
        <span style={{ display: "flex", gap: 6, marginTop: 6 }}>
          <input
            style={{ flex: 1, padding: "5px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-2)", color: "var(--text-1)", fontSize: 12.5 }}
            placeholder="新目标…"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newTitle.trim()) {
                act("goals-add", { title: newTitle.trim() });
                setNewTitle("");
                setShowAdd(false);
              }
            }}
            autoFocus
          />
          <button className="confirm-btn confirm-primary" style={{ fontSize: 12 }} onClick={() => { act("goals-add", { title: newTitle.trim() }); setNewTitle(""); setShowAdd(false); }}>保存</button>
        </span>
      ) : (
        <button className="msg-op" onClick={() => setShowAdd(true)}>＋ 添加目标</button>
      )}
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
      const d = await api.getPermissions().catch(() => null);
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
      const d = await api.savePermissions({ [key]: items }).catch(() => null);
      if (d && d.ok) {
        setPerms({ ...perms, [key]: items });
        setSavedTip("已保存到 permissions.json");
        setTimeout(() => setSavedTip(""), 1800);
      }
    } catch (e) { silentWarn(e, "Pages"); }
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
      {/* 黑名单一键总开关：默认开（黑名单空 = 0 限制）；关 = 一键全放行（连黑名单也不拦） */}
      <div className="perm-group" style={{ borderColor: "var(--border-strong)" }}>
        <div className="perm-group-head">
          <b>黑名单总开关（blocklist_enabled）</b>
          <span>{perms.blocklist_enabled ? "已开启：黑名单条目生效（默认空 = 0 限制）" : "已关闭：一键全放行，黑名单条目不拦截"}</span>
        </div>
        <button
          className="confirm-btn"
          onClick={async () => {
            setBusy(true);
            try {
              const d = await api.savePermissions({ blocklist_enabled: !perms.blocklist_enabled }).catch(() => null);
              if (d && d.ok) {
                setPerms({ ...perms, blocklist_enabled: !perms.blocklist_enabled });
                setSavedTip("已保存到 permissions.json");
                setTimeout(() => setSavedTip(""), 1800);
              }
            } catch (e) { silentWarn(e, "Pages"); }
            setBusy(false);
          }}
          disabled={busy}
        >
          {perms.blocklist_enabled ? "🔓 一键全放行" : "🛡 启用黑名单限制"}
        </button>
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
      const d = await api.getTasks().catch(() => null);
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
      const d = await api.listFiles().catch(() => null);
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
          <EmptyState icon="📦" title="还没有产物" hint="工具生成的文件会自动出现在这里。" compact />
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
    const d = await api.getEvolutions().catch(() => null);
    if (d) setEvos(d.evolutions);
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const apply = async (name) => {
    if (!window.confirm(`采纳提案「${name}」？原文件将备份为 .evobak`)) return;
    setBusy(true);
    const r = await api.applyEvolution(name).catch(() => null);
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
    const r = await api.ignoreEvolution(name).catch(() => null);
    setBusy(false);
    if (r && r.ok) {
      setDetail(null);
      load();
    } else if (r && r.error) {
      alert(`失败：${r.error}`);
    }
  };

  const showDetail = async (name) => {
    const d = await api.getEvolutionDetail(name).catch(() => null);
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
          {(!evos || evos.length === 0) && <EmptyState icon="💡" title="还没有进化提案" hint="AI 自我审查后会在这里自动生成改进提案。" compact />}
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
      const s = await api.getStatus().catch(() => null);
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

  const refetch = () => api.getSchedules().then((d) => d && setSchedules(d.schedules || [])).catch(() => {});
  React.useEffect(() => {
    refetch();
  }, []);

  const save = async (items) => {
    setSchedules(items);
    try {
      await api.saveSchedules(items);
      refetch();  // 重新拉取，拿到后端计算的 next_run
    } catch (e) { silentWarn(e, "Pages"); }
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
      <div className="wb-card-title">⏰ 计划与定时任务（{schedules.filter((s) => s.enabled !== false).length} 启用）</div>
      <div className="sched-list">
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
              <button className="msg-op" onClick={() => del(i)}>✕</button>
            </div>
            {s.text && <div className="sched-text">{s.text}</div>}
          </div>
        ))}
        {schedules.length === 0 && <EmptyState icon="⏰" title="还没有定时任务" hint="AI 用 schedule_task 工具也能创建定时任务。" compact />}
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

export function WorkbenchPage({ onApply, onPickSession }) {
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

  React.useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 30000);
    return () => clearInterval(iv);
  }, [refresh]);

  const u = (status && status.usage_total) || {};
  const running = Object.values(procs || {}).filter((p) => !p.exited);
  const missingDeps = (deps || []).filter((d) => !d.ok);
  const recentFiles = (files && files.recent) || [];
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
        <button className="confirm-btn wb-refresh" onClick={refresh} title="手动刷新">
          ⟳ 刷新{updatedAt ? ` · ${updatedAt}` : ""}
        </button>
      </div>

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
          <div className="wb-card-title">⚡ 快捷行动</div>
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
            <EmptyState icon="⚡" title="还没有快捷行动" hint="在「指令库」添加指令，或使用后高频指令会自动出现在这里。" compact />
          )}
        </div>

        <div className="wb-side">
          <div className="wb-card">
            <div className="wb-card-title">⏳ 进行中</div>
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
            <div className="wb-card-title">ℹ 快速了解</div>
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
          <div className="wb-card-title">💬 最近会话（{sessions.length}）</div>
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
          <div className="wb-card-title">📦 最近产物（{recentFiles.length}）</div>
          <div className="wb-file-list">
            {recentFiles.slice(0, 6).map((p) => (
              <div className="wb-file-item" key={p}>
                <span className="wb-file-ic">{fileIcon(p)}</span>
                <span className="wb-file-name" title={p}>{String(p).split(/[\\/]/).pop()}</span>
                <div className="wb-file-ops">
                  <button className="pm-op" onClick={() => openFile(p)}>打开</button>
                  <button className="pm-op" onClick={() => openFile(p, true)}>定位</button>
                </div>
              </div>
            ))}
            {recentFiles.length === 0 && <EmptyState icon="📦" title="还没有产物" hint="AI 生成的文件会出现在这里。" compact />}
          </div>
        </div>
      </div>

      {/* 计划区 */}
      <div style={{ marginTop: 12 }}>
        <SchedulesBlock />
      </div>
    </div>
  );
}