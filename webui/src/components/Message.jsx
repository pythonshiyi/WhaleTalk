import React from "react";
import Markdown from "./Markdown.jsx";
import * as api from "../api.js";
import { unwrapLongText } from "../longTextUtil.js";
import { cleanForSpeech, speakText, stopSpeak, primeAudio } from "../ttsUtil.js";
import extractProducts from "../extractProducts.js";
import { splitSegments } from "../segmentNotes.js";
import { Icon } from "./icons.jsx";

import { silentWarn } from "../quiet.js";

// ── 小工具 ────────────────────────────────────────────
function fmtTokens(n) {
  const v = Number(n) || 0;
  if (v >= 10000) return `${Math.round(v / 1000)}k`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}
function fmtMs(ms) {
  const v = Number(ms) || 0;
  return v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`;
}
function fmtSize(n) {
  const v = Number(n) || 0;
  if (v >= 1048576) return `${(v / 1048576).toFixed(1)}MB`;
  if (v >= 1024) return `${Math.round(v / 1024)}KB`;
  return `${v}B`;
}
function baseName(p) {
  return String(p || "").split(/[\\/]/).pop() || String(p || "");
}
// 工具名 → 图标（按真实工具名前缀归类，未知用 settings 兜底）
function toolIconName(tool) {
  const t = String(tool || "");
  if (/^search_|^net_|^fetch_|^http|^web_|^browser_|^track_web/.test(t)) return "search";
  if (/^read_|^write_|^edit_|^file_|^list_dir|^search_local|^find_images|^asset_|^clipboard|^delete_file|^archive_|^extract_|^batch_rename/.test(t)) return "file";
  if (/^run_|^exec|^code|^dev_|^test|^verify_|^project_|^pip_install|^subagent/.test(t)) return "code";
  if (/^image_|^ocr_|^screen_|^chart_|^scan_|^qrcode|^make_gif/.test(t)) return "image";
  if (/^tts_|^speech_|^voice_/.test(t)) return "mic";
  if (/email|_mail|^send_webhook|^im_send/.test(t)) return "book";
  if (/^schedule_|^watch_|^task_|^run_workflow|^recall_/.test(t)) return "clock";
  if (/memory|self_profile|knowledge_/.test(t)) return "database";
  if (/^git/.test(t)) return "git-branch";
  if (/^dail|^mv_|^media_/.test(t)) return "play";
  return "settings";
}
// 产物类型 → (图标, 标签)
function kindOf(path) {
  const e = String(path || "").split(".").pop().toLowerCase();
  if (/^(png|jpg|jpeg|gif|webp|bmp|svg|ico)$/.test(e)) return { icon: "image", tag: "图片" };
  if (/^(mp4|mov|webm|mkv|avi|wav|mp3|flac)$/.test(e)) return { icon: "play", tag: "媒体" };
  if (/^(zip|rar|7z|tar|gz)$/.test(e)) return { icon: "package", tag: "压缩包" };
  if (/^(py|js|mjs|ts|tsx|jsx|vue|html|htm|css|json|yaml|yml|toml|ini|sql|md)$/.test(e)) return { icon: "code", tag: "代码/文本" };
  return { icon: "file", tag: "文档" };
}

// ── 用户消息附件（图片缩略图 + 文件 chip）──────────────
function UserAttachments({ images, files }) {
  const imgList = images || [];
  const fileList = files || [];
  const [loaded, setLoaded] = React.useState([]);
  const [zoom, setZoom] = React.useState(null);
  React.useEffect(() => {
    if (!imgList.length) { setLoaded([]); return undefined; }
    let alive = true;
    const urls = [];
    setLoaded([]);
    (async () => {
      const got = [];
      for (const p of imgList) {
        if (!alive) break;
        try {
          const r = await api.fetchFileBlob(p);
          if (!alive) return;
          if (r && r.ok && r.blob) {
            const u = URL.createObjectURL(r.blob);
            if (!alive) { URL.revokeObjectURL(u); return; }
            urls.push(u);
            got.push({ path: p, url: u });
            setLoaded([...got]);
          } else {
            got.push({ path: p, url: "", missing: true });
            setLoaded([...got]);
          }
        } catch (e) { silentWarn(e, "Message"); }
      }
    })();
    return () => {
      alive = false;
      urls.forEach((u) => { try { URL.revokeObjectURL(u); } catch (e) { /* noop */ } });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [images]);

  const shown = loaded.length ? loaded : imgList.map((p) => ({ path: p, url: "" }));
  if (!shown.length && !fileList.length) return null;
  return (
    <>
      {shown.length > 0 && (
        <div className="msg-att-imgs">
          {shown.map((it, i) => (
            <button key={it.path || i} type="button" className="msg-att-img"
              title={it.missing ? "图片已失效" : "查看大图"} onClick={() => it.url && setZoom(it.url)}>
              {it.url ? <img src={it.url} alt="" loading="lazy" /> : <span className="msg-att-img-load">…</span>}
            </button>
          ))}
        </div>
      )}
      {fileList.length > 0 && (
        <div className="msg-att-files">
          {fileList.map((f, i) => (
            <span className="msg-att-file" key={f.path || i} title={f.path}>
              <Icon name="file" size={13} />
              <span className="msg-att-file-name">{f.name || baseName(f.path)}</span>
              {f.size ? <span className="msg-att-file-size">{fmtSize(f.size)}</span> : null}
              <button type="button" className="msg-att-file-open" title="用系统程序打开" aria-label="打开文件"
                onClick={() => f.path && api.openFile(f.path).catch(() => {})}><Icon name="external" size={12} /></button>
            </span>
          ))}
        </div>
      )}
      {zoom && (
        <div className="msg-img-lightbox" role="dialog" aria-label="图片预览" onClick={() => setZoom(null)}>
          <img src={zoom} alt="" />
        </div>
      )}
    </>
  );
}

// ── 思考块（生成中展开+脉冲；结束自动收起）─────────────
function ThinkBlock({ text, streaming }) {
  const [open, setOpen] = React.useState(streaming);
  React.useEffect(() => { if (!streaming) setOpen(false); }, [streaming]);
  const chars = (text || "").length;
  const meta = chars >= 1000 ? `${(chars / 1000).toFixed(1)}k 字` : `${chars} 字`;
  return (
    <div className={`think-block ${open ? "think-open" : ""} ${streaming ? "think-live" : ""}`}>
      <div className="think-head" role="button" tabIndex={0} aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); } }}>
        <span className="think-dot" />
        <span>思考过程</span>
        {streaming
          ? <span className="think-streaming">进行中</span>
          : <span className="think-meta">{meta}</span>}
        <svg className="think-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </div>
      {open && <div className="think-body">{text}</div>}
    </div>
  );
}

// ── 计划（来自 plan_request 的待执行工具计划）───────────
function PlanBlock({ plan, tools }) {
  if (!plan || !plan.length) return null;
  const byName = (name) => (tools || []).filter((t) => t && t.tool === name);
  let done = 0;
  const items = plan.map((s, i) => {
    const hits = byName(s.name);
    let st = "wait";
    if (hits.some((t) => t.status === "failed")) st = "fail";
    else if (hits.some((t) => t.status === "running")) st = "run";
    else if (hits.some((t) => t.status === "done")) { st = "done"; done += 1; }
    return { ...s, st, i };
  });
  return (
    <div className="plan-block">
      <div className="plan-head"><Icon name="list" size={13} /><span>执行计划</span>
        <span className="plan-cnt">{done}/{plan.length}</span></div>
      <ol className="plan-list">
        {items.map((it, i) => (
          <li key={i} className={`plan-item plan-${it.st}`}>
            <span className="plan-node">{it.st === "done" ? "✓" : it.st === "fail" ? "✕" : it.i + 1}</span>
            <span className="plan-name">{it.name}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ── 单个步骤 ─────────────────────────────────────────
function Step({ t, index }) {
  const status = t.status === "failed" ? "fail" : t.status === "running" ? "run" : "done";
  const [open, setOpen] = React.useState(status === "fail");
  const argsText = React.useMemo(() => {
    try { return JSON.stringify(t.args || {}, null, 2); } catch (e) { return String(t.args || ""); }
  }, [t.args]);
  const argsSummary = React.useMemo(() => {
    const a = t.args || {};
    const parts = Object.entries(a).slice(0, 2).map(([k, v]) => `${k}=${String(v).length > 24 ? String(v).slice(0, 24) + "…" : String(v)}`);
    return parts.join("  ") || "";
  }, [t.args]);
  const result = t.result == null ? "" : String(t.result);
  return (
    <div className={`step step-${status} ${open ? "step-open" : ""}`}>
      <span className="step-node">{status === "done" ? "✓" : status === "fail" ? "✕" : index}</span>
      <div className="step-row" role="button" tabIndex={0} aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); } }}>
        <span className="step-ic"><Icon name={toolIconName(t.tool)} size={13} /></span>
        <span className="step-name">{t.tool}</span>
        {argsSummary && <span className="step-args">{argsSummary}</span>}
        <span className="step-right">
          {status === "run" ? <span className="step-run">执行中…</span>
            : t.duration && t.duration !== "—" ? <span className="step-dur">{t.duration}s</span> : null}
          <span className="step-chev">{open ? "收起" : "详情"}</span>
        </span>
      </div>
      {open && (
        <div className="step-detail">
          {argsText !== "{}" && <div className="sd-args"><span className="sd-lbl">参数</span>{argsText}</div>}
          {result && <div className="sd-result"><span className="sd-lbl">结果</span>{result}</div>}
          {!result && status === "run" && <div className="sd-result"><span className="sd-lbl">结果</span>等待中…</div>}
          <div className="sd-ops">
            <button className="msg-op" onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(`# ${t.tool}\n参数：${argsText}\n结果：\n${result}`).catch(() => {}); }}>复制</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── 过程带（内联时间线）───────────────────────────────
function ProcessRail({ tools, streaming, onFocusActivity }) {
  const list = (tools || []).filter((t) => t && t.tool);
  const [open, setOpen] = React.useState(streaming);
  React.useEffect(() => { if (!streaming) setOpen(false); }, [streaming]);
  if (!list.length) return null;
  const total = list.length;
  const done = list.filter((t) => t.status === "done").length;
  const failed = list.filter((t) => t.status === "failed").length;
  const running = list.filter((t) => t.status === "running").length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const durSum = list.reduce((n, t) => n + (Number(String(t.duration || "").replace(/[^\d.]/g, "")) || 0), 0);
  return (
    <div className={`rail ${open ? "rail-open" : ""}`}>
      <div className="rail-head" role="button" tabIndex={0} aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); } }}>
        <span className="rail-ic"><Icon name="settings" size={13} /></span>
        <span className="rail-title">执行过程</span>
        <span className="rail-sub">
          {streaming ? `${done}/${total}` : `${total} 步`}
          {failed ? ` · ${failed} 失败` : ""}
          {durSum > 0 ? ` · 共 ${durSum.toFixed(1)}s` : ""}
        </span>
        <span className="rail-prog"><span className="rail-track"><i style={{ width: `${pct}%` }} /></span>
          {streaming && running ? <span className="rail-live" /> : null}</span>
        <svg className="rail-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9l6 6 6-6" /></svg>
      </div>
      {open && (
        <div className="rail-body">
          {list.map((t, i) => <Step key={t.id || i} t={t} index={i + 1} />)}
          {onFocusActivity && (
            <button className="rail-more" onClick={onFocusActivity}>在右侧「活动」查看全部与筛选 ▸</button>
          )}
        </div>
      )}
    </div>
  );
}

