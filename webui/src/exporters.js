// ── 会话导出（对齐原程序 exporters.py 的四格式行为）──

function buildMarkdown(messages, meta = {}) {
  const lines = [`# 会话记录`, ``, `- 模型：${meta.model || ""}`];
  if (meta.name) lines.push(`- 会话：${meta.name}`);
  lines.push(``);
  for (const m of messages) {
    if (m.role === "system") continue;
    if (m.role === "user") {
      lines.push(`## 我`, ``);
      if (m.images && m.images.length) {
        for (const img of m.images) lines.push(`[图片] ${img}`);
        lines.push(``);
      }
      lines.push(m.text || "", ``);
    } else {
      lines.push(`## 助手`, ``);
      if (m.think) lines.push(`（思考过程）`, "```text", m.think, "```", ``);
      if (m.tools && m.tools.length) {
        for (const t of m.tools) {
          lines.push(`### 工具调用: ${t.tool}`, "```json", JSON.stringify(t.args || {}, null, 2), "```", ``);
          if (t.result) lines.push(`> 工具结果`, `> ${String(t.result).replace(/\n/g, "\n> ")}`, ``);
        }
      }
      lines.push(m.text || "", ``);
    }
  }
  return lines.join("\n");
}

function buildText(md) {
  return md
    .replace(/```text\n/g, "【思考】\n")
    .replace(/```\n/g, "")
    .replace(/```json\n/g, "")
    .replace(/^## /gm, "")
    .replace(/^# /gm, "");
}

function buildHtml(messages, meta = {}) {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const parts = [`<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>会话记录</title><style>
body{font-family:'Microsoft YaHei',sans-serif;max-width:820px;margin:24px auto;background:#0b1220;color:#e8eefc;padding:0 16px}
.msg{margin:14px 0;padding:12px 16px;border-radius:12px;line-height:1.7}
.user{background:rgba(14,165,233,.16);border:1px solid rgba(14,165,233,.3);text-align:right}
.assistant{background:rgba(255,255,255,.05);border:1px solid rgba(140,165,255,.12)}
.think{background:rgba(139,92,246,.1);color:#9fb0cc;font-size:12px;border-radius:8px;padding:8px 12px;margin-bottom:8px}
.tool{background:rgba(140,165,255,.06);color:#9fb0cc;font-size:12px;border-radius:8px;padding:8px 12px;margin-bottom:8px}
pre{background:#0d1526;border:1px solid rgba(140,165,255,.12);border-radius:8px;padding:10px;overflow-x:auto;font-size:12px}
.role{font-size:11px;color:#5c6b8a;margin-bottom:4px}
.time{float:right;color:#5c6b8a;font-size:10px}
h1{color:#38bdf8;font-size:22px}</style></head><body><h1>会话记录</h1><p style="color:#5c6b8a">模型：${esc(meta.model || "")}${meta.name ? ` · ${esc(meta.name)}` : ""}</p>`];
  for (const m of messages) {
    if (m.role === "system") continue;
    const cls = m.role === "user" ? "user" : "assistant";
    parts.push(`<div class="msg ${cls}">`);
    if (m.role !== "user") {
      if (m.think) parts.push(`<div class="think">🧠 ${esc(m.think)}</div>`);
      if (m.tools) {
        for (const t of m.tools) {
          parts.push(`<div class="tool">🔧 ${esc(t.tool)} ${esc(JSON.stringify(t.args || {}))}</div>`);
          if (t.result) parts.push(`<div class="tool">${esc(String(t.result).slice(0, 800))}</div>`);
        }
      }
    }
    parts.push(`<div class="role">${m.role === "user" ? "我" : "助手"}<span class="time">${esc(m.time || "")}</span></div>`);
    parts.push(esc(m.text || ""));
    parts.push(`</div>`);
  }
  parts.push(`</body></html>`);
  return parts.join("");
}

function buildJsonl(messages) {
  return messages
    .filter((m) => m.role !== "system")
    .map((m) =>
      JSON.stringify({
        role: m.role,
        content: m.text || "",
        ...(m.think ? { reasoning_content: m.think } : {}),
      })
    )
    .join("\n");
}

export function exportSession(messages, meta = {}) {
  const md = buildMarkdown(messages, meta);
  const base = `whaletalk_${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}`;
  const files = [
    { name: `${base}.md`, content: md, type: "text/markdown" },
    { name: `${base}.txt`, content: buildText(md), type: "text/plain" },
    { name: `${base}.html`, content: buildHtml(messages, meta), type: "text/html" },
    { name: `${base}.jsonl`, content: buildJsonl(messages), type: "application/x-ndjson" },
  ];
  for (const f of files) {
    const blob = new Blob([f.content], { type: f.type });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = f.name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
}

export function exportSessionJson(messages) {
  const blob = new Blob([JSON.stringify(messages.map((m) => ({
    role: m.role,
    content: m.text || "",
    ...(m.think ? { reasoning_content: m.think } : {}),
  })), null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `whaletalk_${Date.now()}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

export function parseImportedText(raw) {
  // JSON 数组 / {"messages": [...]} / JSONL 逐行
  const text = String(raw || "").trim();
  if (!text) return [];
  try {
    const data = JSON.parse(text);
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.messages)) return data.messages;
  } catch {}
  const msgs = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    try {
      msgs.push(JSON.parse(line));
    } catch {}
  }
  return msgs;
}