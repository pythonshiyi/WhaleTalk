// 多段输出的分段切分（纯函数，便于单测）。
//
// 一轮里 AI 会跨多个工具轮次输出多段文字（各自是一条 assistant 消息），前端合并进
// 同一气泡时用空行分隔。分段锚点 `segs` 记录每段的起点字符偏移 `i` 与「该段之前的
// 工具步数」`n`；本函数据此把正文切成 [{ text, note }]，note>0 时渲染
// 「↳ 基于第 N 步结果」，把输出与步骤的因果显式化。
//
// 健壮性：越界/非法锚点一律忽略；i 单调递增只保证切分不回退。
export function splitSegments(text, segs) {
  const s = String(text || "");
  const list = (Array.isArray(segs) ? segs : [])
    .filter((x) => x && Number.isFinite(Number(x.i)) && Number(x.i) > 0 && Number(x.i) < s.length)
    .map((x) => ({ i: Math.floor(Number(x.i)), n: Math.floor(Number(x.n) || 0) }))
    .sort((a, b) => a.i - b.i);
  const out = [];
  let last = 0;
  let note = null;
  for (const x of list) {
    if (x.i <= last) continue;            // 与上一锚点同位置或回退 → 忽略
    out.push({ text: s.slice(last, x.i), note });
    note = x.n > 0 ? x.n : null;           // 下一段的因果标注（首段 notes=null）
    last = x.i;
  }
  out.push({ text: s.slice(last), note });
  return out;
}

// 把「正文分段 + 工具步骤」重排为**按时间顺序的信息流**：
//   [{type:"text", text, note}] 与 [{type:"steps", steps:[...]}] 交替。
//
// 依据：分段锚点 `segs` 的 `n` = 该段正文之前的工具步数——于是可以把工具按
// 「发生在哪两段正文之间」切组，插到正确的时序位置，而不是全部堆在正文之前。
//
// 无锚点（历史会话/尚未产生分段）时退化为「先步骤、后正文」的近似顺序。
export function buildTurnFlow(text, segs, tools) {
  const list = (Array.isArray(tools) ? tools : []).filter((t) => t && t.tool);
  const s = String(text || "");
  const hasSegs = Array.isArray(segs) && segs.some((x) => x && Number(x.i) > 0);
  const flow = [];
  if (!hasSegs) {
    if (list.length) flow.push({ type: "steps", steps: list, start: 0 });
    if (s) flow.push({ type: "text", text: s, note: null });
    return flow;
  }
  const chunks = splitSegments(s, segs);
  let cursor = 0;
  chunks.forEach((c, idx) => {
    if (idx > 0) {
      const want = c.note == null ? cursor : c.note;
      const n = Math.min(list.length, Math.max(cursor, want));
      if (n > cursor) flow.push({ type: "steps", steps: list.slice(cursor, n), start: cursor });
      cursor = n;
    }
    if (c.text) flow.push({ type: "text", text: c.text, note: idx > 0 ? c.note : null });
  });
  if (cursor < list.length) flow.push({ type: "steps", steps: list.slice(cursor), start: cursor });
  return flow;
}
