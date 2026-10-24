import React from "react";
import * as api from "../api.js";
import Overlay from "./Overlay.jsx";
import { SkeletonList } from "./Skeleton.jsx";
import { confirmDialog } from "../dialog.js";
import { silentWarn } from "../quiet.js";

const BLANK = {
  id: "",
  name: "",
  avatar: "🎭",
  tagline: "",
  personality: "",
  speaking_style: "",
  background: "",
  world: "",
  relationship: "",
  greeting: "",
  examples: "",
  boundaries: "",
};

function PersonaCard({ persona, onChat, onEdit, onDuplicate, onDelete, onMemory }) {
  return (
    <div className="pm-card persona-card">
      <div className="persona-card-head">
        <span className="persona-card-avatar" aria-hidden="true">{persona.avatar || "🎭"}</span>
        <div className="persona-card-title">
          <b>{persona.name}</b>
          {persona.builtin ? <span className="pm-badge">内置</span> : null}
          <span className="persona-card-tagline">{persona.tagline || "还没有一句话设定"}</span>
        </div>
      </div>
      <div className="persona-card-desc">
        {persona.personality || persona.background || "还没有填写角色性格。"}
      </div>
      {persona.greeting ? <div className="persona-card-greeting">“{persona.greeting}”</div> : null}
      <div className="pm-foot">
        <span className="pm-meta">{persona.memory_count ? `角色记忆 ${persona.memory_count} 条` : "还没有角色记忆"}</span>
        <div className="pm-ops">
          <button className="pm-op persona-chat-op" onClick={() => onChat(persona)}>开始对话</button>
          <button className="pm-op" onClick={() => onMemory(persona)}>记忆</button>
          <button className="pm-op" onClick={() => onEdit(persona)}>编辑</button>
          <button className="pm-op" onClick={() => onDuplicate(persona)}>复制</button>
          {!persona.builtin ? (
            <button className="pm-op pm-op-danger" onClick={() => onDelete(persona)}>删除</button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function PersonaEditor({ value, onClose, onSave }) {
  const [form, setForm] = React.useState({ ...BLANK, ...value });
  const set = (key, val) => setForm((prev) => ({ ...prev, [key]: val }));
  const canSave = !!String(form.name || "").trim();

  return (
    <Overlay title={value.id ? "编辑角色" : "新建角色"} onClose={onClose} wide>
      <div className="pm-form persona-form">
        <div className="pm-row">
          <div className="pm-field pm-field-sm">
            <label>头像</label>
            <input
              className="set-select persona-avatar-input"
              value={form.avatar || ""}
              placeholder="🐋"
              maxLength={8}
              onChange={(e) => set("avatar", e.target.value)}
            />
          </div>
          <div className="pm-field">
            <label>角色名 *</label>
            <input
              className="set-select"
              value={form.name || ""}
              placeholder="例如：小鲸"
              maxLength={40}
              onChange={(e) => set("name", e.target.value)}
            />
          </div>
        </div>
        <div className="pm-field">
          <label>一句话设定</label>
          <input
            className="set-select"
            value={form.tagline || ""}
            placeholder="例如：住在深海的温柔倾听者"
            maxLength={120}
            onChange={(e) => set("tagline", e.target.value)}
          />
        </div>
        <div className="pm-field">
          <label>性格</label>
          <textarea
            className="pm-textarea"
            rows={3}
            value={form.personality || ""}
            placeholder="她/他是什么样的人？如何理解情绪、冲突和亲密感？"
            onChange={(e) => set("personality", e.target.value)}
          />
        </div>
        <div className="pm-row">
          <div className="pm-field">
            <label>说话方式</label>
            <textarea
              className="pm-textarea"
              rows={3}
              value={form.speaking_style || ""}
              placeholder="句子长短、口头禅、语气、是否使用 emoji"
              onChange={(e) => set("speaking_style", e.target.value)}
            />
          </div>
          <div className="pm-field">
            <label>与用户的关系</label>
            <textarea
              className="pm-textarea"
              rows={3}
              value={form.relationship || ""}
              placeholder="朋友 / 搭档 / 倾听者……以及亲近的边界"
              onChange={(e) => set("relationship", e.target.value)}
            />
          </div>
        </div>
        <div className="pm-field">
          <label>背景</label>
          <textarea
            className="pm-textarea"
            rows={3}
            value={form.background || ""}
            placeholder="经历、身份、在意的事（不需要写成小说）"
            onChange={(e) => set("background", e.target.value)}
          />
        </div>
        <div className="pm-field">
          <label>所处世界 / 场景</label>
          <textarea
            className="pm-textarea"
            rows={2}
            value={form.world || ""}
            placeholder="日常陪伴 / 奇幻世界 / 某个具体场景"
            onChange={(e) => set("world", e.target.value)}
          />
        </div>
        <div className="pm-field">
          <label>开场白</label>
          <input
            className="set-select"
            value={form.greeting || ""}
            placeholder="新对话时展示的一句话"
            maxLength={1000}
            onChange={(e) => set("greeting", e.target.value)}
          />
        </div>
        <div className="pm-field">
          <label>对话示例</label>
          <textarea
            className="pm-textarea"
            rows={4}
            value={form.examples || ""}
            placeholder={"用户：今天有点累。\n小鲸：那就先不急着解决什么。要不要把最累的那一段说给我听听？"}
            onChange={(e) => set("examples", e.target.value)}
          />
          <div className="pm-hint">示例只用于校准语气，不会被逐字复述。</div>
        </div>
        <div className="pm-field">
          <label>角色边界</label>
          <textarea
            className="pm-textarea"
            rows={3}
            value={form.boundaries || ""}
            placeholder="不希望角色做/说什么；例如：不替用户做重大决定、不诱导疏远现实关系"
            onChange={(e) => set("boundaries", e.target.value)}
          />
        </div>
        <div className="persona-rule-note">
          角色只作用于「对话」模式；任务模式不会注入角色提示。系统会额外加上「不冒充真人、不编造共同经历、可随时出戏」等安全规则。
        </div>
        <div className="pm-form-ops">
          <button className="confirm-btn" onClick={onClose}>取消</button>
          <button className="confirm-btn confirm-primary" disabled={!canSave} onClick={() => onSave(form)}>
            保存
          </button>
        </div>
      </div>
    </Overlay>
  );
}

function PersonaMemory({ persona, onClose, onChanged }) {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setErr("");
    try {
      const d = await api.getPersonaMemory(persona.id);
      setData(d);
    } catch (e) {
      setErr(`读取角色记忆失败：${e.message || e}`);
    }
  }, [persona.id]);

  React.useEffect(() => { load(); }, [load]);

  const clear = async () => {
    if (!(await confirmDialog(`清空「${persona.name}」的全部角色记忆？此操作不可恢复。`, { danger: true, okText: "清空" }))) return;
    setBusy(true);
    try {
      await api.clearPersonaMemory(persona.id);
      setData({ persona_id: persona.id, count: 0, facts: [] });
      onChanged?.();
    } catch (e) {
      setErr(`清空失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const removeOne = async (factId) => {
    setBusy(true);
    try {
      await api.deletePersonaMemory(persona.id, factId);
      await load();
      onChanged?.();
    } catch (e) {
      setErr(`删除失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Overlay title={`${persona.avatar || "🎭"} ${persona.name} 的角色记忆`} onClose={onClose} wide>
      <div className="persona-memory">
        <div className="persona-memory-note">
          这些内容只属于当前角色，与全局长期记忆/大脑隔离；角色对话会自动沉淀，也可手动清理。
        </div>
        {err ? <div className="empty-tip is-err">{err}</div> : null}
        {!data ? <SkeletonList rows={3} /> : null}
        {data && data.facts.length === 0 ? (
          <div className="empty-tip">还没有角色记忆。和它多聊几次后会自动出现。</div>
        ) : null}
        <div className="persona-memory-list">
          {(data?.facts || []).map((f) => (
            <div className="persona-memory-item" key={f.id || `${f.text}-${f.ts}`}>
              <div className="persona-memory-text">{f.text}</div>
              <div className="persona-memory-meta">
                <span>{f.ts || "—"}</span>
                <span>{f.origin === "agent" ? "推断" : f.origin === "user" ? "用户" : f.origin || "未知"}</span>
                <button className="pm-op pm-op-danger" disabled={busy} onClick={() => removeOne(f.id)}>删除</button>
              </div>
            </div>
          ))}
        </div>
        <div className="pm-form-ops">
          <button className="confirm-btn" onClick={onClose}>关闭</button>
          <button className="confirm-btn pm-op-danger" disabled={busy || !data || data.count === 0} onClick={clear}>清空记忆</button>
        </div>
      </div>
    </Overlay>
  );
}

export default function PersonasPage({ onChat }) {
  const [items, setItems] = React.useState(null);
  const [editing, setEditing] = React.useState(null);
  const [memoryFor, setMemoryFor] = React.useState(null);
  const [tip, setTip] = React.useState("");
  const [err, setErr] = React.useState("");

  const flash = (text) => {
    setTip(text);
    setTimeout(() => setTip(""), 1800);
  };

  const load = React.useCallback(async () => {
    setErr("");
    try {
      const d = await api.listPersonas();
      setItems(Array.isArray(d?.personas) ? d.personas : []);
    } catch (e) {
      setItems([]);
      setErr("角色列表读取失败，请确认后端已连接。");
      silentWarn(e, "PersonasPage");
    }
  }, []);

  React.useEffect(() => { load(); }, [load]);

  const save = async (persona) => {
    try {
      const d = await api.savePersona(persona);
      setItems(Array.isArray(d?.personas) ? d.personas : []);
      setEditing(null);
      flash("角色已保存");
    } catch (e) {
      flash(`保存失败：${e.message || e}`);
    }
  };

  const duplicate = async (persona) => {
    try {
      const d = await api.duplicatePersona(persona.id);
      setItems(Array.isArray(d?.personas) ? d.personas : []);
      flash("已复制为新角色");
    } catch (e) {
      flash(`复制失败：${e.message || e}`);
    }
  };

  const remove = async (persona) => {
    if (!(await confirmDialog(`确定删除角色「${persona.name}」？`, { danger: true, okText: "删除" }))) return;
    try {
      const d = await api.deletePersona(persona.id);
      setItems(Array.isArray(d?.personas) ? d.personas : []);
      flash("角色已删除");
    } catch (e) {
      flash(`删除失败：${e.message || e}`);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>角色</h1>
        <p>为对话模式创建长期角色：性格、说话方式、背景、开场白与边界。会话会记住自己绑定的角色。</p>
      </div>

      <div className="pm-toolbar">
        <div className="persona-toolbar-note">
          角色只影响「对话」模式；任务模式仍按工具与任务纪律执行。
        </div>
        <div className="pm-actions">
          <button className="confirm-btn confirm-primary" onClick={() => setEditing({ ...BLANK })}>+ 新建角色</button>
        </div>
      </div>

      <div className="pm-wrap persona-wrap">
        <main className="pm-main">
          {!items && <SkeletonList rows={4} />}
          {items && err && <div className="empty-tip is-err">{err}</div>}
          {items && !err && items.length === 0 && (
            <div className="empty-tip">还没有角色。点击「新建角色」开始。</div>
          )}
          <div className="pm-grid persona-grid">
            {(items || []).map((p) => (
              <PersonaCard
                key={p.id}
                persona={p}
                onChat={(persona) => onChat?.(persona)}
                onEdit={(persona) => setEditing(persona)}
                onDuplicate={duplicate}
                onDelete={remove}
                onMemory={(persona) => setMemoryFor(persona)}
              />
            ))}
          </div>
        </main>
      </div>

      {editing && (
        <PersonaEditor
          value={editing}
          onClose={() => setEditing(null)}
          onSave={save}
        />
      )}
      {memoryFor && (
        <PersonaMemory
          persona={memoryFor}
          onClose={() => setMemoryFor(null)}
          onChanged={load}
        />
      )}
      {tip && <div className="set-saved-tip">{tip}</div>}
    </div>
  );
}
