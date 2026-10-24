import React from "react";
import * as api from "../api.js";
import { SkeletonCard } from "./Skeleton.jsx";
import { silentWarn } from "../quiet.js";

const EMPTY = {
  enabled: false,
  api_base: "http://127.0.0.1:3000",
  listen_port: 8756,
  default_persona_id: "",
  allowed_users: [],
  max_reply_chars: 1200,
  rate_limit_per_minute: 10,
  quiet_hours: "",
  has_token: false,
};

export default function QQPage() {
  const [status, setStatus] = React.useState(null);
  const [personas, setPersonas] = React.useState([]);
  const [cfg, setCfg] = React.useState(EMPTY);
  const [tokenDraft, setTokenDraft] = React.useState("");
  const [clearToken, setClearToken] = React.useState(false);
  const [usersText, setUsersText] = React.useState("");
  const [testUser, setTestUser] = React.useState("");
  const [testText, setTestText] = React.useState("🐋 鲸语 QQ 通道测试消息");
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
    const next = { ...EMPTY, ...s };
    setCfg(next);
    setUsersText((s.allowed_users || []).join(", "));
  }, []);

  const load = React.useCallback(async () => {
    setErr("");
    try {
      const [s, p] = await Promise.all([
        api.getQqStatus(),
        api.listPersonas().catch(() => null),
      ]);
      applyStatus(s);
      setPersonas(Array.isArray(p?.personas) ? p.personas : []);
    } catch (e) {
      setErr("QQ 状态读取失败，请确认后端已连接。");
      silentWarn(e, "QQPage");
    }
  }, [applyStatus]);

  React.useEffect(() => { load(); }, [load]);

  const refresh = async (r) => {
    if (r?.status) applyStatus(r.status);
    else await load();
  };

  const buildPayload = (extra = {}) => ({
    enabled: cfg.enabled,
    api_base: cfg.api_base,
    listen_port: Number(cfg.listen_port) || 8756,
    default_persona_id: cfg.default_persona_id || "",
    allowed_users: usersText,
    max_reply_chars: Number(cfg.max_reply_chars) || 1200,
    rate_limit_per_minute: Number(cfg.rate_limit_per_minute) || 10,
    quiet_hours: cfg.quiet_hours || "",
    ...(clearToken ? { clear_token: true } : {}),
    ...(tokenDraft.trim() ? { access_token: tokenDraft.trim() } : {}),
    ...extra,
  });

  const save = async (extra = {}) => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.saveQqConfig(buildPayload(extra));
      setTokenDraft("");
      setClearToken(false);
      await refresh(r);
      flash("QQ 配置已保存");
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
      const r = await api.saveQqConfig(buildPayload({ enabled }));
      setCfg((s) => ({ ...s, enabled }));
      setTokenDraft("");
      setClearToken(false);
      await refresh(r);
      flash(enabled ? "QQ 通道已启动" : "QQ 通道已停止");
    } catch (e) {
      setErr(`操作失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    if (!testUser.trim()) {
      setErr("请先填写测试 QQ 号");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const r = await api.testQq(testUser.trim(), testText.trim() || "测试消息");
      await refresh(r);
      flash("测试消息已发送");
    } catch (e) {
      setErr(`发送失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const probe = async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.probeQq();
      await refresh(r);
      flash(r.message || "OneBot API 连接正常");
    } catch (e) {
      setErr(`连接检测失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  if (!status && !err) return <div className="page"><SkeletonCard /></div>;

  return (
    <div className="page qq-page">
      <div className="page-head">
        <h1>QQ 陪伴</h1>
        <p>本地 OneBot / NapCat 私聊接入：每个 QQ 独立会话与角色，只走对话模式，不启用 Agent 工具；支持文字、图片与语音转写（语音需安装可选 faster-whisper）。官方 QQ Bot 请用左侧「QQ 官方」。</p>
      </div>

      {err && <div className="empty-tip is-err">{err}</div>}

      <div className="qq-status">
        <div className={`qq-status-dot ${status?.running ? "on" : "off"}`} />
        <div className="qq-status-main">
          <b>{status?.running ? "监听中" : status?.enabled ? "已启用但未运行" : "未启用"}</b>
          <span>{status?.last_error || `监听 127.0.0.1:${cfg.listen_port || "—"} · API ${cfg.api_base || "—"}`}</span>
        </div>
        <div className="qq-status-actions">
          <button className="confirm-btn" disabled={busy} onClick={probe}>检测连接</button>
          <button className="confirm-btn" disabled={busy} onClick={() => toggle("start")}>启动</button>
          <button className="confirm-btn" disabled={busy} onClick={() => toggle("stop")}>停止</button>
        </div>
      </div>

      <div className="qq-grid">
        <section className="set-card qq-card">
          <div className="svc-title">连接配置</div>
          <div className="pm-form">
            <label className="pm-check">
              <input type="checkbox" checked={!!cfg.enabled} onChange={(e) => setCfg((s) => ({ ...s, enabled: e.target.checked }))} />
              <span>启用 QQ 通道</span>
            </label>
            <div className="pm-field">
              <label>OneBot HTTP API 地址</label>
              <input className="set-select" value={cfg.api_base || ""} placeholder="http://127.0.0.1:3000"
                onChange={(e) => setCfg((s) => ({ ...s, api_base: e.target.value }))} />
            </div>
            <div className="pm-row">
              <div className="pm-field pm-field-sm">
                <label>监听端口</label>
                <input className="set-select" value={cfg.listen_port || ""} placeholder="8756"
                  onChange={(e) => setCfg((s) => ({ ...s, listen_port: e.target.value }))} />
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
            <div className="pm-field">
              <label>访问令牌 {cfg.has_token ? "（已保存，留空保持不变）" : "（可选）"}</label>
              <input className="set-select" type="password" value={tokenDraft} placeholder="与 NapCat 的 API/上报令牌一致"
                onChange={(e) => setTokenDraft(e.target.value)} />
              <label className="pm-check">
                <input type="checkbox" checked={clearToken} onChange={(e) => setClearToken(e.target.checked)} />
                <span>清除已保存的访问令牌</span>
              </label>
            </div>
            <div className="pm-field">
              <label>允许的 QQ 号（留空 = 所有私聊）</label>
              <input className="set-select" value={usersText} placeholder="123456, 789012"
                onChange={(e) => setUsersText(e.target.value)} />
              <div className="pm-hint">建议填写；留空时任何能私聊机器人的人都会消耗模型额度。</div>
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
              <label>目标 QQ 号</label>
              <input className="set-select" value={testUser} placeholder="你的 QQ 号"
                onChange={(e) => setTestUser(e.target.value)} />
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
        <div className="svc-title">NapCat 对接步骤</div>
        <ol className="qq-steps">
          <li>在 NapCat 网络配置里开启 <b>HTTP API</b>（默认 <code>http://127.0.0.1:3000</code>）。</li>
          <li>开启 <b>HTTP POST 上报</b>，地址填 <code>http://127.0.0.1:{cfg.listen_port || 8756}/onebot</code>。</li>
          <li>如设置了访问令牌，NapCat 的 API 令牌与上报令牌填同一个值。</li>
          <li>保存并启动本页通道，然后用另一个 QQ 私聊机器人测试。</li>
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
