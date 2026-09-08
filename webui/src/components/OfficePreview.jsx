import React from "react";
import Markdown from "./Markdown.jsx";
import * as api from "../api.js";
import { silentWarn } from "../quiet.js";

// 产物 Office 预览 + 就地编辑（P0 前端改造）
// - xlsx：把只读表格升级为可编辑网格，改后调 xlsx_edit 回写（保留坐标→A1 映射）
// - docx：Markdown 内容按块拆分，每块可点 ✎ 就地替换，调 docx_edit.replace 回写
// - pptx/pdf/text：结构化预览 + 系统打开（编辑由 AI 在对话里再调工具）

const COL_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

// 预览中 (行idx=header后第i条, 列ci) → 真实 xlsx 坐标。_file_preview 的 xlsx 分支：
// header=sheet 第1行(openpyxl row1)，rows=第2..30行。故数据行 i 在第 sheetRow=i+2，
// 列 ci 在第 sheetCol=ci+1 → 坐标 = {letter(ci+1)}{i+2}。
function xyToCell(ri, ci) {
  const c = ci + 1;
  let col = "";
  let n = c;
  while (n > 0) {
    const rem = (n - 1) % 26;
    col = COL_LETTERS[rem] + col;
    n = Math.floor((n - 1) / 26);
  }
  return `${col}${ri + 2}`;
}

