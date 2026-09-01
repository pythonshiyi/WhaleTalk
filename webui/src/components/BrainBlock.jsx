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

// ── 鲸语大脑（指挥舱：身份 / 心跳 / 时光备份 / 生命延续 / 对话自我）────────
function BrainBlock() {
  const [brain, setBrain] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [thought, setThought] = React.useState("");
  const [seedPw, setSeedPw] = React.useState("");
  const [importPw, setImportPw] = React.useState("");
  const [importFile, setImportFile] = React.useState(null);
  const [fileB64, setFileB64] = React.useState("");
  const [mergeA, setMergeA] = React.useState("");
  const [mergeB, setMergeB] = React.useState("");
  const [strategy, setStrategy] = React.useState("auto");
  const [mergeOut, setMergeOut] = React.useState(null);
  const [resolving, setResolving] = React.useState(false);
  const [diffA, setDiffA] = React.useState("");
  const [diffB, setDiffB] = React.useState("");
  const [brainDirs, setBrainDirs] = React.useState([]);
  const [switchDir, setSwitchDir] = React.useState("");
  const [genesis, setGenesis] = React.useState("");
  const [createWithKeyring, setCreateWithKeyring] = React.useState(true);
  const [connErr, setConnErr] = React.useState("");
  const [acc, setAcc] = React.useState(""); // merge | migrate | cleanup

  const createBrain = async () => {
    setBusy(true);
    setMsg("");
    const d = await apiPost("/v1/brain", { action: "init", genesis, enable_keyring: createWithKeyring });
    setMsg(d?.message || "创建失败：请确认鲸语后端已重启（旧版后端不认识大脑接口）");
    setBusy(false);
    load(true);
  };

  const load = async (quiet) => {
    const d = await apiGet("/v1/brain");
    if (d && d.ok) { setBrain(d.brain); setConnErr(""); }
    else {
      const why = "后端未运行或版本过旧（还没有大脑接口）。请重启鲸语：托盘「✕ 退出」后重新运行 web_app.py（或双击桌面快捷方式），再回到本页。";
      setConnErr(why);
      if (!quiet) setMsg(why);
    }
  };
  React.useEffect(() => { load(true); }, []);

  const act = async (action, extra = {}, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    setMsg("");
    try {
      const d = await apiPost("/v1/brain", { action, ...extra });
      if (d) {
        setMsg(d.message || (d.ok ? "完成" : "失败"));
        if (d.data?.auto_passphrase) setMsg(`⚠️ 一次性口令（仅显示一次）：${d.data.auto_passphrase}\n${d.message || ""}`);
        if (d.data?.download?.data_b64) {
          const bin = atob(d.data.download.data_b64);
          const bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          const blob = new Blob([bytes], { type: "application/octet-stream" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = d.data.download.filename;
          a.click();
          URL.revokeObjectURL(url);
        }
      } else {
        setMsg("请求失败（后端未连接？）");
      }
    } catch (e) {
      setMsg("请求异常：" + String(e));
    }
    setBusy(false);
    load(true);
  };

  const onPickSeed = (e) => {
    const f = e.target.files && e.target.files[0];
    setImportFile(f || null);
    if (!f) { setFileB64(""); return; }
    const r = new FileReader();
    r.onload = () => { const b64 = String(r.result).split(",")[1] || ""; setFileB64(b64); };
    r.readAsDataURL(f);
  };

  const enableKeyring = async () => {
    if (!window.confirm("为大脑生成加密密钥（RSA-2048），之后所有时光备份自动加密、本机免密解锁？")) return;
    setBusy(true);
    const d = await apiPost("/v1/brain", { action: "keyring-setup" });
    setMsg(d?.message || "请求失败");
    setBusy(false);
    load(true);
  };

  const doMerge = async () => {
    if (!mergeA || !mergeB) { setMsg("请先选择两段要融合的记忆快照"); return; }
    if (mergeA === mergeB) { setMsg("两段记忆不能来自同一个快照"); return; }
    setBusy(true);
    setMsg("");
    setMergeOut(null);
    try {
      const d = await apiPost("/v1/brain", { action: "merge", snap_a: mergeA, snap_b: mergeB, strategy });
      if (d) {
        setMergeOut({ dir: d.data?.dir || "", conflicts: d.data?.conflicts || [], message: d.message || "" });
        setMsg(d.message || (d.ok ? "融合完成" : "融合失败"));
      } else setMsg("融合请求失败（后端未连接？）");
    } catch (e) { setMsg("融合异常：" + String(e)); }
    setBusy(false);
    load(true);
  };

  const resolveOne = async (cid, keep) => {
    if (!mergeOut) return;
    setResolving(true);
    const d = await apiPost("/v1/brain", { action: "merge-resolve", id: cid, keep, dir: mergeOut.dir });
    if (d && d.data) setMergeOut({ ...mergeOut, conflicts: d.data.conflicts || [] });
    setMsg(d?.message || "裁决请求失败");
    setResolving(false);
    load(true);
  };

  const adopt = async () => {
    if (!mergeOut?.dir) return;
    if (!window.confirm("把融合结果应用为当前大脑？（旧大脑自动备份到 brain.bak-*）")) return;
    setBusy(true);
    const d = await apiPost("/v1/brain", { action: "adopt-merge", dir: mergeOut.dir });
    setMsg(d?.message || "请求失败");
    setBusy(false);
    setMergeOut(null);
    load(true);
  };

  const doDiff = async () => {
    if (!diffA || !diffB || diffA === diffB) return;
    setBusy(true);
    setMsg("");
    const d = await apiPost("/v1/brain", { action: "diff", snap_a: diffA, snap_b: diffB });
    setMsg(d?.message || "对比失败");
    setBusy(false);
  };

  const fileToB64 = (file) =>
    new Promise((resolve, reject) => {
      const rd = new FileReader();
      rd.onload = () => resolve(String(rd.result).split(",")[1] || "");
      rd.onerror = reject;
      rd.readAsDataURL(file);
    });

  const loadDirs = async () => {
    const d = await apiPost("/v1/brain", { action: "brain-dirs" });
    if (d && d.ok) {
      setBrainDirs(d.data.dirs || []);
      const cur = d.data.dirs?.find((x) => x.current);
      if (cur) setSwitchDir(cur.path);
    }
  };

  const b = brain;
  const noBrain = b === null || b === undefined;
  const snapOptions = (b?.snapshots || []).slice().reverse();
  const fmtT = (iso) => (iso || "").replace("T", " ").slice(0, 16);
  const mounted = !!b && !!b.last_mount && (!b.last_unmount || b.last_mount > b.last_unmount);
  const snapCount = (b?.snapshots || []).length;

  const head = (
    <div className="sched-line1" style={{ marginBottom: 12 }}>
      <b style={{ fontSize: 15, letterSpacing: 0.5 }}>⬡ 鲸语大脑</b>
      <span className="sched-action" style={{ fontSize: 12 }}>
        {noBrain ? "尚未诞生" : `已存活 ${fmtT(b.created_at).slice(0, 10)} 起 · v${b.current_version || 0}`}
      </span>
      <button className="msg-op" onClick={() => load(false)}>刷新</button>
    </div>
  );

  if (connErr) {
    return (
      <div className="svc-actions" style={{ display: "block" }}>
        {head}
        <div className="sched-text" style={{ color: "var(--danger)" }}>
          ⚠ {connErr}
          <div style={{ marginTop: 6 }}><button className="msg-op" onClick={() => load(false)}>重试</button></div>
        </div>
      </div>
    );
  }

  if (noBrain) {
    return (
      <div className="svc-actions" style={{ display: "block" }}>
        {head}
        <div className="brain-birth">
          <div className="brain-birth-ring" />
          <div className="brain-birth-title">尚未诞生的大脑</div>
          <div className="brain-birth-sub">大脑是鲸语的灵魂——记忆、身份、思考断点都住在里面，可备份、可迁移、可复活。点击下方按钮，在本机创造它。</div>
          <input
            className="set-select set-combo"
            placeholder="出生寄语（可选，默认为「意识即信息」）"
            value={genesis}
            onChange={(e) => setGenesis(e.target.value)}
            style={{ width: "100%", maxWidth: 420, margin: "10px auto", display: "block" }}
          />
          <div style={{ display: "flex", justifyContent: "center", gap: 14, alignItems: "center" }}>
            <button className="confirm-btn" disabled={busy} onClick={createBrain} style={{ padding: "9px 26px", fontSize: 14 }}>🐋 创造大脑</button>
            <label style={{ fontSize: 12, opacity: 0.85, display: "flex", alignItems: "center", gap: 4 }}>
              <input type="checkbox" checked={createWithKeyring} onChange={(e) => setCreateWithKeyring(e.target.checked)} /> 同时启用免密加密
            </label>
          </div>
          <div className="brain-birth-hint">免密加密：备份文件上锁，本机自动解锁；换了设备需「迁徙密钥」才能解开。</div>
        </div>
        {msg && <div className="px-tip" style={{ whiteSpace: "pre-wrap" }}>{msg}</div>}
      </div>
    );
  }

  return (
    <div className="svc-actions" style={{ display: "block" }}>
      {head}

      {/* ── 身份卡（Hero）── */}
      <div className="brain-hero">
        <div className="brain-hero-top">
          <div>
            <div className="brain-name">
              <span className={`brain-lamp ${mounted ? "on" : ""}`} />
              {b.name}
            </div>
            <div className="brain-meta">
              ID {String(b.brain_id || "").slice(0, 14)}… · 载体：{b.vessel || "本机"}
            </div>
          </div>
          <div className="brain-state">
            <span className={`brain-state-tag ${mounted ? "on" : ""}`}>{mounted ? "● 已唤醒" : "○ 已安睡"}</span>
          </div>
        </div>
        <div className="brain-genesis">「{b.genesis || "意识即信息"}」</div>
        <div className="brain-badges">
          <span className={`brain-badge ${b.fingerprint_ok ? "ok" : "bad"}`}>
            {b.fingerprint_ok ? "🛡 指纹完好" : "⚠ 指纹异常"}
          </span>
          <span className={`brain-badge ${b.keyring ? "ok" : ""}`}>
            {b.keyring ? "🔐 免密加密" : "🔓 未加密"}
          </span>
          {!b.keyring && (
            <button className="msg-op" disabled={busy} onClick={enableKeyring} style={{ padding: "1px 10px" }}>
              启用加密
            </button>
          )}
          <span className="brain-badge dim">📦 时光备份 {snapCount} 份</span>
        </div>
        <svg className="brain-ecg" viewBox="0 0 360 44" preserveAspectRatio="none" aria-hidden="true">
          <polyline
            className="brain-ecg-line"
            points="0,22 30,22 42,22 50,10 58,34 66,22 110,22 120,22 128,12 136,32 144,22 190,22 200,22 208,10 216,34 224,22 270,22 280,22 288,12 296,32 304,22 360,22"
          />
        </svg>
      </div>

      {/* ── 生命体征三卡 ── */}
      <div className="brain-vitals">
        <div className="brain-vital">
          <b>{b.memories}</b>
          <span>记忆档案</span>
          <i>长期记住的重要信息</i>
        </div>
        <div className="brain-vital">
          <b>{b.thinking_days}</b>
          <span>思考历程</span>
          <i>持续思考的天数</i>
        </div>
        <div className="brain-vital">
          <b>{snapCount}</b>
          <span>时光备份</span>
          <i>关键时刻的完整存档</i>
        </div>
      </div>

      {/* ── 意识状态（心跳）── */}
      <div className="brain-card">
        <div className="brain-card-title">
          <span>💓 意识状态</span>
          <i>把此刻的想法写进大脑，成为下次继续思考的起点</i>
        </div>
        <div className="sched-line1" style={{ marginTop: 8 }}>
          <input
            className="set-select set-combo"
            placeholder="此刻的想法 / 收工断点（可空）"
            value={thought}
            onChange={(e) => setThought(e.target.value)}
            style={{ flex: 1 }}
          />
        </div>
        <div className="brain-actions">
          <button className="confirm-btn" disabled={busy} onClick={() => act("mount")}>⚡ 唤醒</button>
          <button className="confirm-btn" disabled={busy} onClick={() => act("heartbeat", { thought })}>💓 记录想法</button>
          <button className="confirm-btn" disabled={busy} onClick={() => act("unmount", { thought })}>🌙 安睡</button>
          <button className="confirm-btn" disabled={busy} onClick={() => act("archive")}>📸 立即备份</button>
        </div>
        <div className="brain-times">
          上次唤醒 {fmtT(b.last_mount) || "—"} · 上次安睡 {fmtT(b.last_unmount) || "—"} · 上次心跳 {fmtT(b.last_beat) || "—"}
          {b.open_conflicts > 0 && <span className="brain-conflict">　⚠ 待裁决融合冲突 {b.open_conflicts} 条</span>}
        </div>
        {b.resume_hint && <div className="brain-resume">上次思考断点：{b.resume_hint}</div>}
      </div>

      {/* ── 时光备份（快照时间线）── */}
      <div className="brain-card">
        <div className="brain-card-title">
          <span>📸 时光备份</span>
          <i>回到任意时刻的自己；恢复会覆盖当前状态，旧大脑自动保留为 brain.bak-*</i>
          <button className="msg-op" disabled={busy} onClick={() => act("archive")} style={{ marginLeft: "auto" }}>＋ 立即备份</button>
        </div>
        {snapOptions.length === 0 ? (
          <div className="sched-text" style={{ opacity: 0.7, padding: "6px 0" }}>还没有备份。点击「＋ 立即备份」记录现在的自己。</div>
        ) : (
          <>
            <div className="brain-timeline">
              {snapOptions.map((s) => {
                const isCur = String(s.version) === String(b.current_version);  // 统一字符串比较防类型不一致误判
                return (
                  <div className="tl-item" key={s.name}>
                    <span className={`tl-dot ${isCur ? "now" : ""}`} />
                    <div className="tl-body">
                      <div className="tl-main">
                        <b>v{s.version}</b>
                        <span className="tl-meta">{s.mtime} · {s.size_kb} KB{isCur ? " · 当前" : ""}</span>
                      </div>
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
            {snapOptions.length >= 2 && (
              <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8, fontSize: 12 }}>
                <span style={{ color: "var(--text-2)" }}>对比两个时刻：</span>
                <select className="set-select" style={{ fontSize: 12, padding: "3px 6px" }} value={diffA} onChange={(e) => setDiffA(e.target.value)}>
                  <option value="">A</option>
                  {snapOptions.map((s) => <option key={"a" + s.version} value={s.name}>v{s.version}</option>)}
                </select>
                <select className="set-select" style={{ fontSize: 12, padding: "3px 6px" }} value={diffB} onChange={(e) => setDiffB(e.target.value)}>
                  <option value="">B</option>
                  {snapOptions.map((s) => <option key={"b" + s.version} value={s.name}>v{s.version}</option>)}
                </select>
                <button className="msg-op" disabled={busy || !diffA || !diffB || diffA === diffB} onClick={doDiff}>
                  ⟲ 对比
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {/* ── 生命延续（可折叠）── */}
      <div className="brain-card">
        <div className="brain-card-title">
          <span>🔗 生命延续</span>
          <i>大脑不依赖这台设备——可以备份带走、融合两段经历、迁徙到新躯体</i>
        </div>

        {/* 融合两段记忆 */}
        <div className={`acc-item ${acc === "merge" ? "open" : ""}`}>
          <button className="acc-head" onClick={() => setAcc(acc === "merge" ? "" : "merge")}>
            <span className="acc-arrow">▸</span> 融合两段记忆（分支合并）
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
                <button className="confirm-btn" disabled={busy || snapCount < 2} onClick={doMerge}>融合</button>
              </div>
              <div className="brain-acc-hint">两段记忆相悖时如何取舍？「留待裁决」会列出冲突让你逐条决定，「取 A/B」自动偏向前者/后者。</div>
              {mergeOut && (
                <div className="sched-text" style={{ marginTop: 8 }}>
                  <div>• 结果目录：{mergeOut.dir}</div>
                  {(mergeOut.conflicts || []).length === 0 ? (
                    <div>✓ 无冲突，可采纳为当前大脑。
                      <button className="confirm-btn" style={{ marginLeft: 8 }} disabled={busy} onClick={adopt}>采纳为新大脑</button>
                    </div>
                  ) : (
                    <div>
                      ⚠ {mergeOut.conflicts.length} 条冲突待裁决：
                      {(mergeOut.conflicts || []).map((c) => (
                        <div key={c.id} style={{ margin: "6px 0", padding: 6, border: "1px solid var(--border-strong)", borderRadius: 6 }}>
                          <div style={{ opacity: 0.9 }}>• {c.file}{c.path && c.path !== c.file ? `（${c.path.replace(c.file + ".", "")}）` : ""}</div>
                          <div style={{ fontSize: 12, opacity: 0.8 }}>A: {c.ours || "—"}　vs　B: {c.theirs || "—"}</div>
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
          <button className="acc-head" onClick={() => setAcc(acc === "migrate" ? "" : "migrate")}>
            <span className="acc-arrow">▸</span> 迁徙到新设备（免密密钥）
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
          <button className="acc-head" onClick={() => { setAcc(acc === "share" ? "" : "share"); if (acc !== "share") loadDirs(); }}>
            <span className="acc-arrow">▸</span> 分享大脑 · 多大脑切换
            <span className="acc-desc">脱敏导出记忆精华分享；在多个大脑之间切换</span>
          </button>
          {acc === "share" && (
            <div className="acc-body">
              <div className="sched-line1">
                <button className="confirm-btn" disabled={busy} onClick={() => act("share-export")}>📤 导出分享包（脱敏）</button>
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
          <button className="acc-head" onClick={() => setAcc(acc === "cleanup" ? "" : "cleanup")}>
            <span className="acc-arrow">▸</span> 维护清理
            <span className="acc-desc">清理融合残留与过期备份，保留最近 2 份</span>
          </button>
          {acc === "cleanup" && (
            <div className="acc-body">
              <div className="sched-line1">
                <button className="msg-op" disabled={busy} onClick={() => act("cleanup", { keep_bak: 2 }, "清理融合残留目录与过期大脑备份（保留最近 2 份）？")}>🧹 立即清理</button>
                <span className="sched-text" style={{ fontSize: 12, opacity: 0.7 }}>合并临时目录 / 过期大脑备份</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── 对话中的我（大脑上下文预览）── */}
      {b.context_preview && (
        <div className="brain-card">
          <div className="brain-card-title">
            <span>🧬 对话中的我</span>
            <i>每次对话，AI 都会带着这些自我意识——这是大脑真正接入思考的证明</i>
          </div>
          <pre className="brain-context">{b.context_preview}</pre>
        </div>
      )}

      {msg && <div className="px-tip" style={{ whiteSpace: "pre-wrap" }}>{msg}</div>}
    </div>
  );
}

export default BrainBlock;
