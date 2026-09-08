// ── 工具结果展示归一（前端兜底）──
// 后端流式路径已把 dict/list 归一为字符串；但任何渠道漏进来的对象结果若被 JS
// 直接渲染/拼接，会变成 "[object Object]"（get_status 等历史问题）。这里统一转文本。
export default function formatToolResult(r) {
  if (r === null || r === undefined) return "";
  if (typeof r === "string") return r;
  if (typeof r === "object") {
    try { return JSON.stringify(r, null, 2); } catch (e) { return String(r); }
  }
  return String(r);
}
