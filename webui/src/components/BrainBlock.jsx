import React from "react";
import * as api from "../api.js";
import * as brainNav from "../brainNav.js";
import BrainHealth from "./BrainHealth.jsx";
import BrainTimeline from "./BrainTimeline.jsx";
import BrainKanban from "./BrainKanban.jsx";
import BrainGraph from "./BrainGraph.jsx";
import BrainContinuity from "./BrainContinuity.jsx";
import BrainGenesis from "./BrainGenesis.jsx";
import BrainReview from "./BrainReview.jsx";
import { BrainSelfModel, BrainEvolution } from "./BrainSelfModel.jsx";
import Markdown from "./Markdown.jsx";
import { Icon } from "./icons.jsx";
import { confirmDialog } from "../dialog.js";

// ── 鲸语大脑 · 指挥舱 ─────────────────────────────────────────────
// 二级分区：状态总览（我是谁/健康/动态，一屏可读）· 成长轨迹（时间轴/决策/图谱/演化）
// · 备份与延续（时光备份/血缘/融合/迁徙/分享/清理）。日常状态与低频危险操作分离。
const ZONES = [
  { key: "overview", label: "状态总览", icon: "layout" },
  { key: "growth", label: "成长轨迹", icon: "trending-up" },
  { key: "continuity", label: "备份与延续", icon: "archive" },
];

