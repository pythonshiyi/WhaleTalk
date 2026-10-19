// ── 执行时间轴的数据整理（纯函数，便于测试）──
// 设计前提：过程是**状态**，不是内容。它不该长在正文里，而该常驻一侧、
// 可回放、可筛选。这里只做「把原始 steps 变成可渲染的时间轴模型」，
// 不含任何 DOM 逻辑。

/** 单步耗时（秒）；"1.2s" / 1200ms / "—" 都能吃 */
export function stepSeconds(step) {
  if (!step) return 0;
  const raw = step.duration;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : 0;
  const m = String(raw || "").match(/([\d.]+)/);
  return m ? Number(m[1]) || 0 : 0;
}

/** 一组步骤的汇总：步数 / 失败 / 执行中 / 总耗时 */
export function stepsSummary(steps) {
  const list = (steps || []).filter((s) => s && s.tool);
  const failed = list.filter((s) => s.status === "failed").length;
  const running = list.filter((s) => s.status === "running").length;
  const done = list.filter((s) => s.status === "done").length;
  const seconds = list.reduce((n, s) => n + stepSeconds(s), 0);
  return { count: list.length, done, failed, running, seconds };
}

/** 整个会话的汇总（所有轮次） */
export function traceSummary(turns) {
  const list = (turns || []).filter(Boolean);
  let count = 0, failed = 0, running = 0, seconds = 0;
  for (const t of list) {
    const s = stepsSummary(t.steps);
    count += s.count;
    failed += s.failed;
    running += s.running;
    seconds += s.seconds;
  }
  return { turns: list.length, count, failed, running, seconds };
}

/**
 * 按状态筛选轮次。
 * - "all"    全部（默认）
 * - "failed" 只保留含失败的轮次——排查时最常用的入口
 * - "active" 只保留正在执行的轮次
 * 返回的是**原轮次对象**（不复制），保证点击定位时 taskId 仍可对应消息下标。
 */
export function filterTurns(turns, mode = "all") {
  const list = (turns || []).filter(Boolean);
  if (mode === "failed") return list.filter((t) => stepsSummary(t.steps).failed > 0);
  if (mode === "active") return list.filter((t) => stepsSummary(t.steps).running > 0 || t.streaming);
  return list;
}

/** 秒数展示：<60 用 12.3s，否则 1m23s（避免出现 823.4s 这种读不动的数字） */
export function fmtSeconds(sec) {
  const v = Number(sec) || 0;
  if (v <= 0) return "";
  if (v < 60) return `${v.toFixed(1)}s`;
  const m = Math.floor(v / 60);
  const s = Math.round(v - m * 60);
  return `${m}m${String(s).padStart(2, "0")}s`;
}
