// ── 会话/标签语义配色（单一来源，可测）──
// 内置语义标签用固定语义色；任意用户标签按稳定哈希映射到调色板，
// 保证同一标签在列表中始终同色。类名对应 app.css 里 `.sl-tag.c-*`。

export const TAG_PALETTE = ["c-brand", "c-ai", "c-ok", "c-warn", "c-danger"];
export const TAG_SEMANTIC = {
  "调研": "c-brand", "写作": "c-danger", "开发": "c-ai",
  "数据": "c-ok", "临时": "c-warn", "会话": "c-brand",
};

/** 返回标签应使用的调色板类名（同一标签恒同色） */
export function tagColorClass(tag) {
  const t = String(tag || "").trim();
  if (TAG_SEMANTIC[t]) return TAG_SEMANTIC[t];
  let h = 0;
  for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0;
  return TAG_PALETTE[h % TAG_PALETTE.length];
}
