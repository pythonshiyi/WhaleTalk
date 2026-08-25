import React from "react";
import { getStatus } from "../api.js";

// 真实导航布局（非演示数据）：会话 / 工作台 / 能力中心 / 插件市场 / 记忆与知识 / 设置
const NAV_ITEMS = [
  { id: "chat", label: "会话", icon: "chat" },
  { id: "workbench", label: "工作台", icon: "home" },
  { id: "abilities", label: "能力中心", icon: "grid" },
  { id: "plugins", label: "插件市场", icon: "puzzle" },
  { id: "memory", label: "记忆与知识", icon: "brain" },
  { id: "settings", label: "设置", icon: "gear" },
];

const ICONS = {
  chat: <path d="M21 12a8 8 0 01-8 8H5l-3 3V12a8 8 0 018-8h3a8 8 0 018 8zM9 10h6M9 14h4" />,
  home: <path d="M3 10l9-7 9 7v10a2 2 0 01-2 2H5a2 2 0 01-2-2V10zM9 21v-8h6v8" />,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
  puzzle: <path d="M10 3a2 2 0 014 0v2h4a1 1 0 011 1v4h-2a2 2 0 000 4h2v4a1 1 0 01-1 1h-4v-2a2 2 0 00-4 0v2H6a1 1 0 01-1-1v-4h2a2 2 0 000-4H5V6a1 1 0 011-1h4V3z" />,
  brain: <path d="M9.5 3A2.5 2.5 0 007 5.5v.6A3 3 0 005 9a3 3 0 00-1 5.8A3 3 0 007 18.5h.5A2.5 2.5 0 0012 21a2.5 2.5 0 004.5-2.5h.5a3 3 0 002-3.7A3 3 0 0018 9a3 3 0 00-2-2.9v-.6A2.5 2.5 0 0013.5 3a2.5 2.5 0 00-2 1M12 3v18" />,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33h.09a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82v.09a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z" /></>,
};

export default function Sidebar({ page, onPage }) {
  const [status, setStatus] = React.useState(null);
  const [statusErr, setStatusErr] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await getStatus();
        if (alive) {
          setStatus(s);
          setStatusErr(false);
        }
      } catch {
        if (alive) setStatusErr(true);
      }
    };
    load();
    const iv = setInterval(load, 10000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  return (
    <nav className="sidebar">
      <div className="sb-logo" title="鲸语 WhaleTalk">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--brand-strong)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12c1.5-4 4-6 7-6 3.5 0 5.5 2 9 2 1.6 0 2.8-.6 4-1.5-1 3-3 4.5-5 4.8.6 1.4.9 2.9.9 4.5 0 .8-.1 1.6-.3 2.3-1-.4-1.8-1-2.2-1.8-.9 1-2.4 1.7-4.2 1.7s-3.3-.7-4.2-1.7c-.4.8-1.2 1.4-2.2 1.8A11 11 0 015 15c0-1.6.3-3.1.9-4.5C4.7 10.2 3.3 8.7 3 12z" />
        </svg>
      </div>
      <div className="sb-items">
        {NAV_ITEMS.map((n) => (
          <button
            key={n.id}
            className={`sb-item ${page === n.id ? "sb-item-on" : ""}`}
            title={n.label}
            onClick={() => onPage(n.id)}
          >
            <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              {ICONS[n.icon]}
            </svg>
            <span>{n.label}</span>
          </button>
        ))}
      </div>
      <div className="sb-bottom">
        <button
          className="sb-status"
          title="点击打开设置：模型 / 思考档 / 场景 / 外观"
          onClick={() => onPage("settings")}
        >
          <span className="sb-status-dot" />
          <span className="sb-status-model">{status ? status.model : "…"}</span>
          <span className="sb-status-sub">
            {status ? (status.full_auto ? "🚀任务" : "💬对话") : statusErr ? "未连接" : "连接中"}
          </span>
        </button>
        <button
          className="sb-avatar"
          title="点击打开设置"
          onClick={() => onPage("settings")}
        >
          🐋
        </button>
      </div>
    </nav>
  );
}