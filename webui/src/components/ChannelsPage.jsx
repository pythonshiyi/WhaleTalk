import React from "react";
import * as api from "../api.js";
import { SkeletonCard } from "./Skeleton.jsx";
import { silentWarn } from "../quiet.js";

/**
 * 统一通道页：一个页面承载所有平台接入（QQ 本地 / QQ 官方 / Telegram / …）。
 *
 * 重构前这里是两个 300 行、结构几乎相同的页面（QQPage / QQOfficialPage），
 * 加一个平台就要再复制一份。现在改为**声明式通道表**：
 * 新增平台 = 往 CHANNELS 里加一项，页面与逻辑自动复用。
 *
 * 后端对应 /v1/channels 统一接口（能力 + 运行状态）。
 */

const CHANNELS = [
  {
    key: "qq",
    name: "QQ 本地",
    badge: "私聊",
    desc: "本地 OneBot / NapCat 私聊接入。每个 QQ 独立会话与角色，只走对话模式，不启用 Agent 工具；支持文字、图片与语音转写（语音需安装可选 faster-whisper）。",
    fields: [
      { key: "api_base", label: "OneBot HTTP API 地址", placeholder: "http://127.0.0.1:3000" },
      { key: "listen_port", label: "监听端口", placeholder: "8756", small: true },
    ],
    secret: {
      key: "access_token",
      clearKey: "clear_token",
      label: "访问令牌",
      placeholder: "与 NapCat 的 API/上报令牌一致",
      clearLabel: "清除已保存的访问令牌",
      hasField: "has_token",
    },
    allow: { label: "允许的 QQ 号（留空 = 所有私聊）", placeholder: "123456, 789012", hint: "建议填写；留空时任何能私聊机器人的人都会消耗模型额度。" },
    test: { label: "目标 QQ 号", placeholder: "你的 QQ 号" },
    steps: [
      <>在 NapCat 网络配置里开启 <b>HTTP API</b>（默认 <code>http://127.0.0.1:3000</code>）。</>,
      <>开启 <b>HTTP POST 上报</b>，地址填 <code>http://127.0.0.1:8756/onebot</code>。</>,
      <>如设置了访问令牌，NapCat 的 API 令牌与上报令牌填同一个值。</>,
      <>保存并启动本页通道，然后用另一个 QQ 私聊机器人测试。</>,
    ],
  },
  {
    key: "qq_official",
    name: "QQ 官方",
    badge: "私聊",
    desc: "走腾讯官方 QQ Bot API v2：AppID + AppSecret → WebSocket 网关 → 私聊 C2C。每个 openid 独立会话与角色，不启用 Agent 工具。无需安装任何外部程序。",
    fields: [
      { key: "app_id", label: "AppID", placeholder: "QQ 开放平台机器人 AppID" },
      { key: "api_base", label: "API 地址", placeholder: "https://api.bot.qq.com" },
    ],
    secret: {
      key: "client_secret",
      clearKey: "clear_secret",
      label: "AppSecret",
      placeholder: "QQ 开放平台机器人 AppSecret",
      clearLabel: "清除已保存的 AppSecret",
      hasField: "has_secret",
    },
    allow: { label: "允许的 user_openid（留空 = 所有私聊）", placeholder: "每行一个 openid；官方 Bot 拿不到 QQ 号，只能拿到 openid", hint: "建议先私聊机器人一次，在下方联系人里复制自己的 openid 填入。", textarea: true },
    test: { label: "user_openid", placeholder: "从最近联系人复制" },
    steps: [
      <>在 <code>q.qq.com</code> 创建机器人，拿到 <b>AppID</b> 和 <b>AppSecret</b>。</>,
      <>在机器人配置里开启 <b>单聊 / C2C 消息</b> 与 <b>GROUP_AND_C2C_EVENT</b> intent。</>,
      <>测试阶段使用沙箱环境；把机器人加为好友或从沙箱入口私聊。</>,
      <>保存并启动本页通道；给机器人发第一条私聊，联系人会出现在下方。</>,
    ],
  },
  {
    key: "telegram",
    name: "Telegram",
    badge: "私聊 · 群聊",
    desc: "Telegram Bot 长轮询接入。支持私聊与群聊，每个会话独立角色；无需公网 IP 或端口映射。", 
    fields: [
      { key: "bot_token", label: "Bot Token", placeholder: "从 @BotFather 获取", secretOnly: true },
      { key: "chat_id", label: "限定 chat_id（可选）", placeholder: "留空 = 接受所有会话" },
    ],
    secret: null,
    allow: { label: "允许的 chat_id（留空 = 全部）", placeholder: "123456789, -1001234567890", hint: "留空时任何能私聊机器人的人都会消耗模型额度。" },
    test: { label: "目标 chat_id", placeholder: "你的 chat_id" },
    steps: [
      <>在 Telegram 里找 <code>@BotFather</code>，<code>/newbot</code> 创建机器人并复制 Token。</>,
      <>私聊你的机器人发一句话，或把它拉进群。</>,
      <>保存并启动本页通道；联系人会出现在下方。</>,
    ],
  },
];

