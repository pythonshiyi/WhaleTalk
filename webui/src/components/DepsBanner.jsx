import React from "react";
import * as api from "../api.js";

// 可选能力提示条：首次启动后如检测到可选能力未安装，显示一条可关闭的非阻断提示。
// 知情权（知道缺什么）与体验（启动零弹窗、可关闭不重复）兼得；想装随时去设置页。
const DISMISS_KEY = "whaletalk.deps.banner.dismissed";

export default function DepsBanner({ onGoSettings }) {
  const [info, setInfo] = React.useState(null);
  const [dismissed, setDismissed] = React.useState(() => {
    try {
      return localStorage.getItem(DISMISS_KEY) === "1";
    } catch {
      return false;
    }
  });

  React.useEffect(() => {
    let alive = true;
    api
      .getDeps()
      .then((d) => {
        if (!alive || !d) return;
        const missing = (d.heavy || []).filter((h) => !h.ok);
        setInfo(
          missing.length
            ? { n: missing.length, labels: missing.map((h) => h.label) }
            : null
        );
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  if (!info || dismissed) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {}
    setDismissed(true);
  };

  const btnStyle = {
    marginLeft: 10,
    background: "rgba(255,255,255,.22)",
    color: "#fff",
    border: "1px solid rgba(255,255,255,.5)",
    borderRadius: 6,
    padding: "2px 10px",
    fontSize: 12,
    cursor: "pointer",
  };

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 199,
        background: "#b45309",
        color: "#fff",
        textAlign: "center",
        padding: "7px 12px",
        fontSize: 13,
        fontWeight: 600,
        boxShadow: "0 3px 10px rgba(0,0,0,.25)",
      }}
    >
      🟡 {info.n} 个可选能力未安装：{info.labels.join("、")}（不影响启动，按需安装）
      <button style={btnStyle} onClick={() => onGoSettings && onGoSettings()}>
        去安装
      </button>
      <button style={btnStyle} onClick={dismiss}>
        知道了
      </button>
    </div>
  );
}
