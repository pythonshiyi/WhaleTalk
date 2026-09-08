// ── 时间显示统一工具（企业级信息细节） ──
// 全站消息/会话时间采用同一种人读格式：
//   今天     → HH:MM（如 14:03）
//   今年     → MM-DD HH:MM（如 09-08 14:03）
//   跨年     → YYYY-MM-DD（如 2025-12-31）
// 避免此前「HH:MM:SS 带秒 + 无日期」与后端「YYYY-MM-DD HH:MM:SS」
// 混用的观感噪音。

const pad = (n) => String(n).padStart(2, "0");

/**
 * 把任意可解析时间(ms/Date/ISO/后端 YYYY-MM-DD HH:MM:SS 串)格式化为统一人读时钟。
 * 入参为 null/空/非法时返回空串（调用方自行兜底）。
 */
export function formatClock(input) {
  if (input === null || input === undefined || input === "") return "";
  let d;
  if (input instanceof Date) d = input;
  else if (typeof input === "number") d = new Date(input);
  else {
    // 后端串形如 "2026-09-08 19:55:16"；直接 new Date("...") 亦能解析，此处归一。
    const iso = String(input).includes("T") ? input : String(input).replace(" ", "T");
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return "";
    d = parsed;
  }
  if (Number.isNaN(d.getTime())) return "";

  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const sameDay = sameYear
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;

  if (sameDay) return hm;                                   // 今天 → HH:MM
  if (sameYear) return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 取当前时刻的统一时钟串（用于新消息打标），语义同 formatClock(Date.now()) */
export const nowClock = () => formatClock(Date.now());
