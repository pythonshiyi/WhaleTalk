// ── 流式渲染预算：决定「这次文本增量要不要立刻重新解析 Markdown」──
// 为什么需要它：流式输出时每一帧都会带来新的字符，若每帧都全量重解析，
// 5000 字的回复等于每秒 60 次 O(n) 解析（n 还在增长），主线程被吃满 → 掉帧，
// 打字卡顿、滚动发涩。而人眼其实分辨不出 30fps 与 60fps 的文本逐字刷新。
//
// 策略：按**上一次解析实测耗时**自适应。
//   - 解析很便宜（< 2ms，短回复）→ 不节流，保持每帧更新；
//   - 解析开始变贵 → 间隔按耗时的倍数拉长，上限 240ms。
// 这样短文本依旧丝滑，长文本自动让出主线程，且不需要任何魔法常数调参。

export const MIN_PARSE_MS = 2;      // 低于此耗时视为「便宜」，不节流
export const MAX_GAP_MS = 240;      // 节流上限：再慢也不至于看起来卡死

/**
 * 计算下一次允许重解析前的等待时间。
 * @param {number} costMs 上一次 Markdown 解析实测耗时（毫秒）
 * @param {number} elapsedMs 距离上一次真正重解析已过的时间（毫秒）
 * @returns {number} 还需等待的毫秒数；0 表示可以立刻解析
 */
export function streamGateDelay(costMs, elapsedMs) {
  const cost = Number.isFinite(costMs) && costMs > 0 ? costMs : 0;
  const elapsed = Number.isFinite(elapsedMs) && elapsedMs > 0 ? elapsedMs : 0;
  if (cost < MIN_PARSE_MS) return 0;              // 便宜：每帧都解析
  // 预算 = 耗时的 6 倍：解析占主线程 ≤ ~16%，留足给滚动与动画
  const gap = Math.min(MAX_GAP_MS, cost * 6);
  return elapsed >= gap ? 0 : gap - elapsed;
}

/**
 * 流式文本是否值得立刻重解析（纯函数，便于测试与复用）。
 * 非流式（历史消息、终态）永远立刻解析——那时解析只发生一次。
 */
export function shouldReparse({ streaming, costMs, elapsedMs }) {
  if (!streaming) return true;
  return streamGateDelay(costMs, elapsedMs) === 0;
}
