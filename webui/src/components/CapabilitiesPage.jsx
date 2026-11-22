import React from "react";
import { capabilities, capabilityAction } from "../api.js";
import { confirmDialog } from "../dialog.js";

/**
 * 能力包（v3.18）：运行时装载的能力包与工具清单。
 *
 * 设计取舍：界面上**不出现「插件」这个词**——用户看到的是「能力」，
 * 来源（内置/自建）只是详情里的一个字段。内置能力包只允许停用，不允许卸载：
 * 它是产品的一部分，卸载等于能力面缺失，用户会当成 bug。
 */
export default function CapabilitiesPage({ embedded = false }) {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState("");
  const [tip, setTip] = React.useState("");
  const [busy, setBusy] = React.useState("");
  const fileRef = React.useRef(null);

  const load = React.useCallback(async () => {
    try {
      const d = await capabilities();
      setData(d || null);
      setErr("");
    } catch (e) {
      setErr(e && e.message ? e.message : "读取能力失败");
    }
  }, []);

  React.useEffect(() => { load(); }, [load]);

  const packs = (data && data.packages) || [];
  const items = (data && data.items) || [];
  const health = (data && data.health) || {};
  const builtin = packs.filter((p) => p.bundled);
  const mine = packs.filter((p) => !p.bundled);
  const builtinTools = data && data.total_tools ? Math.max(0, data.total_tools - items.length) : null;

  const flash = (text) => {
    setTip(text);
    setTimeout(() => setTip(""), 2500);
  };

  const act = async (body, okText) => {
    setBusy(body.pack_id || body.name || body.action);
    setErr("");
    try {
      const r = await capabilityAction(body);
      const res = r && r.result;
      if (res && res.ok === false) {
        setErr((res.errors || [res.error]).filter(Boolean).join("；") || "操作失败");
      } else {
        flash(okText || "操作完成");
      }
      await load();
    } catch (e) {
      setErr(String((e && e.message) || e));
    } finally {
      setBusy("");
    }
  };

  const reloadPack = (p) => {
    const file = String(p.path || "").split(/[\\/]/).pop();
    act({ action: "reload", file, pack_id: p.plugin_id }, `已重载 ${p.name || p.plugin_id}`);
  };

  const unloadPack = async (p) => {
    if (!(await confirmDialog(`卸载能力包「${p.name || p.plugin_id}」？\n已装载的 ${p.tools.length} 项能力会立即失效。`, { danger: true, okText: "卸载" }))) return;
    act({ action: "unload", pack_id: p.plugin_id }, `已卸载 ${p.name || p.plugin_id}`);
  };

  const installFile = async (f) => {
    if (!f) return;
    try {
      const text = await f.text();
      const manifest = JSON.parse(text);
      await act({ action: "install", name: f.name, manifest }, `已安装 ${f.name}`);
    } catch (e) {
      setErr(`能力包解析失败：${(e && e.message) || e}`);
    }
  };

  const resetHealth = (it) => act({ action: "reset_health", name: it.name }, `已重置 ${it.name} 的熔断`);

  const card = {
    background: "var(--bg-1)",
    border: "1px solid var(--border)",
    borderRadius: "var(--r-lg)",
    padding: "14px 16px",
    marginBottom: 12,
  };
  const muted = { color: "var(--text-3)", fontSize: "var(--fs-sm)" };
  const th = { textAlign: "left", padding: "6px 8px", fontWeight: 500, fontSize: "var(--fs-sm)", color: "var(--text-2)" };
  const td = { padding: "7px 8px", fontSize: "var(--fs-sm)", borderTop: "1px solid var(--border)" };

  return (
    <div className={embedded ? "page-embedded" : "page"}>
      {!embedded && (
        <div className="page-head">
          <h2 style={{ fontSize: "var(--fs-lg)", fontWeight: 500, margin: "0 0 4px" }}>能力包</h2>
          <p style={{ ...muted, margin: "0 0 16px" }}>运行时装载的能力包与工具清单。</p>
        </div>
      )}

      {err && <div style={{ ...card, color: "var(--danger-text)" }}>{err}</div>}
      {data && data.ok === false && (
        <div style={{ ...card, color: "var(--danger-text)" }}>{data.error || "能力状态读取失败"}</div>
      )}

      <div style={card}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
          <div style={{ fontSize: "var(--fs-md)", fontWeight: 500 }}>
            能力包 · 内置 {builtin.length} 个 / 自建 {mine.length} 个 · 共 {items.length} 项运行时能力
          </div>
          <span style={{ flex: 1 }} />
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            style={{ display: "none" }}
            onChange={(e) => { installFile(e.target.files?.[0]); e.target.value = ""; }}
          />
          <button className="confirm-btn" onClick={() => fileRef.current?.click()} disabled={!!busy}>＋ 安装能力包</button>
        </div>
        {packs.length === 0 && <div style={muted}>还没有装载任何能力包。</div>}
        {packs.map((p) => (
          <div key={p.plugin_id} className="cap-pack-row">
            <span style={{ fontSize: "var(--fs-sm)", fontWeight: 600 }}>{p.name || p.plugin_id}</span>
            <span style={muted}>
              {p.bundled ? "内置" : "自建"} · {p.tools.length} 项能力
            </span>
            <span style={{ ...muted, marginLeft: "auto" }}>
              {p.bundled ? "随应用启动 · 不可卸载" : "可卸载"}
            </span>
            <button className="pm-op" disabled={!!busy} onClick={() => reloadPack(p)}>重载</button>
            {!p.bundled && (
              <button className="pm-op pm-op-danger" disabled={!!busy} onClick={() => unloadPack(p)}>卸载</button>
            )}
          </div>
        ))}
      </div>

      <div style={card}>
        <div style={{ fontSize: "var(--fs-md)", fontWeight: 500, marginBottom: 6 }}>
          运行时能力清单
          {health.count ? ` · 熔断 ${health.breaker_open || 0} 项 / 有失败记录 ${health.broken || 0} 项` : ""}
        </div>
        {items.length === 0 ? (
          <div style={muted}>暂无运行时能力。</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={th}>能力</th>
                  <th style={th}>来源</th>
                  <th style={th}>执行体</th>
                  <th style={th}>调用</th>
                  <th style={th}>失败率</th>
                  <th style={th}>均耗时</th>
                  <th style={th}>状态</th>
                  <th style={th}>操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.name}>
                    <td style={td}>{it.name}</td>
                    <td style={td}>{it.plugin_id}</td>
                    <td style={td}>{it.exec}</td>
                    <td style={td}>{it.calls}</td>
                    <td style={{ ...td, color: it.fail_rate > 0 ? "var(--danger-text)" : undefined }}>
                      {Math.round((it.fail_rate || 0) * 100)}%
                    </td>
                    <td style={td}>{it.avg_ms ? `${Math.round(it.avg_ms)}ms` : "—"}</td>
                    <td style={td}>
                      {it.breaker_open ? <span className="pm-badge" style={{ color: "var(--danger-text)" }}>熔断</span>
                        : it.last_error ? <span title={it.last_error} className="pm-cat">有失败记录</span>
                        : <span className="ok-text">正常</span>}
                    </td>
                    <td style={td}>
                      {it.breaker_open && (
                        <button className="pm-op" disabled={!!busy} onClick={() => resetHealth(it)}>重置熔断</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <p style={{ ...muted, margin: "4px 0 0" }}>
        内置工具{builtinTools != null ? ` ${builtinTools} 项` : ""} + 运行时能力 {items.length} 项，统一在「工具库」中管理；
        想加一项能力？直接对鲸语说「加一个能 XX 的工具」，它会用 create_capability 生成能力包并立即生效。
        规范见 docs/能力协议.md。
      </p>
      {tip && <div className="set-saved-tip">{tip}</div>}
    </div>
  );
}