function useToolInvoke() {
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const run = async (name, args) => {
    setBusy(true);
    setMsg("");
    try {
      const r = await api.invokeTool(name, args);
      const txt = r && (r.result || r.error);
      const isErr = !txt || (typeof txt === "string" && /^(错误|未安装|失败)/.test(txt));
      setMsg(typeof txt === "string" ? txt : JSON.stringify(r || {}));
      return !isErr;
    } catch (e) {
      setMsg((e && e.message) || "调用失败");
      silentWarn(e, "OfficePreview");
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, msg, setMsg, run };
}

// ── P0.2 xlsx 可编辑表格 ─────────────────────────────
export function EditableTable({ path, name, header = [], rows = [], total = 0 }) {
  // grid: rows 数组，每格 {v, dirty, editing}。仅在原数据上克隆，不改后端。
  const [grid, setGrid] = React.useState(() => rows.map((r) => (r || []).map((c) => ({ v: c, dirty: false }))));
  const { busy, msg, setMsg, run } = useToolInvoke();
  const [saved, setSaved] = React.useState(false);

  const dirtyCells = React.useMemo(() => {
    const out = {};
    grid.forEach((r, ri) => r.forEach((c, ci) => {
      if (c.dirty) {
        let v = c.v;
        const orig = (rows[ri] || [])[ci];
        // 数值类型保留：原值是 number/可数字、用户输入为纯数字串 → 存为数字（否则 xlsx 变文本）
        const isNumericStr = typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v.trim());
        if (typeof orig === "number" && isNumericStr) v = Number(v.trim());
        out[xyToCell(ri, ci)] = v;
      }
    }));
    return out;
  }, [grid, rows]);

  const nDirty = Object.keys(dirtyCells).length;
  const colWidth = Math.max(header.length, ...(rows.map((r) => (r || []).length)));

  const setVal = (ri, ci, v) => {
    setSaved(false);
    setGrid((g) => {
      const ng = g.map((row) => row.slice());
      ng[ri] = ng[ri].map((c) => ({ ...c }));
      const orig = (rows[ri] || [])[ci];
      ng[ri][ci] = { v, dirty: String(v) !== String(orig ?? "") };
      return ng;
    });
  };

  const save = async () => {
    if (nDirty === 0) { setMsg("没有需要保存的修改"); return; }
    const ok = await run("xlsx_edit", { path, cells: dirtyCells });
    if (ok) {
      setSaved(true);
      setGrid((g) => g.map((row) => row.map((c) => ({ v: c.v, dirty: false }))));
    }
  };

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ opacity: .8, marginBottom: 4 }}>
        📊 {name}（{total > 0 ? total + " 行" : (rows.length || 0) + " 行"}）· 可点击单元格编辑
      </div>
      <div style={{ overflow: "auto", maxHeight: 320, border: "1px solid var(--border)", borderRadius: "var(--r-md)" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--fs-sm)" }}>
          {header.length > 0 && (
            <thead><tr>{(header || []).map((h, i) => (
              <th key={i} style={{ padding: "4px 8px", background: "var(--bg-3)", textAlign: "left", fontWeight: 600, borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>{h}</th>
            ))}</tr></thead>
          )}
          <tbody>
            {(grid || []).map((r, ri) => (
              <tr key={ri}>
                {Array.from({ length: colWidth || 1 }).map((_, ci) => {
                  const cell = r[ci] || { v: "", dirty: false };
                  return (
                    <td key={ci} style={{
                      padding: "2px 4px", borderBottom: "1px solid rgba(128,140,160,.12)",
                      background: cell.dirty ? "rgba(255,214,102,.25)" : undefined,
                      minWidth: 60,
                    }}>
                      <input
                        value={cell.v}
                        onChange={(e) => setVal(ri, ci, e.target.value)}
                        style={{ width: "100%", boxSizing: "border-box", border: "1px solid transparent", background: "transparent", fontSize: 12, padding: "2px 2px", outline: "none" }}
                        onFocus={(e) => (e.target.style.border = "1px solid var(--accent, #4a8cf7)", e.target.style.background = "rgba(74,140,247,.06)")}
                        onBlur={(e) => (e.target.style.border = "1px solid transparent", e.target.style.background = "transparent")}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 6, display: "flex", gap: 8, alignItems: "center", fontSize: 12, flexWrap: "wrap" }}>
        <button className="msg-op" onClick={save} disabled={busy || nDirty === 0} style={nDirty > 0 && !busy ? { borderColor: "var(--accent, #4a8cf7)", color: "var(--accent, #4a8cf7)" } : undefined}>
          {busy ? "保存中…" : (nDirty > 0 ? `保存 ${nDirty} 处修改` : "保存修改")}
        </button>
        {saved && <span style={{ color: "var(--success, #2e9e5b)" }}>✓ 已就地保存（xlsx_edit）</span>}
        {msg && !saved && <span style={{ opacity: .75 }}>{msg}</span>}
      </div>
    </div>
  );
}

// ── P0.3 docx 块级就地编辑 ───────────────────────────
// docx_read 输出按空行分块（段落/表格/代码），把每块渲染成 markdown 并提供 ✎ 替换。
// docx_read 输出是 markdown 结构；docx_edit.replace 却匹配 docx 真实文本（无 #/-/| 标记）。
// 把块剥离回"docx 段落里的原文子串"，find 才命中性高。
function stripMdForFind(block) {
  let t = String(block || "").trim();
  const lines = t.split("\n").filter((l) => l.trim());
  if (lines.length === 1) {
    // 标题 / 列表 / 普通行
    let s = lines[0].replace(/^#{1,6}\s+/, "").replace(/^[-*+]\s+/, "").replace(/^\d+[.)]\s+/, "").replace(/^>\s*/, "");
    return s.trim();
  }
  return ""; // 多行块（表格/代码/多段）无法单段命中，禁就地编辑
}

export function DocxEditable({ path, name, content = "" }) {
  const [blocks, setBlocks] = React.useState(() =>
    String(content || "").split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean));
  // 父级 content（工具初始预览）变更时同步一次（新消息），本地已编辑的 state 不被打断
  const lastContent = React.useRef(String(content || ""));
  React.useEffect(() => {
    const c = String(content || "");
    if (c !== lastContent.current) {
      lastContent.current = c;
      setBlocks(c.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean));
    }
  }, [content]);
  const finds = React.useMemo(() => blocks.map(stripMdForFind), [blocks]);
  const [editing, setEditing] = React.useState(null);
  const [draft, setDraft] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");

  const replaceBlock = async (idx) => {
    const find = finds[idx];
    const replace = draft;
    if (!find || !replace || find === replace) { setEditing(null); return; }
    setBusy(true); setMsg("");
    try {
      const r = await api.invokeTool("docx_edit", { path, action: "replace", find, replace });
      const t = r && (r.result || r.error);
      const isErr = !t || (typeof t === "string" && /^(错误|未安装|失败)/.test(t));
      if (isErr) {
        setMsg(typeof t === "string" ? t : "替换失败");
      } else {
        // 就地替换成功：本地即时更新该块文本，使预览立刻反映改动（无需重拉文件）
        setMsg("✓ 已就地替换（docx_edit）");
        setBlocks((prev) => prev.map((b, i) => (i === idx ? String(replace) : b)));
      }
      setEditing(null);
    } catch (e) { setMsg((e && e.message) || "替换失败"); silentWarn(e, "DocxEditable"); }
    finally { setBusy(false); }
  };

  const editableCount = finds.filter(Boolean).length;

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ opacity: .8, marginBottom: 4 }}>📄 {name}（Word 内容预览）{editableCount > 0 ? `· ${editableCount} 段可 ✎ 就地编辑` : "· 只读展示（表格/代码块请在对话中指示 AI 修改）"}</div>
      <div style={{ maxHeight: 380, overflow: "auto", borderRadius: "var(--r-md)", border: "1px solid var(--border)", padding: 8, background: "var(--bg-1)" }}>
        {blocks.map((b, i) => (
          <div key={i} style={{ position: "relative", margin: "2px 0" }} onMouseEnter={(e) => { const el = e.currentTarget.querySelector(".blk-edit"); if (el) el.style.opacity = 1; }} onMouseLeave={(e) => { const el = e.currentTarget.querySelector(".blk-edit"); if (el) el.style.opacity = 0; }}>
            <div className="blk-edit" style={{ position: "absolute", right: 2, top: 0, opacity: 0, transition: "opacity .15s", zIndex: 2 }}>
              {editing === i || !finds[i] ? null : (
                <button className="msg-op" title="就地替换此段落" onClick={() => { setEditing(i); setDraft(finds[i]); }}>✎</button>
              )}
            </div>
            {editing === i ? (
              <div style={{ display: "flex", gap: 6, flexDirection: "column", border: "1px solid var(--accent,#4a8cf7)", borderRadius: 6, padding: 4 }}>
                <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={Math.max(1, Math.min(6, String(draft).split("\n").length + 1))} style={{ width: "100%", boxSizing: "border-box", fontSize: 12, fontFamily: "inherit" }} />
                <div style={{ display: "flex", gap: 6 }}>
                  <button className="msg-op" onClick={() => replaceBlock(i)} disabled={busy}>{busy ? "保存中…" : "保存替换"}</button>
                  <button className="msg-op" onClick={() => setEditing(null)}>取消</button>
                </div>
              </div>
            ) : (
              <Markdown text={b} deferCode={false} />
            )}
          </div>
        ))}
      </div>
      {msg && <div style={{ marginTop: 4, fontSize: 12, opacity: .75 }}>{msg}</div>}
    </div>
  );
}

// ── pptx/pdf 结构化预览（编辑由对话内 AI 再调工具）──
export function TextDocPreview({ data }) {
  const d = data;
  const isPptx = !!d.pptx;
  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ opacity: .8, marginBottom: 4 }}>📄 {d.name}{isPptx ? "（PPT 内容预览）" : d.docx ? "（Word 内容预览）" : "（文档预览）"}</div>
      {d.content ? (
        <div style={{ maxHeight: 360, overflow: "auto", borderRadius: "var(--r-md)", border: "1px solid var(--border)", padding: 8, background: "var(--bg-1)" }}>
          <Markdown text={d.content} deferCode={false} />
        </div>
      ) : (
        <div style={{ opacity: .8 }}>（无可用文本预览{d.reason ? "：" + d.reason : ""}）</div>
      )}
    </div>
  );
}
