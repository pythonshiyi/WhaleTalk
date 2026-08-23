import React from "react";

// ── 欢迎页：双模式二选一（对齐真实项目：对话 / 任务）──
export default function WelcomePage({ mode, onPick, onBack }) {
  const modes = [
    {
      key: "dialog",
      icon: "💬",
      title: "对话模式",
      sub: "纯问答 · 不调用工具",
      desc: "适合聊天、翻译、写作、答疑",
    },
    {
      key: "task",
      icon: "🚀",
      title: "任务模式",
      sub: "全部工具自动可用",
      desc: "适合查资料、处理文件、执行任务",
    },
  ];

  return (
    <div className="welcome">
      <div className="welcome-brand">
        <div className="welcome-logo">🐳</div>
        <h1>鲸语</h1>
        <div className="welcome-sub">WhaleTalk · 为 DeepSeek V4 而生的桌面智能体</div>
      </div>
      <div className="welcome-modes">
        {modes.map((m) => {
          const selected = mode === m.key;
          return (
            <button
              key={m.key}
              className={`wmode-card ${selected ? "wmode-on" : ""}`}
              onClick={() => onPick(m.key)}
            >
              {selected && <span className="wmode-check">✓ 当前</span>}
              <div className="wmode-icon">{m.icon}</div>
              <div className="wmode-title">{m.title}</div>
              <div className="wmode-sub">{m.sub}</div>
              <div className="wmode-desc">{m.desc}</div>
            </button>
          );
        })}
      </div>
      <div className="welcome-hint">任务模式：工具按需加载，目录内全自动；对话模式：回归纯粹问答。</div>
      {onBack && (
        <button className="welcome-back" onClick={onBack}>← 返回</button>
      )}
    </div>
  );
}