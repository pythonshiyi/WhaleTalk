import React from "react";
import * as api from "../api.js";
import { SkeletonCard } from "./Skeleton.jsx";
import { silentWarn } from "../quiet.js";

const EMPTY = {
  enabled: false,
  app_id: "",
  api_base: "https://api.bot.qq.com",
  default_persona_id: "",
  allowed_users: [],
  max_reply_chars: 1200,
  rate_limit_per_minute: 10,
  quiet_hours: "",
  has_secret: false,
};

export default function QQOfficialPage() {
  const [status, setStatus] = React.useState(null);
  const [personas, setPersonas] = React.useState([]);
  const [cfg, setCfg] = React.useState(EMPTY);
  const [secretDraft, setSecretDraft] = React.useState("");
  const [clearSecret, setClearSecret] = React.useState(false);
  const [usersText, setUsersText] = React.useState("");
  const [testOpenid, setTestOpenid] = React.useState("");
  const [testText, setTestText] = React.useState("🐋 鲸语 QQ 官方通道测试消息");
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [err, setErr] = React.useState("");

  const flash = (text) => {
    setMsg(text);
    setTimeout(() => setMsg(""), 2500);
  };

  const applyStatus = React.useCallback((s) => {
    if (!s) return;
    setStatus(s);
    setCfg({ ...EMPTY, ...s });
    setUsersText((s.allowed_users || []).join("\n"));
  }, []);

  const load = React.useCallback(async () => {
    setErr("");
    try {
      const [s, p] = await Promise.all([
        api.getQqOfficialStatus(),
        api.listPersonas().catch(() => null),
      ]);
      applyStatus(s);
      setPersonas(Array.isArray(p?.personas) ? p.personas : []);
    } catch (e) {
      setErr("QQ 官方 Bot 状态读取失败，请确认后端已连接。");
      silentWarn(e, "QQOfficialPage");
    }
  }, [applyStatus]);

  React.useEffect(() => { load(); }, [load]);

  const buildPayload = (extra = {}) => ({
    enabled: cfg.enabled,
    app_id: cfg.app_id || "",
    api_base: cfg.api_base || "https://api.bot.qq.com",
    default_persona_id: cfg.default_persona_id || "",
    allowed_users: usersText,
    max_reply_chars: Number(cfg.max_reply_chars) || 1200,
    rate_limit_per_minute: Number(cfg.rate_limit_per_minute) || 10,
    quiet_hours: cfg.quiet_hours || "",
    ...(clearSecret ? { clear_secret: true } : {}),
    ...(secretDraft.trim() ? { client_secret: secretDraft.trim() } : {}),
    ...extra,
  });

  const refresh = async (r) => {
    if (r?.status) applyStatus(r.status);
    else await load();
  };

  const save = async (extra = {}) => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.saveQqOfficialConfig(buildPayload(extra));
      setSecretDraft("");
      setClearSecret(false);
      await refresh(r);
      flash("官方 Bot 配置已保存");
    } catch (e) {
      setErr(`保存失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (action) => {
    setBusy(true);
    setErr("");
    try {
      const enabled = action === "start";
      const r = await api.saveQqOfficialConfig(buildPayload({ enabled }));
      setCfg((s) => ({ ...s, enabled }));
      setSecretDraft("");
      setClearSecret(false);
      await refresh(r);
      flash(enabled ? "官方 Bot 已启动" : "官方 Bot 已停止");
    } catch (e) {
      setErr(`操作失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const probe = async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.probeQqOfficial();
      await refresh(r);
      flash(r.message || "官方 Bot 鉴权正常");
    } catch (e) {
      setErr(`连接检测失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    if (!testOpenid.trim()) {
      setErr("请填写 user_openid（来自联系人列表或 QQ 事件）");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const r = await api.testQqOfficial(testOpenid.trim(), testText.trim() || "测试消息");
      await refresh(r);
      flash("测试消息已发送（主动消息可能受官方频控限制）");
    } catch (e) {
      setErr(`发送失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  if (!status && !err) return <div className="page"><SkeletonCard /></div>;

  return (
    <div className="page qq-page">
      <div className="page-head">
        <h1>QQ 官方 Bot</h1>
        <p>走腾讯官方 QQ Bot API v2：AppID + AppSecret → WebSocket 网关 → 私聊 C2C。每个 openid 独立会话与角色，不启用 Agent 工具。</p>
      </div>

      {err && <div className="empty-tip is-err">{err}</div>}

      <div className="qq-status">
        <div className={`qq-status-dot ${status?.connected ? "on" : "off"}`} />
        <div className="qq-status-main">
          <b>{status?.connected ? "已连接官方网关" : status?.running ? "连接中 / 未就绪" : status?.enabled ? "已启用但未运行" : "未启用"}</b>
          <span>{status?.last_error || (status?.bot_info ? `机器人：${status.bot_info}` : "WebSocket 网关 · C2C 私聊")}</span>
        </div>
        <div className="qq-status-actions">
          <button className="confirm-btn" disabled={busy} onClick={probe}>检测连接</button>
          <button className="confirm-btn" disabled={busy} onClick={() => toggle("start")}>启动</button>
          <button className="confirm-btn" disabled={busy} onClick={() => toggle("stop")}>停止</button>
        </div>
      </div>

      <div className="qq-grid">
        <section className="set-card qq-card">
          <div className="svc-title">官方应用凭据</div>
          <div className="pm-form">
            <label className="pm-check">
              <input type="checkbox" checked={!!cfg.enabled} onChange={(e) => setCfg((s) => ({ ...s, enabled: e.target.checked }))} />
              <span>启用官方 QQ Bot 通道</span>
            </label>
            <div className="pm-field">
              <label>AppID</label>
              <input className="set-select" value={cfg.app_id || ""} placeholder="QQ 开放平台机器人 AppID"
                onChange={(e) => setCfg((s) => ({ ...s, app_id: e.target.value }))} />
            </div>
            <div className="pm-field">
              <label>AppSecret {cfg.has_secret ? "（已保存，留空保持不变）" : ""}</label>
              <input className="set-select" type="password" value={secretDraft} placeholder="QQ 开放平台机器人 AppSecret"
                onChange={(e) => setSecretDraft(e.target.value)} />
              <label className="pm-check">
                <input type="checkbox" checked={clearSecret} onChange={(e) => setClearSecret(e.target.checked)} />
                <span>清除已保存的 AppSecret</span>
              </label>
            </div>
            <div className="pm-field">
              <label>API 地址</label>
              <input className="set-select" value={cfg.api_base || ""} placeholder="https://api.bot.qq.com"
                onChange={(e) => setCfg((s) => ({ ...s, api_base: e.target.value }))} />
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
            <div className="pm-field">
              <label>允许的 user_openid（留空 = 所有私聊）</label>
              <textarea className="pm-textarea" rows={4} value={usersText}
                placeholder="每行一个 openid；官方 Bot 拿不到 QQ 号，只能拿到 openid"
                onChange={(e) => setUsersText(e.target.value)} />
              <div className="pm-hint">建议先私聊机器人一次，在下方联系人里复制自己的 openid 填入。</div>
            </div>
            <div className="pm-row">
              <div className="pm-field">
                <label>单条回复上限（字符）</label>
                <input className="set-select" value={cfg.max_reply_chars || ""}
                  onChange={(e) => setCfg((s) => ({ ...s, max_reply_chars: e.target.value }))} />
              </div>
              <div className="pm-field">
                <label>每分钟上限</label>
                <input className="set-select" value={cfg.rate_limit_per_minute || ""}
                  onChange={(e) => setCfg((s) => ({ ...s, rate_limit_per_minute: e.target.value }))} />
              </div>
            </div>
            <div className="pm-field">
              <label>静默时段（可选）</label>
              <input className="set-select" value={cfg.quiet_hours || ""} placeholder="23:00-08:00"
                onChange={(e) => setCfg((s) => ({ ...s, quiet_hours: e.target.value }))} />
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
              <label>user_openid</label>
              <input className="set-select" value={testOpenid} placeholder="从最近联系人复制"
                onChange={(e) => setTestOpenid(e.target.value)} />
            </div>
            <div className="pm-field">
              <label>消息</label>
              <input className="set-select" value={testText} onChange={(e) => setTestText(e.target.value)} />
            </div>
            <div className="pm-form-ops">
              <button className="confirm-btn" disabled={busy} onClick={sendTest}>发送测试</button>
              <button className="confirm-btn confirm-primary" disabled={busy} onClick={() => save()}>保存配置</button>
            </div>
          </div>
        </section>
      </div>

      <section className="set-card qq-card">
        <div className="svc-title">QQ 开放平台准备</div>
        <ol className="qq-steps">
          <li>在 <code>q.qq.com</code> 创建机器人，拿到 <b>AppID</b> 和 <b>AppSecret</b>。</li>
          <li>在机器人配置里开启 <b>单聊 / C2C 消息</b> 与 <b>GROUP_AND_C2C_EVENT</b> intent。</li>
          <li>测试阶段使用沙箱环境；把机器人加为好友或从沙箱入口私聊。</li>
          <li>保存并启动本页通道；给机器人发第一条私聊，联系人会出现在下方。</li>
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
