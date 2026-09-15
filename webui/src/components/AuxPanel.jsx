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

// ── 可见感知轮询：仅在「页签激活 + 文档可见」时拉取；切回即刷新一次 ──
// 常驻挂载的页签不再无条件轮询，也不因切换而丢失展开/滚动状态。
function useVisiblePolling(fn, interval, active) {
  const fnRef = React.useRef(fn);
  fnRef.current = fn;
  React.useEffect(() => {
    if (!active) return undefined;
    const tick = () => {
      if (typeof document === "undefined" || document.visibilityState === "visible") fnRef.current();
    };
    tick();
    const iv = setInterval(tick, interval);
    const onVis = () => { if (document.visibilityState === "visible") fnRef.current(); };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(iv);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [active, interval]);
}

// ═══ 第四栏 · 控制台（活动 / 参数 / 文件 / 进程）══════

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
      <button
        className={`toggle ${on ? "toggle-on" : ""}`}
        role="switch"
        aria-checked={!!on}
        aria-label={label}
        onClick={onClick}
      >
        <span className="toggle-knob" />
      </button>
    </div>
  );
}

// 复制到剪贴板（带兜底）
function copyText(text) {
  const s = String(text == null ? "" : text);
  if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(s);
  return Promise.reject(new Error("clipboard unavailable"));
}

