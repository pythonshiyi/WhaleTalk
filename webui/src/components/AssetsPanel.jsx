import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";
import EmptyState from "./EmptyState.jsx";
import Overlay from "./Overlay.jsx";
import { silentWarn } from "../quiet.js";

function fileIcon(name = "") {
  const ext = String(name).split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "🖼";
  if (["xlsx", "xls", "csv"].includes(ext)) return "📊";
  if (["docx", "doc"].includes(ext)) return "📄";
  if (["pdf"].includes(ext)) return "📕";
  if (["pptx", "ppt"].includes(ext)) return "📽";
  if (["py", "js", "ts", "html", "css", "json"].includes(ext)) return "🧩";
  if (["zip", "rar", "7z", "gz"].includes(ext)) return "🗜";
  if (["mp3", "wav", "mp4", "mov"].includes(ext)) return "🎵";
  if (["md", "txt", "log"].includes(ext)) return "📝";
  return "📦";
}

function FileRow({ f, onOpenDir, onOpenFile, onFav, onPreview }) {
  return (
    <div className="asset-row" onDoubleClick={() => (f.is_dir ? onOpenDir(f.path) : onOpenFile(f.path))}>
      <span className="asset-ic">{f.is_dir ? "📁" : fileIcon(f.name)}</span>
      <button className="asset-name" onClick={() => (f.is_dir ? onOpenDir(f.path) : onOpenFile(f.path))} title={f.path || f.name}>
        {f.name || String(f.path || "").split(/[\\/]/).pop()}
      </button>
      <span className="asset-meta">{f.is_dir ? "目录" : (f.size_label || "")}</span>
      <span className="asset-meta">
        {f.mtime_label || (typeof f.mtime === "number"
          ? new Date(f.mtime * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
          : (f.mtime || ""))}
      </span>
      <span className="asset-ops">
        <button className="pm-op" onClick={() => onFav(f.path)}>{f.faved ? "★" : "☆"}</button>
        {!f.is_dir && onPreview && <button className="pm-op" onClick={() => onPreview(f.path)}>预览</button>}
        <button className="pm-op" onClick={() => onOpenFile(f.path)}>打开</button>
        <button className="pm-op" onClick={() => api.openDir(f.path).catch(() => {})}>定位</button>
      </span>
    </div>
  );
}

/**
 * 创作资产：按目录浏览工作区，聚合最近产物 / 收藏 / 项目文件夹。
 * 不复制文件管理器的全部能力，只服务「找素材、打开、定位、收藏」。
 */
export default function AssetsPanel({ initialPath = "" }) {
  const [root, setRoot] = React.useState(null);
  const [current, setCurrent] = React.useState(initialPath || "");
  const [data, setData] = React.useState(null);
  const [q, setQ] = React.useState("");
  const [hits, setHits] = React.useState(null);
  const [err, setErr] = React.useState("");
  const [preview, setPreview] = React.useState(null);
  const [busy, setBusy] = React.useState(false);

  const loadRoot = React.useCallback(async () => {
    try {
      const d = await api.listFiles();
      setRoot(d || null);
      setErr("");
    } catch {
      setErr("工作区读取失败（后端未连接）");
    }
  }, []);

  const loadDir = React.useCallback(async (dir) => {
    setBusy(true);
    try {
      const d = await api.listFiles(dir);
      setData(d || null);
      setCurrent(dir || "");
      setErr("");
    } catch {
      setErr("目录读取失败");
    } finally {
      setBusy(false);
    }
  }, []);

  React.useEffect(() => { loadRoot(); }, [loadRoot]);
  React.useEffect(() => {
    if (!initialPath) return;
    // 搜索结果可能是文件：定位到父目录，而不是把文件当目录读取。
    const looksFile = /\.[a-z0-9]{1,8}$/i.test(initialPath);
    const dir = looksFile ? initialPath.replace(/[\\/][^\\/]*$/, "") : initialPath;
    loadDir(dir);
  }, [initialPath, loadDir]);

  const doSearch = async () => {
    if (!q.trim()) { setHits(null); return; }
    setBusy(true);
    try {
      const d = await api.searchFiles(q.trim(), undefined, 80);
      setHits(d?.entries || []);
    } catch {
      setHits([]);
    } finally {
      setBusy(false);
    }
  };

  const openFile = (path) => {
    api.openFile(path).catch(() => {});
  };
  const openDir = (path) => { loadDir(path); setQ(""); setHits(null); };
  const fav = async (path) => {
    try {
      const r = await api.favFile(path);
      const faved = !!r?.faved;
      const patch = (arr) => (arr || []).map((x) => (x.path === path ? { ...x, faved } : x));
      setData((d) => (d ? { ...d, entries: patch(d.entries) } : d));
      setRoot((r0) => (r0 ? { ...r0, entries: patch(r0.entries), favs: patch(r0.favs) } : r0));
      setHits((h) => (h ? patch(h) : h));
    } catch (e) { silentWarn(e, "AssetsPanel"); }
  };

  const previewFile = async (path) => {
    try {
      const d = await api.readFile(path);
      if (d?.content != null) setPreview({ path, content: d.content, truncated: d.truncated });
      else if (d?.error) setPreview({ path, content: d.error });
    } catch (e) { silentWarn(e, "AssetsPanel"); }
  };

  const activeDir = root?.active_dir || "";
  const rel = current && activeDir ? current.replace(activeDir, "").replace(/^[\\/]/, "") : "";
  const crumbs = rel ? rel.split(/[\\/]/).filter(Boolean) : [];
  const entries = hits || data?.entries || [];
  const recent = root?.recent || [];
  const favs = root?.favs || [];

  return (
    <div className="asset-panel">
      <div className="asset-bar">
        <button className="msg-op" onClick={() => { setCurrent(""); setData(null); setQ(""); setHits(null); loadRoot(); }}>⌂ 工作区</button>
        {crumbs.map((c, i) => (
          <React.Fragment key={i}>
            <span className="asset-sep">/</span>
            <button className="msg-op" onClick={() => {
              const p = activeDir + "\\" + crumbs.slice(0, i + 1).join("\\");
              openDir(p);
            }}>{c}</button>
          </React.Fragment>
        ))}
        <span style={{ flex: 1 }} />
        <input
          className="set-select set-combo asset-search"
          placeholder="搜索文件名…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && doSearch()}
        />
        <button className="confirm-btn" onClick={doSearch} disabled={busy}>搜索</button>
        <button className="confirm-btn" onClick={() => { setQ(""); setHits(null); loadRoot(); loadDir(current); }} disabled={busy}>刷新</button>
      </div>

      {err && <div className="empty-tip is-err">{err}</div>}

      {!hits && !current && (
        <>
          <div className="asset-section-title"><Icon name="clock" size={14} /> 最近产物</div>
          {recent.length === 0 ? <div className="empty-tip">还没有产物</div> : (
            <div className="asset-list">
              {recent.slice(0, 12).map((f) => (
                <FileRow key={f.path} f={{ ...f, faved: (favs || []).some((x) => x.path === f.path) }} onOpenDir={openDir} onOpenFile={openFile} onFav={fav} onPreview={previewFile} />
              ))}
            </div>
          )}
          <div className="asset-section-title"><Icon name="star" size={14} /> 收藏</div>
          {favs.length === 0 ? <div className="empty-tip">还没有收藏；在文件行点 ☆ 即可收藏</div> : (
            <div className="asset-list">
              {favs.map((f) => <FileRow key={f.path} f={{ ...f, faved: true }} onOpenDir={openDir} onOpenFile={openFile} onFav={fav} onPreview={previewFile} />)}
            </div>
          )}
        </>
      )}

      <div className="asset-section-title">
        <Icon name="folder" size={14} /> {hits ? `搜索「${q}」` : current ? "当前目录" : "工作区"}
        <span className="pm-cat">{entries.length} 项</span>
      </div>
      {entries.length === 0 ? (
        <EmptyState icon="folder" title={hits ? "没有匹配文件" : "目录是空的"} hint={hits ? "换个关键词，或清空搜索" : "AI 生成的产物会出现在工作区。"} compact />
      ) : (
        <div className="asset-list">
          {entries.map((f) => (
            <FileRow key={f.path} f={f} onOpenDir={openDir} onOpenFile={(p) => { openFile(p); }} onFav={fav} onPreview={previewFile} />
          ))}
        </div>
      )}

      <div className="asset-tip">
        双击文件名可打开；文本类文件可点「预览」。公众号草稿、MV 素材、直播截图等都保存在工作区，按项目文件夹归类即可。
      </div>

      {preview && (
        <Overlay title={`预览 · ${String(preview.path).split(/[\\/]/).pop()}`} onClose={() => setPreview(null)} wide>
          <pre className="asset-preview">{preview.content}{preview.truncated ? "\n…（已截断）" : ""}</pre>
        </Overlay>
      )}
    </div>
  );
}
