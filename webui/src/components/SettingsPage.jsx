import React from "react";
import { ThemeContext, DisplayContext } from "../App.jsx";
import * as api from "../api.js";

const apiGet = async (path) => {
  try {
    return await api.api(path);
  } catch {
    return null;
  }
};
const apiPost = async (path, body) => {
  try {
    return await api.api(path, { method: "POST", body: JSON.stringify(body) });
  } catch {
    return null;
  }
};

// ── 一键预设（新手懒人）──────────────────────────────
const PRESETS = [
  { id: "balanced", name: "🎯 均衡", desc: "Pro + 高思考，全能平衡，日常推荐", cfg: { model: "deepseek-v4-pro", thinking: "high", max_tokens: 16384, temperature: 1.0, top_p: 1.0, json_output: false } },
  { id: "save", name: "💰 省钱", desc: "Flash + 低思考，token 最省", cfg: { model: "deepseek-v4-flash", thinking: "low", max_tokens: 8192, temperature: 1.0, top_p: 1.0 } },
  { id: "power", name: "🚀 性能", desc: "Pro + 最大思考，最强推理输出", cfg: { model: "deepseek-v4-pro", thinking: "max", max_tokens: 32768 } },
  { id: "creative", name: "✍️ 创作", desc: "无思考 + 高温度，写作与创意", cfg: { model: "deepseek-v4-flash", thinking: "none", max_tokens: 16384, temperature: 1.3, top_p: 0.95 } },
];

const THEMES = [
  { id: "starfield", name: "星空黑", desc: "极黑冷底 · 亮青点缀", preview: ["#05070e", "#111827", "#38bdf8"] },
  { id: "deepsea", name: "深海蓝", desc: "深蓝底 · 亮湖蓝", preview: ["#02101f", "#082542", "#22c8ff"] },
  { id: "arctic", name: "北极冰", desc: "冰白底 · 深海蓝文字", preview: ["#edf2f9", "#ffffff", "#0284c7"] },
];

// ── 通用小组件 ──────────────────────────────────────
function Row({ label, desc, children }) {
  return (
    <div className="set-row">
      <div className="set-info"><b>{label}</b>{desc && <span>{desc}</span>}</div>
      {children}
    </div>
  );
}
function Toggle({ on, onClick, label, desc }) {
  return (
    <Row label={label} desc={desc}>
      <button className={`toggle ${on ? "toggle-on" : ""}`} onClick={onClick}>
        <span className="toggle-knob" />
      </button>
    </Row>
  );
}
function NumInput({ value, onChange, min, max, step }) {
  return (
    <input
      className="set-select set-num"
      type="number"
      min={min} max={max} step={step}
      value={value ?? ""}
      onChange={(e) => {
        const v = Number(e.target.value);
        if (e.target.value !== "" && !Number.isNaN(v)) onChange(v);
      }}
    />
  );
}

