import React from "react";
import * as api from "../api.js";
import Overlay from "./Overlay.jsx";

// ── 指令库：用户指令 + 内置模板的统一管理与调用入口 ──
// 数据同源 prompts.json（老数据仅 name/text 也能正常显示），内置模板只读、可复制到我的指令。

const SOURCES = [
  { id: "all", label: "全部" },
  { id: "mine", label: "我的指令" },
  { id: "builtin", label: "内置模板" },
  { id: "plugin", label: "插件技能" },
  { id: "off", label: "已禁用" },
];

const VAR_HINT = "{{TEXT}} = 当前输入/选中文本 · {{DATE}} = 今天日期 · {ASK:问题} = 调用时询问";

const BLANK = {
  id: "", name: "", icon: "⚡", category: "未分类", desc: "",
  tags: [], shortcut: "", text: "", auto_send: false, enabled: true,
};

function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 应用/试跑时的变量预填（{{TEXT}} 保留给用户在输入框填写）。 */
function fillVars(text) {
  return String(text || "").replace(/\{\{DATE\}\}/g, todayStr());
}

export default function PromptsPage({ onApply }) {
  const [items, setItems] = React.useState(null);
  const [pluginSkills, setPluginSkills] = React.useState([]);
  const [q, setQ] = React.useState("");
  const [cat, setCat] = React.useState("全部");
  const [src, setSrc] = React.useState("all");
  const [editing, setEditing] = React.useState(null);
  const [tip, setTip] = React.useState("");
  const fileRef = React.useRef(null);

  const flash = (t) => {
    setTip(t);
    setTimeout(() => setTip(""), 1800);
  };

  const load = React.useCallback(async () => {
    try {
      const list = await api.getPrompts();
      setItems(Array.isArray(list) ? list : []);
    } catch {
      setItems([]);
    }
    try {
      const sk = await api.getPluginSkills();
      setPluginSkills(Array.isArray(sk) ? sk : []);
    } catch {
      setPluginSkills([]);
    }
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const cats = React.useMemo(() => {
    const s = new Set((items || []).map((p) => p.category || "未分类"));
    return ["全部", ...Array.from(s)];
  }, [items]);

  // 插件技能：只读来源（id 带 plugin: 前缀，操作仅「复制」）
  const pluginItems = pluginSkills.map((s) => ({
    id: `plugin:${s.plugin}:${s.name}`,
    name: s.name,
    text: s.text,
    icon: "🧩",
    category: "插件技能",
    desc: `来自插件「${s.plugin}」`,
    fromPlugin: true,
    enabled: true,
  }));

  const filtered = (src === "plugin" ? pluginItems : items || []).filter((p) => {
    if (src === "mine" && p.builtin) return false;
    if (src === "builtin" && !p.builtin) return false;
    if (src === "off" && p.enabled !== false) return false;
    if (cat !== "全部" && (p.category || "未分类") !== cat) return false;
    if (q) {
      const hay = `${p.name} ${p.desc} ${p.text} ${(p.tags || []).join(" ")} ${p.shortcut}`.toLowerCase();
      if (!hay.includes(q.toLowerCase())) return false;
    }
    return true;
  });

  const userItems = (items || []).filter((p) => !p.builtin);

  const save = async (item) => {
    try {
      await api.savePrompt(item);
      await load();
      setEditing(null);
      flash("已保存");
    } catch (e) {
      flash(`保存失败：${e.message || e}`);
    }
  };

  const remove = async (p) => {
    if (!window.confirm(`确定删除指令「${p.name}」？`)) return;
    try {
      await api.deletePrompt(p.id);
      await load();
      flash("已删除");
    } catch (e) {
      flash(`删除失败：${e.message || e}`);
    }
  };

  const toggle = async (p) => {
    await save({ ...p, enabled: p.enabled === false });
  };

  const copyToMine = async (p) => {
    const draft = { ...p, id: "", name: `${p.name} 副本`, builtin: false, use_count: 0 };
    setEditing(draft);
  };

  const move = async (p, dir) => {
    const i = userItems.findIndex((x) => x.id === p.id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= userItems.length) return;
    const next = userItems.slice();
    next[i] = userItems[j];
    next[j] = userItems[i];
    try {
      await api.reorderPrompts(next.map((x) => x.id));
      await load();
    } catch (e) {
      flash(`排序失败：${e.message || e}`);
    }
  };

  const doExport = async () => {
    try {
      const list = await api.exportPrompts();
      const blob = new Blob([JSON.stringify(list, null, 1)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `whaletalk-prompts-${todayStr()}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      flash(`已导出 ${list.length} 条`);
    } catch (e) {
      flash(`导出失败：${e.message || e}`);
    }
  };

  const doImport = async (file, mode) => {
    try {
      const text = await file.text();
      const list = JSON.parse(text);
      if (!Array.isArray(list)) throw new Error("文件内容需为指令数组");
      const r = await api.importPrompts(list, mode);
      await load();
      flash(`已导入 ${r.added} 条（共 ${r.total} 条）`);
    } catch (e) {
      flash(`导入失败：${e.message || e}`);
    }
  };

  const restore = async () => {
    if (!window.confirm("恢复内置模板：只补充你缺失的内置指令，不会覆盖你已有的修改。继续？")) return;
    try {
      const r = await api.restoreBuiltinPrompts();
      await load();
      flash(`已补充 ${r.added} 条内置模板`);
    } catch (e) {
      flash(`恢复失败：${e.message || e}`);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>指令库</h1>
        <p>
          提示词资产中心 · 输入框打 / 即可调用
          {items ? ` · 内置 ${items.filter((p) => p.builtin).length} 条 / 我的 ${userItems.length} 条` : ""}
        </p>
      </div>

      <div className="pm-toolbar">
        <div className="pm-search">
          <input
            placeholder="搜索指令（名称 / 描述 / 内容 / 标签）"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="pm-actions">
          <button className="confirm-btn confirm-primary" onClick={() => setEditing({ ...BLANK })}>＋ 新建指令</button>
          <button className="confirm-btn" onClick={() => fileRef.current?.click()}>导入</button>
          <button className="confirm-btn" onClick={doExport}>导出</button>
          <button className="confirm-btn" onClick={restore}>恢复内置</button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) doImport(f, "merge");
              e.target.value = "";
            }}
          />
        </div>
      </div>

      <div className="pm-wrap">
        <aside className="pm-side">
          <div className="pm-side-title">来源</div>
          {SOURCES.map((s) => (
            <div
              key={s.id}
              className={`pm-side-item ${src === s.id ? "pm-side-on" : ""}`}
              onClick={() => setSrc(s.id)}
            >
              {s.label}
            </div>
          ))}
          <div className="pm-side-title">分类</div>
          {cats.map((c) => (
            <div
              key={c}
              className={`pm-side-item ${cat === c ? "pm-side-on" : ""}`}
              onClick={() => setCat(c)}
            >
              {c}
            </div>
          ))}
        </aside>

        <main className="pm-main">
          {!items && <div className="empty-tip">加载中…</div>}
          {items && filtered.length === 0 && (
            <div className="empty-tip">没有匹配的指令（换个筛选条件，或点「＋ 新建指令」）</div>
          )}
          <div className="pm-grid">
            {filtered.map((p) => {
              const off = p.enabled === false;
              return (
                <div className={`pm-card ${off ? "pm-card-off" : ""}`} key={p.id}>
                  <div className="pm-card-head">
                    <span className="pm-icon">{p.icon || "⚡"}</span>
                    <b className="pm-name">{p.name}</b>
                    {p.builtin && <span className="pm-badge">内置</span>}
                    {p.fromPlugin && <span className="pm-badge">插件</span>}
                    {off && <span className="pm-badge pm-badge-off">已禁用</span>}
                    <span className="pm-cat">{p.category || "未分类"}</span>
                    {p.shortcut && <code className="pm-sc">{p.shortcut}</code>}
                  </div>
                  {p.desc && <div className="pm-desc">{p.desc}</div>}
                  <div className="pm-text">{String(p.text || "").slice(0, 90)}</div>
                  <div className="pm-foot">
                    <span className="pm-meta">{p.use_count ? `用过 ${p.use_count} 次` : "未使用"}</span>
                    <div className="pm-ops">
                      <button className="pm-op" title="应用到会话输入框" onClick={() => onApply?.(fillVars(p.text))}>应用</button>
                      {p.builtin || p.fromPlugin ? (
                        <button className="pm-op" onClick={() => copyToMine(p)}>复制到我的指令</button>
                      ) : (
                        <>
                          <button className="pm-op" onClick={() => setEditing(p)}>编辑</button>
                          <button className="pm-op" onClick={() => toggle(p)}>{off ? "启用" : "禁用"}</button>
                          <button className="pm-op" onClick={() => copyToMine(p)}>复制</button>
                          <button className="pm-op" onClick={() => move(p, -1)}>↑</button>
                          <button className="pm-op" onClick={() => move(p, 1)}>↓</button>
                          <button className="pm-op pm-op-danger" onClick={() => remove(p)}>删除</button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </main>
      </div>

      {editing && (
        <PromptEditor
          value={editing}
          onClose={() => setEditing(null)}
          onSave={save}
        />
      )}
      {tip && <div className="set-saved-tip">{tip}</div>}
    </div>
  );
}

function PromptEditor({ value, onClose, onSave }) {
  const [f, setF] = React.useState({
    ...value,
    tagsText: (value.tags || []).join(", "),
  });
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }));

  return (
    <Overlay title={value.id ? "编辑指令" : "新建指令"} onClose={onClose} wide>
      <div className="pm-form">
        <div className="pm-field">
          <label>名称 *</label>
          <input className="set-select" value={f.name} placeholder="如：周报生成"
            onChange={(e) => set("name", e.target.value)} />
        </div>
        <div className="pm-row">
          <div className="pm-field pm-field-sm">
            <label>图标</label>
            <input className="set-select" value={f.icon || ""} placeholder="⚡"
              onChange={(e) => set("icon", e.target.value)} />
          </div>
          <div className="pm-field">
            <label>分类</label>
            <input className="set-select" value={f.category || ""} placeholder="未分类"
              onChange={(e) => set("category", e.target.value)} />
          </div>
          <div className="pm-field">
            <label>短命令</label>
            <input className="set-select" value={f.shortcut || ""} placeholder="/weekly"
              onChange={(e) => set("shortcut", e.target.value)} />
          </div>
        </div>
        <div className="pm-field">
          <label>说明</label>
          <input className="set-select" value={f.desc || ""} placeholder="一句话说明这条指令做什么"
            onChange={(e) => set("desc", e.target.value)} />
        </div>
        <div className="pm-field">
          <label>标签（逗号分隔）</label>
          <input className="set-select" value={f.tagsText || ""} placeholder="工作, 效率"
            onChange={(e) => set("tagsText", e.target.value)} />
        </div>
        <div className="pm-field">
          <label>指令内容 *</label>
          <textarea
            className="pm-textarea"
            rows={10}
            value={f.text || ""}
            placeholder={"请输入指令模板，例如：\n请把以下内容整理为周报：\n\n{{TEXT}}"}
            onChange={(e) => set("text", e.target.value)}
          />
          <div className="pm-hint">可用变量：{VAR_HINT}</div>
        </div>
        <label className="pm-check">
          <input type="checkbox" checked={!!f.auto_send} onChange={(e) => set("auto_send", e.target.checked)} />
          <span>调用后自动发送（适合无需补充内容的完整指令）</span>
        </label>
        <div className="pm-form-ops">
          <button className="confirm-btn" onClick={onClose}>取消</button>
          <button
            className="confirm-btn confirm-primary"
            disabled={!String(f.name || "").trim() || !String(f.text || "").trim()}
            onClick={() => onSave({
              ...f,
              tags: String(f.tagsText || "").split(",").map((x) => x.trim()).filter(Boolean),
            })}
          >
            保存
          </button>
        </div>
      </div>
    </Overlay>
  );
}
