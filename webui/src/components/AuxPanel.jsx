import React from "react";
import * as api from "../api.js";
import { ThemeContext, DisplayContext } from "../App.jsx";

import { silentWarn } from "../quiet.js";
// ── 启动即预取（不等打开面板才加载）──────────────────
const PREFETCHED = {
  cfg: null,
  ctx: null,
  st: null,
};
const prefetchPromise = (() => {
  const grab = (name, fn) =>
    api
      [fn]()
      .then((d) => {
        if (d) PREFETCHED[name] = d;
      })
      .catch(() => {});
  return Promise.all([grab("cfg", "getConfig"), grab("ctx", "getContext"), grab("st", "getStatus")]);
})();

// ═══ 第四栏 · 控制台（参数 / 文件 / 进程）══════

// ── 常用小组件 ─────────────────────────────────────
function Group({ title, children, right }) {
  return (
    <div className="px-group">
      <div className="px-group-title">
        <span>{title}</span>
        {right && <span className="px-group-right">{right}</span>}
      </div>
      {children}
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <div className="px-field" title={hint}>
      <div className="px-field-head">
        <span className="px-field-label">{label}</span>
        {hint && <span className="px-field-hint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function TglRow({ label, hint, on, onClick }) {
  return (
    <div className="px-tgl-row" title={hint}>
      <span>{label}</span>
      <button className={`toggle ${on ? "toggle-on" : ""}`} onClick={onClick}>
        <span className="toggle-knob" />
      </button>
    </div>
  );
}

// ── 📂 文件：树 + 最近产物（打开/定位/注入）──────────
function FilesTab({ onInject }) {
  const [roots, setRoots] = React.useState(null);
  const [expanded, setExpanded] = React.useState({});
  const [children, setChildren] = React.useState({});
  const [loading, setLoading] = React.useState({});
  const [err, setErr] = React.useState("");
  const [busyPath, setBusyPath] = React.useState(null);
  const [q, setQ] = React.useState("");
  const [favs, setFavs] = React.useState(null); // {path: meta} 收藏缓存

  const load = React.useCallback(async (showLoading) => {
    try {
      const d = await api.listFiles();
      if (d) {
        setRoots(d);
        // 建收藏 path→meta 映射
        const m = {};
        (d.favs || []).forEach((f) => { m[f.path] = f; });
        setFavs(m);
      } else if (showLoading) setErr("文件列表加载失败：后端未连接");
    } catch (e) { if (showLoading) setErr("文件列表加载失败：后端未连接"); }
  }, []);

  React.useEffect(() => {
    load(true);
    const iv = setInterval(() => load(false), 8000); // 新产物自动跟出
    return () => clearInterval(iv);
  }, [load]);

  const flashErr = (e) => { setErr(e && e.message ? e.message : "操作失败"); setTimeout(() => setErr(""), 3000); };
  const openFile = async (path) => {
    if (!path || busyPath) return;
    setBusyPath(path);
    try { await api.openFile(path); } catch (e) { flashErr(e); } finally { setBusyPath(null); }
  };
  const openDir = async (path) => {
    if (!path || busyPath) return;
    setBusyPath(path);
    try { await api.openDir(path); } catch (e) { flashErr(e); } finally { setBusyPath(null); }
  };

  const toggleFav = async (path) => {
    try {
      const r = await api.favFile(path);
      // 乐观更新本地收藏
      setFavs((prev) => {
        const next = { ...(prev || {}) };
        if (r.faved) next[path] = { path, name: String(path).split(/[\\/]/).pop() };
        else delete next[path];
        return next;
      });
      // 后台刷新真实数据（含目录树 faved 标记）
      setTimeout(() => load(false), 200);
    } catch (e) { flashErr(e); }
  };

  const toggle = async (path) => {
    const next = { ...expanded, [path]: !expanded[path] };
    setExpanded(next);
    if (next[path] && !children[path]) {
      setLoading((l) => ({ ...l, [path]: true }));
      try {
        const d = await api.listFiles(path);
        if (d && d.entries) {
          setChildren((c) => ({ ...c, [path]: d.entries }));
          setFavs((prev) => {
            const nv = { ...(prev || {}) };
            d.entries.forEach((en) => { if (en.faved) nv[en.path] = en; });
            return nv;
          });
        }
      } catch (e) { silentWarn(e, "AuxPanel"); }
      setLoading((l) => ({ ...l, [path]: false }));
    }
  };

  const favIcon = (path) => (favs && favs[path] ? "★" : "☆");
  const isFav = (path) => !!(favs && favs[path]);

  // ── 一行文件：图标 + 收藏 + 名称(+状态) + 操作 ──
  const fileRow = (meta, indent) => {
    const p = meta.path;
    const missing = meta.exists === false;
    return (
      <div className={`fx-row ${missing ? "fx-missing" : ""}`} style={{ paddingLeft: 6 + (indent || 0) * 14 }}>
        <button className={`fx-fav ${isFav(p) ? "fx-fav-on" : ""}`} title={isFav(p) ? "取消收藏" : "收藏"}
          onClick={(e) => { e.stopPropagation(); toggleFav(p); }}>
          {favIcon(p)}
        </button>
        <span className="fx-file">
          <span className={`fx-fname ${missing ? "fx-fname-missing" : ""}`} title={`${p}${missing ? "\n（文件已不存在）" : ""}`}
            onDoubleClick={() => !missing && openFile(p)}>
            {meta.is_dir ? "📁" : "📄"} {meta.name}
          </span>
          {!missing && meta.size_label && <span className="fx-size">{meta.size_label}</span>}
          {!missing && (meta.mtime_label || meta.mtime) && (
            <span className="fx-time">{meta.mtime_label || fmtRel(meta.mtime)}</span>
          )}
        </span>
        <span className="fx-acts">
          <button className="fx-act" title="打开文件" onClick={() => !missing && openFile(p)}>打开</button>
          <button className="fx-act" title="打开所在文件夹" onClick={() => !missing && openDir(p)}>⌖</button>
          {!meta.is_dir && <button className="fx-act" title="读取内容到输入框" onClick={() => !missing && onInject && onInject(p)}>注入</button>}
        </span>
      </div>
    );
  };

  // 目录树项（含收藏星）
  const entry = (e, depth) => {
    if (!e || !e.path) return null;
    const p = e.path;
    if (e.is_dir) {
      return (
        <div key={p} className="fx-row" style={{ paddingLeft: 6 + depth * 14 }}>
          <button className={`fx-fav ${isFav(p) ? "fx-fav-on" : ""}`} title={isFav(p) ? "取消收藏" : "收藏文件夹"}
            onClick={(ev) => { ev.stopPropagation(); toggleFav(p); }}>{favIcon(p)}</button>
          <button className="fx-dir" title={p} onClick={() => toggle(p)}>
            {expanded[p] ? "▾" : "▸"} 📁 {e.name}
          </button>
          <button className="fx-act" title="打开该文件夹" onClick={() => openDir(p)}>⌖</button>
        </div>
      );
    }
    return fileRow({ path: p, name: e.name, is_dir: false, size_label: e.size_label, mtime_label: e.mtime, exists: true }, depth);
  };

  const renderDir = (path, depth) => {
    if (!expanded[path]) return null;
    const items = children[path];
    if (!items) return <div className="fx-row fx-hint" style={{ paddingLeft: 20 + depth * 14 }}>{loading[path] ? "加载中…" : "空"}</div>;
    return items
      .filter((e) => !q || e.name.toLowerCase().includes(q.toLowerCase()))
      .map((e) => (
        <React.Fragment key={e.path}>
          {entry(e, depth + 1)}
          {e.is_dir && renderDir(e.path, depth + 1)}
        </React.Fragment>
      ));
  };

  const now = Date.now();
  const justCut = 2 * 60 * 1000; // 刚刚 = <2min
  const dayCut = 24 * 3600 * 1000;
  const bucket = (m) => {
    const t = m.mtime ? m.mtime * 1000 : (m.mtime_epoch ? m.mtime_epoch * 1000 : 0);
    if (!t) return "old";
    if (now - t < justCut) return "just";
    if (now - t < dayCut && new Date(t).getDate() === new Date().getDate()) return "today";
    return "old";
  };
  const fmtRel = (m) => {
    if (!m) return "";
    const t = m.mtime ? m.mtime * 1000 : m;
    if (typeof m !== "number") return m; // 已是标签
    const d = new Date(t);
    return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  };

  // 收藏列表（含缺省展开树里的收藏项）
  const favList = favs ? Object.values(favs) : [];
  const recent = roots ? roots.recent || [] : [];
  const recentFiltered = recent.filter((m) => !q || (m.name || "").toLowerCase().includes(q.toLowerCase()));
  const just = recentFiltered.filter((m) => bucket(m) === "just");
  const today = recentFiltered.filter((m) => bucket(m) === "today");
  const older = recentFiltered.filter((m) => bucket(m) === "old" && m.exists);

  const group = (title, list, emptyHint) => (
    <div key={title}>
      <div className="fx-root">{title}（{list.length}）</div>
      {list.length > 0 ? list.map((m, i) => (
        <React.Fragment key={m.path + i}>{fileRow(m, 0)}</React.Fragment>
      )) : emptyHint && <div className="fx-hint">{emptyHint}</div>}
    </div>
  );

  const showTree = !q || true; // 搜索时仍显示树（树内已过滤）

  return (
    <div className="aux-tab">
      <div className="fx-search">
        <input className="fx-q" placeholder="搜索文件/目录…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="msg-op" title="刷新" onClick={() => load(true)}>⟳</button>
      </div>
      {roots && (
        <>
          <div className="fx-root" style={{ marginTop: 2 }}>
            <span title={roots.active_dir} className="fx-rootpath">{roots.active_dir}</span>
            {roots.active_dir && <button className="fx-act" title="打开工作区文件夹" onClick={() => openDir(roots.active_dir)}>打开</button>}
          </div>

          {group("⭐ 收藏", favList, "点文件/目录旁的 ☆ 收藏到这里")}
          {group("⏱ 刚刚产出", just, null)}
          {group("📅 今天", today, null)}
          {group("🕘 更早", older, q ? "没有匹配的更早文件" : null)}

          {!q && recent.length === 0 && favList.length === 0 && (
            <div className="fx-hint">还没有产物——让 AI 写文件/出图后会出现在这里。</div>
          )}

          <div className="fx-root">🗂 全部文件</div>
          {showTree && (roots.entries || []).filter((e) => !q || e.name.toLowerCase().includes(q.toLowerCase())).length === 0 && q ? (
            <div className="fx-hint">工作区无匹配项</div>
          ) : (
            (roots.entries || []).map((e) => {
              if (q && !e.name.toLowerCase().includes(q.toLowerCase())) {
                // 搜索模式下仅展开含匹配项的目录
                return e.is_dir ? <React.Fragment key={e.path}><div style={{ paddingLeft: 6 }} className="fx-row">
                  <button className="fx-dir" onClick={() => toggle(e.path)}>{expanded[e.path] ? "▾" : "▸"} 📁 {e.name}</button>
                </div>{renderDir(e.path, 0)}</React.Fragment> : null;
              }
              return (
                <React.Fragment key={e.path}>
                  {entry(e, 0)}
                  {e.is_dir && renderDir(e.path, 0)}
                </React.Fragment>
              );
            })
          )}
        </>
      )}
      {!roots && <div className="fx-hint">加载中…</div>}
      {err && <div className="fx-hint fx-err">{err}</div>}
    </div>
  );
}


// ── ⚙ 进程：终端输出 ───────────────────────────────
function ProcessesTab({ onInject }) {
  const [procs, setProcs] = React.useState({});
  const [current, setCurrent] = React.useState(null);
  const [follow, setFollow] = React.useState(true);
  const [cmd, setCmd] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const d = await api.listProcesses();
        if (!alive) return;
        setProcs(d.processes || {});
        setCurrent((c) => (c && d.processes[c] ? c : Object.keys(d.processes)[0] || null));
      } catch (e) { silentWarn(e, "AuxPanel"); }
    };
    load();
    const iv = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  const cur = current ? procs[current] : null;
  const lines = cur?.lines || [];

  const stop = async () => {
    if (!current) return;
    try {
      await api.stopProcess(current);
    } catch (e) { silentWarn(e, "AuxPanel"); }
  };

  const start = async () => {
    if (!cmd.trim()) return;
    try {
      const r = await api.startProcess(cmd.trim());
      setCmd("");
    } catch (e) { silentWarn(e, "AuxPanel"); }
  };

  const errPat = /Traceback|Error|ERROR|Exception|失败|错误|refused|NotImplemented/;

  return (
    <div className="aux-tab">
      <div className="px-head">
        <select className="set-select px-combo" value={current || ""} onChange={(e) => setCurrent(e.target.value)}>
          {Object.keys(procs).map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
          {Object.keys(procs).length === 0 && <option value="">无进程</option>}
        </select>
        <button className="confirm-btn px-stop" onClick={stop} disabled={!cur || cur.exited}>■ 停止</button>
        <button className="msg-op" onClick={() => setFollow(!follow)}>{follow ? "自动跟随 ✓" : "自动跟随"}</button>
      </div>
      {cur && (
        <div className="px-info">
          pid {cur.pid} · 启动 {cur.started}
          <span className={`px-badge ${cur.exited ? "px-badge-exit" : "px-badge-run"}`}>
            {cur.exited ? `■ 已退出 code=${cur.code}` : "● 运行中"}
          </span>
        </div>
      )}
      <div className="px-term" ref={(el) => {
        if (el && follow) el.scrollTop = el.scrollHeight;
      }}>
        {lines.length === 0 && <div className="fx-hint">暂无输出（AI 用 start_process 启动进程后显示在这里）</div>}
        {lines.map((l, i) => (
          <div key={i} className={`px-line ${errPat.test(l) ? "px-err" : l.startsWith("──") ? "px-meta" : ""}`}>{l}</div>
        ))}
      </div>
      <div className="px-start">
        <input
          className="tf-input"
          placeholder="启动命令，如 python -m http.server 8000"
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && start()}
        />
        <button className="confirm-btn confirm-primary" onClick={start} disabled={!cmd.trim()}>▶</button>
      </div>
    </div>
  );
}

