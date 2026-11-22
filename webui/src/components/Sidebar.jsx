import React from "react";
import { usePoll } from "../usePoll.js";
import { getStatus } from "../api.js";
import { Icon } from "./icons.jsx";
import InboxButton from "./InboxButton.jsx";

// 一级入口只保留六个「用户任务」：会话 / 工作台 / 创作 / 能力 / 智能体 / 设置。
// 配置类（接入通道、权限、可选能力）统一收进设置；任务记录收进工作台；
// 角色、指令库、直播收进创作；大脑、记忆、生命、自主收进智能体。
const NAV_ITEMS = [
  { id: "chat", label: "会话", icon: "message" },
  { id: "workbench", label: "工作台", icon: "layout" },
  { id: "create", label: "创作", icon: "sparkles" },
  { id: "abilities", label: "能力", icon: "puzzle" },
  { id: "agent", label: "智能体", icon: "brain" },
  { id: "settings", label: "设置", icon: "settings" },
];

export default function Sidebar({ page, onPage, onNavigate, runningCount = 0 }) {
  const [status, setStatus] = React.useState(null);
  const [statusErr, setStatusErr] = React.useState(false);

  // 侧栏状态 10s 轮询：页面不可见时暂停（usePoll 统一处理可见性与定时器清理）
  const loadStatus = React.useCallback(async () => {
    try {
      const s = await getStatus();
      setStatus(s);
      setStatusErr(false);
    } catch {
      setStatusErr(true);
    }
  }, []);
  usePoll(loadStatus, 10000);

  return (
    <nav className="sidebar" aria-label="主导航">
      <div className="sb-inner">
        <div className="sb-logo" title="鲸语 WhaleTalk" aria-hidden="true">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--brand-strong)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12c1.5-4 4-6 7-6 3.5 0 5.5 2 9 2 1.6 0 2.8-.6 4-1.5-1 3-3 4.5-5 4.8.6 1.4.9 2.9.9 4.5 0 .8-.1 1.6-.3 2.3-1-.4-1.8-1-2.2-1.8-.9 1-2.4 1.7-4.2 1.7s-3.3-.7-4.2-1.7c-.4.8-1.2 1.4-2.2 1.8A11 11 0 015 15c0-1.6.3-3.1.9-4.5C4.7 10.2 3.3 8.7 3 12z" />
          </svg>
        </div>

        <div className="sb-items">
          {NAV_ITEMS.map((n) => {
            const on = page === n.id;
            return (
              <button
                key={n.id}
                type="button"
                className={`sb-item ${on ? "sb-item-on" : ""}`}
                title={n.label}
                aria-label={n.label}
                aria-current={on ? "page" : undefined}
                onClick={() => onPage(n.id)}
              >
                <Icon name={n.icon} size={20} />
                <span className="sb-item-label">{n.label}</span>
                {n.id === "chat" && runningCount > 0 && (
                  <span className="sb-item-busy" title={`${runningCount} 个后台任务正在生成`} aria-label="后台任务运行中" />
                )}
              </button>
            );
          })}
        </div>

        <div className="sb-bottom">
          <InboxButton onNavigate={onNavigate} />
          <button
            className="sb-status"
            title={`当前模型：${status ? status.model : "…"} · 点击打开设置`}
            aria-label="打开设置"
            onClick={() => onPage("settings")}
          >
            <span className="sb-status-dot" aria-hidden="true" />
            <span className="sb-status-sub">
              {status ? (status.full_auto ? "任务" : "对话") : statusErr ? "未连接" : "连接中"}
            </span>
          </button>
        </div>
      </div>
    </nav>
  );
}
