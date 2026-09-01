import React from "react";
import * as api from "../api.js";
import { ToastContext } from "./FlashToast.jsx";

import { silentWarn } from "../quiet.js";
const SLASH_COMMANDS = [
  { cmd: "/code", desc: "插入代码块", text: "```\n\n```" },
  { cmd: "/quote", desc: "插入引用块", text: "> " },
  { cmd: "/table", desc: "插入表格模板", text: "| 列1 | 列2 |\n| --- | --- |\n| 内容 | 内容 |" },
  { cmd: "/clear", desc: "清空输入" },
];

const DRAFT_KEY = "whaletalk.draft";
const HIST_KEY = "whaletalk.input.history";

function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export default React.forwardRef(function Composer({ busy, onSend, onStop, isTask = true }, ref) {
  const [text, setText] = React.useState("");
  const [slashOpen, setSlashOpen] = React.useState(false);
  const [slashQuery, setSlashQuery] = React.useState("");  // 输入 /xxx 时的指令过滤词
  const [promptOpen, setPromptOpen] = React.useState(false);
  const [dirOpen, setDirOpen] = React.useState(false);
  const [prompts, setPrompts] = React.useState([]);
  const [pluginTriggers, setPluginTriggers] = React.useState([]);
  const [dirs, setDirs] = React.useState(null);
  const [attachments, setAttachments] = React.useState([]);
  const [uploading, setUploading] = React.useState(false);
  const [tokens, setTokens] = React.useState(0);
  const { toast } = React.useContext(ToastContext);
  const fileRef = React.useRef(null);
  const taRef = React.useRef(null);
  const histRef = React.useRef([]);
  const histIdxRef = React.useRef(-1);
  const histDraftRef = React.useRef("");

  // ── 应用指令：变量填充（{{TEXT}}/{{DATE}}/{ASK:}）+ 选中文本 + 自动发送 ──
  const applyPrompt = (p, replaceAll = false) => {
    const ta = taRef.current;
    const sel = ta ? String(text).slice(ta.selectionStart || 0, ta.selectionEnd || 0) : "";
    const seed = sel || (replaceAll ? "" : String(text || "").trim());
    let t = String(p.text || "")
      .replace(/\{\{TEXT\}\}/g, seed)
      .replace(/\{\{DATE\}\}/g, todayStr());
    for (const a of t.match(/\{ASK:([^}]+)\}/g) || []) {
      const ans = window.prompt(a.slice(5, -1), "");
      t = t.replace(a, ans || "");
    }
    setSlashOpen(false);
    setPromptOpen(false);
    setSlashQuery("");
    if (p.auto_send && String(t).trim()) {
      setText("");
      onSend(t);
    } else {
      setText(t);
      setTimeout(() => taRef.current?.focus(), 30);
    }
    if (p.id && !p.builtin) api.usePrompt(p.id).catch(() => {});
  };

  // 可用指令（禁用的不出现在调用列表）
  const usablePrompts = React.useMemo(
    () => (prompts || []).filter((p) => p.enabled !== false),
    [prompts]
  );

  // 斜杠过滤：输入 /周报 或 /weekly 都能命中
  const slashPrompts = React.useMemo(() => {
    const q = String(slashQuery || "").toLowerCase();
    if (!q) return usablePrompts;
    return usablePrompts.filter((p) =>
      `${p.name} ${p.shortcut} ${p.desc} ${(p.tags || []).join(" ")}`.toLowerCase().includes(q)
    );
  }, [usablePrompts, slashQuery]);

  const slashCmds = React.useMemo(() => {
    const q = String(slashQuery || "").toLowerCase();
    if (!q) return SLASH_COMMANDS;
    return SLASH_COMMANDS.filter((s) => `${s.cmd} ${s.desc}`.toLowerCase().includes(q));
  }, [slashQuery]);

  // ── 草稿持久化（对齐原程序：停止输入后保存，启动恢复）──
  React.useEffect(() => {
    try {
      const draft = localStorage.getItem(DRAFT_KEY);
      if (draft && draft.trim()) setText(draft);
    } catch (e) { silentWarn(e, "Composer"); }
  }, []);

  const draftTimer = React.useRef(null);
  React.useEffect(() => {
    clearTimeout(draftTimer.current);
    if (text) {
      draftTimer.current = setTimeout(() => {
        try {
          localStorage.setItem(DRAFT_KEY, text);
        } catch (e) { silentWarn(e, "Composer"); }
      }, 1200);
    }
    return () => clearTimeout(draftTimer.current);
  }, [text]);

  // ── 输入历史（Alt+Up/Down，对齐原程序 200 条）──
  React.useEffect(() => {
    try {
      histRef.current = JSON.parse(localStorage.getItem(HIST_KEY) || "[]");
    } catch (e) { silentWarn(e, "Composer"); }
  }, []);

  // ── token 实时估算（对齐原程序：300ms 防抖）──
  React.useEffect(() => {
    const iv = setTimeout(() => {
      const t = text.trim();
      if (!t) {
        setTokens(0);
        return;
      }
      setTokens(Math.max(1, Math.round(t.length / 1.5)));
    }, 300);
    return () => clearTimeout(iv);
  }, [text]);

  // ── 指令 / 目录 / 插件触发词加载 ──
  React.useEffect(() => {
    api.getPrompts().then((p) => p && setPrompts(p)).catch(() => {});
    api.getDirs().then((d) => d && setDirs(d)).catch(() => {});
    api.getContext().then((c) => c && c.tools && setPluginTriggers([])).catch(() => {});
  }, []);

  // ── 对外暴露：insertText（引用/编辑用）──
  React.useImperativeHandle(ref, () => ({
    insertText: (t, focus = true) => {
      setText(t);
      setSlashOpen(false);
      setPromptOpen(false);
      setDirOpen(false);
      if (focus) setTimeout(() => taRef.current?.focus(), 30);
    },
    focus: () => taRef.current?.focus(),
  }));

  React.useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  }, [text]);

  const toggleDir = async () => {
    setDirOpen(!dirOpen);
    if (!dirs) {
      try {
        const d = await api.getDirs();
        if (d) setDirs(d);
      } catch (e) { silentWarn(e, "Composer"); }
    }
  };

  const pickDir = async (path) => {
    try {
      const r = await api.setDir(path);
      if (r && r.ok) setDirs({ ...dirs, active_dir: r.active_dir });
      setDirOpen(false);
    } catch (e) { silentWarn(e, "Composer"); }
  };

  const onPickImage = (file) => {
    if (!file || uploading) return;
    const reader = new FileReader();
    reader.onload = async () => {
      const b64 = String(reader.result || "");
      setUploading(true);
      try {
        const r = await api.uploadImage(b64, file.name);
        if (r && r.path) {
          setAttachments((a) => [...a, { path: r.path, name: r.name }]);
          if (r.note) toast("🖼 " + r.note);
        }
      } catch (e) { silentWarn(e, "Composer"); }
      setUploading(false);
    };
    reader.readAsDataURL(file);
  };

  const submit = () => {
    const v = text.trim();
    if (!v || busy) return;
    // 发送即打断：busy 时挂起待发
    if (busy) {
      onStop && onStop();
      setTimeout(() => onSend && onSend(v, attachments), 350);
      setText("");
      setAttachments([]);
      return;
    }
    setText("");
    setSlashOpen(false);
    setPromptOpen(false);
    // 历史记录（上限 200，去尾重复）
    const hist = histRef.current;
    if (hist[hist.length - 1] !== v) {
      hist.push(v);
      if (hist.length > 200) hist.shift();
      try {
        localStorage.setItem(HIST_KEY, JSON.stringify(hist));
      } catch (e) { silentWarn(e, "Composer"); }
    }
    histIdxRef.current = -1;
    onSend(v, attachments);
    setAttachments([]);
  };

  // ── B9 编辑器增强辅助 ──
  const applyTabIndent = (shift) => {
    const ta = taRef.current;
    if (!ta) return;
    const { selectionStart: s, selectionEnd: e } = ta;
    const value = text;
    if (s === e) {
      // 光标处插入 4 空格（或无选区整行）
      const start = value.lastIndexOf("\n", s - 1) + 1;
      if (!shift) {
        setText(value.slice(0, s) + "    " + value.slice(e));
      } else {
        const lineIndent = value.slice(start).match(/^ */)?.[0] || "";
        if (lineIndent.length >= 4) setText(value.slice(0, start) + value.slice(start + 4));
        else if (lineIndent.length > 0) setText(value.slice(0, start) + value.slice(start + lineIndent.length));
      }
    } else {
      // 选区多行缩进/反缩进
      const lines = value.slice(0, s).split("\n");
      const startLine = lines.length - 1;
      const linesEnd = value.slice(0, e).split("\n").length - 1;
      const parts = value.slice(0, s).split("\n");
      const segStart = parts[parts.length - 1].length + s - parts[parts.length - 1].length;
      const all = value.split("\n");
      if (!shift) {
        for (let i = startLine; i <= linesEnd; i++) all[i] = "    " + all[i];
      } else {
        for (let i = startLine; i <= linesEnd; i++) all[i] = all[i].replace(/^ {1,4}/, "");
      }
      setText(all.join("\n"));
    }
  };

  const wrapSelection = (left, right) => {
    const ta = taRef.current;
    if (!ta) return;
    const { selectionStart: s, selectionEnd: e } = ta;
    if (s === e) {
      setText(text.slice(0, s) + left + "文本" + right + text.slice(e));
    } else {
      setText(text.slice(0, s) + left + text.slice(s, e) + right + text.slice(e));
    }
  };

  const insertLink = () => {
    const ta = taRef.current;
    if (!ta) return;
    const s = ta.selectionStart;
    const url = "https://";
    const sel = text.slice(s, ta.selectionEnd) || "链接文字";
    setText(text.slice(0, s) + `[${sel}](${url})` + text.slice(ta.selectionEnd));
  };

  const insertCodeBlock = () => {
    const ta = taRef.current;
    if (!ta) return;
    const s = ta.selectionStart;
    setText(text.slice(0, s) + "```python\n" + text.slice(s, ta.selectionEnd) + "\n```" + text.slice(ta.selectionEnd));
  };

  const isMatchingPair = (a, b) => {
    const pairs = { "(": ")", "[": "]", "{": "}", "\"": "\"", "'": "'" };
    return pairs[a] === b;
  };

  const insertBracket = (ch) => {
    const ta = taRef.current;
    const close = { "(": ")", "[": "]", "{": "}", "\"": "\"", "'": "'" }[ch];
    const s = ta.selectionStart;
    const e = ta.selectionEnd;
    if (s !== e) {
      setText(text.slice(0, s) + ch + text.slice(s, e) + close + text.slice(e));
    } else {
      setText(text.slice(0, s) + ch + close + text.slice(s));
    }
    setTimeout(() => {
      ta.focus();
      ta.setSelectionRange(s + 1, s + 1);
    }, 10);
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
    if (e.key === "Escape") {
      setSlashOpen(false);
      setPromptOpen(false);
      setDirOpen(false);
    }
    // B9 编辑器增强：Tab 缩进 / Shift+Tab 反缩进
    if (e.key === "Tab") {
      e.preventDefault();
      applyTabIndent(e.shiftKey);
      return;
    }
    // Ctrl+B 加粗、Ctrl+I 斜体、Ctrl+K 链接、Ctrl+Shift+C 代码块
    if (e.ctrlKey || e.metaKey) {
      if (e.key.toLowerCase() === "b") { e.preventDefault(); wrapSelection("**", "**"); e.stopPropagation(); return; }
      if (e.key.toLowerCase() === "i") { e.preventDefault(); wrapSelection("*", "*"); e.stopPropagation(); return; }
      if (e.key.toLowerCase() === "k") { e.preventDefault(); insertLink(); e.stopPropagation(); return; }
      if (e.key.toLowerCase() === "c" && e.shiftKey) { e.preventDefault(); insertCodeBlock(); e.stopPropagation(); return; }
    }
    // 括号配对
    if (["(", "[", "{", "\"", "'"].includes(e.key)) {
      const ta = taRef.current;
      if (!ta) return;
      const selStart = ta.selectionStart;
      if (selStart !== ta.selectionEnd) return;
      e.preventDefault();
      insertBracket(e.key);
      return;
    }
    // Backspace 删整对括号
    if (e.key === "Backspace") {
      const ta = taRef.current;
      const pos = ta.selectionStart;
      if (pos !== ta.selectionEnd) return;
      const before = text.charAt(pos - 1);
      const after = text.charAt(pos);
      if (isMatchingPair(before, after)) {
        e.preventDefault();
        setText(text.slice(0, pos - 1) + text.slice(pos + 1));
      }
    }
    // Alt+Up/Down 输入历史
    if (e.altKey && e.key === "ArrowUp") {
      e.preventDefault();
      const hist = histRef.current;
      if (!hist.length) return;
      if (histIdxRef.current === -1) histDraftRef.current = text;
      const idx = Math.min(hist.length - 1, (histIdxRef.current === -1 ? hist.length : histIdxRef.current) - 1);
      if (idx < 0) return;
      histIdxRef.current = idx;
      setText(hist[idx]);
    }
    if (e.altKey && e.key === "ArrowDown") {
      e.preventDefault();
      if (histIdxRef.current === -1) return;
      if (histIdxRef.current >= histRef.current.length - 1) {
        histIdxRef.current = -1;
        setText(histDraftRef.current);
      } else {
        histIdxRef.current += 1;
        setText(histRef.current[histIdxRef.current]);
      }
    }
  };

  return (
    <div className="composer-wrap">
      {attachments.length > 0 && (
        <div className="composer-att">
          {attachments.map((a, i) => (
            <span className="att-chip" key={i}>
              🖼 {a.name}
              <button onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>×</button>
            </span>
          ))}
        </div>
      )}
      <div className="composer">
        <div className="composer-tools">
          <button className="cbtn" title="添加图片" disabled={uploading} onClick={() => fileRef.current?.click()}>
            {uploading ? (
              <span className="tool-spin" style={{ width: 13, height: 13 }} />
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="3" />
                <circle cx="9" cy="9" r="2" />
                <path d="M21 15l-5-5L5 21" />
              </svg>
            )}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files && e.target.files[0];
              if (f) onPickImage(f);
              e.target.value = "";
            }}
          />
          <div className="dir-box">
            <button className="cbtn" title="工作目录" onClick={toggleDir}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
              </svg>
            </button>
            {dirOpen && (
              <div className="dir-menu">
                <div className="dir-current" title={dirs?.active_dir}>
                  📁 {dirs?.active_dir || "加载中…"}
                </div>
                {(dirs?.subdirs || []).map((s) => (
                  <div className="dir-item" key={s} onClick={() => pickDir(s)}>
                    📂 {s.split(/[\\/]/).pop()}
                  </div>
                ))}
                {(dirs?.allowed_dirs || []).filter((a) => a !== dirs?.active_dir).map((a) => (
                  <div className="dir-item" key={a} onClick={() => pickDir(a)}>
                    🗂 {a}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="prompt-box">
            <button className="cbtn" title="指令" onClick={() => setPromptOpen(!promptOpen)}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
              </svg>
            </button>
            {promptOpen && (
              <div className="slash-menu prompt-menu">
                {usablePrompts.map((p) => (
                  <div
                    className="slash-item"
                    key={p.id}
                    title={p.desc || ""}
                    onClick={() => applyPrompt(p)}
                  >
                    <b>{p.icon ? `${p.icon} ` : ""}{p.name}</b>
                    <span>{p.desc || String(p.text || "").slice(0, 26)}</span>
                  </div>
                ))}
                {usablePrompts.length === 0 && (
                  <div className="slash-item"><span>暂无指令（侧栏「指令库」可新建）</span></div>
                )}
              </div>
            )}
          </div>
          <div className="slash-box">
            <button className="cbtn" title="斜杠命令" onClick={() => setSlashOpen(!slashOpen)}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M4 9h16M4 15h16" />
              </svg>
            </button>
            {slashOpen && (
              <div className="slash-menu">
                {slashPrompts.map((p) => (
                  <div
                    className="slash-item"
                    key={p.id}
                    title={p.desc || ""}
                    onClick={() => applyPrompt(p, true)}
                  >
                    <b>{p.icon ? `${p.icon} ` : ""}{p.name}</b>
                    <span>{p.shortcut || p.desc || String(p.text || "").slice(0, 22)}</span>
                  </div>
                ))}
                {slashPrompts.length === 0 && slashQuery && (
                  <div className="slash-item"><span>无匹配指令</span></div>
                )}
                {slashCmds.map((s) => (
                  <div
                    className="slash-item"
                    key={s.cmd}
                    onClick={() => {
                      setText(s.cmd === "/clear" ? "" : s.text);
                      setSlashOpen(false);
                      setSlashQuery("");
                      taRef.current?.focus();
                    }}
                  >
                    <b>{s.cmd}</b>
                    <span>{s.desc}</span>
                  </div>
                ))}
                {pluginTriggers.map((s) => (
                  <div
                    className="slash-item"
                    key={s}
                    onClick={() => {
                      setText(s + " ");
                      setSlashOpen(false);
                      taRef.current?.focus();
                    }}
                  >
                    <b>{s}</b>
                    <span>插件应用</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <textarea
          ref={taRef}
          className="composer-input"
          placeholder={isTask ? "输入任务，Enter 发送，Shift+Enter 换行，/ 唤起命令" : "输入消息，Enter 发送（对话模式 · 纯问答）"}
          value={text}
          onChange={(e) => {
            const v = e.target.value;
            setText(v);
            // 输入 /xxx 自动唤起指令搜索（整行以 / 开头时）
            const m = /^\/(\S*)$/.exec(v);
            if (m) {
              setSlashQuery(m[1]);
              setSlashOpen(true);
            } else if (!v.startsWith("/")) {
              setSlashQuery("");
            }
          }}
          onKeyDown={onKey}
          rows={1}
        />
        {busy ? (
          <button className="send-btn send-stop" onClick={onStop} title="停止生成">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        ) : (
          <button className="send-btn" onClick={submit} disabled={!text.trim()} title="发送">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
            </svg>
          </button>
        )}
      </div>
      <div className="composer-hint">
        <span>{tokens > 0 ? `约 ${tokens.toLocaleString()} token · ` : ""}Enter 发送 · Shift+Enter 换行 · Alt+↑↓ 历史</span>
        <span className="composer-hint-right">
          {isTask ? (
            <>
              <span className="mode-chip">🚀 任务模式</span>
              <span className="mode-chip">🔧 工具自动可用</span>
            </>
          ) : (
            <>
              <span className="mode-chip">💬 对话模式</span>
              <span className="mode-chip">纯问答 · 不调用工具</span>
            </>
          )}
        </span>
      </div>
    </div>
  );
});