// ── 🎛 控制台：模型/思考/场景/外观/状态 ─────────────
const THEME_CHOICES = [
  { id: "starfield", name: "星空", desc: "极黑冷底 · 亮青点缀" },
  { id: "deepsea", name: "深海", desc: "深蓝底 · 湖蓝光" },
  { id: "arctic", name: "北极冰", desc: "冰白底 · 深海蓝字" },
];

function ParamsTab() {
  const { theme, setTheme } = React.useContext(ThemeContext);
  const { density, setDensity, fontSize, setFontSize } = React.useContext(DisplayContext);
  const [cfg, setCfg] = React.useState(PREFETCHED.cfg);
  const [draft, setDraft] = React.useState(null);
  const [tip, setTip] = React.useState("");
  const [ctx, setCtx] = React.useState(PREFETCHED.ctx);
  const [st, setSt] = React.useState(PREFETCHED.st);
  const [customModel, setCustomModel] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [loadErr, setLoadErr] = React.useState("");

  React.useEffect(() => {
    const apply = (d, set, fn) => {
      if (d) {
        set(d);
      } else {
        api[fn]().then((d) => d && set(d)).catch(() => setLoadErr(`「${fn}」加载失败：后端未连接`));
      }
    };
    prefetchPromise.then(() => {
      apply(PREFETCHED.cfg, setCfg, "getConfig");
      apply(PREFETCHED.ctx, setCtx, "getContext");
      apply(PREFETCHED.st, setSt, "getStatus");
    });
  }, []);

  // 草稿：本地编辑不落盘，点「保存」才一次性写盘并锁定
  const edit = (patch) => setDraft((d) => ({ ...(d || {}), ...patch }));

  const save = async () => {
    if (!draft || saving) return;
    setSaving(true);
    try {
      const d = await api.saveConfig(draft);
      if (d && d.ok) {
        setCfg((c) => ({ ...c, ...draft }));
        if ("model" in draft) setCustomModel(!modelOptions.includes(draft.model));
        setDraft(null);
        setTip("✅ 已保存并锁定");
        setTimeout(() => setTip(""), 1800);
      } else {
        setTip("⚠️ 保存失败");
        setTimeout(() => setTip(""), 1800);
      }
    } catch {
      setTip("⚠️ 保存失败");
      setTimeout(() => setTip(""), 1800);
    }
    setSaving(false);
  };

  // 有未保存草稿时标记（视觉提醒）
  const dirty = draft != null;

  if (!cfg) return <div className="aux-tab fx-hint">{loadErr ? <><span>{loadErr}</span><button className="msg-op" style={{ marginLeft: 8 }} onClick={() => window.location.reload()}>重试</button></> : "加载中…"}</div>;

  // 当前生效值 = cfg 上叠加草稿（草稿只含改过的字段，不能整体替换否则下方下拉框/输入框会丢失数据）
  const cur = { ...cfg, ...(draft || {}) };
  const modelOptions = Array.isArray(cfg.models) && cfg.models.length ? cfg.models : [];
  const ctxTools = Array.isArray(ctx?.tools) ? ctx.tools : [];
  const ctxMemCount = ctx?.memory?.count ?? (Array.isArray(ctx?.memory) ? ctx.memory.length : 0);
  const ctxUsage = ctx?.usage || {};
  const ctxPct = Math.min(100, Math.max(0, Math.round(((ctxUsage.prompt || 0) / (ctxUsage.max || 1000000 || 1)) * 100)));
  const isDark = theme !== "arctic";
  const hasKey = cfg.has_key;

  const displayModes = [
    { id: "compact", name: "紧凑" },
    { id: "comfort", name: "舒适" },
    { id: "loose", name: "宽松" },
  ];

  return (
    <div className="aux-tab">
      {/* ── 运行状态：一眼可见 ── */}
      <div className="px-stats">
        <div className="px-stat" title="本轮上下文占用">
          <span className="px-stat-v">{ctxUsage.prompt ? `${ctxPct}%` : "—"}</span>
          <span className="px-stat-k">上下文</span>
        </div>
        <div className="px-stat" title="可用的工具数">
          <span className="px-stat-v">{ctxTools.length || "—"}</span>
          <span className="px-stat-k">工具</span>
        </div>
        <div className="px-stat" title="积累的记忆条数">
          <span className="px-stat-v">{ctxMemCount || "—"}</span>
          <span className="px-stat-k">记忆</span>
        </div>
        <div className="px-stat" title="本月费用 / 预算">
          <span className="px-stat-v">
            {st?.monthly_cost ? `¥${Number(st.monthly_cost).toFixed(2)}` : ctxUsage.cost || "—"}
          </span>
          <span className="px-stat-k">成本</span>
        </div>
      </div>

      {/* ── 模型引擎：最高频切换 ── */}
      <Group title="⚙️ 模型引擎" right={hasKey ? <span className="px-key-ok">● Key 就绪</span> : <span className="px-key-warn">● 未配置 Key</span>}>
        <Field label="模型" hint="编辑后点「保存」生效">
          {customModel || !modelOptions.includes(cur.model) ? (
            <input
              className="tf-input px-sel"
              value={cur.model || ""}
              placeholder="输入任意模型名"
              onChange={(e) => edit({ model: e.target.value })}
            />
          ) : (
            <select
              className="set-select px-sel"
              value={cur.model}
              onChange={(e) => {
                if (e.target.value === "__custom__") setCustomModel(true);
                else edit({ model: e.target.value });
              }}
            >
              {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
              <option value="__custom__">✎ 自定义模型名…</option>
            </select>
          )}
        </Field>
        <div className="px-grid2">
          <Field label="思考档" hint={cur.thinking === "auto" ? "按任务复杂度智能路由" : "none/low/medium/high/xhigh/max"}>
            <select className="set-select px-sel" value={cur.thinking} onChange={(e) => edit({ thinking: e.target.value })}>
              {(Array.isArray(cur.thinking_modes) ? cur.thinking_modes : []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="场景" hint="预设采样参数：通用/编程/Agent/运营/法律/金融/教育/医疗健康/写作创作">
            <select className="set-select px-sel" value={cur.scenario} onChange={(e) => edit({ scenario: e.target.value })}>
              {(Array.isArray(cur.scenarios) ? cur.scenarios : []).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>
        </div>
        <Field label="API 网关" hint="OpenAI 兼容 base_url">
          <input className="tf-input px-sel" value={cur.base_url || ""} placeholder="https://api.deepseek.com" onChange={(e) => edit({ base_url: e.target.value })} />
        </Field>
      </Group>

      {/* ── 输出采样 ── */}
      <Group title="🌡 采样参数" right={cur.thinking === "none" ? "无思考档时生效" : "思考档开启时由模型控制"}>
        <div className="px-grid2">
          <Field label="温度" hint="0-2">
            <input className="tf-input px-num" type="number" step="0.1" min="0" max="2" value={cur.temperature} onChange={(e) => edit({ temperature: Number(e.target.value) })} />
          </Field>
          <Field label="Top-P" hint="0-1">
            <input className="tf-input px-num" type="number" step="0.05" min="0" max="1" value={cur.top_p} onChange={(e) => edit({ top_p: Number(e.target.value) })} />
          </Field>
          <Field label="输出上限" hint="单次回复 tokens">
            <input className="tf-input px-num" type="number" step="1024" min="1024" max="393216" value={cur.max_tokens} onChange={(e) => edit({ max_tokens: Number(e.target.value) })} />
          </Field>
          <Field label="Seed" hint="0=随机">
            <input className="tf-input px-num" type="number" value={cur.seed} onChange={(e) => edit({ seed: Number(e.target.value) })} />
          </Field>
        </div>
      </Group>

      {/* ── 功能开关 ── */}
      <Group title="🧩 功能开关">
        <TglRow label="JSON 输出" hint="response_format，失败自动重试" on={!!cur.json_output} onClick={() => edit({ json_output: !cur.json_output })} />
        <TglRow label="Beta API" hint="前缀续写 / FIM 补全" on={!!cur.beta_api} onClick={() => edit({ beta_api: !cur.beta_api })} />
        <TglRow label="strict 工具" hint="严格遵循 JSON Schema（自动 Beta）" on={!!cur.strict_tools} onClick={() => edit({ strict_tools: !cur.strict_tools })} />
        <TglRow label="工具开关" hint="向模型暴露工具定义" on={!!cur.tools_enabled} onClick={() => edit({ tools_enabled: !cur.tools_enabled })} />
      </Group>

      {/* ── 外观 ── */}
      <Group title="🎨 外观">
        <Field label="风格">
          <div className="px-themes">
            {THEME_CHOICES.map((t) => (
              <button
                key={t.id}
                className={`px-theme ${theme === t.id ? "px-theme-on" : ""}`}
                title={t.desc}
                onClick={() => setTheme(t.id)}
              >
                <span className="px-theme-dot" data-t={t.id} />
                <span>{t.name}</span>
              </button>
            ))}
          </div>
        </Field>
        <div className="px-grid2">
          <Field label="密度">
            <select className="set-select px-sel" value={density} onChange={(e) => setDensity(e.target.value)}>
              {displayModes.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
          <Field label="消息字号">
            <select className="set-select px-sel" value={fontSize} onChange={(e) => setFontSize(Number(e.target.value))}>
              {[12, 13, 14, 15, 16, 17, 18].map((n) => <option key={n} value={n}>{n}px</option>)}
            </select>
          </Field>
        </div>
      </Group>

      {/* ── 保存栏：点保存一次性写盘并锁定 ── */}
      <div className="px-savebar">
        <button
          className={`confirm-btn confirm-primary px-save ${dirty ? "px-save-dirty" : ""}`}
          onClick={save}
          disabled={!dirty || saving}
          title={dirty ? "保存全部修改并锁定" : "暂无未保存修改"}
        >
          {saving ? "保存中…" : dirty ? "💾 保存并锁定" : "🔒 已锁定"}
        </button>
        {dirty && (
          <button className="confirm-btn" onClick={() => setDraft(null)} title="放弃未保存修改">
            取消
          </button>
        )}
      </div>

      {tip && <div className="px-tip">{tip}</div>}
    </div>
  );
}

// ═══ 🔧 活动（AI 工具调用链 · 实时）═══════
// 工具执行从聊天流"搬"到这里：聊天不再堆详细工具卡，活动区按最近一次任务实时
// 列出每一步（名称/状态/耗时），点开某步才见参数与结果——感知与"奖励感"在，细节不刷屏。
function ActivityTab({ activity, products, onOpenChatTools, onGoFiles, onInject }) {
  const [expanded, setExpanded] = React.useState(null);
  const steps = (activity && activity.steps) || [];
  const streaming = !!(activity && activity.streaming);
  const done = steps.filter((s) => s.status === "done").length;
  const failed = steps.filter((s) => s.status === "failed").length;

  React.useEffect(() => setExpanded(null), [activity && activity.taskId]);

  // 会话内「产物书架」：由上层按 msgs 累计传下（新→旧，去重），新消息不冲掉旧产物
  const prodList = Array.isArray(products) ? products : [];
  const prodAct = (path, act) => {
    if (act === "opendir") api.openDir(path);
    else api.openFile(path);
  };

  return (
    <div className="aux-tab">
      <div className={"act-products " + (prodList.length > 0 ? "act-products-pinned" : "act-products-empty")}>
        <div className="act-products-head">
          <span className="act-products-title">📦 本会话产物（{prodList.length}）</span>
          {prodList.length > 0 && onGoFiles && (
            <button className="msg-op" title="去文件栏管理" onClick={() => onGoFiles()}>去文件 ▸</button>
          )}
        </div>
        {prodList.length > 0 ? (
          <div className="act-prod-list">
            {prodList.map((item, i) => {
              const path = item.path || item;
              const nm = item.name || String(path).split(/[\/]/).pop();
              return (
                <div className="act-prod-chip" key={path} title={path}>
                  <span className="act-prod-name">📄 {nm}</span>
                  <button className="msg-op" title="打开" onClick={() => prodAct(path, "open")}>打开</button>
                  <button className="msg-op" title="打开所在文件夹" onClick={() => prodAct(path, "opendir")}>⌖</button>
                  {onInject && <button className="msg-op" title="读取内容到输入框" onClick={() => onInject(path)}>注入</button>}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="fx-hint">AI 写出文件后会像书架一样列在这里，反复可看。</div>
        )}
      </div>

      <div className="px-head" style={{ marginBottom: 6 }}>
        <b style={{ fontSize: 12.5 }}>{activity && activity.label ? activity.label : "工具活动"}</b>
        {steps.length > 0 && (
          <span className={"px-badge " + (streaming ? "px-badge-run" : failed ? "px-badge-exit" : "px-badge-ok")}>
            {streaming ? `⏳ ${done}/${steps.length}` : failed ? `⚠ ${failed} 失败` : `✓ ${steps.length} 完成`}
          </span>
        )}
      </div>

      {steps.length === 0 ? (
        <div className="fx-hint">AI 调用工具时会实时显示在这里（不占用聊天正文）。</div>
      ) : (
        <div className="act-list">
          {steps.map((s, i) => {
            const isOpen = expanded === i;
            const isRun = s.status === "running";
            return (
              <div key={i} className={"act-item " + (isOpen ? "act-open " : "") + s.status}>
                <button className="act-row" onClick={() => setExpanded(isOpen ? null : i)}>
                  <span className="act-icon">
                    {isRun ? <span className="act-spin" /> : s.status === "done" ? "✓" : s.status === "failed" ? "✕" : "…"}
                  </span>
                  <span className="act-name">{s.tool}</span>
                  <span className="act-meta">
                    {s.status === "done" && s.duration ? `${s.duration}s` : ""}
                    {isRun ? "执行中…" : s.status === "failed" ? "失败" : ""}
                  </span>
                  <span className="act-chev">{isOpen ? "▾" : "▸"}</span>
                </button>
                {isOpen && (
                  <div className="act-detail">
                    {s.argsText && <div className="act-args">参数：{s.argsText}</div>}
                    {s.result != null && (
                      <pre className="act-result">{String(s.result)}</pre>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
export default function AuxPanel({ onClose, onInjectFile, activity, products, onOpenChat, tab, onTabChange }) {
  const isControlled = tab != null && typeof onTabChange === "function";
  const [internalTab, setInternalTab] = React.useState("params");
  const curTab = isControlled ? tab : internalTab;
  const setCurTab = isControlled ? onTabChange : setInternalTab;
  const wasStreaming = React.useRef(false);
  // 仅当"从空闲→开始执行"这一跳自动切到「活动」，避免执行中反复跳 tab 打扰
  const hasRun = !!(activity && activity.steps && activity.steps.length > 0 && activity.streaming);
  React.useEffect(() => {
    if (hasRun) {
      if (!wasStreaming.current) setCurTab("activity");
      wasStreaming.current = true;
    } else {
      wasStreaming.current = false; // 任务结束/无执行 → 复位，下一任务到来再自动切
    }
  }, [hasRun, setCurTab]);
  return (
    <aside className="aux-panel">
      <div className="aux-head">
        <b>控制台</b>
        <button className="icon-btn" onClick={onClose}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="aux-tabs">
        <button className={`aux-tab-btn ${curTab === "activity" ? "aux-tab-on" : ""}`}
          onClick={() => setCurTab("activity")}
          title="AI 工具调用链（实时，点开看细节）">🔧 活动
          {activity && activity.streaming && <span className="aux-tab-dot" aria-hidden="true" />}
        </button>
        <button className={`aux-tab-btn ${curTab === "params" ? "aux-tab-on" : ""}`} onClick={() => setCurTab("params")}>🎛 参数</button>
        <button className={`aux-tab-btn ${curTab === "files" ? "aux-tab-on" : ""}`} onClick={() => setCurTab("files")}>📂 文件</button>
        <button className={`aux-tab-btn ${curTab === "procs" ? "aux-tab-on" : ""}`} onClick={() => setCurTab("procs")}>⚙ 进程</button>
      </div>
      <div className="aux-body">
        {curTab === "activity" && <ActivityTab activity={activity} products={products || []} onOpenChatTools={onOpenChat} onGoFiles={() => setCurTab("files")} onInject={onInjectFile} />}
        {curTab === "params" && <ParamsTab />}
        {curTab === "files" && <FilesTab onInject={onInjectFile} />}
        {curTab === "procs" && <ProcessesTab />}
      </div>
    </aside>
  );
}