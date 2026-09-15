import React from "react";
import * as api from "../api.js";

import { silentWarn } from "../quiet.js";
// 可选能力提示条：首次启动后如可选能力未安装，顶部显示一条提示，8 秒自动隐藏，
// 且只出现一次（localStorage 记忆），点「知道了」立即关闭。绝不重复打扰。
const SHOWN_KEY = "whaletalk.deps.banner.shown";
const DISMISS_KEY = "whaletalk.deps.banner.dismissed";

export default function DepsBanner({ onGoSettings }) {
  const [info, setInfo] = React.useState(null);
  const [visible, setVisible] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    api
      .getDeps()
      .then((d) => {
        if (!alive || !d) return;
        const missing = (d.heavy || []).filter((h) => !h.ok);
        if (!missing.length) return;
        try {
          if (localStorage.getItem(SHOWN_KEY) === "1" || localStorage.getItem(DISMISS_KEY) === "1") return;
          localStorage.setItem(SHOWN_KEY, "1");
        } catch (e) { silentWarn(e, "DepsBanner"); }
        setInfo({ n: missing.length, labels: missing.map((h) => h.label) });
        setVisible(true);
        // 8 秒后自动隐藏，无需用户操作
        setTimeout(() => alive && setVisible(false), 8000);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  if (!info || !visible) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch (e) { silentWarn(e, "DepsBanner"); }
    setVisible(false);
  };

  return (
    <div className="app-banner app-banner-info">
      🟡 {info.n} 个可选能力未安装：{info.labels.join("、")}（不影响启动，按需安装）
      <button style={{ marginLeft: 10 }} onClick={() => onGoSettings && onGoSettings()}>
        去安装
      </button>
      <button style={{ marginLeft: 10 }} onClick={dismiss}>
        知道了
      </button>
    </div>
  );
}