// ── 外部服务（邮件/IM/Webhook/数据库/图片/接收端）──────
function ServicesTab({ cfg, onTip }) {
  const [svc, setSvc] = React.useState(null);
  React.useEffect(() => {
    apiGet("/v1/services").then((d) => d && setSvc(d));
  }, []);
  if (!svc) return <div className="empty-tip">加载外部服务配置…</div>;

  const field = (group, key) => (svc[group] || {})[key] || "";
  const setField = (group, key, val) =>
    setSvc((s) => ({ ...s, [group]: { ...(s[group] || {}), [key]: val } }));

  const save = async () => {
    const r = await apiPost("/v1/services", {
      webhooks: svc.webhooks || {},
      im: svc.im || {},
      db: svc.db || {},
      email: svc.email || {},
      agent_mail: svc.agent_mail || {},
      image: svc.image || {},
      inbound: svc.inbound || {},
    });
    if (r && r.ok) onTip("外部服务已保存（敏感字段加密存储）");
  };

  const EmRow = ({ label, placeholder, group, key, desc, type }) => (
    <Row label={label} desc={desc}>
      <input
        className="set-select set-combo"
        type={type || "text"}
        placeholder={placeholder}
        value={field(group, key)}
        onChange={(e) => setField(group, key, e.target.value)}
      />
    </Row>
  );

  return (
    <div className="svc-wrap">
      <div className="svc-group">
        <div className="svc-title">✉️ 邮件收发（SMTP 发送 + IMAP 读取）</div>
        <EmRow label="SMTP 服务器" placeholder="smtp.qq.com" group="email" key="smtp_host" />
        <EmRow label="SMTP 端口" placeholder="465 或 587" group="email" key="smtp_port" />
        <EmRow label="邮箱账号" placeholder="user@example.com" group="email" key="user" />
        <EmRow label="密码 / 授权码" placeholder="加密存储" group="email" key="password" type="password" />
        <EmRow label="发件人" placeholder="默认=账号" group="email" key="from" />
        <EmRow label="IMAP 服务器" placeholder="imap.qq.com" group="email" key="imap_host" desc="（兼容扁平键）" />
        <EmRow label="IMAP 端口" placeholder="993" group="email" key="imap_port" />
        <EmRow label="IMAP 账号" group="email" key="imap_user" />
        <EmRow label="IMAP 密码" type="password" group="email" key="imap_password" />
      </div>
      <div className="svc-group">
        <div className="svc-title">📱 IM 通道（企业微信 / Telegram）</div>
        <EmRow label="企业微信机器人 Webhook" group="im" key="wecom_webhook" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…" />
        <EmRow label="企业微信智能机器人 Bot ID" group="im" key="wecom_aibot_bot_id" />
        <EmRow label="企业微信 Secret" group="im" key="wecom_aibot_secret" type="password" />
        <EmRow label="Telegram Bot Token" group="im" key="telegram_bot_token" type="password" />
        <EmRow label="Telegram Chat ID" group="im" key="telegram_chat_id" />
      </div>
      <div className="svc-group">
        <div className="svc-title">🔗 Webhook 推送（钉钉 / Server酱 / Slack / 通用）</div>
        <EmRow label="钉钉 URL" group="webhooks" key="dingtalk" placeholder="https://oapi.dingtalk.com/robot/send?access_token=…" />
        <EmRow label="Server酱 URL" group="webhooks" key="serverchan" placeholder="https://sctapi.ftqq.com/KEY.send" />
        <EmRow label="Slack URL" group="webhooks" key="slack" />
        <EmRow label="通用 URL（POST {title,text}）" group="webhooks" key="generic" />
      </div>
      <div className="svc-group">
        <div className="svc-title">🗄 数据库连接（只读查询，连接名 default）</div>
        {(["mysql", "postgres"]).map((kind) => {
          const conn = ((svc.db || {})[kind] || {}).default || {};
          const setConn = (key, val) => {
            const db = { ...(svc.db || {}), [kind]: { default: { ...conn, [key]: val } } };
            setSvc((s) => ({ ...s, db }));
          };
          return (
            <div key={kind}>
              <div className="svc-title" style={{ fontSize: 11 }}>{kind === "mysql" ? "MySQL" : "PostgreSQL"}</div>
              <Row label="主机">
                <input className="set-select set-combo" placeholder="127.0.0.1" value={conn.host || ""} onChange={(e) => setConn("host", e.target.value)} />
              </Row>
              <Row label="端口">
                <NumInput min={1} max={65535} value={conn.port || (kind === "mysql" ? 3306 : 5432)} onChange={(v) => setConn("port", v)} />
              </Row>
              <Row label="用户">
                <input className="set-select set-combo" value={conn.user || ""} onChange={(e) => setConn("user", e.target.value)} />
              </Row>
              <Row label="密码">
                <input className="set-select set-combo" type="password" value={conn.password || ""} onChange={(e) => setConn("password", e.target.value)} />
              </Row>
              <Row label="数据库名">
                <input className="set-select set-combo" value={conn.database || ""} onChange={(e) => setConn("database", e.target.value)} />
              </Row>
            </div>
          );
        })}
      </div>
      <div className="svc-group">
        <div className="svc-title">🖼 图片生成（OpenAI 兼容）</div>
        <EmRow label="端点" group="image" key="base_url" placeholder="默认=当前 base_url" />
        <EmRow label="API Key" group="image" key="api_key" type="password" />
        <EmRow label="模型" group="image" key="model" placeholder="gpt-image-1" />
        <Toggle
          on={!!(svc.image || {}).vision_self_review}
          label="视觉自审"
          desc="生成图片后自动看图审阅（默认关控成本）"
          onClick={() => setField("image", "vision_self_review", !(svc.image || {}).vision_self_review)}
        />
      </div>
      <div className="svc-group">
        <div className="svc-title">📡 远程接收端（Webhook 收任务）</div>
        <Row label="监听端口" desc="0=关闭（改后重启生效）">
          <NumInput min={0} max={65535} value={Number(field("inbound", "port")) || 0} onChange={(v) => setField("inbound", "port", v)} />
        </Row>
        <EmRow label="鉴权 Token" group="inbound" key="token" type="password" />
      </div>
      <div className="svc-group">
        <div className="svc-title">✈️ Agent Mail（CLI 邮件代理）</div>
        <Toggle on={!!(svc.agent_mail || {}).enabled} label="启用" onClick={() => setField("agent_mail", "enabled", !(svc.agent_mail || {}).enabled)} />
        <EmRow label="CLI 路径" group="agent_mail" key="cli" placeholder="agently-cli" />
      </div>
      <button className="confirm-btn confirm-primary" onClick={save}>💾 保存外部服务</button>
    </div>
  );
}

