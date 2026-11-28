import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";

// 全局命令面板：Ctrl+K 跨会话 / 工具 / 指令 / 角色 / 记忆 / 文件 / 页面搜索。
// 搜索结果只做导航，不在此处执行破坏性操作。
const PAGES = [
  { label: "会话", sub: "聊天与任务", page: "chat", icon: "message", keys: "chat 聊天 对话" },
  { label: "工作台", sub: "态势 / 任务记录 / 定时任务", page: "workbench", icon: "layout", keys: "workbench 首页 面板 任务" },
  { label: "创作", sub: "角色 / 指令库 / 直播 / 资产", page: "create", icon: "sparkles", keys: "create 创作 内容 角色 直播 资产" },
  { label: "能力", sub: "工具库 / 能力包 / 权限", page: "abilities", icon: "puzzle", keys: "abilities 工具 能力 权限" },
  { label: "设置", sub: "模型 / 服务 / 通道 / 外观", page: "settings", icon: "settings", keys: "settings 设置 配置" },
];

const SETTINGS = [
  { label: "模型与网关", sub: "API Key / 模型 / 思考档", tab: "model", keys: "模型 api key 网关 思考" },
  { label: "外部服务", sub: "Webhook / 工作流", tab: "service", keys: "外部服务 webhook 工作流" },
  { label: "公众号写作", sub: "字数 / 受众 / 信源", tab: "wechat", keys: "公众号 写作 草稿" },
  { label: "接入通道", sub: "QQ / Telegram", tab: "channels", keys: "qq telegram 通道" },
  { label: "系统人格与工具", sub: "系统提示词 / 工具开关", tab: "persona", keys: "人格 角色 提示词 工具" },
  { label: "通知、安全与权限", sub: "通知 / 黑名单 / 权限", tab: "notice", keys: "通知 安全 权限 黑名单 审批" },
  { label: "外观", sub: "主题 / 密度 / 字号", tab: "look", keys: "外观 主题 字号 密度" },
  { label: "可选能力", sub: "依赖安装", tab: "deps", keys: "依赖 可选能力 安装" },
  { label: "高级", sub: "备份 / 更新 / 清理 / 审计", tab: "adv", keys: "高级 备份 更新 清理 审计" },
];

const LIMIT = 6;

