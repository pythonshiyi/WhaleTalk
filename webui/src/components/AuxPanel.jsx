import React from "react";
import * as api from "../api.js";
import { ThemeContext, DisplayContext } from "../App.jsx";
import { Icon } from "./icons.jsx";
import Group from "./CollapsibleGroup.jsx";
import formatToolResult from "../formatToolResult.js";

import { silentWarn } from "../quiet.js";

// ── 启动即预取（不等打开面板才加载）──────────────────
const PREFETCHED = {
  cfg: null,
  ctx: null,
  st: null,
};
const prefetchPromise = (() => {
  const grab = (name, fn) =>
    api
      [fn]()
      .then((d) => {
        if (d) PREFETCHED[name] = d;
      })
      .catch(() => {});
  return Promise.all([grab("cfg", "getConfig"), grab("ctx", "getContext"), grab("st", "getStatus")]);
})();

// ── 可见感知轮询：仅在「页签激活 + 文档可见」时拉取；切回即刷新一次 ──
function useVisiblePolling(fn, interval, active) {
  const fnRef = React.useRef(fn);
  fnRef.current = fn;
  React.useEffect(() => {
    if (!active) return undefined;
    const tick = () => {
      if (typeof document === "undefined" || document.visibilityState === "visible") fnRef.current();
    };
    tick();
    const iv = setInterval(tick, interval);
    const onVis = () => { if (document.visibilityState === "visible") fnRef.current(); };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(iv);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [active, interval]);
}

// ── 小组件 ─────────────────────────────────────────
function MiniBtn({ icon, label, onClick, danger, active }) {
  return (
    <button
      className={`ico-btn ${danger ? "ico-danger" : ""} ${active ? "ico-on" : ""}`}
      title={label}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
    >
      <Icon name={icon} size={14} />
    </button>
  );
}

function Field({ label, hint, children }) {
  return (
    <div className="px-field" title={hint}>
      <div className="px-field-head">
        <span className="px-field-label">{label}</span>
        {hint && <span className="px-field-hint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function TglRow({ label, hint, on, onClick }) {
  return (
    <div className="px-tgl-row" title={hint}>
      <span>{label}</span>
      <button
        className={`toggle ${on ? "toggle-on" : ""}`}
        role="switch"
        aria-checked={!!on}
        aria-label={label}
        onClick={onClick}
      >
        <span className="toggle-knob" />
      </button>
    </div>
  );
}

function copyText(text) {
  const s = String(text == null ? "" : text);
  if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(s);
  return Promise.reject(new Error("clipboard unavailable"));
}

function fmtRel(m) {
  if (!m) return "";
  if (typeof m !== "number") return m;
  const t = m < 1e12 ? m * 1000 : m;
  const d = new Date(t);
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function fmtUptime(s) {
  const n = Math.max(0, Math.floor(Number(s) || 0));
  if (n < 60) return `${n}s`;
  if (n < 3600) return `${Math.floor(n / 60)}m`;
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  return m ? `${h}h${m}m` : `${h}h`;
}

// ── 文件 ───────────────────────────────────────────
const FILE_TYPE_BY_EXT = {
  image: ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico"],
  doc: ["md", "txt", "doc", "docx", "pdf", "rtf", "odt", "ppt", "pptx", "epub", "mobi"],
  code: ["py", "js", "jsx", "ts", "tsx", "java", "c", "cpp", "cs", "go", "rs", "rb", "php", "sh", "ps1", "html", "css", "json", "xml", "yml", "yaml", "vue", "sql"],
  data: ["csv", "xlsx", "xls", "tsv"],
  archive: ["zip", "rar", "7z", "tar", "gz"],
};
const FILE_TYPE_ICON = { folder: "folder", image: "image", doc: "file", code: "code", data: "table", archive: "archive", other: "file" };
const FILE_FILTERS = [
  { id: "all", label: "全部" },
  { id: "folder", label: "目录" },
  { id: "image", label: "图片" },
  { id: "doc", label: "文档" },
  { id: "code", label: "代码" },
  { id: "data", label: "数据" },
  { id: "archive", label: "压缩" },
  { id: "other", label: "其他" },
];
const extOf = (name) => (String(name || "").split(".").pop() || "").toLowerCase();
const fileTypeOf = (name, isDir) => {
  if (isDir) return "folder";
  const ext = extOf(name);
  for (const [t, exts] of Object.entries(FILE_TYPE_BY_EXT)) if (exts.includes(ext)) return t;
  return "other";
};
const epochOf = (m) => (m.mtime_epoch ? m.mtime_epoch : (typeof m.mtime === "number" ? m.mtime : (m.mtime ? Date.parse(m.mtime) / 1000 : 0)));
function sortEntries(list, key, dir) {
  const mul = dir === "desc" ? -1 : 1;
  return [...list].sort((a, b) => {
    let r;
    if (key === "name") r = String(a.name || "").localeCompare(String(b.name || ""), "zh");
    else if (key === "size") r = (a.size || 0) - (b.size || 0);
    else if (key === "type") r = (a.is_dir ? 0 : 1) - (b.is_dir ? 0 : 1) || String(extOf(a.name)).localeCompare(String(extOf(b.name)));
    else r = epochOf(a) - epochOf(b);
    return r * mul;
  });
}

function FilesTab({ onInject, active, onBadge }) {
  const [roots, setRoots] = React.useState(null);
  const [expanded, setExpanded] = React.useState({});
  const [children, setChildren] = React.useState({});
  const [loading, setLoading] = React.useState({});
  const [err, setErr] = React.useState("");
  const [busyPath, setBusyPath] = React.useState(null);
  const [q, setQ] = React.useState("");
  const [favs, setFavs] = React.useState(null);
  const [ftype, setFtype] = React.useState("all");
  const [sortKey, setSortKey] = React.useState(() => { try { return localStorage.getItem("wt_files_sort") || "time"; } catch { return "time"; } });
  const [sortDir, setSortDir] = React.useState(() => { try { return localStorage.getItem("wt_files_sortdir") || "desc"; } catch { return "desc"; } });
  const [collapsed, setCollapsed] = React.useState({});
  const [copiedPath, setCopiedPath] = React.useState("");
  const [selMode, setSelMode] = React.useState(false);
  const [selected, setSelected] = React.useState(() => new Set());
  const [bTip, setBTip] = React.useState("");
  const [renaming, setRenaming] = React.useState(null);
  const [renameVal, setRenameVal] = React.useState("");
  const [gResult, setGResult] = React.useState(null);
  const [gBusy, setGBusy] = React.useState(false);

  React.useEffect(() => { try { localStorage.setItem("wt_files_sort", sortKey); } catch (e) { silentWarn(e, "AuxPanel"); } }, [sortKey]);
  React.useEffect(() => { try { localStorage.setItem("wt_files_sortdir", sortDir); } catch (e) { silentWarn(e, "AuxPanel"); } }, [sortDir]);

  const load = React.useCallback(async (showLoading) => {
    try {
      const d = await api.listFiles();
      if (d) {
        setRoots(d);
        const m = {};
        (d.favs || []).forEach((f) => { m[f.path] = f; });
        setFavs(m);
        setErr("");
      } else if (showLoading) setErr("文件列表加载失败：后端未连接");
    } catch {
      if (showLoading) setErr("文件列表加载失败：后端未连接");
    }
  }, []);

  useVisiblePolling(() => load(false), 8000, active);

  const flashErr = (e) => { setErr(e && e.message ? e.message : "操作失败"); setTimeout(() => setErr(""), 3000); };
  const openFile = async (path) => {
    if (!path || busyPath) return;
    setBusyPath(path);
    try { await api.openFile(path); } catch (e) { flashErr(e); } finally { setBusyPath(null); }
  };
  const openDir = async (path) => {
    if (!path || busyPath) return;
    setBusyPath(path);
    try { await api.openDir(path); } catch (e) { flashErr(e); } finally { setBusyPath(null); }
  };
  const copyPath = (path) => copyText(path).then(() => { setCopiedPath(path); setTimeout(() => setCopiedPath(""), 1200); }).catch(() => flashErr("复制失败"));
  const toggleFav = async (path) => {
    try {
      const r = await api.favFile(path);
      setFavs((prev) => {
        const next = { ...(prev || {}) };
        if (r.faved) next[path] = { path, name: String(path).split(/[\\/]/).pop() };
        else delete next[path];
        return next;
      });
      setTimeout(() => load(false), 200);
    } catch (e) { flashErr(e); }
  };
  const toggle = async (path) => {
    const next = { ...expanded, [path]: !expanded[path] };
    setExpanded(next);
    if (next[path] && !children[path]) {
      setLoading((l) => ({ ...l, [path]: true }));
      try {
        const d = await api.listFiles(path);
        if (d && d.entries) {
          setChildren((c) => ({ ...c, [path]: d.entries }));
          setFavs((prev) => {
            const nv = { ...(prev || {}) };
            d.entries.forEach((en) => { if (en.faved) nv[en.path] = en; });
            return nv;
          });
        }
      } catch (e) { silentWarn(e, "AuxPanel"); flashErr(e); }
      setLoading((l) => ({ ...l, [path]: false }));
    }
  };

  const startRename = (meta) => { setRenaming(meta.path); setRenameVal(meta.name); };
  const cancelRename = () => { setRenaming(null); setRenameVal(""); };
  const reloadParent = async (p) => {
    const parent = String(p).replace(/[\\/][^\\/]+$/, "");
    if (expanded[parent]) {
      const d = await api.listFiles(parent);
      if (d && d.entries) setChildren((c) => ({ ...c, [parent]: d.entries }));
    }
  };
  const commitRename = async () => {
    const p = renaming; const nn = renameVal.trim();
    if (!p || !nn) { cancelRename(); return; }
    const oldName = String(p).split(/[\\/]/).pop();
    if (nn === oldName) { cancelRename(); return; }
    try {
      const r = await api.renameFile(p, nn);
      if (r && r.error) { flashErr(r.error); return; }
      const wasFav = isFav(p);
      await reloadParent(p);
      await load(false);
      if (wasFav && r && r.path) {
        try { await api.favFile(p); await api.favFile(r.path); } catch (e) { silentWarn(e, "AuxPanel"); }
      }
      flashTip(`已重命名为 ${nn}`);
    } catch (e) { flashErr(e); }
    cancelRename();
  };
  const doDelete = async (path, isDir) => {
    try {
      const r = await api.invokeTool("delete_file", { path });
      const msg = r && r.result != null ? String(r.result) : "";
      if (!r || r.error || /^错误/.test(msg)) { flashErr((r && r.error) || msg || "删除失败"); return; }
      await reloadParent(path);
      await load(false);
      flashTip(isDir ? "目录已移入回收站" : "文件已移入回收站");
    } catch (e) { flashErr(e); }
  };
  const runGlobalSearch = async () => {
    const query = q.trim();
    if (!query || !roots) return;
    setGBusy(true);
    try {
      const d = await api.searchFiles(query, roots.active_dir, 80);
      if (d && d.error) { flashErr(d.error); setGResult(null); }
      else setGResult(d || null);
    } catch (e) { flashErr(e); } finally { setGBusy(false); }
  };

  const isFav = (path) => !!(favs && favs[path]);
  const typeOk = (name, isDir) => ftype === "all" || fileTypeOf(name, isDir) === ftype;
  const matchesSearch = (name) => !q || String(name || "").toLowerCase().includes(q.toLowerCase());
  const passFilter = (name, isDir) => typeOk(name, isDir) && matchesSearch(name);
  const view = (list, isDirFn) => sortEntries(list.filter((m) => passFilter(m.name, isDirFn ? isDirFn(m) : !!m.is_dir)), sortKey, sortDir);

  const flashTip = (t) => { setBTip(t); setTimeout(() => setBTip(""), 1800); };
  const toggleSel = (p) => setSelected((s) => { const n = new Set(s); if (n.has(p)) n.delete(p); else n.add(p); return n; });
  const clearSel = () => setSelected(new Set());
  const startDrag = (e, p) => {
    try {
      e.dataTransfer.setData("application/x-whaletalk-path", p);
      e.dataTransfer.setData("text/plain", p);
      e.dataTransfer.effectAllowed = "copy";
    } catch (err) { silentWarn(err, "AuxPanel"); }
  };
  const batchCopy = () => copyText([...selected].join("\n")).then(() => flashTip(`已复制 ${selected.size} 条路径`)).catch(() => flashErr("复制失败"));
  const batchFav = async () => {
    try {
      for (const p of selected) await api.favFile(p);
      flashTip(`已收藏 ${selected.size} 项`);
      setTimeout(() => load(false), 200);
    } catch (e) { flashErr(e); }
  };
  const batchInject = async () => {
    if (!onInject) return;
    const list = [...selected];
    for (const p of list) await onInject(p);
    flashTip(`已注入 ${list.length} 个文件`);
  };

  const starBtn = (p, title) => (
    <button
      className={`fx-fav ${isFav(p) ? "on" : ""}`}
      title={isFav(p) ? "取消收藏" : title}
      aria-label={isFav(p) ? "取消收藏" : title}
      aria-pressed={isFav(p)}
      onClick={(e) => { e.stopPropagation(); toggleFav(p); }}
    >
      <Icon name="star" size={13} fill={isFav(p) ? "currentColor" : "none"} />
    </button>
  );

  const fileRow = (meta, indent) => {
    const p = meta.path;
    const missing = meta.exists === false;
    const checkable = !meta.is_dir && !missing;
    return (
      <div
        className={`fx-row ${missing ? "fx-missing" : ""} ${selMode && selected.has(p) ? "fx-sel" : ""}`}
        style={{ paddingLeft: 4 + (indent || 0) * 14 }}
        draggable={checkable}
        onDragStart={(e) => checkable && startDrag(e, p)}
      >
        {selMode && checkable && (
          <button className={`fx-check ${selected.has(p) ? "on" : ""}`} title={selected.has(p) ? "取消选择" : "选择"}
            aria-label="选择" aria-pressed={selected.has(p)} onClick={(e) => { e.stopPropagation(); toggleSel(p); }}>
            {selected.has(p) && <Icon name="check" size={11} />}
          </button>
        )}
        {starBtn(p, "收藏")}
        <span className="fx-ico"><Icon name={meta.is_dir ? "folder" : FILE_TYPE_ICON[fileTypeOf(meta.name, false)]} size={14} /></span>
        {renaming === p ? (
          <input
            className="tf-input fx-rename" autoFocus value={renameVal} aria-label="新名称"
            onChange={(e) => setRenameVal(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitRename(); } else if (e.key === "Escape") { e.preventDefault(); cancelRename(); } }}
            title="回车确认 · Esc 取消"
          />
        ) : (
          <span className={`fx-fname ${missing ? "fx-fname-missing" : ""}`} title={`${p}${missing ? "\n（文件已不存在）" : ""}`}
            onClick={selMode && checkable ? (e) => { e.stopPropagation(); toggleSel(p); } : undefined}
            onDoubleClick={() => !missing && openFile(p)}>
            {meta.name}
          </span>
        )}
        {!missing && (meta.size_label || meta.mtime_label || meta.mtime) && (
          <span
            className="fx-meta"
            title={`${meta.size_label || ""}${(meta.mtime_label || meta.mtime) ? " · " + (meta.mtime_label || fmtRel(meta.mtime)) : ""}`.trim()}
          >
            {meta.size_label && <span className="fx-size">{meta.size_label}</span>}
            {(meta.mtime_label || meta.mtime) && <span className="fx-time">{meta.mtime_label || fmtRel(meta.mtime)}</span>}
          </span>
        )}
        <span className="fx-acts">
          <button className="fx-act" title="打开" aria-label="打开" onClick={() => !missing && openFile(p)}><Icon name="external" size={13} /></button>
          <button className="fx-act" title="打开所在文件夹" aria-label="打开所在文件夹" onClick={() => !missing && openDir(p)}><Icon name="folder-open" size={13} /></button>
          <button className="fx-act" title={copiedPath === p ? "已复制路径" : "复制路径"} aria-label="复制路径" onClick={() => copyPath(p)}><Icon name={copiedPath === p ? "check" : "link"} size={13} /></button>
          {!meta.is_dir && <button className="fx-act" title="读取到输入框" aria-label="读取到输入框" onClick={() => !missing && onInject && onInject(p)}><Icon name="import" size={13} /></button>}
          {!missing && <button className="fx-act" title="重命名" aria-label="重命名" onClick={() => startRename(meta)}><Icon name="pencil" size={13} /></button>}
          {!missing && <button className="fx-act" title="删除（移入回收站，可恢复）" aria-label="删除" onClick={() => doDelete(p, !!meta.is_dir)}><Icon name="trash" size={13} /></button>}
        </span>
      </div>
    );
  };

  const entry = (e, depth) => {
    if (!e || !e.path) return null;
    const p = e.path;
    if (e.is_dir) {
      return (
        <div key={p} className="fx-row" style={{ paddingLeft: 4 + depth * 14 }}>
          {starBtn(p, "收藏文件夹")}
          {renaming === p ? (
            <>
              <Icon name="folder" size={14} />
              <input
                className="tf-input fx-rename" autoFocus value={renameVal} aria-label="新名称"
                onChange={(ev) => setRenameVal(ev.target.value)}
                onClick={(ev) => ev.stopPropagation()}
                onKeyDown={(ev) => { if (ev.key === "Enter") { ev.preventDefault(); commitRename(); } else if (ev.key === "Escape") { ev.preventDefault(); cancelRename(); } }}
                title="回车确认 · Esc 取消"
              />
            </>
          ) : (
            <button className="fx-dir" title={p} aria-expanded={!!expanded[p]} onClick={() => toggle(p)}>
              <Icon name={expanded[p] ? "chevron-down" : "chevron-right"} size={13} />
              <Icon name={expanded[p] ? "folder-open" : "folder"} size={14} />
              <span className="fx-dname">{e.name}</span>
            </button>
          )}
          <span className="fx-acts">
            <button className="fx-act" title="打开该文件夹" aria-label="打开该文件夹" onClick={() => openDir(p)}><Icon name="folder-open" size={13} /></button>
            <button className="fx-act" title={copiedPath === p ? "已复制路径" : "复制路径"} aria-label="复制路径" onClick={() => copyPath(p)}><Icon name={copiedPath === p ? "check" : "link"} size={13} /></button>
            <button className="fx-act" title="重命名" aria-label="重命名" onClick={() => startRename({ path: p, name: e.name })}><Icon name="pencil" size={13} /></button>
            <button className="fx-act" title="删除（移入回收站，可恢复）" aria-label="删除" onClick={() => doDelete(p, true)}><Icon name="trash" size={13} /></button>
          </span>
        </div>
      );
    }
    return fileRow({ path: p, name: e.name, is_dir: false, size_label: e.size_label, mtime: e.mtime, mtime_epoch: e.mtime_epoch, exists: true }, depth);
  };

  const renderDir = (path, depth) => {
    if (!expanded[path]) return null;
    const items = children[path];
    if (!items) return <div className="fx-hint" style={{ paddingLeft: 22 + depth * 14 }}>{loading[path] ? "加载中…" : "空"}</div>;
    const shown = view(items);
    if (shown.length === 0) return <div className="fx-hint" style={{ paddingLeft: 22 + depth * 14 }}>无匹配项</div>;
    return shown.map((e) => (
      <React.Fragment key={e.path}>
        {entry(e, depth + 1)}
        {e.is_dir && renderDir(e.path, depth + 1)}
      </React.Fragment>
    ));
  };

  const now = Date.now();
  const bucket = (m) => {
    const t = epochOf(m) * 1000;
    if (!t) return "old";
    if (now - t < 2 * 60 * 1000) return "just";
    if (now - t < 24 * 3600 * 1000 && new Date(t).getDate() === new Date().getDate()) return "today";
    return "old";
  };

  const favList = sortEntries(view(favs ? Object.values(favs) : []), sortKey, sortDir);
  const recent = roots ? roots.recent || [] : [];
  const recentFiltered = view(recent, () => false);
  const just = recentFiltered.filter((m) => bucket(m) === "just");
  const today = recentFiltered.filter((m) => bucket(m) === "today");
  const older = recentFiltered.filter((m) => bucket(m) === "old" && m.exists);

  const freshCount = just.length + today.length;
  React.useEffect(() => { if (onBadge) onBadge("files", freshCount); }, [freshCount, onBadge]);

  const group = (icon, title, list, emptyHint) => {
    const isCollapsed = !!collapsed[title];
    return (
      <div key={title}>
        <button className="fx-group-title" aria-expanded={!isCollapsed} onClick={() => setCollapsed((c) => ({ ...c, [title]: !c[title] }))}>
          <Icon name={isCollapsed ? "chevron-right" : "chevron-down"} size={12} />
          <Icon name={icon} size={12} /><span>{title}</span><span className="fx-group-n">{list.length}</span>
        </button>
        {!isCollapsed && (list.length > 0 ? list.map((m, i) => (
          <React.Fragment key={m.path + i}>{fileRow(m, 0)}</React.Fragment>
        )) : emptyHint && <div className="fx-hint">{emptyHint}</div>)}
      </div>
    );
  };

  // 工作区顶层：搜索态下目录始终保留（便于逐层下钻），文件按类型/关键词过滤
  const activeDirEntries = roots
    ? sortEntries((roots.entries || []).filter((e) => {
        if (!typeOk(e.name, e.is_dir)) return false;
        if (e.is_dir) return true;
        return matchesSearch(e.name);
      }), sortKey, sortDir)
    : [];
  // 搜索态下：某目录的子项命中时，即使目录自身名不匹配也自动展开
  const filteredChildrenHasMatch = (path) => {
    const items = children[path];
    if (!items) return false;
    return items.some((e) => typeOk(e.name, e.is_dir) && matchesSearch(e.name));
  };

  return (
    <div className="aux-tab">
      <div className="fx-search">
        <Icon name="search" size={14} className="fx-search-ic" />
        <input className="fx-q" placeholder="筛选 / 回车全局搜索…" aria-label="搜索文件或目录" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") runGlobalSearch(); }} />
        <MiniBtn icon="search" label={gBusy ? "搜索中…" : "在工作区全局搜索（回车）"} onClick={runGlobalSearch} />
        <MiniBtn icon="refresh" label="刷新" onClick={() => load(true)} />
      </div>
      <div className="fx-toolbar">
        <select className="set-select fx-sort-sel" aria-label="文件类型" value={ftype} onChange={(e) => setFtype(e.target.value)}>
          {FILE_FILTERS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </select>
        <select className="set-select fx-sort-sel" aria-label="排序字段" value={sortKey} onChange={(e) => setSortKey(e.target.value)}>
          <option value="time">按时间</option>
          <option value="name">按名称</option>
          <option value="size">按大小</option>
          <option value="type">按类型</option>
        </select>
        <MiniBtn icon="arrow-down" label={sortDir === "desc" ? "降序（点击切换升序）" : "升序（点击切换降序）"} active={sortDir === "desc"} onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))} />
        <MiniBtn icon="check" label={selMode ? "退出多选" : "多选"} active={selMode} onClick={() => { setSelMode((v) => !v); clearSel(); }} />
      </div>
      {selMode && (
        <div className="fx-batchbar">
          <span className="fx-batch-n">{selected.size} 选中</span>
          <button className="msg-op" disabled={!selected.size} onClick={batchCopy}>复制路径</button>
          <button className="msg-op" disabled={!selected.size} onClick={batchInject}>注入输入框</button>
          <button className="msg-op" disabled={!selected.size} onClick={batchFav}>收藏</button>
          <button className="msg-op" onClick={clearSel}>清空</button>
        </div>
      )}
      {bTip && <div className="fx-hint fx-tip">{bTip}</div>}
      {!roots && !err && <div className="empty-tip is-loading">正在加载文件…</div>}
      {err && <div className="empty-tip is-err">{err}</div>}
      {roots && (
        <>
          <div className="fx-root">
            <Icon name="folder-open" size={13} />
            <span title={roots.active_dir} className="fx-rootpath">{roots.active_dir}</span>
            {roots.active_dir && <button className="fx-act" title="打开工作区文件夹" onClick={() => openDir(roots.active_dir)}><Icon name="external" size={13} /></button>}
          </div>

          {gResult && (
            <div className="fx-gsearch">
              <div className="fx-group-title">
                <Icon name="search" size={12} /><span>搜索结果「{gResult.query}」</span>
                <span className="fx-group-n">{gResult.entries.length}{gResult.truncated ? "+" : ""}</span>
                <button className="msg-op" onClick={() => setGResult(null)}>清除</button>
              </div>
              {gResult.entries.length === 0
                ? <div className="fx-hint">无匹配</div>
                : gResult.entries.map((m) => <React.Fragment key={m.path}>{fileRow(m, 0)}</React.Fragment>)}
              {gResult.truncated && <div className="fx-hint">结果已截断（已扫描 {gResult.scanned} 项）</div>}
            </div>
          )}

          {group("star", "收藏", favList, "点文件旁的星标收藏到这里")}
          {group("clock", "刚刚产出", just, null)}
          {group("clock", "今天", today, null)}
          {group("clock", "更早", older, (q || ftype !== "all") ? "没有匹配的更早文件" : null)}

          {!q && ftype === "all" && recent.length === 0 && favList.length === 0 && (
            <div className="fx-hint">还没有产物——让 AI 写文件/出图后会出现在这里。</div>
          )}

          <button className="fx-group-title" aria-expanded={!collapsed["全部文件"]} onClick={() => setCollapsed((c) => ({ ...c, 全部文件: !c["全部文件"] }))}>
            <Icon name={collapsed["全部文件"] ? "chevron-right" : "chevron-down"} size={12} />
            <Icon name="layers" size={12} /><span>全部文件</span><span className="fx-group-n">{activeDirEntries.length}</span>
          </button>
          {!collapsed["全部文件"] && (
            activeDirEntries.length === 0 ? <div className="fx-hint">工作区无匹配项</div> : activeDirEntries.map((e) => {
              const showChildren = e.is_dir && (expanded[e.path] || (q && filteredChildrenHasMatch(e.path)));
              return (
                <React.Fragment key={e.path}>
                  {entry(e, 0)}
                  {showChildren && renderDir(e.path, 0)}
                </React.Fragment>
              );
            })
          )}
        </>
      )}
    </div>
  );
}

// ── 进程 ───────────────────────────────────────────
const PROC_HIST_KEY = "wt_proc_hist";
const PROC_CWD_KEY = "wt_proc_cwd";
const PROC_PIN_KEY = "wt_proc_pins";

function ProcessesTab({ active, onBadge }) {
  const [procs, setProcs] = React.useState({});
  const [current, setCurrent] = React.useState(null);
  const [follow, setFollow] = React.useState(true);
  const [cmd, setCmd] = React.useState("");
  const [cwd, setCwd] = React.useState(() => { try { return localStorage.getItem(PROC_CWD_KEY) || ""; } catch { return ""; } });
  const [adv, setAdv] = React.useState(false);
  const [tip, setTip] = React.useState("");
  const [hist, setHist] = React.useState(() => {
    try { const a = JSON.parse(localStorage.getItem(PROC_HIST_KEY) || "[]"); return Array.isArray(a) ? a : []; } catch { return []; }
  });
  const [histIdx, setHistIdx] = React.useState(-1);
  const [cleared, setCleared] = React.useState(0);
  const [outQ, setOutQ] = React.useState("");
  const [searchOut, setSearchOut] = React.useState(false);
  const [pinned, setPinned] = React.useState(() => {
    try { const a = JSON.parse(localStorage.getItem(PROC_PIN_KEY) || "[]"); return Array.isArray(a) ? a : []; } catch { return []; }
  });
  // 「已退出」分组：null=自动（超过阈值才默认收起，用户手动点后以其为准）
  const [exitedOpen, setExitedOpen] = React.useState(null);
  const termRef = React.useRef(null);

  const load = React.useCallback(async () => {
    try {
      const d = await api.listProcesses();
      setProcs(d.processes || {});
      setCurrent((c) => (c && d.processes[c] ? c : Object.keys(d.processes)[0] || null));
    } catch (e) { silentWarn(e, "AuxPanel"); }
  }, []);

  useVisiblePolling(load, 2500, active);

  const cur = current ? procs[current] : null;
  const allLines = cur?.lines || [];
  const lines = cleared > 0 ? allLines.slice(cleared) : allLines;
  const shownLines = outQ.trim() ? lines.filter((l) => String(l).toLowerCase().includes(outQ.trim().toLowerCase())) : lines;
  const running = Object.values(procs).filter((p) => p && !p.exited).length;
  React.useEffect(() => { if (onBadge) onBadge("procs", running); }, [running, onBadge]);
  React.useEffect(() => { setCleared(0); setFollow(true); }, [current]);

  // 命令历史持久化（跨会话保留）
  React.useEffect(() => { try { localStorage.setItem(PROC_HIST_KEY, JSON.stringify(hist.slice(-30))); } catch (e) { silentWarn(e, "AuxPanel"); } }, [hist]);
  React.useEffect(() => { try { localStorage.setItem(PROC_CWD_KEY, cwd); } catch (e) { silentWarn(e, "AuxPanel"); } }, [cwd]);
  React.useEffect(() => { try { localStorage.setItem(PROC_PIN_KEY, JSON.stringify(pinned)); } catch (e) { silentWarn(e, "AuxPanel"); } }, [pinned]);
  const togglePin = (n) => setPinned((s) => (s.includes(n) ? s.filter((x) => x !== n) : [...s, n]));

  // 输出自动滚底：仅在「跟随」且贴底时；用户上滑自动暂停并提示
  React.useEffect(() => {
    const el = termRef.current;
    if (!el || !follow) return;
    el.scrollTop = el.scrollHeight;
  }, [allLines.length, cleared, follow, current]);
  const onTermScroll = () => {
    const el = termRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    setFollow((f) => (f === atBottom ? f : atBottom));
  };
  const jumpToBottom = () => {
    const el = termRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    setFollow(true);
  };

  const flash = (t) => { setTip(t); setTimeout(() => setTip(""), 3000); };
  const runCmd = async (command, workingDir) => {
    const c = String(command || "").trim();
    if (!c) return false;
    try {
      const r = await api.startProcess(c, { cwd: workingDir ? workingDir.trim() : undefined });
      if (r && r.ok === false) { flash("❌ " + (r.error || "启动失败")); return false; }
      setHist((h) => (h[h.length - 1] === c ? h : [...h, c]).slice(-30));
      setHistIdx(-1);
      return true;
    } catch (e) { flash("❌ 启动失败：" + (e && e.message ? e.message : "")); return false; }
  };
  const start = async () => { if (await runCmd(cmd, cwd)) setCmd(""); };
  const stop = async () => {
    if (!current) return;
    try {
      const r = await api.stopProcess(current);
      if (r && r.ok === false) flash("❌ " + (r.error || "停止失败"));
    } catch (e) { flash("❌ 停止失败：" + (e && e.message ? e.message : "")); }
  };

  const onCmdKey = (e) => {
    if (e.key === "Enter") { start(); return; }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!hist.length) return;
      const idx = histIdx < 0 ? hist.length - 1 : Math.max(0, histIdx - 1);
      setHistIdx(idx); setCmd(hist[idx]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (histIdx < 0) return;
      const idx = histIdx + 1;
      if (idx >= hist.length) { setHistIdx(-1); setCmd(""); }
      else { setHistIdx(idx); setCmd(hist[idx]); }
    }
  };

  const errPat = /Traceback|Error|ERROR|Exception|失败|错误|refused|NotImplemented/;
  const names = Object.keys(procs);
  const runningList = names.filter((n) => procs[n] && !procs[n].exited);
  const exitedList = names.filter((n) => procs[n] && procs[n].exited);
  // 已退出项多了会淹没运行中项：自动收起（>3 条），用户手动切换后以其为准
  const exitedShown = exitedOpen === null ? exitedList.length <= 3 : exitedOpen;

  const procRow = (n) => {
    const p = procs[n];
    const isPinned = pinned.includes(n);
    return (
      <div key={n} role="button" tabIndex={0}
        className={`px-proc ${current === n ? "on" : ""} ${p.exited ? "is-exit" : ""}`}
        onClick={() => setCurrent(n)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setCurrent(n); } }}
        title={p.command || n}>
        <span className={`px-proc-dot ${p.exited ? "exit" : "run"}`} aria-hidden="true" />
        <span className="px-proc-name">{p.name || n}</span>
        {!p.exited && <span className="px-proc-res">{p.cpu != null ? `${p.cpu}%` : ""}{p.mem_mb != null ? ` · ${p.mem_mb}MB` : ""}</span>}
        <span className="px-proc-meta">{p.exited ? `退出 ${p.code}` : (p.uptime != null ? fmtUptime(p.uptime) : `pid ${p.pid}`)}</span>
        <button className={`px-proc-pin ${isPinned ? "on" : ""}`} title={isPinned ? "取消固定" : "固定到标签"} aria-label="固定到标签" aria-pressed={isPinned}
          onClick={(e) => { e.stopPropagation(); togglePin(n); }}>
          <Icon name="pin" size={11} fill={isPinned ? "currentColor" : "none"} />
        </button>
      </div>
    );
  };

  const pinnedAlive = pinned.filter((n) => procs[n]);

  return (
    <div className="aux-tab">
      {names.length > 0 && (
        <div className="px-proc-list">
          {runningList.length > 0 && <div className="px-proc-group">运行中 · {runningList.length}</div>}
          {runningList.map(procRow)}
          {exitedList.length > 0 && (
            <button type="button" className="px-proc-group px-proc-group-btn" aria-expanded={exitedShown}
              onClick={() => setExitedOpen(!exitedShown)}>
              <Icon name={exitedShown ? "chevron-down" : "chevron-right"} size={11} />
              <span>已退出 · {exitedList.length}</span>
            </button>
          )}
          {exitedShown && exitedList.map(procRow)}
        </div>
      )}

      {pinnedAlive.length > 0 && (
        <div className="px-proc-tabs" role="tablist" aria-label="已固定进程">
          {pinnedAlive.map((n) => (
            <button key={n} role="tab" aria-selected={current === n} className={`px-proc-tab ${current === n ? "on" : ""}`} onClick={() => setCurrent(n)} title={n}>
              <span className={`px-proc-dot ${procs[n].exited ? "exit" : "run"}`} aria-hidden="true" />
              <span className="px-proc-tab-name">{n}</span>
              <span className="px-proc-tab-x" role="presentation" title="取消固定" onClick={(e) => { e.stopPropagation(); togglePin(n); }}><Icon name="x" size={11} /></span>
            </button>
          ))}
        </div>
      )}

      <div className="px-term-head">
        <Icon name="terminal" size={14} className="px-term-ic" />
        <b className="px-term-title">{current || "进程终端"}</b>
        {cur && (
          <span className={`px-badge ${cur.exited ? "px-badge-exit" : "px-badge-run"}`}>
            {cur.exited ? `退出 ${cur.code}` : "运行中"}
          </span>
        )}
      </div>

      <div className="px-term-ops">
        <MiniBtn icon="arrow-down" label={follow ? "跟随中（点击暂停）" : "已暂停跟随（点击恢复）"} active={follow} onClick={() => { setFollow(!follow); if (!follow) jumpToBottom(); }} />
        <MiniBtn icon="search" label="搜索输出" active={searchOut} onClick={() => { setSearchOut((v) => !v); if (searchOut) setOutQ(""); }} />
        <MiniBtn icon="copy" label="复制全部输出" onClick={() => copyText(shownLines.join("\n")).catch(() => flash("❌ 复制失败"))} />
        <MiniBtn icon="eraser" label="清屏（不影响进程）" onClick={() => setCleared(allLines.length)} />
        <MiniBtn icon="rotate" label="以相同命令重启" onClick={() => cur && cur.command && runCmd(cur.command, cur.cwd)} />
        <span className="px-term-spacer" />
        <MiniBtn icon="stop" label="停止进程" danger onClick={stop} />
      </div>
      {searchOut && (
        <div className="px-term-search">
          <Icon name="search" size={13} className="fx-search-ic" />
          <input className="fx-q" placeholder="过滤输出行…" aria-label="过滤输出行" value={outQ} onChange={(e) => setOutQ(e.target.value)} />
          {outQ && <span className="px-term-search-n">{shownLines.length}/{lines.length}</span>}
        </div>
      )}
      {cur && (
        <div className="px-term-meta">
          pid {cur.pid} · 启动 {cur.started}
          {cur.cpu != null ? ` · CPU ${cur.cpu}%` : ""}
          {cur.mem_mb != null ? ` · 内存 ${cur.mem_mb}MB` : ""}
          {cur.uptime != null ? ` · 已运行 ${fmtUptime(cur.uptime)}` : ""}
          {cur.command ? ` · ${cur.command}` : ""}
        </div>
      )}

      <div className="px-term-wrap">
        <div className="px-term" ref={termRef} onScroll={onTermScroll}>
          {shownLines.length === 0 && <div className="fx-hint">{outQ ? "无匹配输出行" : "暂无输出（AI 用 start_process 启动进程后显示在这里）"}</div>}
          {shownLines.map((l, i) => (
            <div key={i} className={`px-line ${errPat.test(l) ? "px-err" : l.startsWith("──") ? "px-meta" : ""}`}>{l}</div>
          ))}
        </div>
        {!follow && (
          <button className="px-jump" onClick={jumpToBottom} title="回到最新输出">
            <Icon name="arrow-down" size={13} /> 回到最新
          </button>
        )}
      </div>

      {adv && (
        <input className="tf-input px-cwd" placeholder="工作目录（可选，默认工作区）" value={cwd} onChange={(e) => setCwd(e.target.value)} />
      )}
      <div className="px-start">
        <input
          className="tf-input"
          placeholder="启动命令，如 python -m http.server 8000（↑↓ 历史）"
          aria-label="启动命令"
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={onCmdKey}
        />
        <MiniBtn icon="folder" label="工作目录" active={adv} onClick={() => setAdv(!adv)} />
        <button className="ico-btn ico-primary" title="启动" aria-label="启动" onClick={start} disabled={!cmd.trim()}><Icon name="play" size={14} /></button>
      </div>
      {tip && <div className="px-tip px-tip-err">{tip}</div>}
    </div>
  );
}

