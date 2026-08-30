import React from "react";
import * as api from "../api.js";

// 启动安装进度横幅：后端后台安装常规组件时，全局实时显示进度；
// 装完短暂显示「安装完成」庆祝提示。让用户从启动那一刻起就感知系统在构建。
// 与 DepsBanner（可选能力黄条）互不干扰；本横幅优先级更高（zIndex 201）。
export default function InstallBanner() {
  const [phase, setPhase] = React.useState("idle"); // idle | installing | done
  const [st, setSt] = React.useState(null);
  const prevRunning = React.useRef(false);

  React.useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await api.getDeps();
        if (!alive || !d || !d.install) return;
        const ins = d.install;
        setSt(ins);
        if (ins.running) {
          setPhase("installing");
        } else if (prevRunning.current && ins.total > 0 && ins.done >= ins.total) {
          const nFail = (ins.failed || []).length;
          if (nFail > 0) {
            setSt({ ...ins, nFail });
            setPhase("warn");
            setTimeout(() => alive && setPhase("idle"), 6000);
          } else {
            setPhase("done");
            setTimeout(() => alive && setPhase("idle"), 3500);
          }
        } else if (prevRunning.current) {
          setPhase("idle"); // 异常中断：不提示，静默收起
        }
        prevRunning.current = ins.running;
      } catch {
        /* 后端未就绪时静默重试 */
      }
    };
    tick();
    const timer = setInterval(tick, 2500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (phase === "idle" || !st) return null;
  const pct = st.total ? Math.min(100, Math.round((st.done / st.total) * 100)) : 0;

  if (phase === "done") {
    return (
      <div
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          zIndex: 201,
          background: "#16a34a",
          color: "#fff",
          textAlign: "center",
          padding: "10px 12px",
          fontSize: 14,
          fontWeight: 700,
          boxShadow: "0 3px 10px rgba(0,0,0,.25)",
          animation: "wtd-banner-in .35s",
        }}
      >
        🎉 全部依赖安装完成，鲸语已完整就绪
      </div>
    );
  }

  if (phase === "warn") {
    return (
      <div
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          zIndex: 201,
          background: "#b45309",
          color: "#fff",
          textAlign: "center",
          padding: "10px 12px",
          fontSize: 13,
          fontWeight: 600,
          boxShadow: "0 3px 10px rgba(0,0,0,.25)",
          animation: "wtd-banner-in .35s",
        }}
      >
        ⚠ {st.nFail} 个组件安装失败（可在 设置 → 依赖与能力 中重试），其余已就绪
      </div>
    );
  }

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 201,
        background: "linear-gradient(90deg, #0284c7, #2563eb)",
        color: "#fff",
        padding: "9px 16px",
        boxShadow: "0 3px 10px rgba(0,0,0,.25)",
        animation: "wtd-banner-in .35s",
      }}
    >
      <div style={{ maxWidth: 760, margin: "0 auto", display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 14, fontWeight: 700, whiteSpace: "nowrap" }}>🐋 正在初始化组件</span>
        <div style={{ flex: 1, height: 8, background: "rgba(255,255,255,.28)", borderRadius: 99, overflow: "hidden" }}>
          <div
            style={{
              height: "100%",
              width: pct + "%",
              background: "#fff",
              borderRadius: 99,
              transition: "width .4s ease",
            }}
          />
        </div>
        <span style={{ fontSize: 13, whiteSpace: "nowrap", opacity: 0.95 }}>
          {st.done}/{st.total} · {st.current || "准备中…"}
        </span>
      </div>
    </div>
  );
}