export default function CommandPalette({ open, onClose, onNavigate }) {
  const [q, setQ] = React.useState("");
  const [items, setItems] = React.useState([]);
  const [active, setActive] = React.useState(0);
  const [busy, setBusy] = React.useState(false);
  const inputRef = React.useRef(null);
  const seqRef = React.useRef(0);

  React.useEffect(() => {
    if (!open) return;
    setQ("");
    setItems([]);
    setActive(0);
    setTimeout(() => inputRef.current?.focus(), 30);
  }, [open]);

  React.useEffect(() => {
    if (!open) return undefined;
    const query = q.trim().toLowerCase();
    if (!query) {
      setItems([
        ...PAGES.filter((p) => !query || (p.label + p.sub + p.keys).toLowerCase().includes(query))
          .map((p) => ({ group: "页面", title: p.label, sub: p.sub, icon: p.icon, dest: { page: p.page } })),
        ...SETTINGS.map((s) => ({ group: "设置", title: s.label, sub: s.sub, icon: "settings", dest: { page: "settings", settingsTab: s.tab } })),
      ]);
      setActive(0);
      return undefined;
    }
    const seq = ++seqRef.current;
    setBusy(true);
    const timer = setTimeout(async () => {
      const [sess, prompts, personas, abilities, files] = await Promise.allSettled([
        api.searchSessions(query, { type: "message" }),
        api.getPrompts(),
        api.listPersonas(),
        api.listAbilities(),
        api.searchFiles(query, undefined, LIMIT),
      ]);
      if (seq !== seqRef.current) return;

      const pages = PAGES.filter((p) => (p.label + " " + p.sub + " " + p.keys).toLowerCase().includes(query))
        .map((p) => ({ group: "页面", title: p.label, sub: p.sub, icon: p.icon, dest: { page: p.page } }));
      const settings = SETTINGS.filter((s) => (s.label + " " + s.sub + " " + s.keys).toLowerCase().includes(query))
        .map((s) => ({ group: "设置", title: s.label, sub: s.sub, icon: "settings", dest: { page: "settings", settingsTab: s.tab } }));

      const seen = new Set();
      const sessions = [];
      if (sess.status === "fulfilled") {
        for (const r of (sess.value?.results || [])) {
          const key = `${r.session_id}:${r.snippet}`;
          if (seen.has(key)) continue;
          seen.add(key);
          sessions.push({
            group: "会话", title: r.session_name || r.session_id,
            sub: r.snippet || "", icon: "message",
            dest: { page: "chat", sessionId: r.session_id },
          });
          if (sessions.length >= LIMIT) break;
        }
      }

      const toolItems = [];
      if (abilities.status === "fulfilled") {
        for (const d of (abilities.value?.domains || [])) {
          for (const t of (d.tools || [])) {
            if (!(t.name + " " + (t.description || "") + " " + d.name).toLowerCase().includes(query)) continue;
            toolItems.push({
              group: "工具", title: t.name, sub: `${d.name} · ${String(t.description || "").slice(0, 70)}`,
              icon: "puzzle", dest: { page: "abilities", toolQuery: t.name },
            });
            if (toolItems.length >= LIMIT) break;
          }
          if (toolItems.length >= LIMIT) break;
        }
      }

      const promptItems = [];
      if (prompts.status === "fulfilled") {
        for (const p of (prompts.value || [])) {
          if (!(p.name + " " + (p.desc || "") + " " + (p.text || "")).toLowerCase().includes(query)) continue;
          promptItems.push({
            group: "指令", title: p.name, sub: p.desc || String(p.text || "").slice(0, 70),
            icon: "zap", dest: { page: "create", createTab: "prompts" },
          });
          if (promptItems.length >= LIMIT) break;
        }
      }

      const personaItems = [];
      if (personas.status === "fulfilled") {
        for (const p of (personas.value?.personas || [])) {
          if (!(p.name + " " + (p.tagline || "") + " " + (p.personality || "")).toLowerCase().includes(query)) continue;
          personaItems.push({
            group: "角色", title: p.name, sub: p.tagline || p.personality || "",
            icon: "user", dest: { page: "chat", personaId: p.id },
          });
          if (personaItems.length >= LIMIT) break;
        }
      }

      const fileItems = [];
      if (files.status === "fulfilled") {
        for (const f of (files.value?.entries || [])) {
          fileItems.push({
            group: "文件", title: f.name || f.path, sub: f.path || "",
            icon: "file", dest: { page: "create", createTab: "assets", assetPath: f.path },
          });
          if (fileItems.length >= LIMIT) break;
        }
      }

      setItems([...pages, ...sessions, ...toolItems, ...promptItems, ...personaItems,  ...fileItems, ...settings]);
      setActive(0);
      setBusy(false);
    }, 220);
    return () => clearTimeout(timer);
  }, [q, open]);

  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
      if (!items.length) return;
      if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (i + 1) % items.length); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (i - 1 + items.length) % items.length); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const it = items[active];
        if (it) { onNavigate?.(it.dest); onClose(); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, items, active, onNavigate, onClose]);

  if (!open) return null;
  let lastGroup = "";
  return (
    <div className="cmdk-mask" onClick={onClose}>
      <div className="cmdk-panel" role="dialog" aria-modal="true" aria-label="全局搜索" onClick={(e) => e.stopPropagation()}>
        <div className="cmdk-head">
          <Icon name="search" size={17} />
          <input
            ref={inputRef}
            className="cmdk-input"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索会话、工具、指令、角色、记忆、文件…"
            aria-label="全局搜索"
          />
          <kbd className="cmdk-kbd">Esc</kbd>
        </div>
        <div className="cmdk-body">
          {busy && <div className="cmdk-empty">搜索中…</div>}
          {!busy && items.length === 0 && <div className="cmdk-empty">没有匹配结果</div>}
          {!busy && items.map((it, i) => {
            const showGroup = it.group !== lastGroup;
            lastGroup = it.group;
            return (
              <React.Fragment key={`${it.group}-${it.title}-${i}`}>
                {showGroup && <div className="cmdk-group">{it.group}</div>}
                <button
                  type="button"
                  className={`cmdk-item ${i === active ? "is-active" : ""}`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => { onNavigate?.(it.dest); onClose(); }}
                >
                  <span className="cmdk-ic"><Icon name={it.icon} size={15} /></span>
                  <span className="cmdk-main">
                    <b>{it.title}</b>
                    {it.sub && <span>{it.sub}</span>}
                  </span>
                  <span className="cmdk-enter">↵</span>
                </button>
              </React.Fragment>
            );
          })}
        </div>
        <div className="cmdk-foot">
          <span>↑↓ 选择</span><span>↵ 打开</span><span>Esc 关闭</span>
          <span className="cmdk-hint">Ctrl+K</span>
        </div>
      </div>
    </div>
  );
}
