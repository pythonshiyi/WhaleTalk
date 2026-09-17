import React from "react";
import * as api from "../api.js";
import { Icon } from "./icons.jsx";
import BrainLineage from "./BrainLineage.jsx";
import { confirmDialog } from "../dialog.js";

const fmtT = (iso) => (iso || "").replace("T", " ").slice(0, 16);

// ── 备份与延续：时光备份（快照/对比/恢复）+ 生命延续（融合/迁徙/分享/清理）──
// 把低频、危险、不可逆的操作集中在这一分区，与日常状态（总览）隔离。
export default function BrainContinuity({ brain, act, busy }) {
  const b = brain || {};
  const snapOptions = (b.snapshots || []).slice().reverse();
  const snapCount = snapOptions.length;

  const [acc, setAcc] = React.useState("");
  const [mergeA, setMergeA] = React.useState("");
  const [mergeB, setMergeB] = React.useState("");
  const [strategy, setStrategy] = React.useState("auto");
  const [mergeOut, setMergeOut] = React.useState(null);
  const [preview, setPreview] = React.useState(null);
  const [previewing, setPreviewing] = React.useState(false);
  const [resolving, setResolving] = React.useState(false);
  const [diffA, setDiffA] = React.useState("");
  const [diffB, setDiffB] = React.useState("");
  const [seedPw, setSeedPw] = React.useState("");
  const [importPw, setImportPw] = React.useState("");
  const [fileB64, setFileB64] = React.useState("");
  const [brainDirs, setBrainDirs] = React.useState([]);
  const [switchDir, setSwitchDir] = React.useState("");
  const [mirrorDir, setMirrorDir] = React.useState("");
  const [diff, setDiff] = React.useState(null); // {version, text} 恢复前对比

  const diffCurrent = async (version) => {
    setDiff({ version, text: "" });
    const d = await api.brainAction({ action: "diff-current", version }).catch(() => null);
    setDiff({ version, text: (d && (d.data?.output || d.message)) || "对比失败（后端未连接？）" });
  };

  const onPickSeed = (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) { setFileB64(""); return; }
    const r = new FileReader();
    r.onload = () => setFileB64(String(r.result).split(",")[1] || "");
    r.readAsDataURL(f);
  };

  const fileToB64 = (file) =>
    new Promise((resolve, reject) => {
      const rd = new FileReader();
      rd.onload = () => resolve(String(rd.result).split(",")[1] || "");
      rd.onerror = reject;
      rd.readAsDataURL(file);
    });

  const doPreview = async () => {
    if (!mergeA || !mergeB || mergeA === mergeB) return;
    setPreviewing(true);
    setPreview(null);
    const d = await api.brainAction({ action: "merge-preview", snap_a: mergeA, snap_b: mergeB, strategy }).catch(() => null);
    if (d) {
      setPreview({
        ok: d.ok,
        lca_found: !!d.data?.lca_found,
        conflict_count: d.data?.conflict_count ?? 0,
        conflicts_preview: d.data?.conflicts_preview || [],
        message: d.message || "",
      });
    } else {
      setPreview({ ok: false, conflict_count: -1, conflicts_preview: [], message: "预演请求失败（后端未连接？）" });
    }
    setPreviewing(false);
  };

  const doMerge = async () => {
    if (!mergeA || !mergeB || mergeA === mergeB) return;
    setMergeOut(null);
    setPreview(null);
    const d = await act("merge", { snap_a: mergeA, snap_b: mergeB, strategy });
    if (d) setMergeOut({ dir: d.data?.dir || "", conflicts: d.data?.conflicts || [], message: d.message || "" });
  };

  const resolveOne = async (cid, keep) => {
    if (!mergeOut) return;
    setResolving(true);
    const d = await api.brainAction({ action: "merge-resolve", id: cid, keep, dir: mergeOut.dir }).catch(() => null);
    if (d && d.data) setMergeOut({ ...mergeOut, conflicts: d.data.conflicts || [] });
    setResolving(false);
  };

  const adopt = async () => {
    if (!mergeOut?.dir) return;
    if (!(await confirmDialog("把融合结果应用为当前大脑？（旧大脑自动备份到 brain.bak-*）", { danger: true, okText: "应用融合" }))) return;
    await act("adopt-merge", { dir: mergeOut.dir });
    setMergeOut(null);
  };

  const loadDirs = async () => {
    const d = await api.brainAction({ action: "brain-dirs" }).catch(() => null);
    if (d && d.ok) {
      setBrainDirs(d.data.dirs || []);
      const cur = d.data.dirs?.find((x) => x.current);
      if (cur) setSwitchDir(cur.path);
    }
  };

  return (
    <>
      {/* ── 时光备份 ── */}
      <div className="brain-card">
        <div className="brain-card-title">
          <Icon name="archive" size={14} />
          <span>时光备份</span>
          <i>回到任意时刻的自己；恢复会覆盖当前状态，旧大脑自动保留为 brain.bak-*</i>
          <button className="msg-op" disabled={busy} onClick={() => act("archive")} style={{ marginLeft: "auto" }}>
            ＋ 立即备份
          </button>
        </div>
        {snapCount === 0 ? (
          <div className="sched-text" style={{ opacity: 0.7, padding: "6px 0" }}>还没有备份。点击「＋ 立即备份」记录现在的自己。</div>
        ) : (
          <>
            <div className="brain-timeline">
              {snapOptions.map((s) => {
                const isCur = String(s.version) === String(b.current_version);
                return (
                  <div className="tl-item" key={s.name}>
                    <span className={`tl-dot ${isCur ? "now" : ""}`} />
                    <div className="tl-body">
                      <div className="tl-main">
                        <b>v{s.version}</b>
                        <span className="tl-meta">{s.mtime} · {s.size_kb} KB{isCur ? " · 当前" : ""}</span>
                      </div>
                      {!isCur && (
                        <button className="msg-op" disabled={busy} onClick={() => diffCurrent(s.version)} title="先看差异再决定是否恢复">
                          <Icon name="refresh" size={12} /> 对比当前
                        </button>
                      )}
                      <button
                        className="msg-op"
                        disabled={busy || isCur}
                        onClick={() => act("restore", { version: s.version, replace: true }, `回到 v${s.version} 的时刻？当前大脑会自动备份到 brain.bak-*，之后可用「清理」找回空间。`)}
                      >
                        {isCur ? "当前" : "回到此刻"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
            {diff && (
              <div className="brain-diff">
                <div className="brain-diff-head">
                  <span>当前 ↔ v{diff.version} 差异（恢复前预览）</span>
                  <button className="msg-op" onClick={() => setDiff(null)} aria-label="关闭对比"><Icon name="x" size={13} /></button>
                </div>
                <pre className="brain-context">{diff.text || "对比中…"}</pre>
              </div>
            )}
            {snapCount >= 2 && (
              <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8, fontSize: "var(--fs-xs)" }}>
                <span style={{ color: "var(--text-2)" }}>对比两个时刻：</span>
                <select className="set-select" style={{ fontSize: "var(--fs-xs)", padding: "3px 6px" }} value={diffA} onChange={(e) => setDiffA(e.target.value)}>
                  <option value="">A</option>
                  {snapOptions.map((s) => <option key={"a" + s.version} value={s.name}>v{s.version}</option>)}
                </select>
                <select className="set-select" style={{ fontSize: "var(--fs-xs)", padding: "3px 6px" }} value={diffB} onChange={(e) => setDiffB(e.target.value)}>
                  <option value="">B</option>
                  {snapOptions.map((s) => <option key={"b" + s.version} value={s.name}>v{s.version}</option>)}
                </select>
                <button className="msg-op" disabled={busy || !diffA || !diffB || diffA === diffB} onClick={() => act("diff", { snap_a: diffA, snap_b: diffB })}>
                  <Icon name="refresh" size={12} /> 对比
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {/* ── 快照血缘图 ── */}
      <div className="brain-card">
        <div className="brain-card-title">
          <Icon name="git-branch" size={14} />
          <span>快照血缘图</span>
          <i>把时光备份铺成一条可追溯的线——哪些是延续、哪些来自恢复、哪几段发生过融合</i>
        </div>
        <BrainLineage />
      </div>

      {/* ── 生命延续 ── */}
      <div className="brain-card">
        <div className="brain-card-title">
          <Icon name="trending-up" size={14} />
          <span>生命延续</span>
          <i>大脑不依赖这台设备——可备份带走、融合两段经历、迁徙到新躯体</i>
        </div>
        {b.open_conflicts > 0 && (
          <div className="brain-conflict" style={{ marginTop: 8, fontSize: "var(--fs-xs)" }}>
            <Icon name="warning" size={13} /> 有 {b.open_conflicts} 条融合冲突待裁决（见下方「融合两段记忆」）
          </div>
        )}

        {/* 融合两段记忆 */}
        <div className={`acc-item ${acc === "merge" ? "open" : ""}`}>
          <button className="acc-head" aria-expanded={acc === "merge"} onClick={() => setAcc(acc === "merge" ? "" : "merge")}>
            <span className="acc-arrow">▸</span>
            <Icon name="git-branch" size={14} /> 融合两段记忆（分支合并）
            <span className="acc-desc">把两个时光备份的经历合成一个</span>
          </button>
          {acc === "merge" && (
            <div className="acc-body">
              <div className="sched-line1">
                <select className="set-select" value={mergeA} onChange={(e) => setMergeA(e.target.value)} style={{ flex: 1 }}>
                  <option value="">快照 A（较早）…</option>
                  {snapOptions.map((s) => <option key={s.name} value={s.version}>{s.name}（{s.mtime}）</option>)}
                </select>
                <select className="set-select" value={mergeB} onChange={(e) => setMergeB(e.target.value)} style={{ flex: 1 }}>
                  <option value="">快照 B（较晚）…</option>
                  {snapOptions.map((s) => <option key={s.name} value={s.version}>{s.name}（{s.mtime}）</option>)}
                </select>
                <select className="set-select" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                  <option value="auto">冲突留待裁决</option>
                  <option value="ours">冲突取 A</option>
                  <option value="theirs">冲突取 B</option>
                </select>
                <button className="msg-op" disabled={busy || previewing || snapCount < 2} onClick={doPreview}>
                  {previewing ? "预演中…" : "⧩ 预演"}
                </button>
                <button className="confirm-btn" disabled={busy || snapCount < 2} onClick={doMerge}>融合</button>
              </div>
              <div className="brain-acc-hint">「预演」先跑 dry-run，只告诉你会撞出多少冲突（不真合并）；确认后再点「融合」。两段记忆相悖时「留待裁决」逐条定 /「取 A/B」自动偏向前者/后者。</div>

              {preview && (
                <div style={{ margin: "10px 0 0", padding: 8, border: "1px solid var(--border-strong)", borderRadius: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", fontSize: "var(--fs-xs)" }}>
                    <span className="tl-dot" style={{ background: "var(--brand)" }} /> 主干 v{mergeA}
                    <span className="tl-dot" style={{ background: "var(--warn)" }} /> 分支 v{mergeB}
                    {preview.lca_found
                      ? <span style={{ opacity: 0.8 }}>→ 找到共同祖先，可做三路合并（更安全）</span>
                      : <span style={{ color: "var(--warn-text)" }}>→ 未找到共同祖先（历史快照缺失），将做双路合并</span>}
                  </div>
                  {preview.conflict_count >= 0 && (
                    <div style={{ marginTop: 6, fontSize: "var(--fs-xs)" }}>
                      {preview.conflict_count === 0
                        ? <span style={{ color: "var(--ok-text)" }}><Icon name="check" size={12} /> 预演无冲突，可直接融合</span>
                        : <span style={{ color: "var(--warn-text)" }}><Icon name="warning" size={12} /> 预演将产生 {preview.conflict_count} 条待裁决冲突：</span>}
                      {(preview.conflicts_preview || []).length > 0 && (
                        <div style={{ marginTop: 4, paddingLeft: 10, opacity: 0.85 }}>
                          {preview.conflicts_preview.map((c) => <div key={c.id}>• {c.file}</div>)}
                        </div>
                      )}
                      {preview.message && <div style={{ marginTop: 4, opacity: 0.7, whiteSpace: "pre-wrap" }}>{preview.message}</div>}
                    </div>
                  )}
                </div>
              )}
              {mergeOut && (
                <div className="sched-text" style={{ marginTop: 8 }}>
                  <div>• 结果目录：{mergeOut.dir}</div>
                  {(mergeOut.conflicts || []).length === 0 ? (
                    <div><Icon name="check" size={12} /> 无冲突，可采纳为当前大脑。
                      <button className="confirm-btn" style={{ marginLeft: 8 }} disabled={busy} onClick={adopt}>采纳为新大脑</button>
                    </div>
                  ) : (
                    <div>
                      <Icon name="warning" size={12} /> {mergeOut.conflicts.length} 条冲突待裁决：
                      {(mergeOut.conflicts || []).map((c) => (
                        <div key={c.id} style={{ margin: "6px 0", padding: 6, border: "1px solid var(--border-strong)", borderRadius: 6 }}>
                          <div style={{ opacity: 0.9 }}>• {c.file}{c.path && c.path !== c.file ? `（${c.path.replace(c.file + ".", "")}）` : ""}</div>
                          <div style={{ fontSize: "var(--fs-xs)", opacity: 0.8 }}>A: {c.ours || "—"}　vs　B: {c.theirs || "—"}</div>
                          <div style={{ marginTop: 4 }}>
                            {["ours", "theirs", "both"].map((k) => (
                              <button key={k} className="msg-op" disabled={resolving} onClick={() => resolveOne(c.id, k)}>{k === "ours" ? "取A" : k === "theirs" ? "取B" : "两者都要"}</button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 迁徙到新设备 */}
        <div className={`acc-item ${acc === "migrate" ? "open" : ""}`}>
          <button className="acc-head" aria-expanded={acc === "migrate"} onClick={() => setAcc(acc === "migrate" ? "" : "migrate")}>
            <span className="acc-arrow">▸</span>
            <Icon name="key" size={14} /> 迁徙到新设备（免密密钥）
            <span className="acc-desc">把大脑带去另一台电脑，免密解锁</span>
          </button>
          {acc === "migrate" && (
            <div className="acc-body">
              <div className="sched-line1">
                <input className="set-select set-combo" type="password" placeholder="迁徙口令（留空自动生成）" value={seedPw} onChange={(e) => setSeedPw(e.target.value)} style={{ flex: 1 }} />
                <button className="confirm-btn" disabled={busy} onClick={() => act("export-key", { passphrase: seedPw })}>导出迁徙密钥</button>
              </div>
              <div className="sched-line1" style={{ marginTop: 8 }}>
                <input type="file" accept=".whale" onChange={onPickSeed} style={{ flex: 1 }} />
                <input className="set-select set-combo" type="password" placeholder="一次性口令" value={importPw} onChange={(e) => setImportPw(e.target.value)} style={{ flex: 1 }} />
                <button className="confirm-btn" disabled={busy || !fileB64} onClick={() => act("import-key", { file_b64: fileB64, passphrase: importPw })}>导入密钥</button>
              </div>
              <div className="brain-acc-hint">在新设备上导入密钥包后，即可免密解开这里的加密备份。种子文件与口令用后即焚。</div>
            </div>
          )}
        </div>

        {/* 分享与多大脑 */}
        <div className={`acc-item ${acc === "share" ? "open" : ""}`}>
          <button className="acc-head" aria-expanded={acc === "share"} onClick={() => { const next = acc === "share" ? "" : "share"; setAcc(next); if (next === "share") loadDirs(); }}>
            <span className="acc-arrow">▸</span>
            <Icon name="share" size={14} /> 分享大脑 · 多大脑切换
            <span className="acc-desc">脱敏导出记忆精华分享；在多个大脑之间切换</span>
          </button>
          {acc === "share" && (
            <div className="acc-body">
              <div className="sched-line1">
                <button className="confirm-btn" disabled={busy} onClick={() => act("share-export")}>
                  <Icon name="upload" size={13} /> 导出分享包（脱敏）
                </button>
                <input type="file" accept=".json" style={{ flex: 1 }} onChange={async (e) => {
                  const f = e.target.files && e.target.files[0];
                  if (!f) return;
                  act("share-import", { file_b64: await fileToB64(f) });
                }} />
              </div>
              <div className="brain-acc-hint">导出只包含人格与记忆精华（脱敏，不含密钥/私密文件）；导入会把分享记忆并入当前大脑。</div>
              <div className="sched-line1" style={{ marginTop: 8 }}>
                <select className="set-select" style={{ flex: 1 }} value={switchDir} onChange={(e) => setSwitchDir(e.target.value)}>
                  {brainDirs.map((d) => (
                    <option key={d.path} value={d.path}>{d.name}{d.current ? "（当前）" : ""}</option>
                  ))}
                </select>
                <button className="confirm-btn" disabled={busy || !switchDir} onClick={() => act("brain-switch", { dir: switchDir }, "切换到该大脑？（当前会话立即生效）")}>
                  切换
                </button>
                <button className="msg-op" onClick={loadDirs}>刷新</button>
              </div>
            </div>
          )}
        </div>

        {/* 维护清理 */}
        <div className={`acc-item ${acc === "cleanup" ? "open" : ""}`}>
          <button className="acc-head" aria-expanded={acc === "cleanup"} onClick={() => setAcc(acc === "cleanup" ? "" : "cleanup")}>
            <span className="acc-arrow">▸</span>
            <Icon name="eraser" size={14} /> 维护清理
            <span className="acc-desc">清理融合残留与过期备份，保留最近 2 份</span>
          </button>
          {acc === "cleanup" && (
            <div className="acc-body">
              <div className="sched-line1">
                <button className="msg-op" disabled={busy} onClick={() => act("cleanup", { keep_bak: 2 }, "清理融合残留目录与过期大脑备份（保留最近 2 份）？")}>
                  <Icon name="trash" size={13} /> 立即清理
                </button>
                <span className="sched-text" style={{ fontSize: "var(--fs-xs)", opacity: 0.7 }}>合并临时目录 / 过期大脑备份</span>
              </div>
              <div className="sched-line1" style={{ marginTop: 8 }}>
                <input
                  className="set-select set-combo"
                  placeholder="异地备份目录（快照外置留存）"
                  value={mirrorDir}
                  onChange={(e) => setMirrorDir(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button className="confirm-btn" disabled={busy || !mirrorDir} onClick={() => act("mirror", { dir: mirrorDir })}>
                  <Icon name="archive" size={13} /> 镜像到异地
                </button>
              </div>
              <div className="brain-acc-hint">把 brain/archive/ 下全部快照镜像到外部目录并生成清单——快照脱离大脑目录独立留存，历史不断链。</div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
