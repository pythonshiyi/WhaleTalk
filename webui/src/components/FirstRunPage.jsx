import React from "react";
import * as api from "../api.js";

// ── 首次启动依赖安装向导 ─────────────────────────────
// 全屏模态：核心组件默认全装 + 可选能力勾选；安装完（或跳过）才进入主界面。
export default function FirstRunPage({ onDone }) {
  const [data, setData] = React.useState(null);       // {first_run, deps:{core,heavy,install}}
  const [err, setErr] = React.useState("");
  const [phase, setPhase] = React.useState("ready");  // ready | installing | done
  const [picked, setPicked] = React.useState(null);   // Set 勾选（core 缺失项默认全选）
  const [prog, setProg] = React.useState({ done: 0, total: 0, current: "" });
  const [logs, setLogs] = React.useState([]);
  const [failed, setFailed] = React.useState([]);
  const [autoErr, setAutoErr] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await api.getFirstRun();
        if (!alive) return;
        setData(d.deps || {});
        const sel = new Set();
        for (const c of d.deps?.core || []) if (!c.ok) sel.add(c.key);
        setPicked(sel);
      } catch (e) {
        if (alive) setErr("无法读取依赖状态：" + e.message);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const coreMiss = (data?.core || []).filter((c) => !c.ok);
  const heavy = data?.heavy || [];

  const toggle = (key) => {
    setPicked((prev) => {
      const n = new Set(prev || []);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  };

  const runInstall = async () => {
    const keys = Array.from(picked || []);
    if (!keys.length) {
      setPhase("done");
      setFailed([]);
      finish(); // 无需安装：直接标记完成并自动进入
      return;
    }
    setPhase("installing");
    setLogs([]);
    setFailed([]);
    setAutoErr("");
    setProg({ done: 0, total: keys.length, current: "" });
    try {
      await api.installMany(keys, {
        onBatchStart: (ev) => setProg({ done: 0, total: ev.total || keys.length, current: "" }),
        onItemStart: (ev) => setProg((p) => ({ ...p, current: ev.label || "" })),
        onLine: (msg) => setLogs((l) => [...l.slice(-60), msg]),
        onItemDone: (ev) => {
          setProg((p) => ({ ...p, done: p.done + 1 }));
          if (!ev.ok) setFailed((f) => [...f, ev.label]);
        },
        onBatchDone: (ev) => {
          setFailed(ev.failed || []);
          setPhase("done");
          finish(); // 安装结束 → 自动标记完成并进入主界面（无需再点击）
        },
        onError: (msg) => {
          setLogs((l) => [...l.slice(-60), "❌ " + msg]);
          setPhase("done");
          finish();
        },
      });
    } catch (e) {
      setLogs((l) => [...l.slice(-60), "❌ 安装中断：" + e.message]);
      setPhase("done");
      finish();
    }
  };

  const finish = async () => {
    setAutoErr("");
    try {
      await api.completeFirstRun();
    } catch (e) {
      setAutoErr("⚠ 未能记录首次完成状态（" + e.message + "），请点击下方按钮重试。");
      return;
    }
    onDone();
  };

  // 样式（跟随主题变量）
  const card = {
    width: "min(720px, 92vw)",
    maxHeight: "88vh",
    overflowY: "auto",
    background: "var(--bg-2)",
    border: "1px solid var(--border)",
    borderRadius: 20,
    boxShadow: "var(--shadow-3)",
    padding: "28px 32px",
  };
  const title = { margin: 0, fontSize: 22, fontWeight: 700, color: "var(--text-1)" };
  const sub = { margin: "8px 0 20px", fontSize: 13, color: "var(--text-2)", lineHeight: 1.6 };
  const group = { margin: "0 0 18px" };
  const groupTitle = {
    fontSize: 13,
    fontWeight: 700,
    color: "var(--text-1)",
    marginBottom: 8,
    display: "flex",
    alignItems: "center",
    gap: 6,
  };
  const item = {
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
    padding: "9px 12px",
    borderRadius: 10,
    background: "var(--bg-1)",
    border: "1px solid var(--border)",
    marginBottom: 6,
  };
  const itemLabel = { fontSize: 13, fontWeight: 600, color: "var(--text-1)" };
  const itemDesc = { fontSize: 12, color: "var(--text-2)", marginTop: 2, lineHeight: 1.5 };
  const chip = (color) => ({
    fontSize: 10,
    padding: "1px 8px",
    borderRadius: "var(--r-full)",
    background: "var(--bg-3)",
    color: "var(--text-2)",
    marginLeft: 8,
    whiteSpace: "nowrap",
  });
  const chipOk = {
    ...chip(),
    background: "var(--ok-soft)",
    color: "var(--ok)",
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "var(--bg-0)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
      }}
    >
      <div style={card}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 30 }}>🐋</span>
          <div>
            <h1 style={title}>欢迎使用鲸语</h1>
            <p style={{ ...sub, margin: "2px 0 0", fontSize: 12 }}>
              首次启动需要安装依赖组件。核心组件默认全部安装，可选能力按需勾选——完成后即可开始使用。
            </p>
          </div>
        </div>

        {err && (
          <div style={{ padding: "10px 14px", borderRadius: 10, background: "var(--danger-soft)", color: "var(--danger)", fontSize: 13, marginBottom: 16 }}>
            {err}
          </div>
        )}

        {data && (
          <>
            <div style={group}>
              <div style={groupTitle}>🧩 核心组件（{coreMiss.length} 项待安装）</div>
              {coreMiss.length === 0 ? (
                <div style={{ ...itemDesc, padding: "6px 4px" }}>✅ 全部就绪，无需安装</div>
              ) : (
                coreMiss.map((c) => {
                  const on = picked && picked.has(c.key);
                  return (
                    <label key={c.key} style={{ ...item, cursor: "pointer", alignItems: "center" }}>
                      <input type="checkbox" checked={!!on} disabled={phase === "installing"} onChange={() => toggle(c.key)} />
                      <div style={{ flex: 1 }}>
                        <span style={itemLabel}>{c.label}</span>
                        <span style={chip()}>{c.pip}</span>
                      </div>
                    </label>
                  );
                })
              )}
            </div>

            <div style={group}>
              <div style={groupTitle}>🔌 可选能力（按需勾选）</div>
              {heavy.map((h) => {
                const on = picked && picked.has(h.key);
                return (
                  <label key={h.key} style={{ ...item, cursor: h.ok ? "default" : "pointer" }}>
                    <input
                      type="checkbox"
                      checked={!!on}
                      disabled={h.ok || phase === "installing"}
                      onChange={() => toggle(h.key)}
                    />
                    <div style={{ flex: 1 }}>
                      <span style={itemLabel}>{h.label}</span>
                      {h.ok ? <span style={chipOk}>✓ 已启用</span> : null}
                      <div style={itemDesc}>{h.desc}</div>
                      {h.note ? <div style={{ ...itemDesc, color: "var(--warn)", fontSize: 11 }}>💡 {h.note}</div> : null}
                    </div>
                  </label>
                );
              })}
            </div>

            {/* 进度区 */}
            {phase === "installing" && (
              <div style={{ ...group, padding: "12px 14px", borderRadius: 12, background: "var(--bg-1)", border: "1px solid var(--border)" }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-1)", marginBottom: 6 }}>
                  正在安装（{prog.done}/{prog.total}）{prog.current ? `：${prog.current}` : ""}
                </div>
                <div style={{ height: 8, borderRadius: 4, background: "var(--bg-3)", overflow: "hidden" }}>
                  <div
                    style={{
                      height: "100%",
                      width: `${prog.total ? Math.round((prog.done / prog.total) * 100) : 0}%`,
                      background: "linear-gradient(90deg, var(--brand), var(--brand-strong))",
                      transition: "width .3s",
                    }}
                  />
                </div>
                <div
                  style={{
                    marginTop: 8,
                    maxHeight: 150,
                    overflowY: "auto",
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    color: "var(--text-2)",
                    whiteSpace: "pre-wrap",
                    lineHeight: 1.5,
                  }}
                >
                  {logs.join("\n")}
                </div>
              </div>
            )}

            {phase === "done" && (
              <div
                style={{
                  padding: "12px 14px",
                  borderRadius: 12,
                  background: autoErr ? "var(--danger-soft)" : failed.length ? "var(--warn-soft)" : "var(--ok-soft)",
                  color: autoErr ? "var(--danger)" : failed.length ? "var(--warn)" : "var(--ok)",
                  fontSize: 13,
                  marginBottom: 16,
                }}
              >
                {autoErr ||
                  (failed.length
                    ? `⚠ 部分组件安装失败：${failed.join("、")}。可稍后在「设置 → 可选能力」重试，不影响程序使用。`
                    : "✅ 依赖组件全部就绪，正在进入鲸语…")}
              </div>
            )}

            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 4 }}>
              {phase === "ready" && (
                <>
                  <button className="confirm-btn" onClick={finish} disabled={!data}>
                    跳过，直接进入
                  </button>
                  <button
                    className="confirm-btn confirm-primary"
                    onClick={runInstall}
                    disabled={!picked || picked.size === 0}
                  >
                    ⚡ 一键安装（{picked ? picked.size : 0} 项）
                  </button>
                </>
              )}
              {phase === "installing" && (
                <button className="confirm-btn" disabled>
                  ⏳ 安装中，完成后自动进入…
                </button>
              )}
              {phase === "done" && (
                <button className="confirm-btn confirm-primary" onClick={finish}>
                  {autoErr ? "↻ 重试进入" : "🚀 进入鲸语"}
                </button>
              )}
            </div>
          </>
        )}

        {!data && !err && (
          <div style={{ padding: 40, textAlign: "center", color: "var(--text-3)" }}>正在读取依赖状态…</div>
        )}
      </div>
    </div>
  );
}