// ── 产物（从工具参数/结果/正文中提取，回合内联）──────────
function Artifacts({ paths }) {
  if (!paths || !paths.length) return null;
  return (
    <div className="ta-wrap">
      <div className="ta-head"><Icon name="package" size={13} /><span>本回合产物</span><span className="ta-cnt">{paths.length}</span></div>
      <div className="ta-grid">
        {paths.map((p) => {
          const k = kindOf(p);
          return (
            <div className="ta-item" key={p} title={p}>
              <span className="ta-ic"><Icon name={k.icon} size={14} /></span>
              <span className="ta-meta"><span className="ta-name">{baseName(p)}</span><span className="ta-tag">{k.tag}</span></span>
              <span className="ta-ops">
                <button className="msg-op" title="打开" onClick={() => api.openFile(p).catch(() => {})}>打开</button>
                <button className="msg-op" title="定位到文件夹" onClick={() => api.openDir(p).catch(() => {})}>定位</button>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── 正文：按「分段锚点」在多段输出间接入因果标注 ──────────
function SegmentedBody({ text, segs, streaming }) {
  const chunks = React.useMemo(() => splitSegments(text, segs), [text, segs]);
  return (
    <>
      {chunks.map((c, i) => (
        <React.Fragment key={i}>
          {c.note != null && <div className="seg-note"><Icon name="refresh" size={11} /><span>基于第 {c.note} 步结果</span></div>}
          {c.text && <Markdown text={c.text} deferCode={streaming && i === chunks.length - 1} />}
        </React.Fragment>
      ))}
    </>
  );
}

// ── 悬停操作条 ────────────────────────────────────────
function Message({ msg, onResend, onStar, onPin, onQuote, onFork, onEdit, onRegenerate, onContinue, onFocusActivity }) {
  const [copied, setCopied] = React.useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(unwrapLongText(msg.text || ""));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch (e) { silentWarn(e, "Message"); }
  };

  const [speaking, setSpeaking] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState("");
  const toggleSpeak = () => {
    if (speaking || loading) { stopSpeak(); setLoading(false); setSpeaking(false); return; }
    primeAudio();
    if (!cleanForSpeech(unwrapLongText(msg.text))) return;
    stopSpeak();
    setErr("");
    setLoading(true);
    speakText(msg.text, {}, {
      onSpeak: () => { setLoading(false); setSpeaking(true); },
      onDone: () => { setLoading(false); setSpeaking(false); },
      onError: (e) => { setErr(e && e.message ? e.message : "朗读失败"); setTimeout(() => setErr(""), 4000); setLoading(false); setSpeaking(false); },
    });
  };

  const time = msg.time || "";
  const isStarred = msg.starred;
  const isPinned = msg.pinned;

  // 产物：仅在本回合结束后提取（避免流式每帧重算），来源=工具参数/结果 + 正文
  const artifacts = React.useMemo(() => {
    if (msg.role !== "assistant" || msg.streaming) return [];
    if (!(msg.tools && msg.tools.length) && !msg.text) return [];
    // 只扫「产出型工具的结果 + 正文」：工具参数里的路径多为*输入*（如 read_file path=用户文件），
    // 只读型工具的结果（read_file/list_dir/search_*/ocr_*…）也常回显输入路径——一并排除，
    // 避免把输入误当本回合产物。
    const READONLY = /^(read_|list_|get_|search_|find_|query_|show_|environment_|usage_|self_report|capability_|knowledge_|recall_|clipboard_get|database_query|pdf_extract|pdf_visual_check|ocr_|image_understand|screen_|chart_read|net_diagnose|verify_|project_map|find_symbol|code_lookup|list_dir|list_processes)/;
    const src = (msg.tools || [])
      .filter((t) => t && t.tool && !READONLY.test(t.tool))
      .map((t) => (t.result == null ? "" : String(t.result))).join("\n")
      + "\n" + (msg.text || "");
    return extractProducts(src, 12);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [msg.role, msg.streaming, msg.tools, msg.text]);

  if (msg.role === "user") {
    const hasAtt = (msg.images && msg.images.length) || (msg.files && msg.files.length);
    return (
      <div className="msg msg-user">
        <div className="msg-user-bubble">
          {isPinned && <span className="msg-flag msg-flag-pin" aria-hidden="true"><Icon name="pin" size={12} fill="currentColor" /></span>}
          {hasAtt ? <UserAttachments images={msg.images} files={msg.files} /> : null}
          {msg.text}
        </div>
        <div className="msg-user-avatar">我</div>
        <div className="msg-ops">
          {time && <span className="msg-time">{time}</span>}
          <button className="msg-op" title="复制" aria-label="复制" onClick={copy}><Icon name="copy" size={14} /></button>
          <button className="msg-op" title={isPinned ? "取消固定" : "固定（压缩时保留进摘要）"} aria-label="固定" onClick={() => onPin && onPin()}><Icon name="pin" size={14} fill={isPinned ? "currentColor" : "none"} /></button>
          <button className="msg-op" title="从此分叉为新会话" aria-label="分叉" onClick={() => onFork && onFork()}><Icon name="git-branch" size={14} /></button>
          <button className="msg-op" title="编辑并重发" aria-label="编辑" onClick={() => onEdit && onEdit()}><Icon name="pencil" size={14} /></button>
          <button className="msg-op" title="引用此消息回复" aria-label="引用" onClick={() => onQuote && onQuote()}><Icon name="quote" size={14} /></button>
          <button className="msg-op" title="重新发送（保留原附件）" aria-label="重新发送" onClick={() => onResend && onResend(msg.text, [
            ...(msg.images || []).map((p) => ({ path: p, kind: "image" })),
            ...(msg.files || []).map((f) => ({ path: f.path, name: f.name, size: f.size, kind: "file" })),
          ])}><Icon name="rotate" size={14} /></button>
        </div>
      </div>
    );
  }

  const stepCount = (msg.tools || []).filter((t) => t && t.tool).length;
  const failedCount = (msg.tools || []).filter((t) => t && t.status === "failed").length;
  const totalMs = msg.metrics && msg.metrics.total_ms;

  return (
    <div className="msg msg-assistant">
      <div className="msg-avatar" aria-hidden="true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12c1.5-4 4-6 7-6 3.5 0 5.5 2 9 2 1.6 0 2.8-.6 4-1.5-1 3-3 4.5-5 4.8.6 1.4.9 2.9.9 4.5 0 .8-.1 1.6-.3 2.3-1-.4-1.8-1-2.2-1.8-.9 1-2.4 1.7-4.2 1.7s-3.3-.7-4.2-1.7c-.4.8-1.2 1.4-2.2 1.8A11 11 0 015 15c0-1.6.3-3.1.9-4.5C4.7 10.2 3.3 8.7 3 12z" />
        </svg>
      </div>
      <div className="msg-body">
        <div className="msg-head">
          <span className="msg-role">助手</span>
          {msg.streaming && <span className="turn-badge turn-badge-run"><span className="turn-live" />生成中</span>}
          {!msg.streaming && msg.error && <span className="turn-badge turn-badge-err">生成中断</span>}
          {!msg.streaming && !msg.error && stepCount > 0 && (
            <span className={`turn-badge ${failedCount ? "turn-badge-warn" : "turn-badge-ok"}`}>
              {failedCount ? `⚠ 完成 · ${failedCount} 步失败` : `✓ 已完成 · ${stepCount} 步`}
            </span>
          )}
          {isStarred && <span className="msg-flag-emoji" title="已收藏" aria-hidden="true"><Icon name="star" size={13} fill="currentColor" /></span>}
          {isPinned && <span className="msg-flag-emoji" title="已固定" aria-hidden="true"><Icon name="pin" size={13} fill="currentColor" /></span>}
          {time && <span className="msg-time">{time}</span>}
          {!msg.streaming && totalMs > 0 && <span className="msg-time">· ⏱ {fmtMs(totalMs)}</span>}
        </div>

        <PlanBlock plan={msg.plan} tools={msg.tools} />
        {msg.think && <ThinkBlock text={msg.think} streaming={msg.streaming} />}
        {stepCount > 0 && <ProcessRail tools={msg.tools} streaming={msg.streaming} onFocusActivity={onFocusActivity} />}
        {msg.text && <SegmentedBody text={msg.text} segs={msg.segs} streaming={msg.streaming} />}
        {msg.streaming && <span className="caret" />}
        {!msg.streaming && <Artifacts paths={artifacts} />}

        {msg.error && (
          <div className="msg-error" role="alert">
            <span className="me-icon">⚠</span>
            <span className="me-text">{msg.error}</span>
            {onRegenerate && <button className="me-retry" onClick={() => onRegenerate()}>重试</button>}
          </div>
        )}

        {!msg.streaming && msg.text && (msg.usage || msg.metrics) && (() => {
          const mt = msg.metrics || {};
          const u = (mt.prompt || mt.completion) ? mt : (msg.usage || {});
          const cachePct = u.prompt > 0 ? Math.round((u.cache_hit || 0) / u.prompt * 100) : 0;
          return (
            <div className="msg-metrics" title="本轮用量与速率：输入/输出 tokens · 输出速率 · 首字延迟 · 总耗时">
              {(u.prompt || u.completion) ? <span className="mm-item">↑{fmtTokens(u.prompt)} ↓{fmtTokens(u.completion)}</span> : null}
              {cachePct > 0 && <span className="mm-item mm-cache">缓存 {cachePct}%</span>}
              {mt.tps > 0 && <span className="mm-item mm-tps">⚡ {mt.tps} tok/s</span>}
              {mt.ttft_ms != null && <span className="mm-item">首字 {fmtMs(mt.ttft_ms)}</span>}
              {mt.total_ms > 0 && <span className="mm-item">耗时 {fmtMs(mt.total_ms)}</span>}
              {mt.interrupted && <span className="mm-item mm-interrupted">已中断</span>}
            </div>
          );
        })()}

        {!msg.streaming && msg.text && (
          <div className="msg-ops">
            <button className="msg-op" title="复制回复" aria-label="复制" onClick={copy}>
              <Icon name={copied ? "check" : "copy"} size={14} />
            </button>
            <button className="msg-op" title={err ? ("朗读失败：" + err) : loading ? "正在合成语音…" : speaking ? "停止朗读" : "朗读回复（服务端合成，跟随语音设置）"} aria-label="朗读" style={err ? { color: "var(--danger-text)" } : undefined} onClick={toggleSpeak}>
              <Icon name={err ? "warning" : speaking ? "stop" : "volume"} size={14} />
            </button>
            <button className="msg-op" title={isStarred ? "取消收藏" : "收藏"} aria-label="收藏" onClick={() => onStar && onStar()}>
              <Icon name="star" size={14} fill={isStarred ? "currentColor" : "none"} />
            </button>
            <button className="msg-op" title="引用此消息回复" aria-label="引用" onClick={() => onQuote && onQuote()}><Icon name="quote" size={14} /></button>
            <button className="msg-op" title="重新生成（旧版存变体）" aria-label="重新生成" onClick={() => onRegenerate && onRegenerate()}><Icon name="rotate" size={14} /></button>
            <button className="msg-op" title="继续生成（Beta 续写）" aria-label="继续" onClick={() => onContinue && onContinue()}><Icon name="play" size={14} /> 继续</button>
            <button className="msg-op" title="从此分叉为新会话" aria-label="分叉" onClick={() => onFork && onFork()}><Icon name="git-branch" size={14} /></button>
          </div>
        )}
      </div>
    </div>
  );
}
export default React.memo(Message, (a, b) => a.msg === b.msg);
