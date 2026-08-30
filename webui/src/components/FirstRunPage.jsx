import React from "react";
import * as api from "../api.js";

// ── 首次启动依赖安装向导（全自动） ─────────────────────
// 核心组件：加载后立即自动安装（无需用户选择/点击），装完自动进入主界面。
// 可选能力：只读展示状态，进入后到「设置 → 可选能力」按需安装。
// 核心安装有失败项时不自动进入，列出清单可重试，确保"装完才进程序"。
export default function FirstRunPage({ onDone }) {
  const [data, setData] = React.useState(null);       // {first_run, deps:{core,heavy,install}}
  const [err, setErr] = React.useState("");
  const [phase, setPhase] = React.useState("loading"); // loading | installing | done
  const [prog, setProg] = React.useState({ done: 0, total: 0, current: "" });
  const [logs, setLogs] = React.useState([]);
  const [failed, setFailed] = React.useState([]);       // 失败显示名
  const [failedKeys, setFailedKeys] = React.useState([]); // 失败 key（重试用）
  const [autoErr, setAutoErr] = React.useState("");

  const finish = React.useCallback(async () => {
    setAutoErr("");
    try {
      await api.completeFirstRun();
    } catch (e) {
      setAutoErr("⚠ 未能记录首次完成状态（" + e.message + "），请点击下方按钮重试。");
      return;
    }
    onDone();
  }, [onDone]);

  // 核心组件自动安装（调用即开始，不做选择）
  const startInstall = React.useCallback(
    async (keys) => {
      if (!keys || !keys.length) {
        setPhase("done");
        finish();
        return;
      }
      setPhase("installing");
      setLogs([]);
      setFailed([]);
      setFailedKeys([]);
      setAutoErr("");
      setProg({ done: 0, total: keys.length, current: "" });
      try {
        await api.installMany(keys, {
          onBatchStart: (ev) => setProg({ done: 0, total: ev.total || keys.length, current: "" }),
          onItemStart: (ev) => setProg((p) => ({ ...p, current: ev.label || "" })),
          onLine: (msg) => setLogs((l) => [...l.slice(-60), msg]),
          onItemDone: (ev) => {
            setProg((p) => ({ ...p, done: p.done + 1 }));
            if (!ev.ok) {
              setFailed((f) => [...f, ev.label]);
              setFailedKeys((k) => [...k, ev.key || ev.label]);
            }
          },
          onBatchDone: (ev) => {
            const fl = ev.failed || [];
            setFailed(fl);
            setPhase("done");
            if (fl.length) {
              // 核心有失败：不自动进入（确保装完才进），展示清单供重试/确认
            } else {
              finish(); // 全部成功 → 自动进入主界面
            }
          },
          onError: (msg) => {
            setLogs((l) => [...l.slice(-60), "❌ " + msg]);
            setPhase("done");
            finish(); // 安装中断：尝试标记完成并进入（失败可重试）
          },
        });
      } catch (e) {
        setLogs((l) => [...l.slice(-60), "❌ 安装中断：" + e.message]);
        setPhase("done");
        finish();
      }
    },
    [finish]
  );

  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await api.getFirstRun();
        if (!alive) return;
        setData(d.deps || {});
        // 核心缺失项全部自动安装（零操作）
        const miss = (d.deps?.core || []).filter((c) => !c.ok).map((c) => c.key);
        if (miss.length) {
          // 让界面先渲染一次再自动开装
          setTimeout(() => {
            if (alive) startInstall(miss);
          }, 300);
        } else {
          setPhase("done");
          finish();
        }
      } catch (e) {
        if (alive) setErr("无法读取依赖状态：" + e.message);
      }
    })();
    return () => {
      alive = false;
    };
  }, [startInstall, finish]);

  const coreMiss = (data?.core || []).filter((c) => !c.ok);
  const coreOk = (data?.core || []).filter((c) => c.ok);
  const heavy = data?.heavy || [];

  // 样式
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
  const sub = { margin: "8px 0 20px", fontSize: 13, color: "var(--text-2)", lineHeight: 1.6 };
  const groupTitle = { fontSize: 13, fontWeight: 700, color: "var(--text-1)", marginBottom: 8, display: "flex", alignItems: "center", gap: 6 };
  const item = { display: "flex", alignItems: "flex-start", gap: 10, padding: "9px 12px", borderRadius: 10, background: "var(--bg-1)", border: "1px solid var(--border)", marginBottom: 6 };
  const itemLabel = { fontSize: 13, fontWeight: 600, color: "var(--text-1)" };
  const itemDesc = { fontSize: 12, color: "var(--text-2)", marginTop: 2, lineHeight: 1.5 };
  const chip = (color) => ({ fontSize: 10, padding: "1px 8px", borderRadius: "var(--r-full)", background: "var(--bg-3)", color: "var(--text-2)", marginLeft: 8, whiteSpace: "nowrap" });
  const chipOk = { ...chip(), background: "var(--ok-soft)", color: "var(--ok)" };

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
          <div style={{ flex: 1 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: "var(--text-1)" }}>欢迎使用鲸语</h1>
            <p style={{ ...sub, margin: "2px 0 0", fontSize: 12 }}>
              首次启动正在自动安装核心组件，完成后自动进入。
            </p>
          </div>
          <button
            className="confirm-btn"
            style={{ fontSize: 12, padding: "4px 12px" }}
            onClick={finish}
            disabled={phase === "installing"}
          >
            跳过，直接进入
          </button>
        </div>

        {err && (
          <div style={{ padding: "10px 14px", borderRadius: 10, background: "var(--danger-soft)", color: "var(--danger)", fontSize: 13, marginBottom: 16 }}>
            {err}
          </div>
        )}

        {data && (
          <>
            {/* 核心组件区 */}
            <div style={{ marginBottom: 18 }}>
              <div style={groupTitle}>
                🧩 核心组件
                {phase === "installing" && (
                  <span style={chip()}>自动安装中（{prog.done}/{prog.total}）…</span>
                )}
                {phase === "done" && failed.length === 0 && <span style={chipOk}>✓ 全部就绪</span>}
                {phase === "done" && failed.length > 0 && <span style={{ ...chip(), background: "var(--danger-soft)", color: "var(--danger)" }}>⚠ {failed.length} 项失败</span>}
              </div>

              {/* 进度区 */}
              {phase === "installing" && (
                <div style={{ padding: "12px 14px", borderRadius: 12, background: "var(--bg-1)", border: "1px solid var(--border)", marginBottom: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-1)", marginBottom: 6 }}>
                    {prog.current ? `正在安装：${prog.current}` : "准备安装…"}
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

              {phase === "done" && failed.length === 0 && (
                <div style={{ padding: "10px 14px", borderRadius: 10, background: "var(--ok-soft)", color: "var(--ok)", fontSize: 13, marginBottom: 10 }}>
                  ✅ 核心组件全部就绪，正在进入鲸语…
                </div>
              )}
              {phase === "done" && failed.length > 0 && (
                <div style={{ padding: "10px 14px", borderRadius: 10, background: "var(--warn-soft)", color: "var(--warn)", fontSize: 13, marginBottom: 10 }}>
                  ⚠ 以下核心组件安装失败：<b>{failed.join("、")}</b>。可重试失败项，或稍后在「设置 → 常规组件」重试。
                </div>
              )}
              {autoErr && (
                <div style={{ padding: "10px 14px", borderRadius: 10, background: "var(--danger-soft)", color: "var(--danger)", fontSize: 13, marginBottom: 10 }}>
                  {autoErr}
                </div>
              )}

              {/* 已就绪清单（折叠展示） */}
              {phase !== "installing" && (
                <div style={{ fontSize: 11, color: "var(--text-3)" }}>
                  {coreOk.length > 0 && <div style={{ marginBottom: 4 }}>✓ 已就绪 {coreOk.length} 项：{coreOk.map((c) => c.label).join("、")}</div>}
                  {coreMiss.length > 0 && <div>⏳ 待安装 {coreMiss.length} 项（自动安装中）</div>}
                </div>
              )}
            </div>

            {/* 可选能力（只读展示，进入后到设置中心安装） */}
            <div>
              <div style={groupTitle}>🔌 可选能力（进入后可在「设置 → 可选能力」按需安装）</div>
              {heavy.map((h) => (
                <div key={h.key} style={{ ...item, cursor: "default" }}>
                  <div style={{ flex: 1 }}>
                    <span style={itemLabel}>{h.label}</span>
                    {h.ok ? <span style={chipOk}>✓ 已启用</span> : <span style={chip()}>未安装</span>}
                    <div style={itemDesc}>{h.desc}</div>
                  </div>
                </div>
              ))}
            </div>

            {/* 底部操作 */}
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 18 }}>
              {phase === "installing" && (
                <button className="confirm-btn" disabled>
                  ⏳ 自动安装中，完成后自动进入…
                </button>
              )}
              {phase === "done" && failed.length > 0 && (
                <>
                  <button className="confirm-btn" onClick={finish}>
                    仍要进入
                  </button>
                  <button
                    className="confirm-btn confirm-primary"
                    onClick={() => startInstall(failedKeys)}
                  >
                    ↻ 重试失败项（{failed.length}）
                  </button>
                </>
              )}
              {phase === "done" && failed.length === 0 && (
                <button className="confirm-btn confirm-primary" onClick={finish}>
                  🚀 进入鲸语
                </button>
              )}
            </div>
          </>
        )}

        {!data && !err && (
          <div style={{ padding: 40, textAlign: "center", color: "var(--text-3)" }}>
            <div style={{ fontSize: 13, marginBottom: 8 }}>正在初始化…</div>
            <div style={{ fontSize: 11 }}>（首次启动需先安装核心组件，请稍候）</div>
          </div>
        )}
      </div>
    </div>
  );
}
