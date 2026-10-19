import React from "react";
import { ModeContext } from "../App.jsx";
import { readIntent, writeIntent, intentSummary, normalizeIntent, ROUND_PRESETS } from "../intent.js";

// ── 意图条：把「这次的边界」放在输入框旁边 ──
// 三件事必须是**发出前**就能看见的：授权（能不能动手）、步数预算、时间提醒。
// 过去它们散在设置页，用户发完才发现「它怎么自己改了文件」——那是坏消息，
// 不该用事后通知的方式送达。
//
// 设计取舍：
//  · 默认只显示一行摘要（可执行 · ≤20 步 · 提醒 5 分钟），点开才展开控件；
//  · 授权沿用既有 mode 开关（task/dialog），不新造权限体系；
//  · 时间预算只提醒、不阻断（见 intent.js 的说明）。
export default function IntentBar({ compact = false }) {
  const { mode, switchMode } = React.useContext(ModeContext) || {};
  const [open, setOpen] = React.useState(false);
  const [intent, setIntent] = React.useState(() => readIntent());
  const taskMode = mode !== "dialog";

  const patch = React.useCallback((p) => {
    setIntent(writeIntent(p));
  }, []);

  // 跨标签页/跨组件同步：意图是全局设置，另一处改了这里要跟上
  React.useEffect(() => {
    const onStorage = (e) => {
      if (e && e.key && !String(e.key).includes("intent")) return;
      setIntent(readIntent());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const it = normalizeIntent(intent);
  return (
    <div className={`intent${open ? " intent-open" : ""}${taskMode ? "" : " intent-readonly"}`}>
      <button
        type="button"
        className="intent-sum"
        aria-expanded={open}
        title="本轮的执行边界：授权、步数预算、时间提醒"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="intent-dot" aria-hidden="true" />
        <span className="intent-text">{compact ? "" : "本轮："}{intentSummary(it, { taskMode })}</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="intent-body">
          <div className="intent-row">
            <span className="intent-k">授权</span>
            <div className="intent-seg" role="group" aria-label="授权级别">
              <button
                type="button"
                className={`intent-opt${taskMode ? " is-on" : ""}`}
                aria-pressed={taskMode}
                onClick={() => switchMode && switchMode("task")}
              >可执行</button>
              <button
                type="button"
                className={`intent-opt${taskMode ? "" : " is-on"}`}
                aria-pressed={!taskMode}
                onClick={() => switchMode && switchMode("dialog")}
              >只回答</button>
            </div>
            <span className="intent-hint">
              {taskMode ? "可用工具、可写文件" : "不执行任何工具，纯问答"}
            </span>
          </div>

          <div className="intent-row">
            <span className="intent-k">步数</span>
            <div className="intent-seg" role="group" aria-label="工具轮数预算">
              {ROUND_PRESETS.map((n) => (
                <button
                  key={n}
                  type="button"
                  className={`intent-opt${it.maxRounds === n ? " is-on" : ""}`}
                  aria-pressed={it.maxRounds === n}
                  onClick={() => patch({ maxRounds: n })}
                >{n === 0 ? "不限" : `≤${n}`}</button>
              ))}
            </div>
            <input
              className="intent-num"
              type="number"
              min="0"
              max="9999"
              value={it.maxRounds || ""}
              placeholder="自定义"
              onChange={(e) => patch({ maxRounds: e.target.value })}
              aria-label="自定义步数上限（0 为不限）"
            />
          </div>

          <div className="intent-row">
            <span className="intent-k">时间</span>
            <input
              className="intent-num"
              type="number"
              min="0"
              max="600"
              value={it.timeBudgetMin || ""}
              placeholder="0"
              onChange={(e) => patch({ timeBudgetMin: e.target.value })}
              aria-label="时间提醒（分钟，0 为关闭）"
            />
            <span className="intent-hint">分钟，到点只提醒、不中断</span>
          </div>

          <p className="intent-note">
            预算只作用于本轮，不改全局设置；授权切换等同于顶部任务/对话开关。
          </p>
        </div>
      )}
    </div>
  );
}