const emptyCfg = (ch) => {
  const base = {
    enabled: false,
    default_persona_id: "",
    allowed_users: [],
    max_reply_chars: 1200,
    rate_limit_per_minute: 10,
    quiet_hours: "",
  };
  for (const f of ch.fields) base[f.key] = "";
  if (ch.secret) base[ch.secret.hasField] = false;
  return base;
};

function ChannelPanel({ ch, personas, onOpenSession }) {
  const [status, setStatus] = React.useState(null);
  const [cfg, setCfg] = React.useState(() => emptyCfg(ch));
  const [secretDraft, setSecretDraft] = React.useState("");
  const [clearSecret, setClearSecret] = React.useState(false);
  const [usersText, setUsersText] = React.useState("");
  const [testTarget, setTestTarget] = React.useState("");
  const [testText, setTestText] = React.useState("🐋 鲸语通道测试消息");
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [err, setErr] = React.useState("");

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 2500); };

  const applyStatus = React.useCallback((s) => {
    if (!s) return;
    setStatus(s);
    setCfg((prev) => ({ ...emptyCfg(ch), ...prev, ...s }));
    setUsersText((s.allowed_users || []).join(", "));
  }, [ch]);

  const load = React.useCallback(async () => {
    setErr("");
    try {
      const s = await api.getChannelStatus(ch.key);
      applyStatus(s?.status || s);
    } catch (e) {
      setErr(`${ch.name} 状态读取失败，请确认后端已连接。`);
      silentWarn(e, `ChannelPanel:${ch.key}`);
    }
  }, [applyStatus, ch]);

  React.useEffect(() => { setCfg(emptyCfg(ch)); load(); }, [ch.key]);  // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = async (r) => {
    if (r?.status) applyStatus(r.status);
    else await load();
  };

  const buildPayload = (extra = {}) => {
    const p = {
      enabled: cfg.enabled,
      default_persona_id: cfg.default_persona_id || "",
      allowed_users: usersText,
      max_reply_chars: Number(cfg.max_reply_chars) || 1200,
      rate_limit_per_minute: Number(cfg.rate_limit_per_minute) || 10,
      quiet_hours: cfg.quiet_hours || "",
    };
    for (const f of ch.fields) {
      if (f.secretOnly) continue;
      p[f.key] = f.key === "listen_port" ? Number(cfg[f.key]) || 8756 : (cfg[f.key] || "");
    }
    if (ch.secret) {
      if (clearSecret) p[ch.secret.clearKey] = true;
      if (secretDraft.trim()) p[ch.secret.key] = secretDraft.trim();
    } else if (secretDraft.trim()) {
      p[ch.fields[0].key] = secretDraft.trim();
    }
    return { ...p, ...extra };
  };

  const run = async (fn, okText) => {
    setBusy(true); setErr("");
    try {
      const r = await fn();
      setSecretDraft(""); setClearSecret(false);
      await refresh(r);
      if (okText) flash(typeof okText === "function" ? okText(r) : okText);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const action = (name, extra = {}) =>
    run(() => api.channelAction(ch.key, { action: name, ...buildPayload(extra) }));

  if (!status && !err) return <SkeletonCard />;

  const running = status?.running || status?.connected;
  const on = !!status?.connected || !!status?.running;

  return (
    <div className="qq-page">
      {err && <div className="empty-tip is-err">{err}</div>}

      <div className="qq-status">
        <div className={`qq-status-dot ${on ? "on" : "off"}`} />
        <div className="qq-status-main">
          <b>{running ? (status?.connected ? "已连接" : "运行中") : status?.enabled ? "已启用但未运行" : "未启用"}</b>
          <span>{status?.last_error || (status?.bot_info ? `机器人：${status.bot_info}` : "—")}</span>
        </div>
        <div className="qq-status-actions">
          <button className="confirm-btn" disabled={busy} onClick={() => action("probe")}>检测连接</button>
          <button className="confirm-btn" disabled={busy} onClick={() => action("start")}>启动</button>
          <button className="confirm-btn" disabled={busy} onClick={() => action("stop")}>停止</button>
        </div>
      </div>

      <div className="qq-grid">
        <section className="set-card qq-card">
          <div className="svc-title">连接配置</div>
          <div className="pm-form">
            <label className="pm-check">
              <input type="checkbox" checked={!!cfg.enabled}
                onChange={(e) => setCfg((s) => ({ ...s, enabled: e.target.checked }))} />
              <span>启用 {ch.name} 通道</span>
            </label>

            {ch.fields.map((f) =>
              f.secretOnly ? (
                <div className="pm-field" key={f.key}>
                  <label>{f.label}{cfg[f.key] ? "（已保存，留空保持不变）" : ""}</label>
                  <input className="set-select" type="password" value={secretDraft}
                    placeholder={f.placeholder}
                    onChange={(e) => setSecretDraft(e.target.value)} />
                </div>
              ) : (
                <div className={`pm-field ${f.small ? "pm-field-sm" : ""}`} key={f.key}>
                  <label>{f.label}</label>
                  <input className="set-select" value={cfg[f.key] || ""} placeholder={f.placeholder}
                    onChange={(e) => setCfg((s) => ({ ...s, [f.key]: e.target.value }))} />
                </div>
              )
            )}

            {ch.secret && (
              <div className="pm-field">
                <label>{ch.secret.label}{cfg[ch.secret.hasField] ? "（已保存，留空保持不变）" : ""}</label>
                <input className="set-select" type="password" value={secretDraft}
                  placeholder={ch.secret.placeholder}
                  onChange={(e) => setSecretDraft(e.target.value)} />
                <label className="pm-check">
                  <input type="checkbox" checked={clearSecret}
                    onChange={(e) => setClearSecret(e.target.checked)} />
                  <span>{ch.secret.clearLabel}</span>
                </label>
              </div>
            )}

            <div className="pm-field">
              <label>{ch.allow.label}</label>
              {ch.allow.textarea ? (
                <textarea className="pm-textarea" rows={4} value={usersText}
                  placeholder={ch.allow.placeholder}
                  onChange={(e) => setUsersText(e.target.value)} />
              ) : (
                <input className="set-select" value={usersText}
                  placeholder={ch.allow.placeholder}
                  onChange={(e) => setUsersText(e.target.value)} />
              )}
              <div className="pm-hint">{ch.allow.hint}</div>
            </div>

            <div className="pm-field">
              <label>默认角色</label>
              <select className="set-select" value={cfg.default_persona_id || ""}
                onChange={(e) => setCfg((s) => ({ ...s, default_persona_id: e.target.value }))}>
                <option value="">自动（第一个角色）</option>
                {personas.map((p) => <option key={p.id} value={p.id}>{p.avatar} {p.name}</option>)}
              </select>
            </div>
          </div>
        </section>

        <section className="set-card qq-card">
          <div className="svc-title">陪伴行为</div>
          <div className="pm-form">
            <div className="pm-row">
              <div className="pm-field">
                <label>单条回复上限（字符）</label>
                <input className="set-select" value={cfg.max_reply_chars || ""} placeholder="1200"
                  onChange={(e) => setCfg((s) => ({ ...s, max_reply_chars: e.target.value }))} />
              </div>
              <div className="pm-field">
                <label>每分钟上限（每联系人）</label>
                <input className="set-select" value={cfg.rate_limit_per_minute || ""} placeholder="10"
                  onChange={(e) => setCfg((s) => ({ ...s, rate_limit_per_minute: e.target.value }))} />
              </div>
            </div>
            <div className="pm-field">
              <label>静默时段（可选）</label>
              <input className="set-select" value={cfg.quiet_hours || ""} placeholder="23:00-08:00"
                onChange={(e) => setCfg((s) => ({ ...s, quiet_hours: e.target.value }))} />
              <div className="pm-hint">静默时段内不自动回复；/角色、/重置、/帮助 仍可用。</div>
            </div>
            <div className="pm-hint qq-command-hint">
              私聊命令：/角色 小鲸 · /重置 · /记忆 · /帮助
            </div>
          </div>
        </section>

        <section className="set-card qq-card">
          <div className="svc-title">测试发送</div>
          <div className="pm-form">
            <div className="pm-field">
              <label>{ch.test.label}</label>
              <input className="set-select" value={testTarget} placeholder={ch.test.placeholder}
                onChange={(e) => setTestTarget(e.target.value)} />
            </div>
            <div className="pm-field">
              <label>消息</label>
              <input className="set-select" value={testText}
                onChange={(e) => setTestText(e.target.value)} />
            </div>
            <div className="pm-form-ops">
              <button className="confirm-btn" disabled={busy}
                onClick={() => {
                  if (!testTarget.trim()) { setErr("请先填写接收方 ID"); return; }
                  action("test", { user_id: testTarget.trim(), text: testText.trim() || "测试消息" });
                }}>发送测试</button>
              <button className="confirm-btn confirm-primary" disabled={busy}
                onClick={() => action("save", {}, "配置已保存")}>保存配置</button>
            </div>
          </div>
        </section>
      </div>

      <section className="set-card qq-card">
        <div className="svc-title">{ch.name} 对接步骤</div>
        <ol className="qq-steps">
          {ch.steps.map((s, i) => <li key={i}>{s}</li>)}
        </ol>
      </section>

      <section className="set-card qq-card">
        <div className="svc-title">最近联系人（{status?.contacts?.length || 0}）</div>
        {!status?.contacts?.length ? (
          <div className="empty-tip">还没有联系人。收到第一条私聊后会出现在这里。</div>
        ) : (
          <div className="qq-contacts">
            {status.contacts.map((c) => (
              <div className="qq-contact" key={c.user_id}>
                <div>
                  <b>{c.user_id}</b>
                  <span>{c.persona_name || "未设置角色"}</span>
                </div>
                <div className="qq-contact-meta">
                  <span>{c.last_seen || "—"}</span>
                  <code>{c.session_id || "—"}</code>
                  {c.session_id && onOpenSession && (
                    <button className="msg-op" onClick={() => onOpenSession(c.session_id)}>打开会话</button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {msg && <div className="set-saved-tip">{msg}</div>}
    </div>
  );
}

export default function ChannelsPage({ embedded = false, onOpenSession }) {
  const [active, setActive] = React.useState(CHANNELS[0].key);
  const [personas, setPersonas] = React.useState([]);

  React.useEffect(() => {
    api.listPersonas()
      .then((p) => setPersonas(Array.isArray(p?.personas) ? p.personas : []))
      .catch((e) => silentWarn(e, "ChannelsPage"));
  }, []);

  const ch = CHANNELS.find((c) => c.key === active) || CHANNELS[0];

  return (
    <div className={embedded ? "page-embedded" : "page"}>
      {!embedded && (
        <div className="page-head">
          <h1>接入通道</h1>
          <p>让 AI 角色住进各个平台：每个会话独立角色与记忆，只走对话模式，不启用 Agent 工具。选择一个通道进行配置。</p>
        </div>
      )}

      <div className="channel-tabs">
        {CHANNELS.map((c) => (
          <button
            key={c.key}
            className={`channel-tab ${c.key === active ? "is-active" : ""}`}
            onClick={() => setActive(c.key)}
          >
            <b>{c.name}</b>
            <span>{c.badge}</span>
          </button>
        ))}
      </div>

      <div className="page-head channel-desc">
        <p>{ch.desc}</p>
      </div>

      <ChannelPanel key={ch.key} ch={ch} personas={personas} onOpenSession={onOpenSession} />
    </div>
  );
}
