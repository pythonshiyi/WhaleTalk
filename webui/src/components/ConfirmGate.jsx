import React from "react";

// ── 确认门 / 面向用户选择器（对齐真实项目：ask_user 询问 + 审批）──
// type: "ask" | "approval"
// 一次显示一个；用户响应后调用 onRespond(payload)。
//
// 设计要点（紧急修复 v3.9.1）：
// 1) ask 支持 options：AI 给出一组可点选答案 → 呈现「标准面向用户选择器」，
//    用户一键拍板；无 options 时回退自由文本输入。
// 2) 任何弹窗都可关闭：右上角 ✕ + 点击遮罩即取消（ask=跳过 / approval=拒绝），
//    杜绝"全屏弹窗无法关闭/无法选择"的历史卡死问题。
// 3) 倒计时归零自动按「未回答」回传，绝不让 AI 工具循环永久阻塞。
// 4) 旧的"白名单"申请对话框已废弃：黑名单主导架构下后端一律放行、不再发
//    对应 SSE 事件，此处已无该类型分支（回归门禁锁定，勿加回）。

const ASK_TIMEOUT_S = 180;   // 与后端 ASK_TIMEOUT 对齐（仅展示用倒计时）

export default function ConfirmGate({ req, onRespond }) {
  const [answer, setAnswer] = React.useState("");
  const [sel, setSel] = React.useState([]); // 多选已勾选项（字符串数组）
  const [seconds, setSeconds] = React.useState(ASK_TIMEOUT_S);

  // 切换请求时重置
  React.useEffect(() => {
    if (!req) return;
    setAnswer("");
    setSel([]);
    setSeconds(ASK_TIMEOUT_S);
  }, [req]);

  // 倒计时
  React.useEffect(() => {
    if (!req) return;
    const iv = setInterval(() => setSeconds((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(iv);
  }, [req]);

  if (!req) return null;

  const expired = seconds === 0;
  // 归一 type：SSE 事件本身带 type="ask_request"/"approval_request"，而 ChatPage 拼
  // `{ type:"ask", ...ev }` 时会被 ...ev 里的 type 覆盖成 "ask_request"。故这里同时
  // 认语义名(ask/approval)与原始 SSE 名(ask_request/approval_request)，避免误判成
  // approval 分支导致响应格式错配 → 后端 _respond 判空拒绝 → AI 永久等待（历史卡死根因）。
  const rt = String(req.type || "");
  const isAsk = rt === "ask" || rt === "ask_request";
  const isApproval = rt === "approval" || rt === "approval_request";
  const options = isAsk && Array.isArray(req.options) && req.options.length ? req.options.slice(0, 6) : null;
  // 多选：仅当 AI 声明 multi 且有 options 时启用（否则单选一点即提交）
  const multi = !!(isAsk && options && (req.multi === true || req.multi === "true"));

  const toggleSel = (opt) => {
    setSel((prev) => (prev.includes(opt) ? prev.filter((x) => x !== opt) : [...prev, opt]));
  };

  const finish = (payload) => {
    try { onRespond(payload); } catch (e) { /* 上层兜底 */ }
  };

  // 超时：按未回答自动回传
  const autoTimeout = () => {
    if (isAsk) finish({ id: req.id, answer: "（用户未在限时内回答，请简化问题或改用其他方式）" });
    else finish({ id: req.id, allow: false, reason: "审批超时未响应（自动拒绝）" });
  };

  // 用户主动关闭（✕ 或点击遮罩）
  const dismiss = () => {
    if (isAsk) finish({ id: req.id, answer: "（用户跳过了该询问）" });
    else finish({ id: req.id, allow: false, reason: "用户关闭了请求框" });
  };

  const handleMaskClick = (e) => {
    if (e.target === e.currentTarget) dismiss(); // 仅点遮罩空白处取消，点卡片不触发
  };

  return (
    <div className="confirm-mask" onClick={handleMaskClick}>
      <div className="confirm-card" role="dialog" aria-modal="true"
        aria-label={isAsk ? "Agent 询问" : "权限确认"}>
        <div className="confirm-head">
          <b>{isAsk ? "🤔 Agent 需要你确认" : "🛡 权限请求"}</b>
          <span className="confirm-head-right">
            <span className={`confirm-timer ${expired ? "confirm-timer-over" : ""}`}>
              {expired ? "已超时" : `${seconds}s`}
            </span>
            <button className="confirm-x" title="关闭并跳过" aria-label="关闭" onClick={dismiss}>✕</button>
          </span>
        </div>

        {expired ? (
          // 超时：给出明确收尾，避免"卡死无出口"
          <div className="confirm-timeout-actions">
            <p className="confirm-prompt">已超时，请选择是否继续等待 AI 处理？</p>
            <div className="confirm-foot">
              <button className="confirm-btn" onClick={dismiss}>结束该次询问</button>
              <button className="confirm-btn confirm-primary" onClick={() => { setSeconds(ASK_TIMEOUT_S); }}>
                再等 {ASK_TIMEOUT_S}s
              </button>
            </div>
          </div>
        ) : isAsk ? (
          <>
            <div className="confirm-prompt">{req.prompt}</div>

            {options ? (
              // ── 标准面向用户选择器 ──
              // 单选：点一下即提交；多选(multi)：勾选多项后点「确认选择」一次提交
              <>
                <div className={`confirm-options ${multi ? "confirm-options-multi" : ""}`}>
                  {options.map((opt, i) => (
                    <button key={i}
                      className={`confirm-opt ${multi && sel.includes(opt) ? "confirm-opt-on" : ""}`}
                      onClick={() => (multi ? toggleSel(opt) : finish({ id: req.id, option: opt, answer: opt }))}>
                      {multi && <span className={`confirm-opt-check ${sel.includes(opt) ? "on" : ""}`} aria-hidden="true">✓</span>}
                      {opt}
                    </button>
                  ))}
                </div>
                {multi && (
                  <div className="confirm-multi-hint">可多选（已选 {sel.length} 项）</div>
                )}
              </>
            ) : null}

            {multi ? (
              // 多选：不需要自由文本分隔线，直接确认提交
              <div className="confirm-foot" style={{ marginTop: 2 }}>
                <button className="confirm-btn" onClick={dismiss}>取消</button>
                <button className="confirm-btn confirm-primary"
                  disabled={sel.length === 0}
                  onClick={() => finish({ id: req.id, selections: sel, answer: sel.join("、") })}>
                  确认选择{sel.length > 0 ? `（${sel.length}）` : ""}
                </button>
              </div>
            ) : (
              <>
                <div className="confirm-ask-or">
                  <div className="confirm-ask-line" />
                  <span>或输入自定义回答</span>
                  <div className="confirm-ask-line" />
                </div>
                <input
                  className="confirm-input"
                  placeholder="输入你的回答…"
                  value={answer}
                  autoFocus={!options}
                  onChange={(e) => setAnswer(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      const v = answer.trim();
                      if (v) finish({ id: req.id, answer: v });
                    }
                  }}
                />
                <div className="confirm-foot">
                  <button className="confirm-btn" onClick={dismiss}>跳过</button>
                  <button className="confirm-btn confirm-primary" disabled={!answer.trim()}
                    onClick={() => finish({ id: req.id, answer: answer.trim() })}>
                    提交
                  </button>
                </div>
              </>
            )}
          </>
        ) : (
          <>
            <div className="confirm-prompt">
              请求调用工具 <b className="confirm-tool">{req.name || "(未知工具)"}</b>
            </div>
            {req.args ? (
              <div className="confirm-args">
                {typeof req.args === "string" ? req.args.slice(0, 300) : JSON.stringify(req.args || {}, null, 2)}
              </div>
            ) : null}
            <div className="confirm-note">该操作在权限范围内，确认后执行（可点 ✕ 或遮罩关闭以拒绝）。</div>
            <div className="confirm-foot">
              <button className="confirm-btn" onClick={dismiss}>拒绝</button>
              <button className="confirm-btn confirm-primary" onClick={() => finish({ id: req.id, allow: true, reason: "用户允许" })}>
                允许
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