// ── 高级参数 ────────────────────────────────────────
function AdvancedTab({ cfg, saveField, onReset }) {
  const [prompts, setPrompts] = React.useState(null);
  React.useEffect(() => {
    apiGet("/v1/prompts").then((d) => d && setPrompts(d.prompts || []));
  }, []);

  const savePrompts = async () => {
    const r = await apiPost("/v1/prompts", { prompts });
    if (r && r.ok) {
      saveField({ _prompts: prompts }, true);
    }
  };

  return (
    <div className="svc-wrap">
      <div className="svc-group">
        <div className="svc-title">🧠 上下文管理（1M 窗口，自动压缩）</div>
        <Row label="压缩阈值 tokens" desc="超限触发压缩">
          <NumInput min={8000} max={900000} step={10000} value={cfg?.max_context_tokens} onChange={(v) => saveField({ max_context_tokens: v })} />
        </Row>
        <Row label="压缩阈值字符">
          <NumInput min={10000} value={cfg?.max_context_chars} onChange={(v) => saveField({ max_context_chars: v })} />
        </Row>
        <Row label="保留轮数" desc="压缩时最少保留的对话轮">
          <NumInput min={3} max={500} value={cfg?.min_kept_turns} onChange={(v) => saveField({ min_kept_turns: v })} />
        </Row>
        <Row label="早期折叠阈值" desc="超过此块数折叠（0=关闭）">
          <NumInput min={0} max={20000} step={100} value={cfg?.fold_early_threshold} onChange={(v) => saveField({ fold_early_threshold: v })} />
        </Row>
      </div>
      <div className="svc-group">
        <div className="svc-title">⚙ 运行参数</div>
        <Row label="请求超时（秒）">
          <NumInput min={10} max={600} value={cfg?.timeout} onChange={(v) => saveField({ timeout: v })} />
        </Row>
        <Row label="工具轮数上限" desc="单条消息最多工具循环轮数">
          <NumInput min={1} max={100} value={cfg?.max_tool_rounds} onChange={(v) => saveField({ max_tool_rounds: v })} />
        </Row>
        <Toggle on={!!cfg?.project_context} label="项目上下文" desc="注入工作区概览（默认关）" onClick={() => saveField({ project_context: !cfg?.project_context })} />
        <Toggle on={cfg?.suggestions_enabled !== false} label="主动建议" desc="状态栏建议条" onClick={() => saveField({ suggestions_enabled: cfg?.suggestions_enabled === false })} />
      </div>
      <div className="svc-group">
        <div className="svc-title">📋 指令库（⚡ 指令菜单）</div>
        {(prompts || []).map((p, i) => (
          <div className="svc-prompt-row" key={i}>
            <input className="set-select set-combo" value={p.name} placeholder="名称"
              onChange={(e) => setPrompts(prompts.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
            <input className="set-select set-combo" value={p.text} placeholder="内容（{{TEXT}} 占位）"
              onChange={(e) => setPrompts(prompts.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))} />
            <button className="msg-op" onClick={() => setPrompts(prompts.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <div className="svc-actions">
          <button className="confirm-btn" onClick={() => setPrompts([...(prompts || []), { name: "", text: "" }])}>＋ 新增指令</button>
          <button className="confirm-btn confirm-primary" onClick={savePrompts}>保存指令库</button>
        </div>
      </div>
      <div className="svc-group">
        <div className="svc-title">🔀 流程编排（workflows.json）</div>
        <WorkflowsBlock />
      </div>
      <div className="svc-group">
        <div className="svc-title">📌 任务检查点 / 知识库 / 审计</div>
        <CheckpointBlock />
        <KnowledgeBlock />
        <AuditBlock />
        <DepsBlock />
      </div>
      <div className="svc-group">
        <div className="svc-title">📦 备份 / 🚀 更新 / 🧹 清理</div>
        <BackupBlock />
        <UpdateBlock />
        <CleanupBlock />
      </div>
      <div className="svc-group">
        <div className="svc-title">♻️ 恢复默认</div>
        <button className="confirm-btn" style={{ color: "var(--danger)" }} onClick={onReset}>
          恢复全部默认配置（保留 API Key）
        </button>
      </div>
    </div>
  );
}

// ── C1 备份 ────────────────────────────────────────
function BackupBlock() {
  const [list, setList] = React.useState(null);
  const [tip, setTip] = React.useState("");
  const load = async () => {
    const d = await apiGet("/v1/backup");
    if (d) setList(d.backups || []);
  };
  React.useEffect(() => { load(); }, []);
  const create = async () => {
    const d = await apiPost("/v1/backup", { action: "create" });
    if (d && d.ok) {
      setTip("备份完成");
      setTimeout(() => setTip(""), 2000);
      load();
    }
  };
  const del = async (name) => {
    if (!window.confirm(`删除备份 ${name}？`)) return;
    const d = await apiPost("/v1/backup", { action: "delete", name });
    if (d && d.ok) { load(); }
  };
  return (
    <div className="svc-actions" style={{ display: "block" }}>
      <div className="sched-line1">
        <b>📦 项目备份</b>
        <span className="sched-action">{list ? list.length : 0} 个</span>
        <button className="confirm-btn" onClick={create}>＋ 立即备份</button>
      </div>
      {(list || []).slice(0, 5).map((b) => (
        <div className="sched-text" key={b.name}>• {b.name}（{Math.round(b.size / 1024)} KB · {b.mtime}）
          <button className="msg-op" onClick={() => del(b.name)}>✕</button>
        </div>
      ))}
      {tip && <div className="px-tip">{tip}</div>}
    </div>
  );
}

// ── C2 更新检查 ────────────────────────────────────
function UpdateBlock() {
  const [info, setInfo] = React.useState(null);
  const check = async () => {
    const d = await apiGet("/v1/update/check");
    if (d) setInfo(d);
  };
  React.useEffect(() => { check(); }, []);
  return (
    <div className="svc-actions" style={{ display: "block" }}>
      <div className="sched-line1">
        <b>🚀 更新检查</b>
        <span className="sched-action">
          {info ? `当前 v${info.current} · ${info.has_update ? `新版 v${info.latest} 可用` : "已是最新"}` : "检查中…"}
        </span>
        <button className="msg-op" onClick={check}>检查</button>
      </div>
      {info && info.has_update && <div className="sched-text">{info.notes.slice(0, 200)}</div>}
    </div>
  );
}

// ── C6 数据清理 ────────────────────────────────────
function CleanupBlock() {
  const [items, setItems] = React.useState([]);
  const [tip, setTip] = React.useState("");
  const doClean = async () => {
    if (!items.length) return;
    if (!window.confirm(`确认清理以下数据（不可恢复）？\n${items.join("、")}`)) return;
    const d = await apiPost("/v1/cleanup", { items });
    if (d && d.ok) {
      setTip(`已清理：${d.removed.join("、")}`);
      setTimeout(() => setTip(""), 2000);
      setItems([]);
    }
  };
  const opts = {
    sessions: "历史会话",
    snapshot: "最近快照",
    stats: "用量统计",
    _archives: "上下文归档",
    logs: "日志",
    prompts: "提示词库",
    memory: "长期记忆",
    failures: "失败模式",
    schedules: "定时任务",
  };
  return (
    <div className="svc-actions" style={{ display: "block" }}>
      <div className="sched-line1">
        <b>🧹 数据清理</b>
        <span className="sched-action">勾选后确认执行（不可恢复）</span>
      </div>
      <div className="sched-form">
        {Object.entries(opts).map(([k, v]) => (
          <button key={k} className={`sl-tag-chip ${items.includes(k) ? "sl-tag-chip-on" : ""}`}
            onClick={() => setItems(items.includes(k) ? items.filter((x) => x !== k) : [...items, k])}>
            {v}
          </button>
        ))}
      </div>
      <button className="confirm-btn" style={{ color: "var(--danger)" }} disabled={!items.length} onClick={doClean}>
        执行清理（{items.length} 项）
      </button>
      {tip && <div className="px-tip">{tip}</div>}
    </div>
  );
}

// ── B10 依赖状态 ───────────────────────────────────
function DepsBlock() {
  const [deps, setDeps] = React.useState(null);
  React.useEffect(() => {
    apiGet("/v1/deps").then((d) => d && setDeps(d.deps || []));
  }, []);
  if (!deps) return null;
  const ok = deps.filter((d) => d.ok).length;
  return (
    <div className="svc-actions" style={{ display: "block" }}>
      <div className="sched-line1">
        <b>🔌 可选依赖</b>
        <span className="sched-action">{ok}/{deps.length} 已就绪</span>
      </div>
      {deps.filter((d) => !d.ok).slice(0, 8).map((d) => (
        <div className="sched-text" key={d.name}>• {d.name}（{d.install}）</div>
      ))}
      {deps.filter((d) => !d.ok).length === 0 && <div className="sched-text">✓ 全部就绪</div>}
    </div>
  );
}

// ── B1 流程管理 ────────────────────────────────────
function WorkflowsBlock() {
  const [wfs, setWfs] = React.useState(null);
  const [name, setName] = React.useState("");
  const [steps, setSteps] = React.useState("");
  React.useEffect(() => {
    apiGet("/v1/workflows").then((d) => d && setWfs(d.workflows || {}));
  }, []);
  if (!wfs) return <div className="empty-tip">加载流程…</div>;

  const save = async (data) => {
    const d = await apiPost("/v1/workflows", { workflows: data });
    if (d && d.ok) setWfs(data);
  };

  const add = () => {
    if (!name.trim() || !steps.trim()) return;
    const stepsArr = steps.split("\n").map((s) => s.trim()).filter(Boolean);
    save({ ...wfs, [name.trim()]: { steps: stepsArr } });
    setName("");
    setSteps("");
  };

  const run = async (n) => {
    try {
      await api.api("/v1/tools/run_workflow/invoke", { method: "POST", body: JSON.stringify({ args: { name: n } }) });
      alert("已触发流程执行（后台运行）");
    } catch (e) {
      alert(`执行失败：${e.message}`);
    }
  };

  return (
    <div className="svc-actions" style={{ display: "block" }}>
      {Object.entries(wfs).map(([n, wf]) => (
        <div className="sched-item" key={n}>
          <div className="sched-line1">
            <b>{n}</b>
            <span className="sched-action">{wf.steps.length} 步</span>
            <button className="msg-op" onClick={() => run(n)}>▶ 运行</button>
            <button className="msg-op" onClick={() => save(Object.fromEntries(Object.entries(wfs).filter(([k]) => k !== n)))}>✕</button>
          </div>
          <div className="sched-text">{wf.steps.join(" → ").slice(0, 120)}</div>
        </div>
      ))}
      <div className="sched-form">
        <input className="set-select set-combo" placeholder="流程名" value={name} onChange={(e) => setName(e.target.value)} />
        <input className="set-select set-combo" placeholder="步骤（每行一步）" value={steps} onChange={(e) => setSteps(e.target.value)} />
        <button className="confirm-btn confirm-primary" onClick={add}>＋ 添加</button>
      </div>
    </div>
  );
}

// ── B2 检查点 ──────────────────────────────────────
function CheckpointBlock() {
  const [cp, setCp] = React.useState(null);
  React.useEffect(() => {
    apiGet("/v1/checkpoint").then((d) => d && setCp(d));
  }, []);
  if (!cp) return <div className="empty-tip">加载检查点…</div>;
  const has = cp && (cp.name || cp.status || cp.pending);
  return (
    <div className="svc-actions" style={{ display: "block" }}>
      {has ? (
        <div className="sched-item">
          <div className="sched-line1"><b>📌 {cp.name}</b></div>
          <div className="sched-text">状态：{cp.status || ""} · 进度：{String(cp.pending || []).length} 条待办 · {cp.saved_at || ""}</div>
          <button className="msg-op" onClick={async () => {
            const d = await apiPost("/v1/checkpoint", {});
            if (d && d.ok) setCp({});
          }}>清除</button>
        </div>
      ) : (
        <div className="empty-tip">暂无任务检查点</div>
      )}
    </div>
  );
}

// ── B5 知识库 ──────────────────────────────────────
function KnowledgeBlock() {
  const [kb, setKb] = React.useState(null);
  React.useEffect(() => {
    apiGet("/v1/knowledge").then((d) => d && setKb(d));
  }, []);
  if (!kb) return null;
  return (
    <div className="svc-actions" style={{ display: "block" }}>
      <div className="sched-line1">
        <b>📚 知识库</b>
        <span className="sched-action">{kb.indexed ? `已索引 ${kb.files.length} 文件` : "未索引"}</span>
      </div>
      {kb.files.slice(0, 5).map((f, i) => <div className="sched-text" key={i}>• {f}</div>)}
    </div>
  );
}

// ── 审计日志 ───────────────────────────────────────
function AuditBlock() {
  const [entries, setEntries] = React.useState([]);
  React.useEffect(() => {
    apiGet("/v1/audit").then((d) => d && setEntries(d.entries || []));
  }, []);
  return (
    <div className="svc-actions" style={{ display: "block" }}>
      <div className="sched-line1"><b>🛡 审计日志</b><span className="sched-action">{entries.length} 条</span></div>
      <div style={{ maxHeight: 160, overflow: "auto" }}>
        {entries.slice(-5).map((l, i) => <div className="sched-text" key={i}>• {l.slice(0, 120)}</div>)}
      </div>
    </div>
  );
}

// ── 设置页主组件 ────────────────────────────────────
export default function SettingsPage() {
  const { theme, setTheme } = React.useContext(ThemeContext);
  const { density, setDensity, fontSize, setFontSize } = React.useContext(DisplayContext);
  const [mode, setMode] = React.useState(() => {
    try {
      return localStorage.getItem("whaletalk.settingmode") || "simple";
    } catch {
      return "simple";
    }
  });
  const [tab, setTab] = React.useState("model");
  const [cfg, setCfg] = React.useState(null);
  const [models, setModels] = React.useState([]);
  const [roles, setRoles] = React.useState([]);
  const [customModel, setCustomModel] = React.useState(false);
  const [tip, setTip] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    (async () => {
      const d = await apiGet("/v1/config");
      const s = await apiGet("/v1/status");
      const r = await apiGet("/v1/roles");
      const m = await apiGet("/v1/models");
      if (alive && d) {
        setCfg({ ...d, privacy_mode: s?.privacy ?? false });
        if (r?.roles) setRoles(r.roles);
        if (m?.models) setModels(m.models);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const modelOptions = Array.isArray(cfg?.models) && cfg.models.length ? cfg.models : models.map((m) => m.name);
  const activeModelMeta = models.find((m) => m.name === cfg?.model);
  const currentRole = roles.find((r) => r.prompt === cfg?.system_prompt)?.name || (cfg?.system_prompt ? "自定义" : "通用角色");

  const saveField = async (patch, silent = false) => {
    setCfg((c) => ({ ...c, ...patch }));
    try {
      const d = await apiPost("/v1/config", patch);
      if (d && d.ok && !silent) {
        setTip("已保存");
        setTimeout(() => setTip(""), 1500);
      }
    } catch {}
  };

  const applyPreset = async (preset) => {
    const d = await apiPost("/v1/config", preset.cfg);
    if (d && d.ok) {
      setCfg((c) => ({ ...c, ...preset.cfg }));
      setTip(`已应用预设「${preset.name}」`);
      setTimeout(() => setTip(""), 2000);
    }
  };

  const resetAll = async () => {
    if (!window.confirm("恢复全部默认配置？（API Key 会保留）")) return;
    const d = await apiGet("/v1/config/reset");
    if (d && d.ok) {
      window.location.reload();
    }
  };

  if (!cfg) return <div className="page"><div className="page-head"><h1>设置</h1><p>加载中…</p></div></div>;

  const TABS = [
    { id: "model", label: "🎛 模型与网关" },
    { id: "service", label: "🔌 外部服务" },
    { id: "persona", label: "🧠 人格与工具" },
    { id: "notice", label: "🔔 通知与安全" },
    { id: "look", label: "🎨 外观" },
    { id: "adv", label: "⚙ 高级" },
  ];

  return (
    <div className="page">
      <div className="page-head">
        <h1>设置</h1>
        <p>简单模式一键预设 · 高级模式全参数自定义</p>
      </div>
      <div className="set-modebar">
        <button className={`ab-tab ${mode === "simple" ? "ab-tab-on" : ""}`} onClick={() => { setMode("simple"); try { localStorage.setItem("whaletalk.settingmode", "simple"); } catch {} }}>
          🌱 简单模式
        </button>
        <button className={`ab-tab ${mode === "advanced" ? "ab-tab-on" : ""}`} onClick={() => { setMode("advanced"); try { localStorage.setItem("whaletalk.settingmode", "advanced"); } catch {} }}>
          🚀 高级模式
        </button>
      </div>

      {mode === "simple" ? (
        <div className="svc-wrap">
          <div className="svc-title">⚡ 一键预设（按场景直接选）</div>
          <div className="preset-grid">
            {PRESETS.map((p) => (
              <button key={p.id} className="preset-card" onClick={() => applyPreset(p)}>
                <b>{p.name}</b>
                <span>{p.desc}</span>
              </button>
            ))}
          </div>
          <div className="svc-title" style={{ marginTop: 16 }}>核心设置</div>
          <div className="set-card">
            <Row label="模型" desc="官方三模型 + 任意 OpenAI 兼容">
              {customModel ? (
                <input className="set-select set-combo" value={cfg.model || ""} placeholder="输入任意模型名" onChange={(e) => saveField({ model: e.target.value })} onBlur={(e) => e.target.value.trim() && saveField({ model: e.target.value.trim() })} />
              ) : (
                <select className="set-select" value={modelOptions.includes(cfg.model) ? cfg.model : "__custom__"} onChange={(e) => {
                  if (e.target.value === "__custom__") setCustomModel(true);
                  else saveField({ model: e.target.value });
                }}>
                  {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
                  <option value="__custom__">✎ 自定义模型名…</option>
                </select>
              )}
            </Row>
            <Row label="思考档位" desc="auto 按任务复杂度智能路由">
              <select className="set-select" value={cfg.thinking || ""} onChange={(e) => saveField({ thinking: e.target.value })}>
                {(Array.isArray(cfg.thinking_modes) ? cfg.thinking_modes : []).map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Row>
            <Row label="API 网关" desc="OpenAI 兼容 base_url">
              <input className="set-select set-combo" value={cfg.base_url || ""} placeholder="https://api.deepseek.com" onChange={(e) => saveField({ base_url: e.target.value })} />
            </Row>
            <Row label="API Key" desc={cfg.has_key ? "✅ 已配置（加密存储）" : "⚠️ 未配置"}>
              <span className="set-badge">{cfg.has_key ? "已配置" : "未配置"}</span>
            </Row>
            <Row label="输出上限" desc={`V4 最大 ${activeModelMeta?.max_output_tokens || 393216}`}>
              <NumInput min={1024} max={393216} step={1024} value={cfg.max_tokens} onChange={(v) => saveField({ max_tokens: v })} />
            </Row>
          </div>
        </div>
      ) : (
        <>
          <div className="ab-tabs">
            {TABS.map((t) => (
              <button key={t.id} className={`ab-tab ${tab === t.id ? "ab-tab-on" : ""}`} onClick={() => setTab(t.id)}>{t.label}</button>
            ))}
          </div>
          <div className="set-card">
            {tab === "model" && (
              <>
                <Row label="API Key" desc={cfg.has_key ? "✅ 已配置（加密存储于 config.json）" : "⚠️ 未配置"}>
                  <span className="set-badge">{cfg.has_key ? "已配置" : "未配置"}</span>
                </Row>
                <Row label="🌐 API 网关地址" desc="支持任意 OpenAI 兼容网关（/beta、中转站）">
                  <input className="set-select set-combo" value={cfg.base_url || ""} placeholder="https://api.deepseek.com" onChange={(e) => saveField({ base_url: e.target.value })} onBlur={(e) => e.target.value.trim() && saveField({ base_url: e.target.value.trim() })} />
                </Row>
                <Row label="模型" desc={activeModelMeta ? `${activeModelMeta.label} · 上下文 ${(activeModelMeta.max_context_tokens / 1000000).toFixed(1)}M · 输出 ${(activeModelMeta.max_output_tokens / 1024).toFixed(0)}K` : "可输入任意兼容模型"}>
                  {customModel ? (
                    <input className="set-select set-combo" value={cfg.model || ""} placeholder="输入任意模型名" onChange={(e) => saveField({ model: e.target.value })} onBlur={(e) => e.target.value.trim() && saveField({ model: e.target.value.trim() })} />
                  ) : (
                    <select className="set-select" value={modelOptions.includes(cfg.model) ? cfg.model : "__custom__"} onChange={(e) => {
                      if (e.target.value === "__custom__") setCustomModel(true);
                      else saveField({ model: e.target.value });
                    }}>
                      {modelOptions.map((m) => <option key={m} value={m}>{m}</option>)}
                      <option value="__custom__">✎ 自定义模型名…</option>
                    </select>
                  )}
                </Row>
                <Row label="思考档位" desc="auto 智能路由（按长度/复杂度评分）">
                  <select className="set-select" value={cfg.thinking || ""} onChange={(e) => saveField({ thinking: e.target.value })}>
                    {(Array.isArray(cfg.thinking_modes) ? cfg.thinking_modes : []).map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </Row>
                <Row label="场景" desc="通用/编程/Agent/自定义">
                  <select className="set-select" value={cfg.scenario || ""} onChange={(e) => saveField({ scenario: e.target.value })}>
                    {(Array.isArray(cfg.scenarios) ? cfg.scenarios : []).map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </Row>
                <Row label="输出上限（max_tokens）" desc={`手填 1024 - ${(activeModelMeta?.max_output_tokens || 393216).toLocaleString()}`}>
                  <NumInput min={1024} max={393216} step={1024} value={cfg.max_tokens} onChange={(v) => saveField({ max_tokens: v })} />
                </Row>
                <Row label="温度" desc="0-2，仅无思考档生效">
                  <NumInput min={0} max={2} step={0.1} value={cfg.temperature ?? 1.0} onChange={(v) => saveField({ temperature: v })} />
                </Row>
                <Row label="Top-P" desc="0-1 核采样">
                  <NumInput min={0} max={1} step={0.05} value={cfg.top_p ?? 1.0} onChange={(v) => saveField({ top_p: v })} />
                </Row>
                <Row label="Seed" desc="固定随机种子，0=随机">
                  <NumInput min={0} value={cfg.seed ?? 0} onChange={(v) => saveField({ seed: v })} />
                </Row>
                <Row label="停止序列（stop）" desc="遇到即停，逗号分隔 ≤16 个">
                  <input className="set-select set-combo" value={(cfg.stop || []).join(",")} placeholder="如：再见,``` 结束（留空不设置）"
                    onChange={(e) => saveField({ stop: e.target.value })}
                    onBlur={(e) => saveField({ stop: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
                </Row>
                <Row label="工具选择（tool_choice）" desc="任务模式生效">
                  <select className="set-select" value={cfg.tool_choice || "auto"} onChange={(e) => saveField({ tool_choice: e.target.value })}>
                    <option value="auto">auto（智能）</option>
                    <option value="none">none（不调工具）</option>
                    <option value="required">required（必调）</option>
                  </select>
                </Row>
                <Toggle on={!!cfg.logprobs} label="输出对数概率（logprobs）" desc="返回每个 token 概率" onClick={() => saveField({ logprobs: !cfg.logprobs })} />
                <Toggle on={!!cfg.json_output} label="JSON 输出" desc="response_format，失败自动重试" onClick={() => saveField({ json_output: !cfg.json_output })} />
                <Toggle on={!!cfg.beta_api} label="Beta API（/beta）" desc="前缀续写 + FIM 补全" onClick={() => saveField({ beta_api: !cfg.beta_api })} />
                <Toggle on={!!cfg.strict_tools} label="strict 工具模式" desc="严格遵循 JSON Schema（自动启用 Beta）" onClick={() => saveField({ strict_tools: !cfg.strict_tools })} />
              </>
            )}
            {tab === "service" && <ServicesTab cfg={cfg} onTip={setTip} />}
            {tab === "persona" && (
              <>
                <Row label="🎭 角色" desc="切换即换系统提示词（影响前缀缓存）">
                  <select className="set-select" value={currentRole} onChange={(e) => {
                    const r = roles.find((x) => x.name === e.target.value);
                    if (r) saveField({ system_prompt: r.prompt });
                  }}>
                    {roles.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
                    {cfg.system_prompt && !roles.some((r) => r.prompt === cfg.system_prompt) && <option value="自定义">自定义</option>}
                  </select>
                </Row>
                <div className="svc-group" style={{ marginTop: 8 }}>
                  <div className="svc-title">🛠 自定义角色管理</div>
                  {roles.filter((r) => !["通用角色", "智能体", "翻译官", "代码评审专家", "面试官", "写作润色师", "心理咨询伙伴", "周报助手"].includes(r.name)).map((r, i) => (
                    <div className="svc-prompt-row" key={i}>
                      <input className="set-select set-combo" value={r.name} placeholder="角色名" onChange={(e) => setRoles(roles.map((x, j) => (j === roles.indexOf(r) ? { ...x, name: e.target.value } : x)))} />
                      <input className="set-select set-combo" value={r.prompt} placeholder="提示词" onChange={(e) => setRoles(roles.map((x, j) => (j === roles.indexOf(r) ? { ...x, prompt: e.target.value } : x)))} />
                      <button className="msg-op" onClick={async () => {
                        const next = roles.filter((_, j) => j !== roles.indexOf(r));
                        setRoles(next);
                        await apiPost("/v1/roles", { roles: next });
                      }}>✕</button>
                    </div>
                  ))}
                  <div className="svc-actions">
                    <button className="confirm-btn" onClick={() => setRoles([...roles, { name: "新角色", prompt: "", thinking: "high", desc: "", category: "我的" }])}>
                      ＋ 新增角色
                    </button>
                    <button className="confirm-btn confirm-primary" onClick={async () => {
                      const clean = roles.filter((r) => !["通用角色", "智能体", "翻译官", "代码评审专家", "面试官", "写作润色师", "心理咨询伙伴", "周报助手"].includes(r.name));
                      const d = await apiPost("/v1/roles", { roles: clean });
                      if (d && d.ok) setTip("角色已保存");
                    }}>
                      💾 保存角色
                    </button>
                  </div>
                </div>
                <Toggle on={!!cfg.tools_enabled} label="工具开关" desc="向模型暴露工具定义" onClick={() => saveField({ tools_enabled: !cfg.tools_enabled })} />
                <Toggle on={!cfg.browser_headless} label="🖥 浏览器可见" desc="AI 操作浏览器弹真实窗口" onClick={() => saveField({ browser_headless: !!cfg.browser_headless })} />
                <Row label="🔧 工具库与权限" desc="109 工具 12 域 · 黑名单管理">
                  <span className="empty-tip" style={{ padding: 0 }}>在「能力中心」页签管理</span>
                </Row>
              </>
            )}
            {tab === "notice" && (
              <>
                <Toggle on={cfg.notify_on_done !== false} label="✅ 完成通知" desc="任务栏闪烁 + 桌面通知" onClick={() => saveField({ notify_on_done: cfg.notify_on_done === false })} />
                <Toggle on={cfg.peak_warning !== false} label="⏰ 高峰提示" desc="每天首次发送提示高峰价" onClick={() => saveField({ peak_warning: cfg.peak_warning === false })} />
                <Toggle on={cfg.autostart !== false} label="🚀 开机自启" desc="注册 HKCU Run 开机自动启动" onClick={() => saveField({ autostart: cfg.autostart === false })} />
                <Toggle on={!!cfg.privacy_mode} label="🔒 隐私模式" desc="不写快照/会话/记忆/统计" onClick={() => saveField({ privacy_mode: !cfg.privacy_mode })} />
                <Row label="💵 本月预算（元）" desc="0=不限">
                  <NumInput min={0} max={10000} step={50} value={cfg.monthly_budget || 0} onChange={(v) => saveField({ monthly_budget: v })} />
                </Row>
                <Toggle on={!!cfg.block_on_budget} label="⛔ 达预算阻止发送" desc="超过预算后拦截发送" onClick={() => saveField({ block_on_budget: !cfg.block_on_budget })} />
              </>
            )}
            {tab === "look" && (
              <>
                <div className="theme-picker">
                  {THEMES.map((t) => (
                    <button key={t.id} className={`theme-card ${theme === t.id ? "theme-card-on" : ""}`} onClick={() => setTheme(t.id)}>
                      <span className="theme-swatch" style={{ background: `linear-gradient(135deg, ${t.preview[0]}, ${t.preview[1]})` }}>
                        <span className="theme-swatch-dot" style={{ background: t.preview[2] }} />
                      </span>
                      <b>{t.name}</b>
                      <span className="theme-desc">{t.desc}</span>
                      {theme === t.id && <span className="theme-check">✓</span>}
                    </button>
                  ))}
                </div>
                <Row label="消息密度">
                  <select className="set-select" value={density} onChange={(e) => setDensity(e.target.value)}>
                    <option value="compact">紧凑</option>
                    <option value="comfort">舒适（默认）</option>
                    <option value="loose">宽松</option>
                  </select>
                </Row>
                <Row label="消息字号">
                  <select className="set-select" value={fontSize} onChange={(e) => setFontSize(Number(e.target.value))}>
                    {[10, 11, 12, 13, 14, 15, 16, 17, 18].map((n) => <option key={n} value={n}>{n}px</option>)}
                  </select>
                </Row>
              </>
            )}
            {tab === "adv" && <AdvancedTab cfg={cfg} saveField={saveField} onReset={resetAll} />}
          </div>
        </>
      )}
      {tip && <div className="set-saved-tip">{tip}</div>}
    </div>
  );
}