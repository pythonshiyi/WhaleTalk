import React from "react";
import * as api from "../api.js";
import { ThemeContext, DisplayContext } from "../App.jsx";

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

  React.useEffect(() => {
    api.api("/v1/files").then((d) => d && setRoots(d)).catch(() => {});
  }, []);

  const openFile = async (path) => {
    if (!path || busyPath) return;
    setBusyPath(path);
    try {
      await api.api("/v1/files/open", { method: "POST", body: JSON.stringify({ path }) });
    } catch (e) {
      setErr(e.message || "打开失败");
      setTimeout(() => setErr(""), 3000);
    } finally {
      setBusyPath(null);
    }
  };

  const openDir = async (path) => {
    if (!path || busyPath) return;
    setBusyPath(path);
    try {
      await api.api("/v1/files/opendir", { method: "POST", body: JSON.stringify({ path }) });
    } catch (e) {
      setErr(e.message || "打开文件夹失败");
      setTimeout(() => setErr(""), 3000);
    } finally {
      setBusyPath(null);
    }
  };

  const toggle = async (path) => {
    const next = { ...expanded, [path]: !expanded[path] };
    setExpanded(next);
    if (next[path] && !children[path]) {
      setLoading((l) => ({ ...l, [path]: true }));
      try {
        const d = await api.api(`/v1/files?dir=${encodeURIComponent(path)}`);
        if (d && d.entries) setChildren((c) => ({ ...c, [path]: d.entries }));
      } catch {}
      setLoading((l) => ({ ...l, [path]: false }));
    }
  };

  const entry = (e, depth) => (
    <div key={e.path} className="fx-row" style={{ paddingLeft: 6 + depth * 14 }}>
      {e.is_dir ? (
        <button className="fx-dir" title={e.path} onClick={() => toggle(e.path)}>
          {expanded[e.path] ? "▾" : "▸"} 📁 {e.name}
        </button>
      ) : (
        <span className="fx-file">
          <span className="fx-fname" title={e.path} onDoubleClick={() => openFile(e.path)}>📄 {e.name}</span>
          <button className="fx-act" title="打开文件" onClick={() => openFile(e.path)}>打开</button>
          <button className="fx-act" title="打开所在文件夹" onClick={() => openDir(e.path)}>⌖</button>
          <button className="fx-act" title="读取内容到输入框" onClick={() => onInject && onInject(e.path)}>注入</button>
        </span>
      )}
      {e.is_dir && (
        <button className="fx-act" title="打开该文件夹" onClick={() => openDir(e.path)}>⌖</button>
      )}
    </div>
  );

  const renderDir = (path, depth) => {
    if (!expanded[path]) return null;
    const items = children[path];
    if (!items) return <div className="fx-row fx-hint" style={{ paddingLeft: 20 + depth * 14 }}>{loading[path] ? "加载中…" : "空"}</div>;
    return items.map((e) => (
      <React.Fragment key={e.path}>
        {entry(e, depth + 1)}
        {e.is_dir && renderDir(e.path, depth + 1)}
      </React.Fragment>
    ));
  };

  return (
    <div className="aux-tab">
      {roots && (
        <>
          <div className="fx-root">
            <span title={roots.active_dir}>{roots.active_dir}</span>
            {roots.active_dir && (
              <button className="fx-act" title="打开工作区文件夹" onClick={() => openDir(roots.active_dir)}>打开</button>
            )}
          </div>
          <div className="fx-root">⭐ 最近产物（{roots.recent?.length || 0}）</div>
          <div className="fx-recent">
            {(roots.recent || []).slice(0, 8).map((r, i) => (
              <div className="fx-row" key={i} title={r}>
                <span className="fx-fname" onDoubleClick={() => openFile(r)}>📦 {String(r).split(/[\\/]/).pop()}</span>
                <button className="fx-act" title="打开文件" onClick={() => openFile(r)}>打开</button>
                <button className="fx-act" title="打开所在文件夹" onClick={() => openDir(r)}>⌖</button>
                <button className="fx-act" title="读取内容到输入框" onClick={() => onInject && onInject(r)}>注入</button>
              </div>
            ))}
          </div>
          <div className="fx-root">📁 工作区</div>
          {(roots.entries || []).map((e) => (
            <React.Fragment key={e.path}>
              {entry(e, 0)}
              {e.is_dir && renderDir(e.path, 0)}
            </React.Fragment>
          ))}
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
        const d = await api.api("/v1/processes");
        if (!alive) return;
        setProcs(d.processes || {});
        setCurrent((c) => (c && d.processes[c] ? c : Object.keys(d.processes)[0] || null));
      } catch {}
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
      await api.api("/v1/processes/stop", { method: "POST", body: JSON.stringify({ name: current }) });
    } catch {}
  };

  const start = async () => {
    if (!cmd.trim()) return;
    try {
      const r = await api.api("/v1/processes/start", { method: "POST", body: JSON.stringify({ command: cmd.trim() }) });
      setCmd("");
    } catch {}
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
  const [tip, setTip] = React.useState("");
  const [ctx, setCtx] = React.useState(PREFETCHED.ctx);
  const [st, setSt] = React.useState(PREFETCHED.st);
  const [customModel, setCustomModel] = React.useState(false);

  React.useEffect(() => {
    const apply = (d, set, fn) => {
      if (d) {
        set(d);
      } else {
        api[fn]().then((d) => d && set(d)).catch(() => {});
      }
    };
    prefetchPromise.then(() => {
      apply(PREFETCHED.cfg, setCfg, "getConfig");
      apply(PREFETCHED.ctx, setCtx, "getContext");
      apply(PREFETCHED.st, setSt, "getStatus");
    });
  }, []);

  const save = async (patch) => {
    setCfg((c) => ({ ...c, ...patch }));
    try {
      const d = await api.api("/v1/config", { method: "POST", body: JSON.stringify(patch) });
      if (d && d.ok) {
        setTip("已保存");
        setTimeout(() => setTip(""), 1500);
      }
    } catch {}
  };

  if (!cfg) return <div className="aux-tab fx-hint">加载中…</div>;

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
        <Field label="模型" hint="Switch 即切换，回车自定义">
          {customModel || !modelOptions.includes(cfg.model) ? (
            <input
              className="tf-input px-sel"
              value={cfg.model || ""}
              placeholder="输入任意模型名"
              onChange={(e) => save({ model: e.target.value })}
              onBlur={(e) => e.target.value.trim() && save({ model: e.target.value.trim() })}
            />
          ) : (
            <select
              className="set-select px-sel"
              value={cfg.model}
              onChange={(e) => {
                if (e.target.value === "__custom__") setCustomModel(true);
                else save({ model: e.target.value });
              }}
            >
              {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
              <option value="__custom__">✎ 自定义模型名…</option>
            </select>
          )}
        </Field>
        <div className="px-grid2">
          <Field label="思考档" hint={cfg.thinking === "auto" ? "按任务复杂度智能路由" : "none/low/medium/high/xhigh/max"}>
            <select className="set-select px-sel" value={cfg.thinking} onChange={(e) => save({ thinking: e.target.value })}>
              {(Array.isArray(cfg.thinking_modes) ? cfg.thinking_modes : []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="场景" hint="通用/编程/Agent（预设参数）">
            <select className="set-select px-sel" value={cfg.scenario} onChange={(e) => save({ scenario: e.target.value })}>
              {(Array.isArray(cfg.scenarios) ? cfg.scenarios : []).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>
        </div>
        <Field label="API 网关" hint="OpenAI 兼容 base_url">
          <input className="tf-input px-sel" value={cfg.base_url || ""} placeholder="https://api.deepseek.com" onChange={(e) => save({ base_url: e.target.value })} />
        </Field>
      </Group>

      {/* ── 输出采样 ── */}
      <Group title="🌡 采样参数" right={cfg.thinking === "none" ? "无思考档时生效" : "思考档开启时由模型控制"}>
        <div className="px-grid2">
          <Field label="温度" hint="0-2">
            <input className="tf-input px-num" type="number" step="0.1" min="0" max="2" value={cfg.temperature} onChange={(e) => save({ temperature: Number(e.target.value) })} />
          </Field>
          <Field label="Top-P" hint="0-1">
            <input className="tf-input px-num" type="number" step="0.05" min="0" max="1" value={cfg.top_p} onChange={(e) => save({ top_p: Number(e.target.value) })} />
          </Field>
          <Field label="输出上限" hint="单次回复 tokens">
            <input className="tf-input px-num" type="number" step="1024" min="1024" max="393216" value={cfg.max_tokens} onChange={(e) => save({ max_tokens: Number(e.target.value) })} />
          </Field>
          <Field label="Seed" hint="0=随机">
            <input className="tf-input px-num" type="number" value={cfg.seed} onChange={(e) => save({ seed: Number(e.target.value) })} />
          </Field>
        </div>
      </Group>

      {/* ── 功能开关 ── */}
      <Group title="🧩 功能开关">
        <TglRow label="JSON 输出" hint="response_format，失败自动重试" on={!!cfg.json_output} onClick={() => save({ json_output: !cfg.json_output })} />
        <TglRow label="Beta API" hint="前缀续写 / FIM 补全" on={!!cfg.beta_api} onClick={() => save({ beta_api: !cfg.beta_api })} />
        <TglRow label="strict 工具" hint="严格遵循 JSON Schema（自动 Beta）" on={!!cfg.strict_tools} onClick={() => save({ strict_tools: !cfg.strict_tools })} />
        <TglRow label="工具开关" hint="向模型暴露工具定义" on={!!cfg.tools_enabled} onClick={() => save({ tools_enabled: !cfg.tools_enabled })} />
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

      {tip && <div className="px-tip">{tip}</div>}
    </div>
  );
}

// ── 面板容器（默认「参数」控制台：模型/思考档/场景一秒可切换）──
export default function AuxPanel({ onClose, onInjectFile }) {
  const [tab, setTab] = React.useState("params");
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
        <button className={`aux-tab-btn ${tab === "params" ? "aux-tab-on" : ""}`} onClick={() => setTab("params")}>🎛 参数</button>
        <button className={`aux-tab-btn ${tab === "files" ? "aux-tab-on" : ""}`} onClick={() => setTab("files")}>📂 文件</button>
        <button className={`aux-tab-btn ${tab === "procs" ? "aux-tab-on" : ""}`} onClick={() => setTab("procs")}>⚙ 进程</button>
      </div>
      <div className="aux-body">
        {tab === "params" && <ParamsTab />}
        {tab === "files" && <FilesTab onInject={onInjectFile} />}
        {tab === "procs" && <ProcessesTab />}
      </div>
    </aside>
  );
}