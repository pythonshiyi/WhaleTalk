import React from "react";

// 多智能体流水线步骤面板：解析工具结果里的 __TEAM_JSON__ 结构化段，渲染步骤条
function TeamRunSteps({ result }) {
  const [steps, setSteps] = React.useState(null);
  React.useEffect(() => {
    if (!result) return setSteps(null);
    const m = String(result).match(/__TEAM_JSON__(\{.*\})/);
    if (m) {
      try {
        const d = JSON.parse(m[1]);
        setSteps(d.team_steps || []);
      } catch { setSteps(null); }
    } else setSteps(null);
  }, [result]);
  if (!steps || !steps.length) return null;
  return (
    <div style={{ marginTop: 6 }}>
      <div className="tool-result-label" style={{ marginBottom: 4 }}>🧩 多智能体流水线</div>
      <ol style={{ margin: 0, paddingLeft: 20, fontSize: 12.5 }}>
        {steps.map((s, i) => (
          <li key={i} style={{ marginBottom: 6 }}>
            <b>[{s.role}]</b> {s.task}
            {s.output && <div style={{ opacity: .85, whiteSpace: "pre-wrap", fontSize: 12, margin: "3px 0" }}>{String(s.output).slice(0, 300)}</div>}
          </li>
        ))}
      </ol>
    </div>
  );
}

// 工具图标（内联 SVG，无图标库依赖）；图标归属按真实工具名前缀展示，无映射时用 code 兜底
const ICONS = {
  search: (
    <path d="M21 21l-4.35-4.35M17 10.5a6.5 6.5 0 11-13 0 6.5 6.5 0 0113 0z" />
  ),
  globe: (
    <path d="M12 21a9 9 0 100-18 9 9 0 000 18zM3.6 9h16.8M3.6 15h16.8M12 3a13.5 13.5 0 010 18M12 3a13.5 13.5 0 000 18" />
  ),
  doc: (
    <path d="M14 3H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V9l-6-6zM14 3v6h6M9 13h6M9 17h6" />
  ),
  code: (
    <path d="M8 7l-5 5 5 5M16 7l5 5-5 5M13 4l-2 16" />
  ),
};

const TOOL_ICON_KIND = [
  [/^search_|^net_|^fetch_|^http|^web_/, "globe"],
  [/^(read|write|edit|file_|md_|doc|pdf|json_|table_|data_|db_|sql)/, "doc"],
  [/^run_|^exec|^code|^dev_|^test_/, "code"],
];

const toolIconKey = (tool) => (TOOL_ICON_KIND.find(([re]) => re.test(tool || "")) || [null, "code"])[1];

export default function ToolCard({ tool, status, args, result, cost, duration }) {
  const iconKey = toolIconKey(tool);
  const Icon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {ICONS[iconKey] || ICONS.code}
    </svg>
  );

  const [open, setOpen] = React.useState(status === "failed");
  const running = status === "running";
  const done = status === "done";

  return (
    <div className={`tool-card tool-${status} ${open ? "tool-open" : ""}`}>
      <div className="tool-card-head" onClick={() => status !== "running" && setOpen(!open)}>
        <span className="tool-icon" style={{ color: "var(--ai)" }}>
          <Icon />
        </span>
        <span className="tool-name">{tool}</span>
        <span className="tool-args">
          {args && Object.entries(args).map(([k, v]) => (
            <span className="tool-arg" key={k}>
              {k}=<em>{String(v).length > 26 ? String(v).slice(0, 26) + "…" : v}</em>
            </span>
          ))}
        </span>
        <span className="tool-right">
          {running ? (
            <>
              <span className="tool-spin" />
              <span className="tool-status">执行中</span>
            </>
          ) : done ? (
            <>
              <span className="tool-check">✓</span>
              <span className="tool-status tool-status-done">{duration && duration !== "—" ? `${duration}s` : "完成"}</span>
            </>
          ) : (
            <>
              <span className="tool-x">✕</span>
              <span className="tool-status">失败</span>
            </>
          )}
          {cost && <span className="tool-cost">{cost}</span>}
          <svg className="tool-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </div>
      {(open || running) && result && (
        <div className="tool-card-body">
          <div className="tool-result">
            <span className="tool-result-label">结果</span>
            {tool === "team_run" ? (
              <>
                <TeamRunSteps result={result} />
                {String(result).replace(/__TEAM_JSON__\{.*\}/, "").trim()}
              </>
            ) : result}
          </div>
        </div>
      )}
    </div>
  );
}