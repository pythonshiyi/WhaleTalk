import React from "react";
import * as api from "../api.js";
import { SkeletonPage } from "./Skeleton.jsx";
import { silentWarn } from "../quiet.js";

// ── 权限（黑名单管理 · 核心哲学：默认全放行，只按禁止拦截）──
function BlacklistGroup({ title, desc, items, color, onAdd, onRemove, placeholder }) {
  const [input, setInput] = React.useState("");
  return (
    <div className="perm-group">
      <div className="perm-group-head">
        <b>{title}</b>
        <span>{desc}</span>
      </div>
      <div className="perm-chips">
        {items.length === 0 ? (
          <span className="perm-empty">空 —— 默认放行（不拦截任何{title === "审批动作" ? "工具" : "目标"}）</span>
        ) : (
          items.map((it) => (
            <span className="perm-chip" key={it} style={{ borderColor: `${color}44`, color }}>
              {it}
              <button className="perm-chip-x" onClick={() => onRemove(it)}>×</button>
            </span>
          ))
        )}
      </div>
      <div className="perm-add">
        <input
          placeholder={placeholder}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && input.trim()) {
              onAdd(input.trim());
              setInput("");
            }
          }}
        />
        <button
          className="confirm-btn"
          disabled={!input.trim()}
          onClick={() => {
            onAdd(input.trim());
            setInput("");
          }}
        >
          添加
        </button>
      </div>
    </div>
  );
}

export default function PermissionsPage({ embedded = false }) {
  const [perms, setPerms] = React.useState(null);
  const [savedTip, setSavedTip] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");

  const load = React.useCallback(async () => {
    setErr("");
    const d = await api.getPermissions().catch(() => null);
    if (d) setPerms(d);
    else setErr("权限设置加载失败（后端未连接）");
  }, []);
  React.useEffect(() => { load(); }, [load]);

  if (!perms) {
    return (
      <div className={embedded ? "page-embedded" : "page"}>
        {err
          ? <div className="empty-tip is-err">{err}　<button className="msg-op" onClick={load}>重试</button></div>
          : <SkeletonPage title="权限" hint="正在加载权限设置…" />}
      </div>
    );
  }

  const update = async (key, items) => {
    setBusy(true);
    try {
      const d = await api.savePermissions({ [key]: items }).catch(() => null);
      if (d && d.ok) {
        setPerms({ ...perms, [key]: items });
        setSavedTip("已保存到 permissions.json");
        setTimeout(() => setSavedTip(""), 1800);
      }
    } catch (e) { silentWarn(e, "PermissionsPage"); }
    setBusy(false);
  };

  const groups = [
    {
      key: "blocked_dirs",
      title: "禁目录（filesystem.blocked_dirs）",
      desc: "命中路径的文件操作直接拒绝",
      color: "var(--danger-text)",
      placeholder: "输入禁用的目录路径，Enter 添加",
    },
    {
      key: "shell_blocklist",
      title: "禁命令（shell.blocklist）",
      desc: "命中命令名的 run_command 拒绝",
      color: "var(--warn-text)",
      placeholder: "输入禁用的命令名，Enter 添加",
    },
    {
      key: "network_blocklist",
      title: "禁网络（network.blocklist）",
      desc: "命中的主机/网段请求拒绝",
      color: "var(--ai)",
      placeholder: "输入禁用的主机或网段，Enter 添加",
    },
    {
      key: "approval_actions",
      title: "审批动作（approval_actions）",
      desc: "仅这些工具执行前需要你确认（其余默认放行）",
      color: "var(--brand)",
      placeholder: "输入工具名，如 send_email，Enter 添加",
    },
  ];

  return (
    <div className={embedded ? "page-embedded" : "page"}>
      {!embedded && (
        <div className="page-head">
          <h1>权限</h1>
          <p>自由优先：默认放行 + 黑名单。AI 拥有全部行动能力，只按你禁止的拦截。</p>
        </div>
      )}
      <div className={`perm-mode-banner ${perms.full_auto ? "perm-mode-task" : ""}`}>
        {perms.full_auto
          ? "🚀 任务模式已开启：零审批、零开关，AI 拥有全部工具（黑名单仍生效）"
          : "💬 对话模式：不调用任何工具，黑名单不生效"}
      </div>
      {/* 黑名单一键总开关：默认开（黑名单空 = 0 限制）；关 = 一键全放行（连黑名单也不拦） */}
      <div className="perm-group" style={{ borderColor: "var(--border-strong)" }}>
        <div className="perm-group-head">
          <b>黑名单总开关（blocklist_enabled）</b>
          <span>{perms.blocklist_enabled ? "已开启：黑名单条目生效（默认空 = 0 限制）" : "已关闭：一键全放行，黑名单条目不拦截"}</span>
        </div>
        <button
          className="confirm-btn"
          onClick={async () => {
            setBusy(true);
            try {
              const d = await api.savePermissions({ blocklist_enabled: !perms.blocklist_enabled }).catch(() => null);
              if (d && d.ok) {
                setPerms({ ...perms, blocklist_enabled: !perms.blocklist_enabled });
                setSavedTip("已保存到 permissions.json");
                setTimeout(() => setSavedTip(""), 1800);
              }
            } catch (e) { silentWarn(e, "PermissionsPage"); }
            setBusy(false);
          }}
          disabled={busy}
        >
          {perms.blocklist_enabled ? "🔓 一键全放行" : "🛡 启用黑名单限制"}
        </button>
      </div>
      <div className="perm-groups">
        {groups.map((g) => (
          <BlacklistGroup
            key={g.key}
            title={g.title}
            desc={g.desc}
            color={g.color}
            items={perms[g.key] || []}
            placeholder={g.placeholder}
            onAdd={(v) => update(g.key, [...(perms[g.key] || []), v])}
            onRemove={(v) => update(g.key, (perms[g.key] || []).filter((x) => x !== v))}
          />
        ))}
      </div>
      <div className="perm-note">配置文件：permissions.json · 修改立即生效</div>
      {savedTip && <div className="set-saved-tip">{savedTip}</div>}
    </div>
  );
}
