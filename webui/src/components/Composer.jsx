import React from "react";
import * as api from "../api.js";
import { ToastContext } from "./FlashToast.jsx";
import { Icon } from "./icons.jsx";
import { promptDialog } from "../dialog.js";

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

// 附件大小可读化（chip 上标注，避免用户拖了超大文件却不知情）
function fmtSize(n) {
  const v = Number(n) || 0;
  if (v >= 1048576) return `${(v / 1048576).toFixed(1)}MB`;
  if (v >= 1024) return `${Math.round(v / 1024)}KB`;
  return `${v}B`;
}

// 单文件上限（base64 后 ≈ 1.34×）：与后端 UPLOAD_BODY_MAX(64MB) 留余量，超限前端先拦
const MAX_UPLOAD_BYTES = 48 * 1024 * 1024;
const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp|ico|avif|svg)$/i;

export default React.forwardRef(function Composer({ busy, onSend, onStop, isTask = true, onPluginRun }, ref) {
  const [text, setText] = React.useState("");
  const [cmdOpen, setCmdOpen] = React.useState(false);   // 统一命令菜单（指令/命令/插件）
  const [slashQuery, setSlashQuery] = React.useState("");  // 输入 /xxx 时的命令过滤词
  const [focused, setFocused] = React.useState(false);
  const [dirOpen, setDirOpen] = React.useState(false);
  const [prompts, setPrompts] = React.useState([]);
  const [pluginApps, setPluginApps] = React.useState([]);  // 应用型插件 [{trigger, name, desc}]
  const [dirs, setDirs] = React.useState(null);
  const [attachments, setAttachments] = React.useState([]);
  const [uploading, setUploading] = React.useState(0);
  const [tokens, setTokens] = React.useState(0);
  const { toast } = React.useContext(ToastContext);
  const fileRef = React.useRef(null);
  const taRef = React.useRef(null);
  const cmdEntryRef = React.useRef(null);
  const dirBoxRef = React.useRef(null);
  const histRef = React.useRef([]);
  const histIdxRef = React.useRef(-1);
  const histDraftRef = React.useRef("");
  const attachmentsRef = React.useRef([]);
  // 发送回调 / 待发队列：busy 时先停止生成，待 busy 回落后用**最新**的 onSend 发出
  // （旧实现 setTimeout 调用的是 busy=true 那次的闭包，onSend 首行 `if(busy)return`
  //   会把消息静默丢掉）。
  const onSendRef = React.useRef(onSend);
  const pendingSendRef = React.useRef(null);
  React.useEffect(() => { onSendRef.current = onSend; });
  React.useEffect(() => {
    if (!busy && pendingSendRef.current) {
      const p = pendingSendRef.current;
      pendingSendRef.current = null;
      try { onSendRef.current && onSendRef.current(p.text, p.atts); } catch (e) { silentWarn(e, "Composer"); }
    }
  }, [busy]);

  React.useEffect(() => { attachmentsRef.current = attachments; }, [attachments]);

  // 命令 / 目录菜单：点击外部或 Esc 关闭（此前缺失，菜单会一直挂着）
  React.useEffect(() => {
    if (!cmdOpen && !dirOpen) return undefined;
    const onDoc = (e) => {
      if (cmdOpen && !(cmdEntryRef.current && cmdEntryRef.current.contains(e.target))) setCmdOpen(false);
      if (dirOpen && !(dirBoxRef.current && dirBoxRef.current.contains(e.target))) setDirOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") { setCmdOpen(false); setDirOpen(false); } };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [cmdOpen, dirOpen]);

  // 释放图片预览 blob URL（防长会话内存累积）：移除单条 / 发送后清空 / 组件卸载
  const revokeUrl = (a) => {
    try { if (a && a.url && String(a.url).startsWith("blob:")) URL.revokeObjectURL(a.url); } catch (e) { silentWarn(e, "Composer"); }
  };
  const clearAttachments = React.useCallback(() => {
    (attachmentsRef.current || []).forEach(revokeUrl);
    setAttachments([]);
  }, []);
  const removeAttachment = (a) => {
    revokeUrl(a);
    setAttachments((list) => list.filter((x) => (x.id || x.path) !== (a.id || a.path)));
  };
  React.useEffect(() => () => { (attachmentsRef.current || []).forEach(revokeUrl); }, []);

  // ── 应用指令：变量填充（{{TEXT}}/{{DATE}}/{ASK:}）+ 选中文本 + 自动发送 ──
  const applyPrompt = async (p, replaceAll = false) => {
    const ta = taRef.current;
    const sel = ta ? String(text).slice(ta.selectionStart || 0, ta.selectionEnd || 0) : "";
    const seed = sel || (replaceAll ? "" : String(text || "").trim());
    // 先解析 {ASK:} 再插入用户文本（且用函数替换器避免 $& 等替换模式展开）：
    // 否则选中文本里若含 "{ASK:...}" 会被误当指令弹窗、文本含 "$&" 会被错误展开。
    let t = String(p.text || "").replace(/\{\{DATE\}\}/g, () => todayStr());
    for (const a of t.match(/\{ASK:([^}]+)\}/g) || []) {
      const ans = await promptDialog(a.slice(5, -1), "");
      t = t.replace(a, () => ans || "");
    }
    t = t.replace(/\{\{TEXT\}\}/g, () => seed);
    setCmdOpen(false);
    setSlashQuery("");
    if (p.auto_send && String(t).trim()) {
      setText("");
      clearAttachments();  // 与 submit() 一致：自动发送后清空已选附件，防 chip 残留
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

  // 斜杠菜单合并项（指令 → 内置命令 → 插件触发词）：统一键盘导航/高亮，避免三处各写一遍
  const slashItems = React.useMemo(() => ([
    ...slashPrompts.map((p) => ({
      kind: "prompt", key: `p:${p.id}`,
      label: `${p.icon ? `${p.icon} ` : ""}${p.name}`,
      sub: p.shortcut || p.desc || String(p.text || "").slice(0, 22),
      // 输入 /xxx 过滤时整段替换；点击按钮调用时保留已输入文本
      run: () => applyPrompt(p, !!slashQuery),
    })),
    ...slashCmds.map((s) => ({
      kind: "cmd", key: `c:${s.cmd}`, label: s.cmd, sub: s.desc,
      run: () => { setText(s.cmd === "/clear" ? "" : s.text); setCmdOpen(false); setSlashQuery(""); taRef.current?.focus(); },
    })),
    ...pluginApps.map((s) => ({
      kind: "plugin", key: `g:${s.trigger}`, label: s.trigger, sub: s.desc || "插件应用",
      run: () => { setText(s.trigger + " "); setCmdOpen(false); setSlashQuery(""); taRef.current?.focus(); },
    })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ]), [slashPrompts, slashCmds, pluginApps, text]);

  // 按类型分组渲染（键盘导航仍用扁平 slashItems 的全局下标）
  const cmdGroups = React.useMemo(() => {
    const labelByKind = { prompt: "指令", cmd: "命令", plugin: "插件" };
    const groups = [];
    slashItems.forEach((it, i) => {
      let g = groups[groups.length - 1];
      if (!g || g.kind !== it.kind) { g = { kind: it.kind, label: labelByKind[it.kind], items: [] }; groups.push(g); }
      g.items.push({ it, i });
    });
    return groups;
  }, [slashItems]);

  // 键盘导航：斜杠菜单高亮项（空菜单/查询变化时归零）
  const [slashIdx, setSlashIdx] = React.useState(0);
  React.useEffect(() => { setSlashIdx(0); }, [slashQuery, cmdOpen]);

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
    let alive = true;
    api.getPrompts().then((p) => { if (alive) p && setPrompts(p); }).catch(() => {});
    api.getDirs().then((d) => { if (alive) d && setDirs(d); }).catch(() => {});
    // 应用型插件（kind=应用型）：取启用项的触发词，供输入框 `/触发词 参数` 直接执行
    api.getPlugins().then((r) => {
      if (!alive || !r) return;
      const apps = (r.installed || [])
        .filter((p) => p && p.kind === "应用型" && p.enabled !== false && p.trigger)
        .map((p) => ({ trigger: String(p.trigger), name: p.name, desc: p.description || "插件应用" }));
      setPluginApps(apps);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // ── 对外暴露：insertText（引用/编辑用）、addFiles（粘贴/拖拽统一入口）──
  React.useImperativeHandle(ref, () => ({
    insertText: (t, focus = true) => {
      setText(t);
      setCmdOpen(false);
      setDirOpen(false);
      if (focus) setTimeout(() => taRef.current?.focus(), 30);
    },
    addFiles: (files) => addFiles(files),
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

  // ── 附件：图片走 /v1/upload（超限自动压缩、进视觉链路）；其它文件走
  // /v1/files/upload（原样落盘、随消息给 AI 路径由工具层读取）。支持多选/粘贴/拖拽。──
  const uploadOne = (file) => {
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      toast(`文件过大（>${Math.round(MAX_UPLOAD_BYTES / 1048576)}MB）：${file.name}`);
      return;
    }
    const isImage = /^image\//.test(file.type || "") || IMAGE_RE.test(file.name || "");
    const previewUrl = isImage ? URL.createObjectURL(file) : "";
    const reader = new FileReader();
    reader.onload = async () => {
      const b64 = String(reader.result || "");
      setUploading((n) => n + 1);
      try {
        const r = isImage
          ? await api.uploadImage(b64, file.name)
          : await api.uploadFile(b64, file.name);
        if (r && r.path) {
          const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
          setAttachments((a) => [...a, {
            id, path: r.path,
            name: r.name || file.name,
            size: typeof r.size === "number" ? r.size : file.size,
            kind: isImage ? "image" : "file",
            url: previewUrl,
          }]);
          if (r.note) toast("🖼 " + r.note);
        } else {
          if (previewUrl) URL.revokeObjectURL(previewUrl);
          toast("上传失败：" + file.name);
        }
      } catch (e) {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        silentWarn(e, "Composer");
        toast(`上传失败：${file.name}（${e && e.message ? e.message : "未知错误"}）`);
      }
      setUploading((n) => Math.max(0, n - 1));
    };
    reader.onerror = () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      toast("读取失败：" + file.name);
    };
    reader.readAsDataURL(file);
  };

  // 批量添加（多选/粘贴/拖拽共用入口）；去重同一次选择里的重复文件
  const addFiles = (fileList) => {
    const list = Array.from(fileList || []);
    if (!list.length) return;
    list.forEach(uploadOne);
  };

  // 应用型插件执行：命中触发词（如 `/鲸群 loop 5`）时在本机执行，输出交给上层展示。
  // 触发词后面必须是行尾或空白，避免 `/鲸群X` 被误当命中。
  const matchPluginApp = (v) => pluginApps.find((a) => {
    if (!v.startsWith(a.trigger)) return false;
    return v.length === a.trigger.length || /\s/.test(v.charAt(a.trigger.length));
  }) || null;

  const runPluginApp = async (app, arg) => {
    setText("");
    setCmdOpen(false);
    setSlashQuery("");
    toast(`🐋 正在运行插件「${app.name}」…`);
    let out;
    try {
      const r = await api.pluginRun(app.name, arg);
      out = r && r.ok ? String(r.output || "") : `插件执行失败：${(r && r.error) || "未知错误"}`;
    } catch (e) {
      out = `插件执行失败：${e && e.message ? e.message : "未知错误"}`;
    }
    if (onPluginRun) onPluginRun(out);
    else setText(out);
  };

  const submit = () => {
    const v = text.trim();
    // 允许「只发附件不发文字」：只要有图片/文件即可发送
    if (!v && attachments.length === 0) return;
    // 应用型插件触发词命中：本机执行插件，输出作为本地消息展示，不进模型历史
    const app = matchPluginApp(v);
    if (app) {
      runPluginApp(app, v.slice(app.trigger.length).trim());
      return;
    }
    // 发送即打断：busy 时先停止当前生成，挂入待发队列——待 busy 回落后再发出
    if (busy) {
      onStop && onStop();
      pendingSendRef.current = { text: v, atts: attachments };
      setText("");
      clearAttachments();
      toast("⏹ 已停止当前生成，稍后发送新消息…");
      return;
    }
    setText("");
    setCmdOpen(false);
    // 历史记录（上限 200，去尾重复）
    const hist = histRef.current;
    if (v && hist[hist.length - 1] !== v) {
      hist.push(v);
      if (hist.length > 200) hist.shift();
      try {
        localStorage.setItem(HIST_KEY, JSON.stringify(hist));
      } catch (e) { silentWarn(e, "Composer"); }
    }
    histIdxRef.current = -1;
    onSend(v, attachments);
    clearAttachments();
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
    // 斜杠菜单开启时的键盘导航（↑↓ 选择 / Enter 确认），优先于「Enter 发送」
    if (cmdOpen && slashItems.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); setSlashIdx((i) => Math.min(slashItems.length - 1, i + 1)); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setSlashIdx((i) => Math.max(0, i - 1)); return; }
      if (e.key === "Enter" && !e.shiftKey) {
        if ((e.nativeEvent && e.nativeEvent.isComposing) || e.keyCode === 229) return;
        e.preventDefault();
        slashItems[slashIdx]?.run?.();
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      // 输入法组合中（拼音/日文等）：回车是「选词」，绝不能当作发送
      if ((e.nativeEvent && e.nativeEvent.isComposing) || e.keyCode === 229) return;
      e.preventDefault();
      submit();
    }
    if (e.key === "Escape") {
      setCmdOpen(false);
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
          {attachments.map((a) => (
            <span className={"att-chip" + (a.kind === "image" ? " att-chip-img" : "")} key={a.id || a.path} title={a.path}>
              {a.kind === "image" && a.url ? (
                <img className="att-thumb" src={a.url} alt={a.name} />
              ) : (
                <Icon name="file" size={14} />
              )}
              <span className="att-name">{a.name}</span>
              {a.size ? <span className="att-size">{fmtSize(a.size)}</span> : null}
              <button title="移除附件" aria-label="移除附件" onClick={() => removeAttachment(a)}><Icon name="x" size={12} /></button>
            </span>
          ))}
        </div>
      )}
      <div className="composer">
        <div className="composer-tools">
          <button className="cbtn" title="添加图片或文件（也可直接粘贴 / 拖拽到对话区）" aria-label="添加图片或文件" disabled={uploading > 0} onClick={() => fileRef.current?.click()}>
            {uploading > 0 ? (
              <span className="tool-spin" style={{ width: 13, height: 13 }} />
            ) : (
              <Icon name="paperclip" size={16} />
            )}
          </button>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv,.json,.zip,.7z,.rar"
            style={{ display: "none" }}
            onChange={(e) => {
              if (e.target.files && e.target.files.length) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <span className="cbtn-sep" aria-hidden="true" />
          <div className="dir-box" ref={dirBoxRef}>
            <button className="cbtn" title="工作目录" aria-label="工作目录" aria-haspopup="menu" aria-expanded={dirOpen} onClick={toggleDir}>
              <Icon name="folder" size={16} />
            </button>
            {dirOpen && (
              <div className="popmenu dir-menu" role="menu">
                <div className="dir-current" title={dirs?.active_dir}>
                  <Icon name="folder" size={13} /> {dirs?.active_dir || "加载中…"}
                </div>
                {(dirs?.subdirs || []).map((s) => (
                  <div className="dir-item" key={s} onClick={() => pickDir(s)}>
                    <Icon name="folder-open" size={13} /> {s.split(/[\\/]/).pop()}
                  </div>
                ))}
                {(dirs?.allowed_dirs || []).filter((a) => a !== dirs?.active_dir).map((a) => (
                  <div className="dir-item" key={a} onClick={() => pickDir(a)}>
                    <Icon name="archive" size={13} /> {a}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="cmd-entry" ref={cmdEntryRef}>
            <button
              className="cbtn"
              title="命令（指令 / 斜杠 / 插件）"
              aria-label="命令"
              aria-haspopup="menu"
              aria-expanded={cmdOpen}
              onClick={() => { setCmdOpen((o) => !o); setSlashQuery(""); }}
            >
              <Icon name="command" size={16} />
            </button>
            {cmdOpen && (
              <div className="popmenu slash-menu" role="menu">
                {cmdGroups.map((g) => (
                  <React.Fragment key={g.kind}>
                    <div className="slash-group-head">{g.label}</div>
                    {g.items.map(({ it, i }) => (
                      <div
                        className={`slash-item ${i === slashIdx ? "slash-item-on" : ""}`}
                        key={it.key}
                        role="menuitem"
                        onMouseEnter={() => setSlashIdx(i)}
                        onClick={() => it.run()}
                      >
                        <span className="si-label">{it.label}</span>
                        <span className="si-sub">{it.sub}</span>
                      </div>
                    ))}
                  </React.Fragment>
                ))}
                {slashItems.length === 0 && (
                  <div className="slash-empty">{slashQuery ? "无匹配命令" : "暂无指令（侧栏「指令库」可新建）"}</div>
                )}
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
            // 输入 /xxx 自动唤起命令菜单（整行以 / 开头时）
            const m = /^\/(\S*)$/.exec(v);
            if (m) {
              setSlashQuery(m[1]);
              setCmdOpen(true);
            } else if (!v.startsWith("/")) {
              setSlashQuery("");
            }
          }}
          onKeyDown={onKey}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          rows={1}
        />
        {tokens > 0 && !busy && (
          <span className="composer-tok" title="估算 token 数（输入消耗参考）">
            {tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : tokens} tok
          </span>
        )}
        {busy ? (
          <button className="send-btn send-stop" onClick={onStop} title="停止生成">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        ) : (
          <button className="send-btn" onClick={submit} disabled={!text.trim() && attachments.length === 0} title="发送">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
            </svg>
          </button>
        )}
      </div>
      {focused && !text.trim() && attachments.length === 0 && (
        <div className="composer-hint">
          <span>Enter 发送 · Shift+Enter 换行 · / 唤起命令 · Alt+↑↓ 历史</span>
        </div>
      )}
    </div>
  );
});