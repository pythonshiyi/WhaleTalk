import React from "react";
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

// ── AI 插件设计工坊 ────────────────────────────────
function StudioModal({ onClose, onInstalled }) {
  const [desc, setDesc] = React.useState("");
  const [name, setName] = React.useState("");
  const [ptype, setPtype] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [pluginJson, setPluginJson] = React.useState("");
  const [error, setError] = React.useState("");
  const [okMsg, setOkMsg] = React.useState("");

  const generate = async () => {
    if (!desc.trim() || busy) return;
    setBusy(true);
    setError("");
    setOkMsg("");
    setPluginJson("");
    try {
      const d = await apiPost("/v1/plugin_studio/generate", { description: desc, name, type: ptype });
      if (d && d.plugin) {
        setPluginJson(JSON.stringify(d.plugin, null, 2));
        setOkMsg("✅ AI 已生成，可编辑后安装");
      } else if (d && d.error) {
        setError(d.error);
      }
    } catch (e) {
      setError(`生成失败：${e.message}`);
    }
    setBusy(false);
  };

  const install = async () => {
    if (!pluginJson.trim()) return;
    setBusy(true);
    setError("");
    try {
      let plugin;
      try {
        plugin = JSON.parse(pluginJson);
      } catch (e) {
        setError(`JSON 解析失败：${e.message}`);
        setBusy(false);
        return;
      }
      const d = await apiPost("/v1/plugin_studio/install", { plugin });
      if (d && d.ok) {
        setOkMsg(`✅ 已安装「${d.name}」`);
        onInstalled && onInstalled();
        setTimeout(onClose, 1200);
      } else if (d && d.error) {
        setError(d.error);
      }
    } catch (e) {
      setError(`安装失败：${e.message}`);
    }
    setBusy(false);
  };

  return (
    <div className="confirm-mask" onClick={onClose}>
      <div className="tf-panel studio-panel" onClick={(e) => e.stopPropagation()}>
        <div className="confirm-head">
          <b>🧩 AI 插件设计工坊</b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="tf-desc-line">描述你想要的功能，AI 自动生成 .wtplugin 插件（工具/技能/流程/场景/应用），可预览编辑后一键安装。</div>
        <div className="tf-fields">
          <div className="tf-row">
            <span className="tf-name">需求</span>
            <textarea
              className="tf-input tf-json"
              rows={3}
              placeholder="如：做一个定时巡检网站可用性的插件，异常时推送 webhook"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
            />
          </div>
          <div className="tf-row">
            <span className="tf-name">插件名</span>
            <input className="tf-input" placeholder="可选，如 网站巡检" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="tf-row">
            <span className="tf-name">类型</span>
            <select className="set-select tf-input" value={ptype} onChange={(e) => setPtype(e.target.value)}>
              <option value="">自动判断</option>
              <option value="app">应用型（自带代码 /触发词）</option>
              <option value="tools">工具（HTTP API）</option>
              <option value="skills">技能（提示词）</option>
              <option value="workflows">流程（多步）</option>
            </select>
          </div>
        </div>
        <div className="tf-foot">
          <button className="confirm-btn" onClick={onClose}>关闭</button>
          <button className="confirm-btn confirm-primary" onClick={generate} disabled={busy || !desc.trim()}>
            {busy ? "AI 生成中…" : "✨ AI 生成"}
          </button>
        </div>
        {error && <div className="tf-result"><pre style={{ color: "var(--danger)" }}>{error}</pre></div>}
        {okMsg && <div className="px-tip" style={{ textAlign: "center", paddingTop: 8 }}>{okMsg}</div>}
        {pluginJson && (
          <>
            <div className="ctx-group-title">📄 生成的插件（可编辑）</div>
            <textarea
              className="fim-input studio-json"
              rows={12}
              value={pluginJson}
              onChange={(e) => setPluginJson(e.target.value)}
            />
            <div className="tf-foot">
              <button className="confirm-btn confirm-primary" onClick={install} disabled={busy}>
                {busy ? "安装中…" : "💾 校验并安装"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function DetailOverlay({ name, onClose }) {
  const [d, setD] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    apiGet(`/v1/plugins/${encodeURIComponent(name)}`).then((x) => alive && x && setD(x));
    return () => {
      alive = false;
    };
  }, [name]);
  if (!d) return null;
  return (
    <div className="confirm-mask" onClick={onClose}>
      <div className="tf-panel" onClick={(e) => e.stopPropagation()}>
        <div className="confirm-head">
          <b>🧩 {d.name} <span className="tf-custom">v{d.version} · by {d.author}</span></b>
          <button className="icon-btn" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="tf-desc-line">{d.description}</div>
        <div className="perm-chips" style={{ marginBottom: 10 }}>
          <span className="sl-tag tag-起草">{d.kind}</span>
          {d.trigger && <span className="plugin-trigger">{d.trigger}</span>}
          {d.enabled ? <span className="evo-badge">已启用</span> : <span className="sl-tag tag-临时">已停用</span>}
          {d.requires && d.requires.length > 0 && (
            <span className="sl-tag tag-考证" style={{ color: "var(--warn)", background: "var(--warn-soft)" }}>
              {d.requires.join(" + ")}
            </span>
          )}
        </div>
        <div className="perm-groups">
          {(d.tools || []).length > 0 && (
            <div className="perm-group"><b style={{ fontSize: 12 }}>🔧 工具（{(d.tools || []).length}）</b>
              <div className="perm-chips">{d.tools.map((t) => <span className="perm-chip" key={t} style={{ color: "var(--brand)", borderColor: "rgba(14,165,233,.4)" }}>{t}</span>)}</div>
            </div>
          )}
          {(d.skills || []).length > 0 && (
            <div className="perm-group"><b style={{ fontSize: 12 }}>⚡ 技能（{(d.skills || []).length}）</b>
              <div className="perm-chips">{d.skills.map((s) => <span className="perm-chip" key={s}>{s}</span>)}</div>
            </div>
          )}
          {(d.workflows || []).length > 0 && (
            <div className="perm-group"><b style={{ fontSize: 12 }}>🔄 流程（{(d.workflows || []).length}）</b>
              <div className="perm-chips">{d.workflows.map((w) => <span className="perm-chip" key={w}>{w}</span>)}</div>
            </div>
          )}
          {d.app_entry && (
            <div className="perm-group"><b style={{ fontSize: 12 }}>📦 应用入口</b>
              <div className="sched-text" style={{ fontFamily: "var(--font-mono)" }}>{d.app_entry}</div>
            </div>
          )}
          {(d.files || []).length > 0 && (
            <div className="perm-group"><b style={{ fontSize: 12 }}>📁 自带文件（{(d.files || []).length}）</b>
              <div className="perm-chips">{d.files.map((f) => <span className="perm-chip" key={f}>{f}</span>)}</div>
            </div>
          )}
          {(d.rating || {}).count != null && (
            <div className="perm-group"><b style={{ fontSize: 12 }}>⭐ 评分</b>
              <div className="sched-text">{d.rating.average?.toFixed(1) || "—"} / 5（{d.rating.count} 人评价）</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const TIER_META = {
  official: { label: "官方", cls: "tier-official" },
  community: { label: "社区", cls: "tier-community" },
  experimental: { label: "实验", cls: "tier-experimental" },
};

export default function PluginsPage({ onApply }) {
  const [tab, setTab] = React.useState("gallery");
  const [installed, setInstalled] = React.useState([]);
  const [gallery, setGallery] = React.useState([]);
  const [market, setMarket] = React.useState([]);
  const [marketInfo, setMarketInfo] = React.useState({});
  const [busy, setBusy] = React.useState("");
  const [detail, setDetail] = React.useState(null);
  const [tip, setTip] = React.useState("");
  const [studioOpen, setStudioOpen] = React.useState(false);
  const [err, setErr] = React.useState("");

  const load = async () => {
    const d = await apiGet("/v1/plugins");
    if (d) {
      setInstalled(d.installed || []);
      setGallery(d.gallery || []);
      setErr("");
    } else {
      setErr("插件列表加载失败：后端未连接，请启动服务后刷新");
    }
    const m = await apiGet("/v1/plugin_market");
    if (m && Array.isArray(m.plugins)) {
      setMarket(m.plugins);
      setMarketInfo({ source: m.source, error: m.error, signature_enforced: m.signature_enforced, count: m.count });
    } else if (m && m.error) {
      setMarket([]);
      setMarketInfo({ source: m.source, error: m.error });
    }
  };
  React.useEffect(() => {
    load();
  }, []);

  const marketInstall = async (name) => {
    setBusy(name);
    try {
      const d = await apiPost("/v1/plugin_market/install", { name });
      if (d && d.ok) {
        setTip(`✅ 已安装「${d.name}」（校验：${d.verified || "sha256"}${d.tier ? " · " + (TIER_META[d.tier]?.label || d.tier) : ""}）`);
        setTimeout(() => setTip(""), 2600);
        load();
      } else if (d && d.error) {
        alert(`安装被拒绝：${d.error}`);
      }
    } catch (e) {
      alert(`安装失败：${e.message}`);
    }
    setBusy("");
  };

  const act = async (name, action) => {
    setBusy(name);
    try {
      const d = await apiPost("/v1/plugins", { name, action });
      if (d && d.ok) {
        setTip(`${action === "install" ? "已安装" : action === "uninstall" ? "已卸载" : action === "enable" ? "已启用" : "已停用"}「${name}」`);
        setTimeout(() => setTip(""), 2000);
        load();
      } else if (d && d.error) {
        alert(d.error);
      }
    } catch (e) {
      alert(`操作失败：${e.message}`);
    }
    setBusy("");
  };

  const isInstalled = (name) => installed.some((p) => p.name === name);

  const Card = ({ p, ver }) => (
    <div className={`plugin-card ${isInstalled(p.name) ? "plugin-on" : ""}`}>
      <div className="plugin-head">
        <span className="plugin-icon">🧩</span>
        <div className="plugin-meta">
          <b>{p.name}</b>
          <div className="plugin-sub">
            <span className="plugin-author">by {p.author || "官方"}</span>
            <span className="plugin-kind">{p.kind}</span>
            {p.version && <span className="plugin-ver">v{p.version}</span>}
          </div>
        </div>
        <span className={`plugin-tag ${isInstalled(p.name) ? "plugin-tag-on" : ""}`}>{p.kind}</span>
      </div>
      <div className="plugin-desc">{p.description}</div>
      <div className="plugin-perms">
        {p.permissions && p.permissions.declared ? (
          <>
            <span className="plugin-perm-tag">🔐 {(p.permissions.tools || []).length} 工具</span>
            {p.permissions.net && <span className="plugin-perm-tag">🌐 联网</span>}
            {(p.permissions.files || []).length > 0 && <span className="plugin-perm-tag">📁 {p.permissions.files.join("/")}</span>}
          </>
        ) : (
          <span className="plugin-perm-tag plugin-perm-warn">⚠️ 未声明权限</span>
        )}
      </div>
      <div className="plugin-foot">
        {p.trigger ? <span className="plugin-trigger">{p.trigger}</span> : <span />}
        <div style={{ display: "flex", gap: 6 }}>
          <button className="msg-op" onClick={() => setDetail(p.name)}>🔍 详情</button>
          {isInstalled(p.name) && (
            <button
              className="msg-op plugin-use"
              onClick={() => {
                const text = p.trigger ? `${p.trigger} ` : `用「${p.name}」插件：`;
                onApply && onApply(text);
              }}
            >
              🚀 使用
            </button>
          )}
          <button
            className={`install-btn ${isInstalled(p.name) ? "install-on" : ""}`}
            disabled={busy === p.name}
            onClick={() => act(p.name, isInstalled(p.name) ? "uninstall" : "install")}
          >
            {busy === p.name ? "…" : isInstalled(p.name) ? "已安装 ✓" : "安装"}
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="page">
      <div className="page-head">
        <h1>插件中心</h1>
        <p>工具 / 技能 / 流程 / 应用四类插件 · 导入即用 · 描述需求 AI 自动生成（插件工坊）</p>
      </div>
      <div className="ab-tabs">
        <button className={`ab-tab ${tab === "gallery" ? "ab-tab-on" : ""}`} onClick={() => setTab("gallery")}>
          🖼 画廊（{gallery.length}）
        </button>
        <button className={`ab-tab ${tab === "market" ? "ab-tab-on" : ""}`} onClick={() => setTab("market")}>
          🌐 市场（{market.length}）
        </button>
        <button className={`ab-tab ${tab === "installed" ? "ab-tab-on" : ""}`} onClick={() => setTab("installed")}>
          📦 已安装（{installed.length}）
        </button>
      </div>

      {tab === "gallery" && (
        <>
          <div className="market-bar">
            <button className="market-fab" onClick={() => setStudioOpen(true)}>
              🧩 AI 插件设计工坊——描述需求，AI 帮你造插件
            </button>
          </div>
          {err && <div className="empty-tip">{err}</div>}
          <div className="plugin-grid">
            {gallery.map((p) => <Card key={p.name} p={p} />)}
          </div>
          {!err && gallery.length === 0 && <div className="empty-tip">画廊暂无插件</div>}
        </>
      )}

      {tab === "market" && (
        <>
          <div className="market-bar" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 12, opacity: .8 }}>
              {marketInfo.signature_enforced ? "🔒 已强制 Ed25519 签名校验" : "🔒 下载后 SHA-256 校验"} · 来源 {marketInfo.source}
            </span>
          </div>
          {marketInfo.error && <div className="empty-tip">市场索引不可用：{marketInfo.error}</div>}
          <div className="plugin-grid">
            {market.map((p) => {
              const tm = TIER_META[p.tier] || TIER_META.community;
              return (
                <div className={`plugin-card ${p.installed ? "plugin-on" : ""}`} key={p.name}>
                  <div className="plugin-head">
                    <span className="plugin-icon">🧩</span>
                    <div className="plugin-meta">
                      <b>{p.name}</b>
                      <div className="plugin-sub">
                        <span className="plugin-author">by {p.author || "社区"}</span>
                        {p.version && <span className="plugin-ver">v{p.version}</span>}
                      </div>
                    </div>
                    <span className={`tier-badge ${tm.cls}`}>{tm.label}</span>
                  </div>
                  <div className="plugin-desc">{p.description}</div>
                  <div className="plugin-perms">
                    <span className={p.has_sha256 || p.signed ? "tier-verify" : "tier-verify-none"}>
                      {p.signed ? "🔏 已签名" : p.has_sha256 ? "🔒 SHA-256 校验" : "⚠️ 无校验信息"}
                    </span>
                    {p.note && <span className="plugin-perm-tag" style={{ opacity: .7 }}>{p.note}</span>}
                  </div>
                  <div className="plugin-foot">
                    <span />
                    <button
                      className={`install-btn ${p.installed ? "install-on" : ""}`}
                      disabled={busy === p.name || p.installed}
                      onClick={() => marketInstall(p.name)}
                    >
                      {busy === p.name ? "校验安装中…" : p.installed ? "已安装 ✓" : "🔐 校验并安装"}
                    </button>
                  </div>
                </div>
              );
            })}
            {!marketInfo.error && market.length === 0 && <div className="empty-tip">市场暂无插件</div>}
          </div>
        </>
      )}

      {tab === "installed" && (
        <>
          {err && <div className="empty-tip">{err}</div>}
        <div className="plugin-grid">
          {installed.length === 0 && <div className="empty-tip">暂无已安装插件——去画廊安装或 AI 工坊生成</div>}
          {installed.map((p) => (
            <div className={`plugin-card ${p.enabled ? "plugin-on" : ""}`} key={p.name}>
              <div className="plugin-head">
                <span className="plugin-icon">🧩</span>
                <div className="plugin-meta">
                  <b>{p.name}</b>
                  <div className="plugin-sub">
                    <span className="plugin-author">by {p.author || "官方"}</span>
                    <span className="plugin-kind">{p.kind}</span>
                    <span className="plugin-ver">v{p.version}</span>
                  </div>
                </div>
                <span className={`plugin-tag ${p.enabled ? "plugin-tag-on" : ""}`}>{p.enabled ? "已启用" : "已停用"}</span>
              </div>
              <div className="plugin-desc">{p.description}</div>
              <div className="plugin-foot">
                {p.trigger ? <span className="plugin-trigger">{p.trigger}</span> : <span />}
                <div style={{ display: "flex", gap: 6 }}>
                  <button className="msg-op" onClick={() => setDetail(p.name)}>🔍 详情</button>
                  <button className="msg-op" disabled={busy === p.name} onClick={() => act(p.name, p.enabled ? "disable" : "enable")}>
                    {p.enabled ? "⏸ 停用" : "▶ 启用"}
                  </button>
                  <button className="msg-op" style={{ color: "var(--danger)" }} disabled={busy === p.name} onClick={() => act(p.name, "uninstall")}>
                    🗑 卸载
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
        </>
      )}

      {detail && <DetailOverlay name={detail} onClose={() => setDetail(null)} />}
      {studioOpen && <StudioModal onClose={() => setStudioOpen(false)} onInstalled={load} />}
      {tip && <div className="set-saved-tip">{tip}</div>}
    </div>
  );
}