// ── 参数 ───────────────────────────────────────────
const THEME_CHOICES = [
  { id: "starfield", name: "星空", desc: "极黑冷底 · 亮青点缀" },
  { id: "deepsea", name: "深海", desc: "深蓝底 · 湖蓝光" },
  { id: "arctic", name: "北极冰", desc: "冰白底 · 深海蓝字" },
];

// 参数分组与「脏值」归属（用于折叠分组头的未保存标记）
const PARAMS_GROUPS = [
  { id: "engine", keys: ["model", "thinking", "scenario", "base_url"] },
  { id: "sampling", keys: ["temperature", "top_p", "max_tokens", "seed"] },
  { id: "context", keys: [] },
  { id: "tools", keys: [] },
  { id: "toggles", keys: ["json_output", "beta_api", "strict_tools", "tools_enabled"] },
  { id: "budget", keys: ["monthly_budget", "peak_warning", "block_on_budget"] },
  { id: "appearance", keys: [] },
];
const PARAMS_OPEN_KEY = "wt_params_open";

function ParamsTab({ active }) {
  const { theme, setTheme } = React.useContext(ThemeContext);
  const { density, setDensity, fontSize, setFontSize } = React.useContext(DisplayContext);
  const [cfg, setCfg] = React.useState(PREFETCHED.cfg);
  const [draft, setDraft] = React.useState(null);
  const [tip, setTip] = React.useState("");
  const [ctx, setCtx] = React.useState(PREFETCHED.ctx);
  const [st, setSt] = React.useState(PREFETCHED.st);
  const [customModel, setCustomModel] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [loadErr, setLoadErr] = React.useState("");
  const [abilities, setAbilities] = React.useState(null);
  const [toolQ, setToolQ] = React.useState("");
  const [openDomains, setOpenDomains] = React.useState(() => new Set());
  // 折叠分组：日常只露大项，展开看细节；展开态持久化
  const [openGroups, setOpenGroups] = React.useState(() => {
    try {
      const a = JSON.parse(localStorage.getItem(PARAMS_OPEN_KEY) || "null");
      return Array.isArray(a) ? new Set(a) : new Set(["engine"]);
    } catch { return new Set(["engine"]); }
  });
  React.useEffect(() => {
    try { localStorage.setItem(PARAMS_OPEN_KEY, JSON.stringify([...openGroups])); } catch (e) { silentWarn(e, "AuxPanel"); }
  }, [openGroups]);
  const toggleGroup = (id) => setOpenGroups((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const isOpen = (id) => openGroups.has(id);

  React.useEffect(() => {
    const apply = (d, set, fn) => {
      if (d) set(d);
      else api[fn]().then((x) => x && set(x)).catch(() => setLoadErr(`「${fn}」加载失败：后端未连接`));
    };
    prefetchPromise.then(() => {
      apply(PREFETCHED.cfg, setCfg, "getConfig");
      apply(PREFETCHED.ctx, setCtx, "getContext");
      apply(PREFETCHED.st, setSt, "getStatus");
    });
  }, []);

  React.useEffect(() => {
    if (!active) return;
    api.getContext().then((d) => d && setCtx(d)).catch(() => {});
    api.getStatus().then((d) => d && setSt(d)).catch(() => {});
    api.listAbilities().then((d) => d && setAbilities(d)).catch(() => {});
  }, [active]);

  const edit = (patch) => setDraft((d) => ({ ...(d || {}), ...patch }));
  const save = async () => {
    if (!draft || saving) return;
    setSaving(true);
    try {
      const d = await api.saveConfig(draft);
      if (d && d.ok) {
        setCfg((c) => ({ ...c, ...draft }));
        if ("model" in draft) setCustomModel(!cfg.models.includes(draft.model));
        setDraft(null);
        setTip("已保存并锁定");
        setTimeout(() => setTip(""), 1800);
      } else { setTip("保存失败"); setTimeout(() => setTip(""), 1800); }
    } catch { setTip("保存失败"); setTimeout(() => setTip(""), 1800); }
    setSaving(false);
  };

  const dirty = draft != null;
  const dirtyGroups = new Set();
  if (draft) PARAMS_GROUPS.forEach((g) => { if (g.keys.some((k) => k in draft)) dirtyGroups.add(g.id); });

  if (!cfg) {
    return (
      <div className="aux-tab">
        {loadErr
          ? <div className="empty-tip is-err">{loadErr}</div>
          : <div className="empty-tip is-loading">正在加载配置…</div>}
      </div>
    );
  }

  const cur = { ...cfg, ...(draft || {}) };
  const modelOptions = Array.isArray(cfg.models) && cfg.models.length ? cfg.models : [];
  const ctxTools = Array.isArray(ctx?.tools) ? ctx.tools : [];
  const ctxMemCount = ctx?.memory?.count ?? (Array.isArray(ctx?.memory) ? ctx.memory.length : 0);
  const ctxUsage = ctx?.usage || {};
  const hasKey = cfg.has_key;
  const samplingLocked = !!cur.thinking && cur.thinking !== "none";

  // ── 上下文装配回执（P1）──
  const inj = ctx?.last_injection || {};
  const injInjected = Array.isArray(inj.injected) ? inj.injected : [];
  const injSkipped = Array.isArray(inj.skipped) ? inj.skipped : [];
  const injFailed = Array.isArray(inj.failed) ? inj.failed : [];
  const injChars = (inj.total_chars || 0) + (inj.prompt_chars || 0);
  const ctxBudgetChars = Number(cur.max_context_chars) || 0;
  const injPct = ctxBudgetChars > 0 ? Math.min(100, Math.round((injChars / ctxBudgetChars) * 100)) : (injChars > 0 ? 100 : 0);
  const tokBudget = Number(cur.max_context_tokens) || 0;
  const tokUsed = ctxUsage.prompt || 0;
  const tokPct = tokBudget > 0 ? Math.min(100, Math.round((tokUsed / tokBudget) * 100)) : 0;
  const monthBudget = Number(st?.monthly_budget) || 0;
  const monthCost = Number(st?.monthly_cost) || 0;
  const monthPct = monthBudget > 0 ? Math.min(100, Math.round((monthCost / monthBudget) * 100)) : 0;

  // ── 工具清单（能力域）──
  const domains = (abilities && abilities.domains) || [];
  const toolTotal = (abilities && abilities.total) || ctxTools.length || 0;
  const tq = toolQ.trim().toLowerCase();
  const filteredDomains = domains
    .map((d) => ({ ...d, tools: tq ? d.tools.filter((t) => `${t.name} ${t.description || ""}`.toLowerCase().includes(tq)) : d.tools }))
    .filter((d) => d.tools.length > 0);

  const displayModes = [
    { id: "compact", name: "紧凑" },
    { id: "comfort", name: "舒适" },
    { id: "loose", name: "宽松" },
  ];

  return (
    <div className="aux-tab">
      <div className="px-stats">
        <div className="px-stat" title={`窗口 tokens ${tokUsed.toLocaleString()}${tokBudget ? ` / ${tokBudget.toLocaleString()}` : ""}`}>
          <Icon name="layers" size={14} className="px-stat-ic" />
          <span className="px-stat-v">{tokUsed ? (tokBudget ? `${tokPct}%` : tokUsed.toLocaleString()) : "—"}</span>
          <span className="px-stat-k">上下文</span>
        </div>
        <div className="px-stat" title="可用工具数">
          <Icon name="grid" size={14} className="px-stat-ic" />
          <span className="px-stat-v">{toolTotal || "—"}</span>
          <span className="px-stat-k">工具</span>
        </div>
        <div className="px-stat" title="积累的记忆条数">
          <Icon name="database" size={14} className="px-stat-ic" />
          <span className="px-stat-v">{ctxMemCount || "—"}</span>
          <span className="px-stat-k">记忆</span>
        </div>
        <div className="px-stat" title="本月费用 / 预算">
          <Icon name="yen" size={14} className="px-stat-ic" />
          <span className="px-stat-v">{st?.monthly_cost ? `¥${Number(st.monthly_cost).toFixed(2)}` : ctxUsage.cost || "—"}</span>
          <span className="px-stat-k">成本</span>
        </div>
      </div>

      <div className="px-groups-bar">
        <span className="px-groups-hint">点击大项展开细节</span>
        <button className="msg-op" onClick={() => setOpenGroups(new Set(PARAMS_GROUPS.map((g) => g.id)))}>全部展开</button>
        <button className="msg-op" onClick={() => setOpenGroups(new Set())}>全部收起</button>
      </div>

      <Group id="engine" icon="cpu" title="模型引擎" open={isOpen("engine")} onToggle={toggleGroup} dirty={dirtyGroups.has("engine")}
        right={hasKey ? <span className="px-key-ok">{cur.model}</span> : <span className="px-key-warn">未配置 Key</span>}>
        <Field label="模型" hint="编辑后点保存生效">
          {customModel || !modelOptions.includes(cur.model) ? (
            <input className="tf-input px-sel" value={cur.model || ""} placeholder="输入任意模型名" onChange={(e) => edit({ model: e.target.value })} />
          ) : (
            <select className="set-select px-sel" value={cur.model} onChange={(e) => { if (e.target.value === "__custom__") setCustomModel(true); else edit({ model: e.target.value }); }}>
              {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
              <option value="__custom__">自定义模型名…</option>
            </select>
          )}
        </Field>
        <div className="px-grid2">
          <Field label="思考档" hint={cur.thinking === "auto" ? "智能路由" : "none/low/medium/high/xhigh/max"}>
            <select className="set-select px-sel" value={cur.thinking} onChange={(e) => edit({ thinking: e.target.value })}>
              {(Array.isArray(cur.thinking_modes) ? cur.thinking_modes : []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="场景" hint="预设采样参数">
            <select className="set-select px-sel" value={cur.scenario} onChange={(e) => edit({ scenario: e.target.value })}>
              {(Array.isArray(cur.scenarios) ? cur.scenarios : []).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>
        </div>
        <Field label="API 网关" hint="OpenAI 兼容 base_url">
          <input className="tf-input px-sel" value={cur.base_url || ""} placeholder="https://api.deepseek.com" onChange={(e) => edit({ base_url: e.target.value })} />
        </Field>
      </Group>

      <Group
        id="sampling"
        icon="thermometer"
        title="采样参数"
        open={isOpen("sampling")} onToggle={toggleGroup} dirty={dirtyGroups.has("sampling")}
        right={samplingLocked
          ? <span className="px-lock-tag" title="思考档开启时由模型控制，切到 none 可手动调节"><Icon name="lock" size={11} /> 接管</span>
          : `温度 ${cur.temperature}`}
      >
        <div className={`px-grid2 ${samplingLocked ? "px-locked" : ""}`}>
          <Field label="温度" hint="0-2">
            <input className="tf-input px-num" type="number" step="0.1" min="0" max="2" disabled={samplingLocked} value={cur.temperature} onChange={(e) => edit({ temperature: Number(e.target.value) })} />
          </Field>
          <Field label="Top-P" hint="0-1">
            <input className="tf-input px-num" type="number" step="0.05" min="0" max="1" disabled={samplingLocked} value={cur.top_p} onChange={(e) => edit({ top_p: Number(e.target.value) })} />
          </Field>
          <Field label="输出上限" hint="单次 tokens">
            <input className="tf-input px-num" type="number" step="1024" min="1024" max="393216" value={cur.max_tokens} onChange={(e) => edit({ max_tokens: Number(e.target.value) })} />
          </Field>
          <Field label="Seed" hint="0=随机">
            <input className="tf-input px-num" type="number" disabled={samplingLocked} value={cur.seed} onChange={(e) => edit({ seed: Number(e.target.value) })} />
          </Field>
        </div>
      </Group>

      <Group id="context" icon="layers" title="上下文装配" open={isOpen("context")} onToggle={toggleGroup} dirty={false}
        right={<span className="px-scope px-scope-session">{injChars ? `${injChars.toLocaleString()} 字` : "本会话 · 只读"}</span>}>
        <div className="px-budget" title={`本轮注入 ${injChars} 字符${ctxBudgetChars ? `，字符预算 ${ctxBudgetChars}` : "（未设字符预算）"}`}>
          <div className="px-budget-bar"><div className={`px-budget-fill ${injPct >= 90 ? "is-warn" : ""}`} style={{ transform: `scaleX(${injPct / 100})` }} /></div>
          <div className="px-budget-legend">
            <span>装配字符 {injChars.toLocaleString()}{ctxBudgetChars ? ` / ${ctxBudgetChars.toLocaleString()}` : ""}</span>
            <span>{inj.mode === "dialog" ? "对话模式" : inj.mode === "task" ? "任务模式" : "本轮"}{inj.quiet_mode ? " · 纯净" : ""}</span>
          </div>
        </div>
        {injInjected.length === 0 && injSkipped.length === 0 && injFailed.length === 0 ? (
          <div className="fx-hint">发起一次对话后，这里显示本轮注入了哪些上下文。</div>
        ) : (
          <>
            <div className="px-inj-list">
              {injInjected.map((it) => (
                <div className="px-inj" key={it.name}>
                  <span className="px-inj-dot on" />
                  <span className="px-inj-name">{it.name}</span>
                  <span className="px-inj-meta">{it.chars}{it.truncated ? " · 截断" : ""}</span>
                </div>
              ))}
              {injFailed.map((it) => (
                <div className="px-inj" key={"f" + it.name}>
                  <span className="px-inj-dot err" />
                  <span className="px-inj-name">{it.name}</span>
                  <span className="px-inj-meta is-warn">{it.critical ? "关键失败" : "失败"}</span>
                </div>
              ))}
              {injSkipped.filter((it) => !/本次无内容/.test(it.reason || "")).map((it) => (
                <div className="px-inj" key={"s" + it.name}>
                  <span className="px-inj-dot" />
                  <span className="px-inj-name">{it.name}</span>
                  <span className="px-inj-meta">{it.reason}</span>
                </div>
              ))}
            </div>
            <div className="px-inj-sum">注入 {injInjected.length} · 跳过 {injSkipped.length} · 失败 {injFailed.length}</div>
          </>
        )}
        <div className="px-budget" title="模型上下文窗口用量">
          <div className="px-budget-bar"><div className={`px-budget-fill ${tokPct >= 90 ? "is-warn" : ""}`} style={{ transform: `scaleX(${tokPct / 100})` }} /></div>
          <div className="px-budget-legend">
            <span>窗口 tokens {tokUsed ? tokUsed.toLocaleString() : "—"}{tokBudget ? ` / ${tokBudget.toLocaleString()}` : ""}</span>
            <span>{tokBudget ? `${tokPct}%` : ""}</span>
          </div>
        </div>
      </Group>

      <Group id="tools" icon="grid" title={`工具清单 · ${toolTotal}`} open={isOpen("tools")} onToggle={toggleGroup} dirty={false}
        right={<span className="px-scope">任务模式全可用</span>}>
        <div className="fx-search" style={{ marginBottom: 6 }}>
          <Icon name="search" size={13} className="fx-search-ic" />
          <input className="fx-q" placeholder="搜索工具…" aria-label="搜索工具" value={toolQ} onChange={(e) => setToolQ(e.target.value)} />
        </div>
        {domains.length === 0 ? (
          <div className="fx-hint">工具清单加载中…</div>
        ) : filteredDomains.length === 0 ? (
          <div className="fx-hint">无匹配工具</div>
        ) : (
          <div className="px-tool-domains">
            {filteredDomains.map((d) => {
              const open = !!tq || openDomains.has(d.name);
              return (
                <div className="px-domain" key={d.name}>
                  <button className="px-domain-head" aria-expanded={open}
                    onClick={() => setOpenDomains((s) => { const n = new Set(s); if (n.has(d.name)) n.delete(d.name); else n.add(d.name); return n; })}>
                    <Icon name={open ? "chevron-down" : "chevron-right"} size={12} />
                    <span className="px-domain-name">{d.name}</span>
                    <span className="px-domain-n">{d.tools.length}</span>
                  </button>
                  {open && (
                    <div className="px-domain-tools">
                      {d.tools.map((t) => (
                        <div className="px-tool" key={t.name} title={t.description}>
                          <span className={`px-tool-dot ${t.enabled ? "on" : ""}`} />
                          <span className="px-tool-name">{t.name}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Group>

      <Group id="toggles" icon="puzzle" title="功能开关" open={isOpen("toggles")} onToggle={toggleGroup} dirty={dirtyGroups.has("toggles")}
        right={`开 ${[cur.json_output, cur.beta_api, cur.strict_tools, cur.tools_enabled].filter(Boolean).length}/4`}>
        <TglRow label="JSON 输出" hint="response_format，失败自动重试" on={!!cur.json_output} onClick={() => edit({ json_output: !cur.json_output })} />
        <TglRow label="Beta API" hint="前缀续写 / FIM 补全" on={!!cur.beta_api} onClick={() => edit({ beta_api: !cur.beta_api })} />
        <TglRow label="strict 工具" hint="严格遵循 JSON Schema（自动 Beta）" on={!!cur.strict_tools} onClick={() => edit({ strict_tools: !cur.strict_tools })} />
        <TglRow label="工具开关" hint="向模型暴露工具定义" on={!!cur.tools_enabled} onClick={() => edit({ tools_enabled: !cur.tools_enabled })} />
      </Group>

      <Group id="budget" icon="yen" title="预算与峰谷" open={isOpen("budget")} onToggle={toggleGroup} dirty={dirtyGroups.has("budget")}
        right={<span className={monthBudget > 0 && monthPct >= 90 ? "px-key-warn" : ""}>{st?.peak_hour ? "高峰" : "空闲"} · ¥{monthCost.toFixed(2)}</span>}>
        <Field label="月预算（元）" hint="0 = 不限">
          <input className="tf-input px-num" type="number" step="1" min="0" value={cur.monthly_budget} onChange={(e) => edit({ monthly_budget: Number(e.target.value) })} />
        </Field>
        <div className="px-budget">
          <div className="px-budget-bar"><div className={`px-budget-fill ${monthPct >= 90 ? "is-warn" : ""}`} style={{ transform: `scaleX(${monthPct / 100})` }} /></div>
          <div className="px-budget-legend">
            <span>本月 ¥{monthCost.toFixed(2)}{monthBudget > 0 ? ` / ¥${monthBudget.toFixed(2)}` : "（未设预算）"}</span>
            <span>{st?.peak_hour ? "高峰时段" : "空闲时段"}</span>
          </div>
        </div>
        <TglRow label="高峰提醒" hint="高峰时段发送前提醒" on={!!cur.peak_warning} onClick={() => edit({ peak_warning: !cur.peak_warning })} />
        <TglRow label="超预算拦截" hint="达到月预算时阻止发送" on={!!cur.block_on_budget} onClick={() => edit({ block_on_budget: !cur.block_on_budget })} />
      </Group>

      <Group id="appearance" icon="palette" title="外观" open={isOpen("appearance")} onToggle={toggleGroup} dirty={false}
        right={(THEME_CHOICES.find((t) => t.id === theme) || {}).name || theme}>
        <Field label="风格">
          <div className="px-themes">
            {THEME_CHOICES.map((t) => (
              <button key={t.id} className={`px-theme ${theme === t.id ? "px-theme-on" : ""}`} title={t.desc}
                aria-label={`切换到${t.name}主题`} aria-pressed={theme === t.id} onClick={() => setTheme(t.id)}>
                <span className="px-theme-dot" data-t={t.id} />
                <span>{t.name}</span>
              </button>
            ))}
          </div>
        </Field>
        <div className="px-grid2">
          <Field label="密度">
            <select className="set-select px-sel" value={density} onChange={(e) => setDensity(e.target.value)}>
              {displayModes.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
          <Field label="消息字号">
            <select className="set-select px-sel" value={fontSize} onChange={(e) => setFontSize(Number(e.target.value))}>
              {[12, 13, 14, 15, 16, 17, 18].map((n) => <option key={n} value={n}>{n}px</option>)}
            </select>
          </Field>
        </div>
      </Group>

      <div className="px-savebar">
        <button className={`confirm-btn confirm-primary px-save ${dirty ? "px-save-dirty" : ""}`} onClick={save} disabled={!dirty || saving}
          title={dirty ? "保存全部修改并锁定" : "暂无未保存修改"}>
          <Icon name={dirty ? "save" : "lock"} size={14} />
          {saving ? "保存中…" : dirty ? "保存并锁定" : "已锁定"}
        </button>
        {dirty && <button className="confirm-btn" onClick={() => setDraft(null)} title="放弃未保存修改">取消</button>}
        {dirty && dirtyGroups.size > 0 && <span className="px-dirty-note">{dirtyGroups.size} 组未保存</span>}
      </div>

      {tip && <div className="px-tip">{tip}</div>}
    </div>
  );
}

// ── 活动 ───────────────────────────────────────────
const RESULT_MAX = 2400;

// 文件扩展名 → 图标（未知回落通用文件）
function fileIconName(name) {
  const ext = (String(name || "").split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico"].includes(ext)) return "image";
  if (["py", "js", "jsx", "ts", "tsx", "java", "c", "cpp", "cs", "go", "rs", "rb", "php", "sh", "ps1", "html", "css", "json", "xml", "yml", "yaml", "vue"].includes(ext)) return "code";
  if (["csv", "xlsx", "xls", "tsv"].includes(ext)) return "table";
  if (["zip", "rar", "7z", "tar", "gz"].includes(ext)) return "archive";
  return "file";
}

// 工具名 → 图标（按前缀归类，未知回落 command）
function toolIconName(tool) {
  const t = String(tool || "");
  // 具体域优先于通用前缀（如 read_memory 应先于 read_ 归到 brain）
  if (/^(send_email|read_email|email_|im_|telegram_|agent_mail|daily_brief|run_wechat)/.test(t)) return "mail";
  if (/^(write_memory|read_memory|update_memory|delete_memory|self_profile|query_memory|failure_memory|memory|knowledge_)/.test(t)) return "brain";
  if (/^(search_|net_|fetch_|http|web_|browser_|rss_|call_api|track_web|download_file)/.test(t)) return "globe";
  if (/^(image_|ocr_|screen_|vision_|chart_|media_|qrcode|mv_|sprite|make_gif|tts_|speech_)/.test(t)) return "image";
  if (/^(read_excel|write_excel|database_|pdf_|docx_|pptx_|html_to_|create_doc|read_csv|write_csv)/.test(t)) return "table";
  if (/^(schedule_|run_workflow|task_checkpoint|watch_files|recall_)/.test(t)) return "calendar";
  if (/^(get_date|get_weather|notify_|git|usage_report|list_my_capabilities|capability_heatmap|self_report|create_evolution|self_evolve|hardware_accel|app_manage)/.test(t)) return "settings";
  if (/^(run_|exec|code_|dev_|test_|verify_|project_|find_symbol|write_code|pip_|subagent)/.test(t)) return "code";
  if (/^(read_|write_|edit_|file_|list_|find_|asset_|clipboard|delete_|archive_|snapshot|batch_|start_process|stop_process|list_processes|environment)/.test(t)) return "folder";
  return "command";
}

// 参数摘要：优先展示路径/命令等主键，避免整坨 JSON
const ARG_SUMMARY_KEYS = ["path", "command", "url", "query", "name", "file", "output", "target"];
function argsSummary(s) {
  const a = s.args;
  if (a && typeof a === "object") {
    for (const k of ARG_SUMMARY_KEYS) if (a[k] != null) return `${k}=${String(a[k]).slice(0, 90)}`;
    const first = Object.keys(a)[0];
    if (first) return `${first}=${String(a[first]).slice(0, 90)}`;
  }
  return s.argsText || "";
}

// 结构化结果：JSON 对象 → 键值行；其余 → 等宽文本
function ResultView({ raw }) {
  const text = formatToolResult(raw);
  const trimmed = text.trim();
  let parsed = null;
  if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
    try { parsed = JSON.parse(trimmed); } catch { parsed = null; }
  }
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    const entries = Object.entries(parsed);
    if (entries.length > 0 && entries.length <= 30) {
      return (
        <div className="act-kv">
          {entries.map(([k, v]) => (
            <div className="act-kv-row" key={k}>
              <span className="act-kv-k">{k}</span>
              <span className="act-kv-v">{v !== null && typeof v === "object" ? JSON.stringify(v) : String(v)}</span>
            </div>
          ))}
        </div>
      );
    }
  }
  if (parsed) return <pre className="act-result">{JSON.stringify(parsed, null, 2)}</pre>;
  return <pre className="act-result">{text}</pre>;
}

function ActivityTab({ activity, products, onGoFiles, onInject, active }) {
  const [fullResult, setFullResult] = React.useState({});
  const [prodOpen, setProdOpen] = React.useState(true);
  const [copied, setCopied] = React.useState("");
  const [expanded, setExpanded] = React.useState(null);
  const [statusFilter, setStatusFilter] = React.useState("all");
  const [toolFilter, setToolFilter] = React.useState("");
  const [q, setQ] = React.useState("");
  const [openTurns, setOpenTurns] = React.useState(() => new Set());
  // 运行事件合流：降级 / 出网 / 信任 / 未消解失败模式（P1）
  const [evCtx, setEvCtx] = React.useState(null);
  const [evSt, setEvSt] = React.useState(null);
  const [evFail, setEvFail] = React.useState(null);
  const [evOpen, setEvOpen] = React.useState(false);
  useVisiblePolling(() => {
    api.getContext().then((d) => d && setEvCtx(d)).catch(() => {});
    api.getStatus().then((d) => d && setEvSt(d)).catch(() => {});
    api.getFailures().then((d) => d && setEvFail(d)).catch(() => {});
  }, 10000, active);

  const degradations = (evCtx && evCtx.degradations) || [];
  const egressSum = (evSt && evSt.egress) || (evCtx && evCtx.egress && evCtx.egress.summary) || null;
  const egressRecent = (evCtx && evCtx.egress && evCtx.egress.recent) || [];
  const trust = evSt && evSt.trust;
  const unresolvedFailures = ((evFail && evFail.failures) || []).filter((f) => !f.resolved);
  const critDeg = degradations.filter((d) => d.critical).length;
  const trustPending = (trust && trust.state === "unconfirmed") ? (trust.pending || (trust.files || []).length) : 0;
  const egressCount = egressSum ? (egressSum.count || 0) : 0;
  const hasEvents = degradations.length > 0 || egressCount > 0 || trustPending > 0 || unresolvedFailures.length > 0;

  const turns = (activity && activity.turns) || [];
  const streaming = !!(activity && activity.streaming);
  const lastTaskId = turns.length ? turns[turns.length - 1].taskId : -1;

  // 新轮次到达时自动展开最新轮并清空旧展开态
  React.useEffect(() => {
    setExpanded(null); setFullResult({});
    setOpenTurns(new Set(lastTaskId >= 0 ? [lastTaskId] : []));
  }, [lastTaskId]);

  const toolNames = React.useMemo(() => {
    const set = new Set();
    turns.forEach((t) => t.steps.forEach((s) => set.add(s.tool)));
    return [...set].sort();
  }, [turns]);

  const filterOn = statusFilter !== "all" || !!toolFilter || !!q.trim();
  const matchStep = (s) => {
    if (statusFilter !== "all" && s.status !== statusFilter) return false;
    if (toolFilter && s.tool !== toolFilter) return false;
    const kw = q.trim().toLowerCase();
    if (kw) {
      const hay = `${s.tool} ${argsSummary(s)} ${s.result == null ? "" : String(s.result)}`.toLowerCase();
      if (!hay.includes(kw)) return false;
    }
    return true;
  };

  const totalSteps = turns.reduce((n, t) => n + t.steps.length, 0);
  const doneCount = turns.reduce((n, t) => n + t.steps.filter((s) => s.status === "done").length, 0);
  const failedCount = turns.reduce((n, t) => n + t.failed, 0);
  const matchCount = filterOn ? turns.reduce((n, t) => n + t.steps.filter(matchStep).length, 0) : totalSteps;

  const prodList = Array.isArray(products) ? products : [];
  const prodAct = (path, act) => {
    const p = act === "opendir" ? api.openDir(path) : api.openFile(path);
    p && p.catch && p.catch(() => {});
  };
  const flashCopy = (key) => { setCopied(key); setTimeout(() => setCopied(""), 1500); };
  const copyStep = (s, key) => copyText(`# ${s.tool}\n参数：${s.argsText || "—"}\n结果：\n${s.result == null ? "" : String(s.result)}`).then(() => flashCopy(key)).catch(() => {});
  const copyAll = () => copyText(turns.map((t) => `## 轮次 ${t.taskId + 1}${t.time ? ` (${t.time})` : ""}\n` + t.steps.map((s) => `[${s.status}] ${s.tool}${s.duration ? ` (${s.duration}s)` : ""}\n  ${s.argsText || ""}\n  ${s.result == null ? "" : String(s.result)}`).join("\n\n")).join("\n\n")).then(() => flashCopy("all")).catch(() => {});

  const toggleTurn = (id) => {
    setOpenTurns((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  };

  return (
    <div className="aux-tab">
      <section className="act-products">
        <div className="act-products-head">
          <button className="act-products-toggle" aria-expanded={prodOpen} onClick={() => setProdOpen(!prodOpen)}>
            <Icon name={prodOpen ? "chevron-down" : "chevron-right"} size={13} />
            <Icon name="package" size={14} />
            <span>本会话产物</span>
            <span className="act-count">{prodList.length}</span>
          </button>
          {prodList.length > 0 && onGoFiles && (
            <button className="msg-op" title="去文件栏管理" onClick={() => onGoFiles()}>去文件</button>
          )}
        </div>
        {prodOpen && (prodList.length > 0 ? (
          <div className="act-prods">
            {prodList.map((item) => {
              const path = item.path || item;
              const nm = item.name || String(path).split(/[\\/]/).pop();
              return (
                <div className="act-prod" key={path} title={path}>
                  <span className="act-prod-name"><Icon name={fileIconName(nm)} size={13} />{nm}</span>
                  <span className="act-prod-ops">
                    <MiniBtn icon="external" label="打开" onClick={() => prodAct(path, "open")} />
                    <MiniBtn icon="folder-open" label="定位" onClick={() => prodAct(path, "opendir")} />
                    {onInject && <MiniBtn icon="import" label="注入输入框" onClick={() => onInject(path)} />}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="fx-hint">AI 写出文件后会自动列在这里，反复可看。</div>
        ))}
      </section>

      {hasEvents && (
        <section className="act-events">
          <button className="act-events-head" aria-expanded={evOpen} onClick={() => setEvOpen((v) => !v)}>
            <Icon name={evOpen ? "chevron-down" : "chevron-right"} size={13} />
            <Icon name="shield" size={14} />
            <span>运行事件</span>
            <span className="act-events-sum">
              {degradations.length > 0 && <em className={critDeg ? "is-warn" : ""}>降级 {degradations.length}{critDeg ? `·关键${critDeg}` : ""}</em>}
              {egressCount > 0 && <em>出网 {egressCount}</em>}
              {trustPending > 0 && <em className="is-warn">信任 {trustPending}</em>}
              {unresolvedFailures.length > 0 && <em className="is-warn">失败 {unresolvedFailures.length}</em>}
            </span>
          </button>
          {evOpen && (
            <div className="act-events-body">
              {degradations.length > 0 && (
                <div className="act-ev-group">
                  <div className="act-ev-title"><Icon name="warning" size={12} /> 降级</div>
                  {degradations.map((d, i) => (
                    <div className="act-ev-row" key={(d.key || d.component || "") + i}>
                      <span className="act-ev-k">{d.component || d.key}</span>
                      <span className={`act-ev-v ${d.critical ? "is-warn" : ""}`}>{d.last_impact || d.sample || "—"}{d.count > 1 ? ` ×${d.count}` : ""}</span>
                    </div>
                  ))}
                </div>
              )}
              {egressCount > 0 && (
                <div className="act-ev-group">
                  <div className="act-ev-title"><Icon name="globe" size={12} /> 出网（{egressCount} 次 · {egressSum.bytes || 0} 字节）</div>
                  {egressRecent.length > 0 ? egressRecent.slice(0, 8).map((e, i) => (
                    <div className="act-ev-row" key={i}>
                      <span className="act-ev-k">{e.channel || e.tool || "出网"}</span>
                      <span className="act-ev-v">{e.target || ""}{e.bytes ? ` · ${e.bytes}B` : ""}{e.ok === false ? " · 失败" : ""}</span>
                    </div>
                  )) : <div className="act-ev-row"><span className="act-ev-v">{(egressSum.targets || []).join("、") || "—"}</span></div>}
                </div>
              )}
              {trustPending > 0 && (
                <div className="act-ev-group">
                  <div className="act-ev-title"><Icon name="shield" size={12} /> 自我完整性</div>
                  <div className="act-ev-row"><span className="act-ev-v">{(trust.files || []).join("、") || `${trustPending} 处改动待确认`}</span></div>
                </div>
              )}
              {unresolvedFailures.length > 0 && (
                <div className="act-ev-group">
                  <div className="act-ev-title"><Icon name="warning" size={12} /> 未消解失败模式</div>
                  {unresolvedFailures.slice(0, 10).map((f) => (
                    <div className="act-ev-row" key={f.fingerprint || f.tool}>
                      <span className="act-ev-k">{f.tool} ×{f.hits || 1}</span>
                      <span className="act-ev-v" title={f.error}>{String(f.error || "").slice(0, 80)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      <div className="px-head">
        <b className="act-title">{streaming ? "AI 正在执行" : "本会话工具活动"}</b>
        {totalSteps > 0 && (
          <span className={"px-badge " + (streaming ? "px-badge-run" : failedCount ? "px-badge-exit" : "px-badge-ok")}>
            {streaming ? `${doneCount}/${totalSteps}` : failedCount ? `${failedCount} 失败` : `${totalSteps} 完成`}
          </span>
        )}
        {totalSteps > 0 && (
          <button className="msg-op" title="复制全部步骤" onClick={copyAll}>{copied === "all" ? "已复制" : "复制全部"}</button>
        )}
      </div>

      {totalSteps === 0 ? (
        <div className="fx-hint">AI 调用工具时会实时显示在这里（不占用聊天正文）。</div>
      ) : (
        <>
          {totalSteps > 1 && (
            <div className="act-filters">
              <select className="set-select act-filter-sel" aria-label="按状态筛选" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="all">全部状态</option>
                <option value="running">执行中</option>
                <option value="done">已完成</option>
                <option value="failed">失败</option>
              </select>
              <select className="set-select act-filter-sel" aria-label="按工具筛选" value={toolFilter} onChange={(e) => setToolFilter(e.target.value)}>
                <option value="">全部工具</option>
                {toolNames.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              <div className="act-filter-search">
                <Icon name="search" size={13} className="fx-search-ic" />
                <input className="fx-q" placeholder="搜索步骤…" aria-label="搜索步骤" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
            </div>
          )}
          {filterOn && <div className="act-filter-note">匹配 {matchCount} / {totalSteps} 步</div>}

          <div className="act-timeline">
            {turns.map((t, ti) => {
              const visible = filterOn ? t.steps.filter(matchStep) : t.steps;
              if (filterOn && visible.length === 0) return null;
              const open = filterOn || t.streaming || openTurns.has(t.taskId);
              const turnNo = turns.length - ti; // 最新轮为最大序号
              return (
                <section className={`act-turn ${t.streaming ? "is-run" : t.failed ? "is-fail" : ""}`} key={t.taskId}>
                  <button className="act-turn-head" aria-expanded={open} onClick={() => toggleTurn(t.taskId)} disabled={t.streaming}>
                    <Icon name={open ? "chevron-down" : "chevron-right"} size={13} />
                    <span className="act-turn-no">轮次 {turnNo}</span>
                    {t.time && <span className="act-turn-time">{t.time}</span>}
                    <span className="act-turn-sum">
                      {t.streaming ? `${t.steps.filter((s) => s.status === "done").length}/${t.steps.length} 执行中` : t.failed ? `${t.failed} 失败` : `${t.steps.length} 步完成`}
                    </span>
                  </button>
                  {open && (
                    <ol className="act-steps">
                      {visible.map((s) => {
                        const stepKey = `${t.taskId}:${t.steps.indexOf(s)}`;
                        const isOpen = expanded === stepKey;
                        const isRun = s.status === "running";
                        const raw = s.result == null ? "" : String(s.result);
                        const isLong = raw.length > RESULT_MAX;
                        const shown = isLong && !fullResult[stepKey] ? raw.slice(0, RESULT_MAX) + "\n…（已截断）" : null;
                        return (
                          <li key={stepKey} className={`act-step ${s.status} ${isOpen ? "open" : ""}`}>
                            <span className="act-node" aria-hidden="true">
                              {isRun ? <span className="act-spin" /> : s.status === "done" ? <Icon name="check" size={11} /> : s.status === "failed" ? <Icon name="x" size={11} /> : <Icon name="dot" size={7} />}
                            </span>
                            <button className="act-step-head" aria-expanded={isOpen} onClick={() => setExpanded(isOpen ? null : stepKey)}>
                              <span className="act-tool-ic"><Icon name={toolIconName(s.tool)} size={13} /></span>
                              <span className="act-name">{s.tool}</span>
                              <span className="act-dur">
                                {isRun ? "执行中" : s.status === "failed" ? "失败" : s.duration ? `${s.duration}s` : ""}
                              </span>
                              <Icon name={isOpen ? "chevron-down" : "chevron-right"} size={14} className="act-chev" />
                            </button>
                            {!isOpen && argsSummary(s) && <div className="act-step-sum" title={argsSummary(s)}>{argsSummary(s)}</div>}
                            {isOpen && (
                              <div className="act-detail">
                                {argsSummary(s) && <div className="act-args">参数：{s.argsText || argsSummary(s)}</div>}
                                {s.result != null && (
                                  <>
                                    {shown != null ? <pre className="act-result">{shown}</pre> : <ResultView raw={s.result} />}
                                    <div className="act-detail-ops">
                                      {isLong && (
                                        <button className="msg-op" onClick={() => setFullResult((f) => ({ ...f, [stepKey]: !f[stepKey] }))}>
                                          {fullResult[stepKey] ? "收起" : `展开全部（${raw.length} 字）`}
                                        </button>
                                      )}
                                      <button className="msg-op" onClick={() => copyStep(s, stepKey)}>{copied === stepKey ? "已复制" : "复制"}</button>
                                    </div>
                                  </>
                                )}
                              </div>
                            )}
                          </li>
                        );
                      })}
                    </ol>
                  )}
                </section>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// ── 容器 ───────────────────────────────────────────
const AUX_MIN_W = 240;
const AUX_MAX_W = 620;
const AUX_W_KEY = "wt_aux_w";

function clampWidth(w) {
  const maxByView = typeof window !== "undefined" ? Math.max(AUX_MIN_W + 20, window.innerWidth - 480) : AUX_MAX_W;
  return Math.min(Math.min(AUX_MAX_W, maxByView), Math.max(AUX_MIN_W, w));
}

const TABS = [
  { id: "activity", icon: "activity", label: "活动" },
  { id: "params", icon: "sliders", label: "参数" },
  { id: "files", icon: "folder", label: "文件" },
  { id: "procs", icon: "terminal", label: "进程" },
];

export default function AuxPanel({ onClose, onInjectFile, activity, products, tab, onTabChange, onPopout, inPopout }) {
  const isControlled = tab != null && typeof onTabChange === "function";
  const [internalTab, setInternalTab] = React.useState("params");
  const curTab = isControlled ? tab : internalTab;
  const setCurTab = isControlled ? onTabChange : setInternalTab;

  const [badges, setBadges] = React.useState({});
  const onBadge = React.useCallback((id, val) => {
    setBadges((b) => (b[id] === val ? b : { ...b, [id]: val }));
  }, []);

  const [width, setWidth] = React.useState(() => {
    try {
      const v = Number(localStorage.getItem(AUX_W_KEY));
      return Number.isFinite(v) && v > 0 ? clampWidth(v) : 300;
    } catch { return 300; }
  });
  const widthRef = React.useRef(width);
  widthRef.current = width;
  const resizeRef = React.useRef(null);
  const startResize = (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = widthRef.current;
    const prevUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    const onMove = (ev) => setWidth(clampWidth(startW + (startX - ev.clientX)));
    const onUp = () => {
      document.body.style.userSelect = prevUserSelect;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      resizeRef.current = null;
      try { localStorage.setItem(AUX_W_KEY, String(widthRef.current)); } catch { /* 存储不可用不致命 */ }
    };
    resizeRef.current = { onMove, onUp, prevUserSelect };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };
  // 卸载兜底：拖拽中卸载时移除全局监听并还原 body 样式（此前无清理）
  React.useEffect(() => () => {
    const r = resizeRef.current;
    if (r) {
      window.removeEventListener("mousemove", r.onMove);
      window.removeEventListener("mouseup", r.onUp);
      document.body.style.userSelect = r.prevUserSelect;
      resizeRef.current = null;
    }
  }, []);

  const wasStreaming = React.useRef(false);
  const hasRun = !!(activity && activity.steps && activity.steps.length > 0 && activity.streaming);
  React.useEffect(() => {
    if (hasRun) {
      if (!wasStreaming.current) setCurTab("activity");
      wasStreaming.current = true;
    } else {
      wasStreaming.current = false;
    }
  }, [hasRun, setCurTab]);

  const activityFailed = !!(activity && activity.turns && activity.turns.length)
    ? activity.turns.reduce((n, t) => n + (t.failed || 0), 0)
    : (activity && activity.steps || []).filter((s) => s.status === "failed").length;
  const badgeFor = (id) => {
    if (id === "activity") {
      if (activity && activity.streaming) return <span className="aux-tab-badge aux-tab-badge-live" aria-hidden="true" />;
      if (activityFailed > 0) return <span className="aux-tab-badge aux-tab-badge-err" aria-hidden="true">{activityFailed}</span>;
      return null;
    }
    const n = badges[id];
    return Number.isFinite(n) && n > 0 ? <span className="aux-tab-badge" aria-hidden="true">{n}</span> : null;
  };

  const selectTab = (id) => {
    setCurTab(id);
    try { localStorage.setItem("wt_aux_tab", id); } catch (e) { silentWarn(e, "AuxPanel"); }
  };
  const onTabKey = (e) => {
    const i = TABS.findIndex((x) => x.id === curTab);
    if (i < 0) return;
    let ni = -1;
    if (e.key === "ArrowRight") ni = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") ni = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") ni = 0;
    else if (e.key === "End") ni = TABS.length - 1;
    if (ni < 0) return;
    e.preventDefault();
    selectTab(TABS[ni].id);
    try { document.getElementById("auxtab-" + TABS[ni].id)?.focus(); } catch (err) { silentWarn(err, "AuxPanel"); }
  };

  return (
    <aside className="aux-panel" style={{ "--aux-w": width + "px" }}>
      <div className="aux-resize" role="separator" aria-orientation="vertical" aria-label="调整面板宽度" onMouseDown={startResize} />
      <div className="aux-head">
        <b>控制台</b>
        <span className="aux-head-ops">
          {onPopout && (
            <button className="icon-btn" onClick={onPopout} title={inPopout ? "收回面板到主窗口" : "弹出为独立窗口"}
              aria-label={inPopout ? "收回面板" : "弹出为独立窗口"}>
              <Icon name={inPopout ? "import" : "external"} size={14} />
            </button>
          )}
          <button className="icon-btn" onClick={onClose} title="关闭" aria-label="关闭">
            <Icon name="x" size={14} />
          </button>
        </span>
      </div>
      <div className="aux-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={"auxtab-" + t.id}
            aria-selected={curTab === t.id}
            aria-controls={"auxpane-" + t.id}
            className="aux-tabbtn"
            tabIndex={curTab === t.id ? 0 : -1}
            onClick={() => selectTab(t.id)}
            onKeyDown={onTabKey}
          >
            <Icon name={t.icon} size={15} />
            <span>{t.label}</span>
            {badgeFor(t.id)}
          </button>
        ))}
      </div>
      <div className="aux-body">
        <div role="tabpanel" id="auxpane-activity" aria-labelledby="auxtab-activity" className="aux-pane" hidden={curTab !== "activity"}>
          <ActivityTab activity={activity} products={products || []} onGoFiles={() => selectTab("files")} onInject={onInjectFile} active={curTab === "activity"} />
        </div>
        <div role="tabpanel" id="auxpane-params" aria-labelledby="auxtab-params" className="aux-pane" hidden={curTab !== "params"}>
          <ParamsTab active={curTab === "params"} />
        </div>
        <div role="tabpanel" id="auxpane-files" aria-labelledby="auxtab-files" className="aux-pane" hidden={curTab !== "files"}>
          <FilesTab onInject={onInjectFile} active={curTab === "files"} onBadge={onBadge} />
        </div>
        <div role="tabpanel" id="auxpane-procs" aria-labelledby="auxtab-procs" className="aux-pane" hidden={curTab !== "procs"}>
          <ProcessesTab active={curTab === "procs"} onBadge={onBadge} />
        </div>
      </div>
    </aside>
  );
}
