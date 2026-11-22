import React from "react";
import PersonasPage from "./PersonasPage.jsx";
import PromptsPage from "./PromptsPage.jsx";
import LivePage from "./LivePage.jsx";
import AssetsPanel from "./AssetsPanel.jsx";
import { Icon } from "./icons.jsx";

const TABS = [
  { id: "personas", label: "角色", icon: "user" },
  { id: "prompts", label: "指令库", icon: "zap" },
  { id: "live", label: "直播角色", icon: "sparkles", badge: "Beta" },
  { id: "assets", label: "资产", icon: "folder" },
];

/**
 * 创作：角色、指令与直播的统一入口。
 * 三个子页面共用同一层页面壳与标签栏，避免各自再占一个一级菜单。
 */
export default function CreatePage({ tab = "personas", onTab, onChatPersona, onApply, onGoWechat, assetPath = "" }) {
  const active = TABS.some((t) => t.id === tab) ? tab : "personas";
  return (
    <div className="page">
      <div className="page-head">
        <h1>创作</h1>
        <p>
          角色设定、提示词资产与直播角色。公众号写作在
          <button className="msg-op" style={{ margin: "0 6px" }} onClick={() => onGoWechat?.()}>
            设置 → 公众号写作
          </button>
          中管理。
        </p>
      </div>

      <div className="ab-tabs" role="tablist" aria-label="创作分区">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={active === t.id}
            className={`ab-tab ${active === t.id ? "ab-tab-on" : ""}`}
            onClick={() => onTab?.(t.id)}
          >
            <Icon name={t.icon} size={15} /> {t.label}
            {t.badge && <span className="pm-badge" style={{ marginLeft: 6 }}>{t.badge}</span>}
          </button>
        ))}
      </div>

      {active === "personas" && <PersonasPage embedded onChat={onChatPersona} />}
      {active === "prompts" && <PromptsPage embedded onApply={onApply} />}
      {active === "live" && <LivePage embedded />}
      {active === "assets" && <AssetsPanel initialPath={assetPath} />}
    </div>
  );
}
