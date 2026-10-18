import React from "react";
import { capabilities } from "../api.js";

/**
 * 能力页（v3.18）：运行时装载的能力包与工具清单。
 *
 * 设计取舍：界面上**不出现「插件」这个词**——用户看到的是「能力」，
 * 来源（内置/自建）只是详情里的一个字段。内置能力包只允许停用，不允许卸载：
 * 它是产品的一部分，卸载等于能力面缺失，用户会当成 bug。
 */
export default function CapabilitiesPage() {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState("");

  const load = React.useCallback(() => {
    capabilities()
      .then((d) => setData(d || null))
      .catch((e) => setErr(e && e.message ? e.message : "读取能力失败"));
  }, []);

  React.useEffect(() => { load(); }, [load]);

  const packs = (data && data.packages) || [];
  const items = (data && data.items) || [];
  const health = (data && data.health) || {};
  const builtin = packs.filter((p) => p.bundled);
  const mine = packs.filter((p) => !p.bundled);

  const card = {
    background: "var(--color-background-primary)",
    border: "0.5px solid var(--color-border-tertiary)",
    borderRadius: "var(--border-radius-lg)",
    padding: "14px 16px",
    marginBottom: 12,
  };
  const muted = { color: "var(--color-text-tertiary)", fontSize: 12 };
  const th = { textAlign: "left", padding: "6px 8px", fontWeight: 500, fontSize: 12, color: "var(--color-text-secondary)" };
  const td = { padding: "7px 8px", fontSize: 13, borderTop: "0.5px solid var(--color-border-tertiary)" };

  return (
    <div style={{ padding: "18px 22px", maxWidth: 980, margin: "0 auto", overflow: "auto" }}>
      <h2 style={{ fontSize: 15, fontWeight: 500, margin: "0 0 4px" }}>能力</h2>
      <p style={{ ...muted, margin: "0 0 16px" }}>
        这里列的是运行时装载的能力；内置工具（169 项）在「能力中心」里管理。
        能力与内置工具共用同一张注册表，因此同样有分组、预激活、权限与健康度。
      </p>

      {err && <div style={{ ...card, color: "var(--color-text-danger)" }}>{err}</div>}

      <div style={card}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>
          能力包 · 内置 {builtin.length} 个 / 自建 {mine.length} 个 · 共 {items.length} 项能力
        </div>
        {packs.length === 0 && <div style={muted}>还没有装载任何能力包。</div>}
        {packs.map((p) => (
          <div key={p.plugin_id} style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "5px 0" }}>
            <span style={{ fontSize: 13 }}>{p.name || p.plugin_id}</span>
            <span style={muted}>
              {p.bundled ? "内置" : "自建"} · {p.tools.length} 项能力
            </span>
            <span style={{ ...muted, marginLeft: "auto" }}>
              {p.bundled ? "可停用，不可卸载" : "可卸载"}
            </span>
          </div>
        ))}
      </div>

      <div style={card}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>
          能力清单{health.count ? ` · 熔断 ${health.breaker_open || 0} 项 / 有失败记录 ${health.broken || 0} 项` : ""}
        </div>
        {items.length === 0 ? (
          <div style={muted}>暂无运行时能力。</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>能力</th>
                <th style={th}>来源</th>
                <th style={th}>执行体</th>
                <th style={th}>分组</th>
                <th style={th}>调用</th>
                <th style={th}>失败率</th>
                <th style={th}>均耗时</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.name}>
                  <td style={td}>{it.name}</td>
                  <td style={td}>{it.plugin_id}</td>
                  <td style={td}>{it.exec}</td>
                  <td style={td}>{(it.groups || []).join(" / ")}</td>
                  <td style={td}>{it.calls}</td>
                  <td style={{ ...td, color: it.fail_rate > 0 ? "var(--color-text-danger)" : undefined }}>
                    {Math.round((it.fail_rate || 0) * 100)}%
                  </td>
                  <td style={td}>{it.avg_ms ? `${Math.round(it.avg_ms)}ms` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p style={{ ...muted, margin: "4px 0 0" }}>
        想加一项能力？直接对鲸语说「加一个能 XX 的工具」，它会用 create_capability 生成能力包并立即生效。
        规范见 docs/能力协议.md。
      </p>
    </div>
  );
}
