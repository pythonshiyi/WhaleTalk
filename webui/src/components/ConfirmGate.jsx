import React from "react";

// ── 确认门（对齐真实项目：ask_user 询问 / 审批弹窗）──
// type: "ask" | "approval" | "permission"
// 一次显示一个；响应后调用 onRespond(payload)
export default function ConfirmGate({ req, onRespond }) {
  const [answer, setAnswer] = React.useState("");
  const [seconds, setSeconds] = React.useState(120);

  React.useEffect(() => {
    if (!req) return;
    setAnswer("");
    setSeconds(120);
    const iv = setInterval(() => setSeconds((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(iv);
  }, [req]);

  if (!req) return null;

  const expired = seconds === 0;
  const close = (payload) => {
    if (expired) {
      onRespond(req.type === "ask" ? { id: req.id, answer: "（用户未在限时内回答）" } : { id: req.id, allow: false, reason: "审批超时未响应（自动拒绝）" });
    } else {
      onRespond(payload);
    }
  };

  return (
    <div className="confirm-mask">
      <div className="confirm-card">
        <div className="confirm-head">
          <b>
            {req.type === "ask" ? "🤔 Agent 询问" : req.type === "approval" ? "🛡 AI 权限请求" : "🛡 白名单请求"}
          </b>
          <span className={`confirm-timer ${expired ? "confirm-timer-over" : ""}`}>
            {expired ? "已超时（仍可响应）" : `${seconds}s`}
          </span>
        </div>

        {req.type === "ask" && (
          <>
            <div className="confirm-prompt">{req.prompt}</div>
            <input
              className="confirm-input"
              placeholder="输入你的回答…"
              value={answer}
              autoFocus
              onChange={(e) => setAnswer(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const v = answer.trim();
                  if (v) close({ id: req.id, answer: v });
                }
              }}
            />
            <div className="confirm-foot">
              <button className="confirm-btn" onClick={() => close({ id: req.id, answer: "（用户未在限时内回答，请简化问题或改用其他方式）" })}>
                跳过
              </button>
              <button className="confirm-btn confirm-primary" disabled={!answer.trim()} onClick={() => close({ id: req.id, answer: answer.trim() })}>
                提交
              </button>
            </div>
          </>
        )}

        {req.type === "approval" && (
          <>
            <div className="confirm-prompt">
              请求调用工具 <b className="confirm-tool">{req.name}</b>
            </div>
            <div className="confirm-args">
              {req.args && typeof req.args === "string" ? req.args.slice(0, 300) : JSON.stringify(req.args || {}, null, 2)}
            </div>
            <div className="confirm-note">该操作在权限范围内，确认后执行（不确认将自动超时拒绝）。</div>
            <div className="confirm-foot">
              <button className="confirm-btn" onClick={() => close({ id: req.id, allow: false, reason: "用户拒绝" })}>
                拒绝
              </button>
              <button className="confirm-btn confirm-primary" onClick={() => close({ id: req.id, allow: true, reason: "用户允许" })}>
                允许
              </button>
            </div>
          </>
        )}

        {req.type === "permission" && (
          <>
            <div className="confirm-prompt">
              AI 请求将以下操作加入白名单：
            </div>
            <div className="confirm-args">
              类型：{req.action_type} · 值：{req.value || "（write 类型可留空）"}
            </div>
            <div className="confirm-note">加入后立即生效，可重试被拒绝的操作。</div>
            <div className="confirm-foot">
              <button className="confirm-btn" onClick={() => close({ id: req.id, ok: false, msg: "白名单请求被拒绝" })}>
                拒绝
              </button>
              <button className="confirm-btn confirm-primary" onClick={() => close({ id: req.id, ok: true, msg: "已加入白名单" })}>
                同意
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}