// ── 📂 文件：树 + 最近产物（打开/定位/注入）──────────
function FilesTab({ onInject, active, onBadge }) {
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
        const m = {};
        (d.favs || []).forEach((f) => { m[f.path] = f; });
        setFavs(m);
        setErr("");
      } else if (showLoading) setErr("文件列表加载失败：后端未连接");
    } catch {
      if (showLoading) setErr("文件列表加载失败：后端未连接");
    }
  }, []);

  // 仅当「文件」页签激活时轮询；切回即刷新
  useVisiblePolling(() => load(false), 8000, active);

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
      setFavs((prev) => {
        const next = { ...(prev || {}) };
        if (r.faved) next[path] = { path, name: String(path).split(/[\\/]/).pop() };
        else delete next[path];
        return next;
      });
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
      } catch (e) { silentWarn(e, "AuxPanel"); flashErr(e); }
      setLoading((l) => ({ ...l, [path]: false }));
    }
  };

  const favIcon = (path) => (favs && favs[path] ? "★" : "☆");
  const isFav = (path) => !!(favs && favs[path]);

  const fileRow = (meta, indent) => {
    const p = meta.path;
    const missing = meta.exists === false;
    return (
      <div className={`fx-row ${missing ? "fx-missing" : ""}`} style={{ paddingLeft: 6 + (indent || 0) * 14 }}>
        <button className={`fx-fav ${isFav(p) ? "fx-fav-on" : ""}`} title={isFav(p) ? "取消收藏" : "收藏"}
          aria-label={isFav(p) ? "取消收藏" : "收藏"}
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

  const entry = (e, depth) => {
    if (!e || !e.path) return null;
    const p = e.path;
    if (e.is_dir) {
      return (
        <div key={p} className="fx-row" style={{ paddingLeft: 6 + depth * 14 }}>
          <button className={`fx-fav ${isFav(p) ? "fx-fav-on" : ""}`} title={isFav(p) ? "取消收藏" : "收藏文件夹"}
            aria-label={isFav(p) ? "取消收藏" : "收藏"}
            onClick={(ev) => { ev.stopPropagation(); toggleFav(p); }}>{favIcon(p)}</button>
          <button className="fx-dir" title={p} aria-expanded={!!expanded[p]} onClick={() => toggle(p)}>
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
    const lq = q.toLowerCase();
    return items
      .filter((e) => !q || e.name.toLowerCase().includes(lq))
      .map((e) => (
        <React.Fragment key={e.path}>
          {entry(e, depth + 1)}
          {e.is_dir && renderDir(e.path, depth + 1)}
        </React.Fragment>
      ));
  };

  const now = Date.now();
  const justCut = 2 * 60 * 1000;
  const dayCut = 24 * 3600 * 1000;
  const bucket = (m) => {
    const t = m.mtime ? m.mtime * 1000 : (m.mtime_epoch ? m.mtime_epoch * 1000 : 0);
    if (!t) return "old";
    if (now - t < justCut) return "just";
    if (now - t < dayCut && new Date(t).getDate() === new Date().getDate()) return "today";
    return "old";
  };

  const favList = favs ? Object.values(favs) : [];
  const recent = roots ? roots.recent || [] : [];
  const lq = q.toLowerCase();
  const recentFiltered = recent.filter((m) => !q || (m.name || "").toLowerCase().includes(lq));
  const just = recentFiltered.filter((m) => bucket(m) === "just");
  const today = recentFiltered.filter((m) => bucket(m) === "today");
  const older = recentFiltered.filter((m) => bucket(m) === "old" && m.exists);

  // 徽标：新鲜产物（刚刚 + 今天）
  const freshCount = just.length + today.length;
  React.useEffect(() => { if (onBadge) onBadge("files", freshCount); }, [freshCount, onBadge]);

  const group = (title, list, emptyHint) => (
    <div key={title}>
      <div className="fx-root">{title}（{list.length}）</div>
      {list.length > 0 ? list.map((m, i) => (
        <React.Fragment key={m.path + i}>{fileRow(m, 0)}</React.Fragment>
      )) : emptyHint && <div className="fx-hint">{emptyHint}</div>}
    </div>
  );

  return (
    <div className="aux-tab">
      <div className="fx-search">
        <input className="fx-q" placeholder="搜索文件/目录…" aria-label="搜索文件或目录" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="msg-op" title="刷新" aria-label="刷新文件列表" onClick={() => load(true)}>⟳</button>
      </div>
      {!roots && !err && <div className="empty-tip is-loading">正在加载文件…</div>}
      {err && <div className="empty-tip is-err">{err}</div>}
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
          {(() => {
            const rootEntries = (roots.entries || []).filter((e) => !q || e.name.toLowerCase().includes(lq));
            if (q && rootEntries.length === 0) return <div className="fx-hint">工作区无匹配项</div>;
            return (roots.entries || []).map((e) => {
              if (q && !e.name.toLowerCase().includes(lq)) {
                return e.is_dir ? (
                  <React.Fragment key={e.path}>
                    <div style={{ paddingLeft: 6 }} className="fx-row">
                      <button className="fx-dir" aria-expanded={!!expanded[e.path]} onClick={() => toggle(e.path)}>{expanded[e.path] ? "▾" : "▸"} 📁 {e.name}</button>
                    </div>
                    {renderDir(e.path, 0)}
                  </React.Fragment>
                ) : null;
              }
              return (
                <React.Fragment key={e.path}>
                  {entry(e, 0)}
                  {e.is_dir && renderDir(e.path, 0)}
                </React.Fragment>
              );
            });
          })()}
        </>
      )}
    </div>
  );
}

// fmtRel：把后端可能给的 "MM-DD HH:MM" 标签或 epoch(秒) 统一成相对/绝对时间串
function fmtRel(m) {
  if (!m) return "";
  if (typeof m !== "number") return m; // 已是标签
  const t = m < 1e12 ? m * 1000 : m;
  const d = new Date(t);
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// ── ⚙ 进程：终端输出 ───────────────────────────────
function ProcessesTab({ active, onBadge }) {
  const [procs, setProcs] = React.useState({});
  const [current, setCurrent] = React.useState(null);
  const [follow, setFollow] = React.useState(true);
  const [cmd, setCmd] = React.useState("");
  const [cwd, setCwd] = React.useState("");
  const [adv, setAdv] = React.useState(false);
  const [tip, setTip] = React.useState("");
  const [hist, setHist] = React.useState([]);   // 用户启动过的命令（旧→新）
  const [histIdx, setHistIdx] = React.useState(-1);
  const [cleared, setCleared] = React.useState(0); // 本地清屏游标

  const load = React.useCallback(async () => {
    try {
      const d = await api.listProcesses();
      setProcs(d.processes || {});
      setCurrent((c) => (c && d.processes[c] ? c : Object.keys(d.processes)[0] || null));
    } catch (e) { silentWarn(e, "AuxPanel"); }
  }, []);

  useVisiblePolling(load, 2500, active);

  const cur = current ? procs[current] : null;
  const allLines = cur?.lines || [];
  const lines = cleared > 0 ? allLines.slice(cleared) : allLines;

  // 徽标：运行中的进程数
  const running = Object.values(procs).filter((p) => p && !p.exited).length;
  React.useEffect(() => { if (onBadge) onBadge("procs", running); }, [running, onBadge]);

  React.useEffect(() => { setCleared(0); }, [current]);

  const flash = (t) => { setTip(t); setTimeout(() => setTip(""), 3000); };

  const stop = async () => {
    if (!current) return;
    try {
      const r = await api.stopProcess(current);
      if (r && r.ok === false) flash("❌ " + (r.error || "停止失败"));
    } catch (e) { flash("❌ 停止失败：" + (e && e.message ? e.message : "")); }
  };

  const start = async () => {
    const c = cmd.trim();
    if (!c) return;
    try {
      const r = await api.startProcess(c, { cwd: cwd.trim() || undefined });
      if (r && r.ok === false) { flash("❌ " + (r.error || "启动失败")); return; }
      setHist((h) => [...h, c].slice(-30));
      setHistIdx(-1);
      setCmd("");
    } catch (e) { flash("❌ 启动失败：" + (e && e.message ? e.message : "")); }
  };

  const onCmdKey = (e) => {
    if (e.key === "Enter") { start(); return; }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!hist.length) return;
      const idx = histIdx < 0 ? hist.length - 1 : Math.max(0, histIdx - 1);
      setHistIdx(idx);
      setCmd(hist[idx]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (histIdx < 0) return;
      const idx = histIdx + 1;
      if (idx >= hist.length) { setHistIdx(-1); setCmd(""); }
      else { setHistIdx(idx); setCmd(hist[idx]); }
    }
  };

  const errPat = /Traceback|Error|ERROR|Exception|失败|错误|refused|NotImplemented/;

  return (
    <div className="aux-tab">
      <div className="px-head">
        <select className="set-select px-combo" aria-label="选择进程" value={current || ""} onChange={(e) => setCurrent(e.target.value)}>
          {Object.keys(procs).map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
          {Object.keys(procs).length === 0 && <option value="">无进程</option>}
        </select>
        <button className="confirm-btn px-stop" onClick={stop} disabled={!cur || cur.exited}>■ 停止</button>
      </div>

      {cur && (
        <>
          <div className="px-info">
            pid {cur.pid} · 启动 {cur.started}
            <span className={`px-badge ${cur.exited ? "px-badge-exit" : "px-badge-run"}`}>
              {cur.exited ? `■ 已退出 code=${cur.code}` : "● 运行中"}
            </span>
          </div>
          {cur.command && (
            <div className="px-cmd">
              <code title={cur.command}>{cur.command}</code>
              <button
                className="msg-op"
                title="以相同命令重新启动"
                onClick={async () => {
                  try {
                    const r = await api.startProcess(cur.command, { cwd: cur.cwd || undefined });
                    if (r && r.ok === false) flash("❌ " + (r.error || "重启失败"));
                  } catch { flash("❌ 重启失败"); }
                }}
              >重启</button>
            </div>
          )}
        </>
      )}

      <div className="px-term-bar">
        <button className="msg-op" onClick={() => setFollow(!follow)} aria-pressed={follow} title="新输出时自动滚到底">
          {follow ? "自动跟随 ✓" : "自动跟随"}
        </button>
        <button className="msg-op" title="复制全部输出" onClick={() => copyText(lines.join("\n")).catch(() => flash("❌ 复制失败"))}>复制</button>
        <button className="msg-op" title="仅清空本面板显示（不影响进程）" onClick={() => setCleared(allLines.length)}>清屏</button>
      </div>

      <div className="px-term" ref={(el) => { if (el && follow) el.scrollTop = el.scrollHeight; }}>
        {lines.length === 0 && <div className="fx-hint">暂无输出（AI 用 start_process 启动进程后显示在这里）</div>}
        {lines.map((l, i) => (
          <div key={i} className={`px-line ${errPat.test(l) ? "px-err" : l.startsWith("──") ? "px-meta" : ""}`}>{l}</div>
        ))}
      </div>

      {adv && (
        <input
          className="tf-input px-cwd"
          placeholder="工作目录（可选，默认工作区）"
          value={cwd}
          onChange={(e) => setCwd(e.target.value)}
        />
      )}
      <div className="px-start">
        <input
          className="tf-input"
          placeholder="启动命令，如 python -m http.server 8000（↑↓ 调历史）"
          aria-label="启动命令"
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={onCmdKey}
        />
        <button className="msg-op px-adv" title="工作目录" aria-pressed={adv} onClick={() => setAdv(!adv)}>⌂</button>
        <button className="confirm-btn confirm-primary" onClick={start} disabled={!cmd.trim()}>▶</button>
      </div>
      {tip && <div className="px-tip" style={{ color: "var(--danger-text)" }}>{tip}</div>}
    </div>
  );
}

// ── 🎛 控制台：模型/思考/场景/外观/状态 ─────────────
const THEME_CHOICES = [
  { id: "starfield", name: "星空", desc: "极黑冷底 · 亮青点缀" },
  { id: "deepsea", name: "深海", desc: "深蓝底 · 湖蓝光" },
  { id: "arctic", name: "北极冰", desc: "冰白底 · 深海蓝字" },
];

function ParamsTab({ active }) {
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
      if (d) set(d);
      else api[fn]().then((x) => x && set(x)).catch(() => setLoadErr(`「${fn}」加载失败：后端未连接`));
    };
    prefetchPromise.then(() => {
      apply(PREFETCHED.cfg, setCfg, "getConfig");
      apply(PREFETCHED.ctx, setCtx, "getContext");
      apply(PREFETCHED.st, setSt, "getStatus");
    });
  }, []);

  // 切回「参数」页签时刷新运行态统计（上下文/工具/记忆/成本），避免长期陈旧
  React.useEffect(() => {
    if (!active) return;
    api.getContext().then((d) => d && setCtx(d)).catch(() => {});
    api.getStatus().then((d) => d && setSt(d)).catch(() => {});
  }, [active]);

  const edit = (patch) => setDraft((d) => ({ ...(d || {}), ...patch }));

  const save = async () => {
    if (!draft || saving) return;
    setSaving(true);
    try {
      const d = await api.saveConfig(draft);
      if (d && d.ok) {
        setCfg((c) => ({ ...c, ...draft }));
        if ("model" in draft) setCustomModel(!cfg.models.includes(draft.model));
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

  const dirty = draft != null;

  if (!cfg) {
    return (
      <div className="aux-tab">
        {loadErr
          ? <div className="empty-tip is-err">{loadErr}　<button className="msg-op" onClick={() => window.location.reload()}>重试</button></div>
          : <div className="empty-tip is-loading">正在加载配置…</div>}
      </div>
    );
  }

  const cur = { ...cfg, ...(draft || {}) };
  const modelOptions = Array.isArray(cfg.models) && cfg.models.length ? cfg.models : [];
  const ctxTools = Array.isArray(ctx?.tools) ? ctx.tools : [];
  const ctxMemCount = ctx?.memory?.count ?? (Array.isArray(ctx?.memory) ? ctx.memory.length : 0);
  const ctxUsage = ctx?.usage || {};
  const ctxPct = Math.min(100, Math.max(0, Math.round(((ctxUsage.prompt || 0) / (ctxUsage.max || 1000000 || 1)) * 100)));
  const hasKey = cfg.has_key;
  // 思考档开启时，采样参数由模型接管——置灰并说明，避免误解
  const samplingLocked = !!cur.thinking && cur.thinking !== "none";

  const displayModes = [
    { id: "compact", name: "紧凑" },
    { id: "comfort", name: "舒适" },
    { id: "loose", name: "宽松" },
  ];

  return (
    <div className="aux-tab">
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

      <Group
        title="🌡 采样参数"
        right={samplingLocked
          ? <span title="当前思考档开启，采样参数由模型控制；切到 none 可手动调节">由思考档接管</span>
          : "无思考档时生效"}
      >
        <div className={`px-grid2 ${samplingLocked ? "px-locked" : ""}`}>
          <Field label="温度" hint="0-2">
            <input className="tf-input px-num" type="number" step="0.1" min="0" max="2" disabled={samplingLocked} title={samplingLocked ? "思考档开启时由模型控制" : ""} value={cur.temperature} onChange={(e) => edit({ temperature: Number(e.target.value) })} />
          </Field>
          <Field label="Top-P" hint="0-1">
            <input className="tf-input px-num" type="number" step="0.05" min="0" max="1" disabled={samplingLocked} title={samplingLocked ? "思考档开启时由模型控制" : ""} value={cur.top_p} onChange={(e) => edit({ top_p: Number(e.target.value) })} />
          </Field>
          <Field label="输出上限" hint="单次回复 tokens">
            <input className="tf-input px-num" type="number" step="1024" min="1024" max="393216" value={cur.max_tokens} onChange={(e) => edit({ max_tokens: Number(e.target.value) })} />
          </Field>
          <Field label="Seed" hint="0=随机">
            <input className="tf-input px-num" type="number" disabled={samplingLocked} title={samplingLocked ? "思考档开启时由模型控制" : ""} value={cur.seed} onChange={(e) => edit({ seed: Number(e.target.value) })} />
          </Field>
        </div>
      </Group>

      <Group title="🧩 功能开关">
        <TglRow label="JSON 输出" hint="response_format，失败自动重试" on={!!cur.json_output} onClick={() => edit({ json_output: !cur.json_output })} />
        <TglRow label="Beta API" hint="前缀续写 / FIM 补全" on={!!cur.beta_api} onClick={() => edit({ beta_api: !cur.beta_api })} />
        <TglRow label="strict 工具" hint="严格遵循 JSON Schema（自动 Beta）" on={!!cur.strict_tools} onClick={() => edit({ strict_tools: !cur.strict_tools })} />
        <TglRow label="工具开关" hint="向模型暴露工具定义" on={!!cur.tools_enabled} onClick={() => edit({ tools_enabled: !cur.tools_enabled })} />
      </Group>

      <Group title="🎨 外观">
        <Field label="风格">
          <div className="px-themes">
            {THEME_CHOICES.map((t) => (
              <button
                key={t.id}
                className={`px-theme ${theme === t.id ? "px-theme-on" : ""}`}
                title={t.desc}
                aria-label={`切换到${t.name}主题`}
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
const RESULT_MAX = 2400; // 单步结果超过此长度先截断，避免大结果卡顿

function ActivityTab({ activity, products, onGoFiles, onInject }) {
  const [expanded, setExpanded] = React.useState(null);
  const [fullResult, setFullResult] = React.useState({});
  const [prodOpen, setProdOpen] = React.useState(true);
  const [copied, setCopied] = React.useState("");
  const steps = (activity && activity.steps) || [];
  const streaming = !!(activity && activity.streaming);
  const done = steps.filter((s) => s.status === "done").length;
  const failed = steps.filter((s) => s.status === "failed").length;

  // 新任务开始时收起上一步的展开（依赖稳定 taskId，此前恒为 undefined 导致从不重置）
  React.useEffect(() => {
    setExpanded(null);
    setFullResult({});
  }, [activity && activity.taskId]);

  const prodList = Array.isArray(products) ? products : [];
  const prodAct = (path, act) => {
    const p = act === "opendir" ? api.openDir(path) : api.openFile(path);
    p && p.catch && p.catch(() => {});
  };

  const flashCopy = (key) => { setCopied(key); setTimeout(() => setCopied(""), 1500); };

  const copyStep = (s, i) => {
    const txt = `# ${s.tool}\n参数：${s.argsText || "—"}\n结果：\n${s.result == null ? "" : String(s.result)}`;
    copyText(txt).then(() => flashCopy("step" + i)).catch(() => {});
  };
  const copyAll = () => {
    const txt = steps.map((s) => `[${s.status}] ${s.tool}${s.duration ? ` (${s.duration}s)` : ""}\n  ${s.argsText || ""}\n  ${s.result == null ? "" : String(s.result)}`).join("\n\n");
    copyText(txt).then(() => flashCopy("all")).catch(() => {});
  };

  return (
    <div className="aux-tab">
      <div className={"act-products " + (prodList.length > 0 ? "act-products-pinned" : "act-products-empty")}>
        <div className="act-products-head">
          <button className="act-products-toggle" aria-expanded={prodOpen} onClick={() => setProdOpen(!prodOpen)}>
            {prodOpen ? "▾" : "▸"} 📦 本会话产物（{prodList.length}）
          </button>
          {prodList.length > 0 && onGoFiles && (
            <button className="msg-op" title="去文件栏管理" onClick={() => onGoFiles()}>去文件 ▸</button>
          )}
        </div>
        {prodOpen && (prodList.length > 0 ? (
          <div className="act-prod-list">
            {prodList.map((item) => {
              const path = item.path || item;
              const nm = item.name || String(path).split(/[\\/]/).pop();
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
        ))}
      </div>

      <div className="px-head" style={{ marginBottom: 6 }}>
        <b className="act-title">{activity && activity.label ? activity.label : "工具活动"}</b>
        {steps.length > 0 && (
          <span className={"px-badge " + (streaming ? "px-badge-run" : failed ? "px-badge-exit" : "px-badge-ok")}>
            {streaming ? `⏳ ${done}/${steps.length}` : failed ? `⚠ ${failed} 失败` : `✓ ${steps.length} 完成`}
          </span>
        )}
        {steps.length > 0 && (
          <button className="msg-op" title="复制全部步骤" onClick={copyAll}>{copied === "all" ? "已复制" : "复制全部"}</button>
        )}
      </div>

      {steps.length === 0 ? (
        <div className="fx-hint">AI 调用工具时会实时显示在这里（不占用聊天正文）。</div>
      ) : (
        <div className="act-list">
          {steps.map((s, i) => {
            const isOpen = expanded === i;
            const isRun = s.status === "running";
            const raw = s.result == null ? "" : String(s.result);
            const isLong = raw.length > RESULT_MAX;
            const shown = isLong && !fullResult[i] ? raw.slice(0, RESULT_MAX) + "\n…（已截断）" : raw;
            return (
              <div key={i} className={"act-item " + (isOpen ? "act-open " : "") + s.status}>
                <button className="act-row" aria-expanded={isOpen} onClick={() => setExpanded(isOpen ? null : i)}>
                  <span className="act-icon" aria-hidden="true">
                    {isRun ? <span className="act-spin" /> : s.status === "done" ? "✓" : s.status === "failed" ? "✕" : "…"}
                  </span>
                  <span className="act-name">{s.tool}</span>
                  <span className="act-meta">
                    {s.status === "done" && s.duration ? `${s.duration}s` : ""}
                    {isRun ? "执行中…" : s.status === "failed" ? "失败" : ""}
                  </span>
                  <span className="act-chev" aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
                </button>
                {isOpen && (
                  <div className="act-detail">
                    {s.argsText && <div className="act-args">参数：{s.argsText}</div>}
                    {s.result != null && (
                      <>
                        <pre className="act-result">{shown}</pre>
                        <div className="act-detail-ops">
                          {isLong && (
                            <button className="msg-op" onClick={() => setFullResult((f) => ({ ...f, [i]: !f[i] }))}>
                              {fullResult[i] ? "收起" : `展开全部（${raw.length} 字）`}
                            </button>
                          )}
                          <button className="msg-op" onClick={() => copyStep(s, i)}>{copied === "step" + i ? "已复制" : "复制"}</button>
                        </div>
                      </>
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

// ── 面板容器：常驻挂载 4 页签（保留各自状态）+ 可见感知轮询 + 可拖拽调宽（持久化）──
const AUX_MIN_W = 240;
const AUX_MAX_W = 620;
const AUX_W_KEY = "wt_aux_w";

function clampWidth(w) {
  const maxByView = typeof window !== "undefined" ? Math.max(AUX_MIN_W + 20, window.innerWidth - 480) : AUX_MAX_W;
  return Math.min(Math.min(AUX_MAX_W, maxByView), Math.max(AUX_MIN_W, w));
}

export default function AuxPanel({ onClose, onInjectFile, activity, products, tab, onTabChange }) {
  const isControlled = tab != null && typeof onTabChange === "function";
  const [internalTab, setInternalTab] = React.useState("params");
  const curTab = isControlled ? tab : internalTab;
  const setCurTab = isControlled ? onTabChange : setInternalTab;

  const [badges, setBadges] = React.useState({});
  const onBadge = React.useCallback((id, val) => {
    setBadges((b) => (b[id] === val ? b : { ...b, [id]: val }));
  }, []);

  // 宽度：localStorage 持久化 + 拖拽（面板在右侧，左边框为拖拽区）
  const [width, setWidth] = React.useState(() => {
    try {
      const v = Number(localStorage.getItem(AUX_W_KEY));
      return Number.isFinite(v) && v > 0 ? clampWidth(v) : 300;
    } catch { return 300; }
  });
  const widthRef = React.useRef(width);
  widthRef.current = width;
  const startResize = (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = widthRef.current;
    const prevUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    const onMove = (ev) => setWidth(clampWidth(startW + (startX - ev.clientX)));
    const onUp = () => {
      document.body.style.userSelect = prevUserSelect;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      try { localStorage.setItem(AUX_W_KEY, String(widthRef.current)); } catch { /* 存储不可用不致命 */ }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // 空闲→开始执行 时自动切到「活动」（不打断执行中）
  const wasStreaming = React.useRef(false);
  const hasRun = !!(activity && activity.steps && activity.steps.length > 0 && activity.streaming);
  React.useEffect(() => {
    if (hasRun) {
      if (!wasStreaming.current) setCurTab("activity");
      wasStreaming.current = true;
    } else {
      wasStreaming.current = false;
    }
  }, [hasRun, setCurTab]);

  const activityFailed = (activity && activity.steps || []).filter((s) => s.status === "failed").length;
  const tabBadge = (id) => {
    const n = badges[id];
    return Number.isFinite(n) && n > 0 ? <span className="aux-tab-badge" aria-hidden="true">{n}</span> : null;
  };
  const tabs = [
    { id: "activity", label: "🔧 活动", title: "AI 工具调用链（实时，点开看细节）" },
    { id: "params", label: "🎛 参数", title: "模型 / 采样 / 外观 / 运行状态" },
    { id: "files", label: "📂 文件", title: "工作区文件与最近产物" },
    { id: "procs", label: "⚙ 进程", title: "后台进程与终端输出" },
  ];

  return (
    <aside className="aux-panel" style={{ "--aux-w": width + "px" }}>
      <div className="aux-resize" role="separator" aria-orientation="vertical" aria-label="调整面板宽度" onMouseDown={startResize} />
      <div className="aux-head">
        <b>控制台</b>
        <button className="icon-btn" onClick={onClose} title="关闭" aria-label="关闭">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="aux-tabs" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={"auxtab-" + t.id}
            aria-selected={curTab === t.id}
            aria-controls={"auxpane-" + t.id}
            className={`aux-tab-btn ${curTab === t.id ? "aux-tab-on" : ""}`}
            onClick={() => setCurTab(t.id)}
            title={t.title}
          >
            {t.label}
            {t.id === "activity" && activity && activity.streaming && <span className="aux-tab-dot" aria-hidden="true" />}
            {t.id === "activity" && !activity?.streaming && activityFailed > 0 && <span className="aux-tab-badge aux-tab-badge-err" aria-hidden="true">{activityFailed}</span>}
            {t.id === "files" && tabBadge("files")}
            {t.id === "procs" && tabBadge("procs")}
          </button>
        ))}
      </div>
      <div className="aux-body">
        <div role="tabpanel" id="auxpane-activity" aria-labelledby="auxtab-activity" className="aux-pane" hidden={curTab !== "activity"}>
          <ActivityTab activity={activity} products={products || []} onGoFiles={() => setCurTab("files")} onInject={onInjectFile} />
        </div>
        <div role="tabpanel" id="auxpane-params" aria-labelledby="auxtab-params" className="aux-pane" hidden={curTab !== "params"}>
          <ParamsTab active={curTab === "params"} />
        </div>
        <div role="tabpanel" id="auxpane-files" aria-labelledby="auxtab-files" className="aux-pane" hidden={curTab !== "files"}>
          <FilesTab onInject={onInjectFile} active={curTab === "files"} onBadge={onBadge} />
        </div>
        <div role="tabpanel" id="auxpane-procs" aria-labelledby="auxtab-procs" className="aux-pane" hidden={curTab !== "procs"}>
          <ProcessesTab active={curTab === "procs"} onBadge={onBadge} />
        </div>
      </div>
    </aside>
  );
}