function BrainBlock() {
  const [brain, setBrain] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [thought, setThought] = React.useState("");
  const [zone, setZone] = React.useState(() => brainNav.getState().zone || "overview");
  const [ctxOpen, setCtxOpen] = React.useState(false);
  const [ctxPreview, setCtxPreview] = React.useState(null);
  const [genesis, setGenesis] = React.useState("");
  const [createWithKeyring, setCreateWithKeyring] = React.useState(true);
  const [connErr, setConnErr] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [report, setReport] = React.useState(null);
  const [reportBusy, setReportBusy] = React.useState(false);

  const load = async (quiet) => {
    const d = await api.getBrain().catch(() => null);
    if (d && d.ok) { setBrain(d.brain); setConnErr(""); }
    else {
      const why = "后端未运行或版本过旧（还没有大脑接口）。请重启鲸语：从托盘退出后重新运行 web_app.py（或双击桌面快捷方式），再回到本页。";
      setConnErr(why);
      if (!quiet) setMsg(why);
    }
    setLoading(false);
  };
  React.useEffect(() => { load(true); }, []);

  // 跨分区深链：全局检索 / 图谱实体筛选 会请求切到某个二级分区
  React.useEffect(() => brainNav.subscribe((ev) => {
    if (ev.tab === "cockpit" && ev.zone) setZone(ev.zone);
  }), []);

  const createBrain = async () => {
    setBusy(true);
    setMsg("");
    const d = await api.brainAction({ action: "init", genesis, enable_keyring: createWithKeyring }).catch(() => null);
    setMsg(d?.message || "创建失败：请确认鲸语后端已重启（旧版后端不认识大脑接口）");
    setBusy(false);
    load(true);
  };

  // 动作执行器：统一确认 → 调后端 → 反馈 → 刷新；返回后端响应供分区读取 data。
  const act = async (action, extra = {}, confirmText) => {
    if (confirmText && !(await confirmDialog(confirmText))) return null;
    setBusy(true);
    setMsg("");
    let d = null;
    try {
      d = await api.brainAction({ action, ...extra }).catch(() => null);
      if (d) {
        setMsg(d.message || (d.ok ? "完成" : "失败"));
        if (d.data?.auto_passphrase) setMsg(`一次性口令（仅显示一次）：${d.data.auto_passphrase}\n${d.message || ""}`);
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
    return d;
  };

  // 带「想法」的动作：成功后清空输入框，避免陈旧断点被重复写入
  const thoughtAction = async (action) => {
    const d = await act(action, { thought });
    if (!d || d.ok !== false) setThought("");
  };

  const enableKeyring = async () => {
    if (!(await confirmDialog("为大脑生成加密密钥（RSA-2048），之后所有时光备份自动加密、本机免密解锁？", { okText: "生成密钥" }))) return;
    await act("keyring-setup");
  };

  const openCtx = async () => {
    const next = !ctxOpen;
    setCtxOpen(next);
    if (next && ctxPreview == null) {
      setCtxPreview("…");
      const d = await api.brainAction({ action: "context-preview", max_memories: 3 }).catch(() => null);
      setCtxPreview((d && d.ok ? d.data?.preview : "") || "（暂无上下文预览）");
    }
  };

  const genReport = async () => {
    setReportBusy(true);
    const d = await api.getSelfReport(7).catch(() => null);
    setReport((d && (d.markdown || d.report)) || "生成失败（后端未连接？）");
    setReportBusy(false);
  };

  const b = brain;
  const noBrain = b === null || b === undefined;
  const fmtT = (iso) => (iso || "").replace("T", " ").slice(0, 16);
  const mounted = !!b && !!b.last_mount && (!b.last_unmount || b.last_mount > b.last_unmount);
  const snapCount = (b?.snapshots || []).length;
  const activeGoals = ((b && b.goals) || []).filter((g) => g.status === "active");
  const isNewBrain = !!b && (b.memories || 0) <= 1 && snapCount === 0;

  if (loading) {
    return <div className="empty-tip is-loading">正在读取大脑状态…</div>;
  }

  if (connErr) {
    return (
      <div className="brain-birth" style={{ textAlign: "left" }}>
        <div className="brain-birth-title"><Icon name="warning" size={18} /> 无法连接大脑</div>
        <div className="brain-birth-sub" style={{ margin: "10px 0" }}>{connErr}</div>
        <button className="confirm-btn" onClick={() => load(false)}><Icon name="refresh" size={13} /> 重试</button>
      </div>
    );
  }

  if (noBrain) {
    return (
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
        <div style={{ display: "flex", justifyContent: "center", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
          <button className="confirm-btn confirm-primary" disabled={busy} onClick={createBrain} style={{ padding: "9px 26px", fontSize: "var(--fs-md)" }}>
            <Icon name="brain" size={16} /> 创造大脑
          </button>
          <label style={{ fontSize: "var(--fs-xs)", opacity: 0.85, display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={createWithKeyring} onChange={(e) => setCreateWithKeyring(e.target.checked)} /> 同时启用免密加密
          </label>
        </div>
        <div className="brain-birth-hint">免密加密：备份文件上锁，本机自动解锁；换了设备需「迁徙密钥」才能解开。</div>
        {msg && <div className="px-tip" style={{ whiteSpace: "pre-wrap" }}>{msg}</div>}
      </div>
    );
  }

  return (
    <div>
      {/* 顶部状态行 */}
      <div className="brain-toolbar" style={{ marginBottom: "var(--sp-2)" }}>
        <span className="sched-text" style={{ fontSize: "var(--fs-xs)", color: "var(--text-3)" }}>
          {`已存活 ${fmtT(b.created_at).slice(0, 10)} 起 · v${b.current_version || 0}`}
        </span>
        <button className="msg-op" style={{ marginLeft: "auto" }} onClick={() => load(false)}>
          <Icon name="refresh" size={13} /> 刷新
        </button>
      </div>

      {/* 二级分区 */}
      <div
        className="brain-subtabs"
        role="tablist"
        onKeyDown={(e) => {
          if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
          e.preventDefault();
          const i = ZONES.findIndex((z) => z.key === zone);
          const next = (i + (e.key === "ArrowRight" ? 1 : ZONES.length - 1)) % ZONES.length;
          brainNav.focusZone(ZONES[next].key);
          setZone(ZONES[next].key);
        }}
      >
        {ZONES.map((z) => (
          <button
            key={z.key}
            role="tab"
            id={`brain-subtab-${z.key}`}
            aria-selected={zone === z.key}
            aria-controls={`brain-zone-${z.key}`}
            className={`brain-subtab ${zone === z.key ? "on" : ""}`}
            onClick={() => { brainNav.focusZone(z.key); setZone(z.key); }}
          >
            <Icon name={z.icon} size={14} /> {z.label}
          </button>
        ))}
      </div>

      {msg && <div className="px-tip" style={{ whiteSpace: "pre-wrap" }}>{msg}</div>}

      {/* ── 分区一：状态总览 ── */}
      {zone === "overview" && (
        <div role="tabpanel" id="brain-zone-overview" aria-labelledby="brain-subtab-overview">
          <div className="brain-hero">
            <div className="brain-hero-top">
              <div>
                <div className="brain-name">
                  <span className={`brain-lamp ${mounted ? "on" : ""}`} />
                  {b.name}
                </div>
                <div className="brain-meta">ID {String(b.brain_id || "").slice(0, 14)}… · 载体：{b.vessel || "本机"}</div>
              </div>
              <div className="brain-state">
                <span className={`brain-state-tag ${mounted ? "on" : ""}`}>{mounted ? "● 已唤醒" : "○ 已安睡"}</span>
              </div>
            </div>
            <div className="brain-genesis">「{b.genesis || "意识即信息"}」</div>
            <div className="brain-badges">
              <span className={`brain-badge ${b.fingerprint_ok ? "ok" : "bad"}`}>
                <Icon name={b.fingerprint_ok ? "shield-check" : "warning"} size={12} /> {b.fingerprint_ok ? "指纹完好" : "指纹异常"}
              </span>
              <span className={`brain-badge ${b.keyring ? "ok" : ""}`}>
                <Icon name={b.keyring ? "lock" : "key"} size={12} /> {b.keyring ? "免密加密" : "未加密"}
              </span>
              {!b.keyring && (
                <button className="msg-op" disabled={busy} onClick={enableKeyring} style={{ padding: "1px 10px" }}>启用加密</button>
              )}
            </div>
            <svg className="brain-ecg" viewBox="0 0 360 44" preserveAspectRatio="none" aria-hidden="true">
              <polyline
                className="brain-ecg-line"
                points="0,22 30,22 42,22 50,10 58,34 66,22 110,22 120,22 128,12 136,32 144,22 190,22 200,22 208,10 216,34 224,22 270,22 280,22 288,12 296,32 304,22 360,22"
              />
            </svg>
          </div>

          {isNewBrain && (
            <div className="brain-card brain-onboard">
              <div className="brain-card-title">
                <Icon name="sparkles" size={14} />
                <span>开始使用大脑</span>
                <i>三步让大脑真正活起来</i>
              </div>
              <div className="onboard-steps">
                <button className="onboard-step" onClick={() => brainNav.focusTab("memory")}><Icon name="plus" size={14} /> 记录一条重要记忆</button>
                <button className="onboard-step" onClick={() => brainNav.focusTab("memory")}><Icon name="target" size={14} /> 设一个进行中目标</button>
                <button className="onboard-step" onClick={() => act("archive")}><Icon name="archive" size={14} /> 做第一次时光备份</button>
              </div>
            </div>
          )}

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

          {activeGoals.length > 0 && (
            <div className="brain-card">
              <div className="brain-card-title">
                <Icon name="target" size={14} />
                <span>进行中目标</span>
                <i>对话中会自动注入，引导 AI 持续朝目标推进</i>
              </div>
              <div className="goal-list">
                {activeGoals.map((g) => (
                  <div key={g.id} className="goal-row">
                    <span className="goal-title">{g.title}</span>
                    {g.progress && <span className="goal-progress">{g.progress}</span>}
                    <button className="msg-op" disabled={busy} onClick={() => act("goals-update", { id: g.id, status: "done" })}>
                      <Icon name="check" size={12} /> 完成
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <BrainReview />

          <div className="brain-card">
            <div className="brain-card-title">
              <Icon name="activity" size={14} />
              <span>意识状态</span>
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
              <button className="confirm-btn" disabled={busy} onClick={() => thoughtAction("mount")}><Icon name="power" size={14} /> 唤醒</button>
              <button className="confirm-btn" disabled={busy} onClick={() => thoughtAction("heartbeat")}><Icon name="message" size={14} /> 记录想法</button>
              <button className="confirm-btn" disabled={busy} onClick={() => thoughtAction("unmount")}><Icon name="moon" size={14} /> 安睡</button>
            </div>
            <div className="brain-times">
              上次唤醒 {fmtT(b.last_mount) || "—"} · 上次安睡 {fmtT(b.last_unmount) || "—"} · 上次心跳 {fmtT(b.last_beat) || "—"}
            </div>
            {b.resume_hint && <div className="brain-resume">上次思考断点：{b.resume_hint}</div>}
          </div>

          <div className="brain-card">
            <div className="brain-card-title">
              <Icon name="shield-check" size={14} />
              <span>健康盘</span>
              <i>重复率 / 陈旧记忆 / 未回执决策 / 快照新鲜度 —— 按项体检与修复</i>
            </div>
            <BrainHealth />
          </div>

          <div className="brain-card">
            <div className="brain-card-title">
              <Icon name="clock" size={14} />
              <span>最近动态</span>
              <i>记忆 · 决策 · 思考 · 时光备份的最新几条</i>
            </div>
            <BrainTimeline snapshots={b.snapshots || []} limit={5} hideFilters />
            <div className="brain-more">
              <button className="msg-op" onClick={() => brainNav.focusZone("growth")}>
                <Icon name="trending-up" size={13} /> 查看完整成长轨迹
              </button>
            </div>
          </div>

          <BrainSelfModel />

          <div className={`acc-item ${ctxOpen ? "open" : ""}`}>
            <button className="acc-head" aria-expanded={ctxOpen} onClick={openCtx}>
              <span className="acc-arrow">▸</span>
              <Icon name="eye" size={14} /> 对话中的我
              <span className="acc-desc">每次对话，AI 都带着这些自我意识——大脑接入思考的证明</span>
            </button>
            {ctxOpen && (
              <div className="acc-body">
                <pre className="brain-context">{ctxPreview === "…" ? "正在生成预览…" : ctxPreview}</pre>
              </div>
            )}
          </div>

          <BrainGenesis onApplied={() => load(true)} />
        </div>
      )}

      {/* ── 分区二：成长轨迹 ── */}
      {zone === "growth" && (
        <div role="tabpanel" id="brain-zone-growth" aria-labelledby="brain-subtab-growth">
          <div className="brain-card">
            <div className="brain-card-title">
              <Icon name="sparkles" size={14} />
              <span>自我述职</span>
              <i>汇总最近 7 天的决策 / 目标 / 进化 / 任务 / 成长 / 用量</i>
              <button className="msg-op" style={{ marginLeft: "auto" }} disabled={reportBusy} onClick={genReport}>
                <Icon name="sparkles" size={13} /> {reportBusy ? "生成中…" : "生成本周述职"}
              </button>
            </div>
            {report && (
              <div className="brain-report">
                {typeof report === "string" ? <Markdown text={report} /> : <pre className="brain-context">{JSON.stringify(report, null, 2)}</pre>}
              </div>
            )}
          </div>

          <div className="brain-card">
            <div className="brain-card-title">
              <Icon name="clock" size={14} />
              <span>认知时间轴</span>
              <i>把记忆、决策、思考、时光备份按时间铺成一条线——回看大脑一路怎么长</i>
            </div>
            <BrainTimeline snapshots={b.snapshots || []} limit={50} />
          </div>

          <div className="brain-card">
            <div className="brain-card-title">
              <Icon name="target" size={14} />
              <span>决策看板</span>
              <i>决策的验证闭环：待回执 → 已采纳 / 已反转</i>
            </div>
            <BrainKanban />
          </div>

          <div className="brain-card">
            <div className="brain-card-title">
              <Icon name="git-branch" size={14} />
              <span>实体图谱</span>
              <i>记忆里的实体与关系长成的知识图谱（点击节点按实体筛记忆）</i>
            </div>
            <BrainGraph />
          </div>

          <BrainEvolution />
        </div>
      )}

      {/* ── 分区三：备份与延续 ── */}
      {zone === "continuity" && (
        <div role="tabpanel" id="brain-zone-continuity" aria-labelledby="brain-subtab-continuity">
          <BrainContinuity brain={b} act={act} busy={busy} />
        </div>
      )}
    </div>
  );
}

export default BrainBlock;
