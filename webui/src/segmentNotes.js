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
