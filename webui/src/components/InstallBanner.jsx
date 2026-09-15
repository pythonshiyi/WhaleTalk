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
    return <div className="app-banner app-banner-done">🎉 全部依赖安装完成，鲸语已完整就绪</div>;
  }

  if (phase === "warn") {
    return (
      <div className="app-banner app-banner-warn">
        ⚠ {st.nFail} 个组件安装失败（可在 设置 → 依赖与能力 中重试），其余已就绪
      </div>
    );
  }

  return (
    <div className="app-banner app-banner-install">
      <div className="app-banner-inner">
        <span className="app-banner-title">🐋 正在初始化组件</span>
        <div className="app-banner-bar">
          <i style={{ transform: `scaleX(${(pct || 0) / 100})` }} />
        </div>
        <span className="app-banner-step">
          {st.done}/{st.total} · {st.current || "准备中…"}
        </span>
      </div>
    </div>
  );
}
