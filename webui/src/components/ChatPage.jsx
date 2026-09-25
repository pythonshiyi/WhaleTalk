import React from "react";
import { createPortal } from "react-dom";
import Message from "./Message.jsx";
import { capLiveWindow, findToolCard, makePatchLast, trimHistory } from "../msgUpdates.js";
import Composer from "./Composer.jsx";
import SessionList from "./SessionList.jsx";
import ContextPanel from "./ContextPanel.jsx";
import StatusBar from "./StatusBar.jsx";
import { Icon } from "./icons.jsx";
import ConfirmGate from "./ConfirmGate.jsx";
import AuxPanel from "./AuxPanel.jsx";
import { BatchPanel, CmdPanel, TimelinePanel, FimPanel, VariantPanel, SearchPanel, StarPanel } from "./ChatPanels.jsx";
import { FlashContext, ToastContext } from "./FlashToast.jsx";
import { ModeContext, DisplayContext } from "../App.jsx";
import * as api from "../api.js";
import { unwrapLongText } from "../longTextUtil.js";
import { buildHistory, withAttachRefs, withSegmentBreak } from "../chatChain.js";
import { speakText, getVoiceConfig, onSpeechState, stopSpeak, resumeSpeak } from "../ttsUtil.js";
import { nowClock } from "../timeFmt.js";
import formatToolResult from "../formatToolResult.js";
import extractProducts from "../extractProducts.js";

import { silentWarn } from "../quiet.js";
import { confirmDialog } from "../dialog.js";

// 响应式：窄屏下侧栏/面板以「浮层抽屉」呈现（见 app.css 的 @media 规则）。
// 初次挂载即按视口决定默认开合，避免窄屏一进来就被抽屉+蒙层盖住对话。
// SSR 安全：window 缺失时按宽屏处理。
function mq(query) {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(query).matches
    : false;
}

// 工具参数中的「路径类」键：这些键的值若为绝对路径，视为本会话产物来源。
const PRODUCT_PATH_KEYS = ["path", "output", "file", "filename", "dst", "dest", "target", "dir", "out"];

// 落盘用消息链：与 buildMessageChain 结构一致，但额外携带 usage/metrics（速率统计），
// 且**仅用于保存**——绝不送入模型（避免未知字段导致 API 400）。
function toSaveMessages(msgs) {
  const out = [];
  for (const m of msgs || []) {
    if (m.local) continue; // 本地提示消息（插件执行结果等）：仅 UI 展示，不落盘、不进模型
    if (m.role === "user") {
      const um = { role: "user", content: unwrapLongText(m.text || "") };
      // 附件与正文分开存储：content 保持纯文本（回显不乱），images/files 供重载回显 +
      // 继续对话时 buildMessageChain 重新拼出附件引用。绝不把 files 送进模型字段。
      if (m.images && m.images.length) um.images = m.images;
      if (m.files && m.files.length) {
        um.files = m.files
          .filter((f) => f && f.path)
          .map((f) => ({ path: f.path, name: f.name, size: f.size }));
      }
      out.push(um);
    } else if (m.role === "assistant") {
      const am = { role: "assistant", content: unwrapLongText(m.text || "") };
      if (m.think) am.reasoning_content = m.think;
      if (m.segs && m.segs.length) am.segs = m.segs;
      if (m.usage) am.usage = m.usage;
      if (m.metrics) am.metrics = m.metrics;
      if (m.tools && m.tools.length) {
        am.tool_calls = m.tools.map((t, i) => ({
          id: `call_${i}`,
          type: "function",
          function: { name: t.tool, arguments: JSON.stringify(t.args || {}) },
        }));
        out.push(am);
        m.tools.forEach((t, i) => {
          out.push({ role: "tool", tool_call_id: `call_${i}`, name: t.tool, content: String(t.result || "").slice(0, 4000) });
        });
      } else {
        out.push(am);
      }
    }
  }
  return out;
}

// 网关会话 id（不透明、按会话稳定）：供 OpenCode Go/Zen 的 x-opencode-session
// 做路由/缓存亲和；与业务会话 id 无关，仅本标签页内有效。
function newGwSessionId() {
  try {
    if (window.crypto && window.crypto.randomUUID) return "wt-" + window.crypto.randomUUID();
  } catch { /* 忽略 */ }
  return "wt-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
}

// 一次生成的稳定标识：后端据此把生成交给独立作业——切换页面/多标签页都不会
// 中断，同 id 的重复请求视为订阅同一作业（不会重复跑一遍生成）。每「轮」生成一个。
function newStreamId() {
  try {
    if (window.crypto && window.crypto.randomUUID) return "st-" + window.crypto.randomUUID();
  } catch { /* 忽略 */ }
  return "st-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
}

// 后端断连横幅：心跳探测到服务不可用时置顶提示，恢复后自动消失；带手动重连入口
export function BackendBanner() {
  const [down, setDown] = React.useState(false);
  const [retrying, setRetrying] = React.useState(false);
  React.useEffect(() => {
    // 保存停止函数并在卸载时清理，避免 StrictMode/卸载后遗留 5s 心跳定时器
    const stop = api.watchBackend(5000, (ok) => setDown(!ok));
    return () => { if (typeof stop === "function") stop(); };
  }, []);
  const retry = () => {
    setRetrying(true);
    (async () => {
      try {
        const ok = await api.probeBackendHealth();
        setDown(!ok);
      } catch (e) { silentWarn(e, "ChatPage"); }
      setRetrying(false);
    })();
  };
  if (!down) return null;
  return (
    <div className="backend-banner">
      <span>⚠️ 后端服务未连接——请启动本机 WhaleTalk 服务，或点击右侧重试</span>
      <button onClick={retry} disabled={retrying}>
        {retrying ? "重连中…" : "立即重连"}
      </button>
    </div>
  );
}

// 全局朗读状态浮标：合成中/开口中/句间呼吸/环境暂停——均有可见反馈，点击可停/续
function SpeakingPill() {
  const [st, setSt] = React.useState({ speaking: false, loading: false, phase: "idle" });
  React.useEffect(() => onSpeechState(setSt), []);
  if (!st.speaking && !st.loading && st.phase !== "paused") return null;
  const paused = st.phase === "paused";
  const breathing = st.phase === "breath";
  const label = paused
    ? "⏸ 已暂停（环境声）· 点击继续"
    : breathing ? "🔊 朗读中…" : (st.speaking ? "🔊 正在朗读 · 点击停止" : "⏳ 正在合成语音…");
  return (
    <div
      className={`speak-pill ${paused ? "paused" : ""}`}
      onClick={() => (paused ? resumeSpeak() : stopSpeak())}
      title={paused ? "点击继续朗读" : "点击停止朗读"}
    >
      {label}
    </div>
  );
}

// ── 真实后端流式对话 ────────────────────────────────
// P0-3：17 个位置参数改为 options 对象——调用点自描述、加参数不再数位；
// 内部闭包语义与重构前完全一致（effect 仍只依赖 [busy]，所有依赖经对象解构注入）。
function useBackendChat({
  busy, setBusy, setMsgs, msgsRef,
  pendingRef, historyRef,
  chatMode, webSearch, quietMode,
  onFinished, stopSignalRef, onPrompt, setGenState,
  continueRef, sessionIdRef, gwSessionRef, streamIdRef, costConfirmedRef, onSession, setGenTps, toast,
}) {

  const updateMsgs = (fn) => {
    setMsgs(fn);
    msgsRef.current = typeof fn === "function" ? fn(msgsRef.current) : fn;
  };
  const stopRef = React.useRef(false);
  // ── SSE 高频增量合并（P2）：reasoning/content 每帧可达几十次回调，
  // 每次都 setState 会触发整树重渲染；改为 rAF 帧批量 flush。
  const batchRef = React.useRef({ think: "", text: "", gen: "" });
  const rafRef = React.useRef(null);

  React.useEffect(() => {
    if (!busy) return;
    let alive = true;
    stopRef.current = false;
    const userText = pendingRef.current.text;
    const images = pendingRef.current.images || [];
    const files = pendingRef.current.files || [];
    // 一次性消费续写意图：effect 启动即取走并清零，杜绝任何遗留标记泄漏到下一次发送。
    // 再对目标 idx 做有效性校验——标记与目标消息不匹配时一律按正常发送处理（宁可不续写，
    // 也绝不把用户的正常消息吞成续写：那会表现为「输入什么都不显示、AI 收不到」）。
    const _cont = (continueRef && continueRef.current) || { active: false, idx: -1 };
    continueRef.current = { active: false, idx: -1 };
    const _carr = msgsRef.current || [];
    const isContinue = !!(_cont.active && Number.isInteger(_cont.idx) && _cont.idx >= 0
      && _carr[_cont.idx] && _carr[_cont.idx].role === "assistant");
    const continueIdx = isContinue ? _cont.idx : -1;
    // 本轮生成的稳定标识（effect 重跑复用同一个，避免重复开新作业）
    if (!streamIdRef.current) streamIdRef.current = newStreamId();
    const streamId = streamIdRef.current;

    // 非续写分支统一走不可变更新（见 makePatchLast 的说明）
    const patchLast = makePatchLast(updateMsgs);

    // 取「当前流式消息」的最新快照。
    // 改用不可变更新后，闭包里的 msg 永远停在初始对象（内容为空），不能再拿它
    // 去落盘；而 updateMsgs 会同步更新 msgsRef 镜像，故从镜像取最后一条即为终态。
    const currentMsg = () => {
      const arr = msgsRef.current || [];
      return arr.length ? arr[arr.length - 1] : null;
    };

    if (isContinue) {
      updateMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, streaming: true } : x)));
    } else {
      const t0 = nowClock();
      // 不再保留局部 msg 变量：后续一律经 patchLast 不可变更新，
      // 落盘用 currentMsg() 从实时镜像取终态（见上）。
      updateMsgs((m) => [...m,
        { role: "user", text: userText, time: t0, ...(images.length ? { images } : {}), ...(files.length ? { files } : {}) },
        { role: "assistant", think: "", tools: [], text: "", streaming: true, time: t0 }]);
    }
    if (!stopSignalRef.current || stopSignalRef.current.signal.aborted) stopSignalRef.current = new AbortController();

    const targetIdx = () => (isContinue ? continueIdx : -1);

    // rAF 批量 flush：一帧内累积的 thinking/content 增量合并为一次状态更新
    const flushBatch = () => {
      rafRef.current = null;
      const b = batchRef.current;
      batchRef.current = { think: "", text: "", gen: "" };
      if (!b.think && !b.text && !b.gen) return;
      if (isContinue) {
        updateMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, think: (x.think || "") + b.think, text: (x.text || "") + b.text } : x)));
      } else {
        patchLast((x) => ({
          ...x,
          think: (x.think || "") + b.think,
          text: (x.text || "") + b.text,
        }));
      }
      if (b.gen) setGenState({ on: true, text: b.gen });
    };
    const scheduleBatch = (patch) => {
      batchRef.current.think += patch.think || "";
      batchRef.current.text += patch.text || "";
      if (patch.gen) batchRef.current.gen = patch.gen;
      if (!rafRef.current) rafRef.current = requestAnimationFrame(flushBatch);
    };
    const flushNow = () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
        flushBatch();
      }
    };

    // ── 自动朗读（跟随设置 voice_config.auto_mode：off/on）──
    let voiceSettings = null;
    getVoiceConfig().then((v) => { voiceSettings = v; });
    let acc = "";              // 本轮流式全文累加
    // 段间分隔：一轮里 AI 会跨多个工具轮次输出多段文字（各自是一条 assistant 消息），
    // 前端合并进同一气泡时若不加分隔会粘成一整块。规则：某段之后发生过工具调用，
    // 则下一段文字开头补一个空行（Markdown 段落分隔）——观感分段，且随文本持久化。
    let needSegBreak = false;
    // 关键：自动朗读不在流式过程中逐句读（那会因流式重切分/缓冲累积导致同段被读多次）。
    // 统一改为「本条回复生成完成时，把最终稳定文本整段读一次」→ 每条消息恰好朗读一次，绝不重复。
    const feedAuto = () => { /* 流式过程不朗读：避免增量竞态导致重复 */ };
    const maybeAutoReadOnce = () => {
      if (!voiceSettings || voiceSettings.auto_mode === "off" || !acc.trim()) return;
      stopSpeak();
      speakText(acc, voiceSettings, {}).catch(() => {});
    };

    (async () => {
      let done = false;
      let needsConfirm = false;   // 后端费用确认闸拦截：不落盘、不提示完成，等待确认重发
      const finish = (ok) => {
        if (done || !alive) return;
        flushNow();  // 冲刷 rAF 残余增量（防末尾半截内容丢失）
        done = true;
        // 本轮结束即复位续写标记。否则一旦续写被「停止/报错/切会话」打断，
        // continueRef 会永久停留 active=true，导致之后每次正常发送都被当成续写
        // （不追加用户消息、请求沿用旧 continue 前缀）——表现为「输入什么都不显示、
        // AI 也收不到，只能点继续」。
        continueRef.current = { active: false, idx: -1 };
        // 费用确认：移除空的 assistant 占位、不保存、不弹「回复完成」；
        // 用户确认后由 resendLastUser 以 cost_confirmed 重发。
        if (needsConfirm) {
          if (costConfirmedRef) costConfirmedRef.current = false;
          if (!isContinue) updateMsgs((m) => (m.length && m[m.length - 1].role === "assistant" ? m.slice(0, -1) : m));
          updateMsgs((m) => m.map((x, i) => (i === (isContinue ? continueIdx : m.length - 1) ? { ...x, streaming: false } : x)));
          setBusy(false);
          setGenState({ on: false, text: "" });
          setGenTps(0);
          return;
        }
        updateMsgs((m) => m.map((x, i) => (i === (isContinue ? continueIdx : m.length - 1) ? { ...x, streaming: false } : x)));
        setBusy(false);
        setGenState({ on: false, text: "" });
        setGenTps(0);
        // 自动朗读收尾：本条回复完成后，整段恰好朗读一次（sentence/full 均整段读，杜绝重复）
        try {
          maybeAutoReadOnce();
        } catch (e) { silentWarn(e, "ChatPage"); }
        // msg 传实时镜像的最后一条（不可变更新后闭包 msg 已非终态）
        onFinished && onFinished({ userText, msg: currentMsg(), ok, isContinue, images, files });
      };
      try {
        // 续写：从原始消息（msgsRef 镜像）重建到 continueIdx 为止——historyRef 里可能
        // 是已构建的链，再 buildMessageChain 一次会把 assistant 变空、tool 消息丢光。
        // 截断统一走 trimHistory（按回合边界、保底保留最后一个回合）——旧的
        // `slice(-80)` 会把工具密集的一轮从中间腰斩，导致模型彻底失忆。
        const rawHistory = isContinue
          ? buildHistory((msgsRef.current || []).slice(0, continueIdx + 1), chatMode)
          : (historyRef.current || []);
        const history = trimHistory(rawHistory);
        // 纯图片（无文字）时给一句占位，避免部分网关拒绝空 content
        const userContent = withAttachRefs(userText, files) || (images.length ? "[图片]" : "");
        // 费用确认闸：本次请求是否已带用户确认；读取后立即复位（只作用于这一次发送）
        const costConfirmed = !!(costConfirmedRef && costConfirmedRef.current);
        if (costConfirmedRef) costConfirmedRef.current = false;
        await api.streamChat(
          {
            messages: isContinue ? history : [...history, { role: "user", content: userContent, ...(images.length ? { images } : {}) }],
            // 不传 thinking：后端 _chat_kwargs 使用 config.json 的 thinking（控制台/设置选择的档位即时生效）
            mode: chatMode,
            toolsEnabled: chatMode === "task",
            // 对话模式联网开关：仅对话模式生效（后端 pure_chat 分支注入 search_web）
            web_search: chatMode === "dialog" && webSearch,
            // 纯净对话总开关：开启后后端停止注入长期记忆/核心自我/大脑，也不自动回写记忆
            quiet_mode: quietMode,
            // 已有会话继续对话时带上会话 id：后端生成完成后自动落盘（前端卸载/断连不丢结果）
            session_id: (sessionIdRef && sessionIdRef.current) || undefined,
            // 本次生成的稳定标识：后端把生成交给独立作业（切页/关标签/多标签页都不打断），
            // 同 id 重复请求视为订阅同一作业，不会重复跑生成。
            stream_id: streamId,
            // 会话名（后端无人订阅时兜底落盘用）
            session_name: (userText || "").replace(/\s+/g, " ").slice(0, 24) || undefined,
            // 网关会话 id（OpenCode Go/Zen 的 x-opencode-session）：每个会话稳定，
            // 供网关做路由/缓存亲和；与业务 session_id 解耦（后端不用于落盘）。
            gw_session: (gwSessionRef && gwSessionRef.current) || undefined,
            continue_prefix: isContinue,
            cost_confirmed: costConfirmed,
          },
          {
            onReasoning: (t) => {
              if (!alive || stopRef.current) return;
              scheduleBatch({ think: t, gen: "🤔 思考中…" });
            },
            onContent: (t) => {
              if (!alive || stopRef.current) return;
              // 新一段（前面已出过文字、且其间调用了工具）：补段间空行，避免多轮输出粘连。
              // 同时记录「分段锚点」{i: 段起点字符偏移, n: 该段之前的工具步数}——
              // 供渲染层在段间插入「↳ 基于第 N 步结果」，把输出与步骤的因果显式化。
              const wasBreak = needSegBreak;
              needSegBreak = false;
              const segIndex = acc.length;
              const chunk = withSegmentBreak(acc, t, wasBreak);
              if (wasBreak && !isContinue) {
                const _cur = currentMsg();
                const stepN = _cur && _cur.tools ? _cur.tools.length : 0;
                patchLast((x) => ({ ...x, segs: [...(x.segs || []), { i: segIndex, n: stepN }] }));
              }
              acc += chunk;
              feedAuto();
              scheduleBatch({ text: chunk, gen: "⏳ 等待模型响应…" });
            },
            onToolStart: ({ name, args, id }) => {
              if (!alive || stopRef.current) return;
              // 工具调用发生 → 之后的文字属于新的一段（段间补空行）
              needSegBreak = true;
              let parsed = args;
              try {
                parsed = typeof args === "string" && args ? JSON.parse(args) : args;
              } catch (e) { silentWarn(e, "ChatPage"); }
              // 记录后端 tool_call_id：同轮并发调用同名工具时，结果/耗时按 id 精确回填
              const card = { tool: name, args: parsed, status: "running", ...(id ? { id } : {}) };
              if (isContinue) {
                updateMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, tools: [...(x.tools || []), card] } : x)));
              } else {
                patchLast((x) => ({ ...x, tools: [...(x.tools || []), card] }));
              }
              setGenState({ on: true, text: "⚙ 正在执行「" + name + "」…" });
            },
            onTool: ({ name, result, id }) => {
              if (!alive || stopRef.current) return;
              // 工具完成：按 tool_call_id 精确配对，退化到「最后一张同名 running 卡片」。
              // 注意必须替换对象而非改 card.status——card 是 state 内 tools 数组里
              // 的共享对象，原地改会污染旧快照（isContinue 分支此前即如此）。
              const finishTools = (tools) => {
                const out = [...(tools || [])];
                const res = formatToolResult(result).slice(0, 8000);
                const idx = findToolCard(out, { id, name, status: "running" });
                if (idx >= 0) out[idx] = { ...out[idx], status: "done", result: res };
                else out.push({ tool: name, result: res, status: "done", ...(id ? { id } : {}) });
                return out;
              };
              if (isContinue) {
                updateMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, tools: finishTools(x.tools) } : x)));
              } else {
                patchLast((x) => ({ ...x, tools: finishTools(x.tools) }));
              }
            },
            onToolDuration: ({ name, duration, id }) => {
              if (!alive || stopRef.current) return;
              // 补写耗时：同样替换对象，不原地改卡片（非续写分支此前甚至不触发更新，
              // 耗时只能等下一次重渲染才出现——现改为立即不可变落地）
              const withDuration = (tools) => {
                const out = [...(tools || [])];
                const idx = findToolCard(out, { id, name, status: "done" });
                if (idx >= 0) out[idx] = { ...out[idx], duration };
                return out;
              };
              if (isContinue) {
                updateMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, tools: withDuration(x.tools) } : x)));
              } else {
                patchLast((x) => ({ ...x, tools: withDuration(x.tools) }));
              }
            },
            onUsage: (u) => {
              if (alive) {
                if (isContinue) updateMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, usage: u } : x)));
                else patchLast((x) => ({ ...x, usage: u }));
              }
            },
            onMetrics: (mt) => {
              if (!alive || stopRef.current) return;
              // 本轮累计（跨工具轮）的 TTFT/输出速率/输入输出：写到最后一条 assistant
              if (isContinue) updateMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, metrics: mt } : x)));
              else patchLast((x) => ({ ...x, metrics: mt }));
              setGenTps(mt?.tps || 0);
            },
            onNotice: (ev) => {
              if (!alive || stopRef.current) return;
              const text = String((ev && ev.text) || "").trim();
              if (!text) return;
              // 生成提前结束/循环防护等真实原因：挂到当前助手消息的 notices，随消息可见
              if (isContinue) updateMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, notices: [...(x.notices || []), text] } : x)));
              else patchLast((x) => ({ ...x, notices: [...(x.notices || []), text] }));
            },
            onCompressed: (ev) => {
              if (!alive) return;
              if (!isContinue) {
                const n = ev.removed_msgs || 0;
                if (n > 0) {
                  let hist = historyRef.current || [];
                  let removed = 0;
                  while (removed < n && hist.length) {
                    if (hist[0].role !== "system") removed += 1;
                    hist = hist.slice(1);
                  }
                  historyRef.current = hist;
                }
              }
              toast("🧠 上下文压缩：" + (ev.mode === "summary" ? "已用 LLM 摘要替换" : "已硬裁剪") + "最早 " + ev.removed_turns + " 轮对话（" + ev.removed_msgs + " 条消息）" + (ev.archived_path ? "（已归档 " + ev.archived_path + "）" : ""));
            },
            onAskRequest: (ev) => {
              if (alive && !stopRef.current) onPrompt && onPrompt({ ...ev, type: "ask" });
            },
            onApprovalRequest: (ev) => {
              if (alive && !stopRef.current) onPrompt && onPrompt({ ...ev, type: "approval" });
            },
            onPlanRequest: (ev) => {
              if (alive && !stopRef.current) {
                // 待执行工具计划：落到当前助手消息（渲染为 checklist，状态由已执行工具推导）
                const steps = Array.isArray(ev && ev.steps)
                  ? ev.steps.map((s) => ({ name: String((s && s.name) || ""), args: s && s.args })).filter((s) => s.name)
                  : [];
                if (steps.length) {
                  if (isContinue) updateMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, plan: steps } : x)));
                  else patchLast((x) => ({ ...x, plan: steps }));
                }
                onPrompt && onPrompt({ ...ev, type: "plan" });
              }
            },
            onNeedsConfirmation: (ev) => {
              // 成本预检拦截：标记本次为「待确认」，由 finish() 移除空占位并等待用户决定。
              if (alive && !stopRef.current) {
                needsConfirm = true;
                onPrompt && onPrompt({ ...ev, type: "confirm" });
              }
            },
            onSession: (ev) => {
              // 后端分配的会话 id（新会话也能拿到）：采用它统一落盘 id，避免
              // 「前端另存一份」与「后端无人订阅时兜底落盘」各生成一个 id → 重复会话。
              if (alive && ev && ev.id) {
                try { onSession && onSession(ev.id); } catch (e) { silentWarn(e, "ChatPage"); }
              }
            },
            onDone: () => finish(true),
            onError: (e) => {
              if (!alive) return;
              if (done) return;  // 已被 finish/其他错误终结，防重复处理
              done = true;       // 错误即终结：短路 streamChat resolve 后误走 finish(true)
              flushNow();
              silentWarn(e, "ChatPage.stream");
              // 展示后端返回的**真实**错误（此前固定成一句通用文案，真正原因被吞掉，无从排查）；
              // 无详情时才回退到通用提示。错误写入独立字段，不污染会话历史/导出。
              const _detail = (typeof e === "string" ? e : (e && e.message) || "").trim();
              const _errText = _detail
                ? `生成中断：${_detail}`
                : "生成中断：后端返回错误，请重试或检查「设置 → 网关 / API Key」。";
              updateMsgs((m) => m.map((x, i) => (i === (isContinue ? continueIdx : m.length - 1) ? { ...x, error: _errText, streaming: false } : x)));
              setBusy(false);
              setGenState({ on: false, text: "" });
              setGenTps(0);
              continueRef.current = { active: false, idx: -1 };
              try {
                onFinished?.({ userText, msg: currentMsg(), ok: false, isContinue, error: String(e), images, files });
              } catch (err2) { silentWarn(err2, "ChatPage"); }
            },
          },
          stopSignalRef.current ? stopSignalRef.current.signal : undefined
        );
        finish(true);
      } catch (err) {
        // 用户主动停止（AbortError）：不算失败，不弹错误 toast；streaming 状态由 onStop 统一清理
        if (err && (err.name === "AbortError" || err.code === 20)) {
          continueRef.current = { active: false, idx: -1 };
          return;
        }
        if (!alive) return;
        setBusy(false);
        setGenState({ on: false, text: "" });
        setGenTps(0);
        continueRef.current = { active: false, idx: -1 };
        try {
          onFinished?.({
            userText: pendingRef.current.text,
            msg: null,
            ok: false,
            isContinue: false,
            error: err.message || String(err),
            images: pendingRef.current.images || [],
            files: pendingRef.current.files || [],
          });
        } catch (e) { silentWarn(e, "ChatPage"); }
      }
    })();

    return () => {
      alive = false;
      stopRef.current = true;
      // 取消挂起的 rAF 批处理：防止停止/卸载后下一帧仍执行 updateMsgs（多冒半帧 / 卸载后 setState）
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      // 清空累积缓冲：停止/中断走 AbortError 分支直接 return 不 flushNow，
      // 残留的 think/text 会被下一轮 scheduleBatch 累加进新回复开头（串味）——必须复位。
      batchRef.current = { think: "", text: "", gen: "" };
      // 中止进行中的流式请求：组件卸载/切换页面时立即断开，避免后台空跑
      try {
        if (stopSignalRef.current) stopSignalRef.current.abort();
      } catch (e) { silentWarn(e, "ChatPage"); }
    };
  }, [busy]);
}

// 后端会话 → 左侧列表项（后端一旦可用就以真实数据为准，空列表也必须清干净）
const toSessionItem = (s) => ({
  id: s.id,
  title: s.name,
  time: (s.saved_at || "").replace("T", " ").slice(5, 16),
  pinned: s.pinned,
  tag: s.scenario || "会话",
  model: s.model,
  brief: `${s.msg_count} 条消息`,
  tags: s.tags || [],
});

function useDataSources() {
  const [mode, setMode] = React.useState("auto");
  const [sessions, setSessions] = React.useState([]);
  const [ctx, setCtx] = React.useState(null);
  const [history, setHistory] = React.useState({});
  const [loadErr, setLoadErr] = React.useState("");
  const [peakInfo, setPeakInfo] = React.useState({ on: false, warn: true });

  // 可重复调用：探测后端并加载会话/上下文/状态。返回是否在线。
  // 用 probeBackendHealth（不缓存）而非 checkBackend（缓存）——否则「加载时后端恰好没起来」
  // 会把 offline 永久钉死，之后即使服务恢复、所有发送仍被 dataMode 拦截（无任何反应）。
  const loadAll = React.useCallback(async () => {
    try {
      const ok = await api.probeBackendHealth();
      if (!ok) {
        setMode("offline");
        setLoadErr("后端服务未连接：请启动「鲸语 WhaleTalk」(web_app.py) 后刷新页面");
        return false;
      }
      setMode("backend");
      setLoadErr("");
      try {
        const real = await api.listSessions();
        setSessions((real || []).map(toSessionItem));
      } catch (e) {
        setLoadErr(`会话列表加载失败：${e.message || "网络异常"}`);
      }
      try {
        const c = await api.getContext();
        if (c) {
          setCtx({
            tools: (c.tools || []).slice(0, 12).map((name) => ({ name, desc: "", state: "on" })),
            memory: (c.memory?.facts || []).map((f, i) => ({ id: `MEM#${i}`, text: f, tag: "记忆" })),
            usage: c.usage,
          });
        }
      } catch (e) { silentWarn(e, "ChatPage"); }
      try {
        const st = await api.getStatus();
        if (st) setPeakInfo({ on: !!st.peak_hour, warn: st.peak_warning !== false });
      } catch (e) { silentWarn(e, "ChatPage"); }
      setLoadErr("");
      return true;
    } catch {
      setMode("offline");
      setLoadErr("后端服务未连接：请启动「鲸语 WhaleTalk」(web_app.py) 后刷新页面");
      return false;
    }
  }, []);

  React.useEffect(() => { loadAll(); }, [loadAll]);

  // 后端恢复即自动回到在线（无需刷新页面）：监听健康探测「断开→恢复」的翻转，重新加载。
  const onlineRef = React.useRef(null);
  React.useEffect(() => {
    const stop = api.watchBackend(5000, (ok) => {
      const was = onlineRef.current;
      onlineRef.current = ok;
      if (ok && was === false) loadAll();  // 仅从「已知离线」翻回时重载，避免与首轮重复
    });
    return () => { if (typeof stop === "function") stop(); };
  }, [loadAll]);

  const pickSession = React.useCallback(
    async (id) => {
      if (mode !== "backend") return null;
      try {
        const d = await api.getSession(id);
        if (d && d.messages) {
          // tool_call_id 只在单轮内唯一（call_0 起步会跨轮重复），按顺序指针向前消费，
          // 保证每轮 assistant 拿到的是自己那一轮的工具结果
          let searchFrom = 0;
          const usedToolIdx = new Set();
          const mapped = [];
          const asstByOrig = new Map();   // 原始下标 → 映射后的 assistant 对象（孤儿归属用）
          d.messages.forEach((m, origIdx) => {
            if (m.role === "tool" || m.role === "system") return;
            if (m.role === "user") {
              mapped.push({
                role: "user",
                text: m.content,
                ...(m.images && m.images.length ? { images: m.images } : {}),
                ...(m.files && m.files.length ? { files: m.files } : {}),
              });
              return;
            }
            const tools = (m.tool_calls || []).map((tc) => {
              let args = {};
              try {
                args = JSON.parse(tc.function?.arguments || "{}");
              } catch (e) { silentWarn(e, "ChatPage"); }
              let hit = -1;
              for (let i = searchFrom; i < d.messages.length; i++) {
                const mm = d.messages[i];
                if (mm.role === "tool" && mm.tool_call_id === tc.id) {
                  hit = i;
                  break;
                }
              }
              if (hit >= 0) {
                searchFrom = hit + 1;
                usedToolIdx.add(hit);
              }
              return {
                tool: tc.function?.name || "?",
                args,
                status: "done",
                result: hit >= 0 ? String(d.messages[hit].content || "").slice(0, 500) : "",
                duration: "—",
                ...(tc.id ? { id: tc.id } : {}),
              };
            });
            const obj = {
              role: "assistant",
              think: m.reasoning_content || "",
              tools,
              text: m.content,
              streaming: false,
              // 多段输出的分段锚点（用于重载后还原「↳ 基于第 N 步」）
              ...(Array.isArray(m.segs) && m.segs.length ? { segs: m.segs } : {}),
              // 历史会话回显：单条用量/速率
              usage: m.usage,
              metrics: m.metrics,
            };
            asstByOrig.set(origIdx, obj);
            mapped.push(obj);
          });
          // 兜底：历史后端曾把 tool_calls 截断（截到 16 条），孤儿 tool 消息按
          // 「前面最近的一条 assistant」归属——绝不能挂到整个会话的最后一条 assistant，
          // 否则会把上一轮的工具结果错记到下一轮（跨任务串味）。
          let curAsst = null;
          d.messages.forEach((mm, i) => {
            if (mm.role === "assistant") {
              curAsst = asstByOrig.get(i) || curAsst;
              return;
            }
            if (mm.role === "tool" && !usedToolIdx.has(i) && curAsst) {
              curAsst.tools.push({
                tool: mm.name || mm.tool_call_id || "tool",
                args: {},
                status: "done",
                result: String(mm.content || "").slice(0, 500),
                duration: "—",
              });
            }
          });
          return { messages: mapped, usage: d.usage_total, stars: d.stars, pinned: d.pinned, tags: d.tags };
        }
      } catch (e) { silentWarn(e, "ChatPage"); }
      return null;
    },
    [mode]
  );

  const refreshSessions = React.useCallback(async () => {
    if (mode !== "backend") return;
    try {
      const real = await api.listSessions();
      setSessions((real || []).map(toSessionItem));
    } catch {
      setLoadErr("会话列表刷新失败：后端未响应");
      setTimeout(() => setLoadErr((e) => (e.startsWith("会话列表刷新失败") ? "" : e)), 3000);
    }
  }, [mode]);

  return { mode, sessions, ctx, history, pickSession, refreshSessions, reload: loadAll, setCtx, loadErr, peakInfo };
}

export default function ChatPage({ onGoWorkbench, onGoSettings, applyPrompt, onApplyDone, openSessionId, onOpenSessionDone, quietMode, onToggleQuiet, active = true }) {
  const { mode, switchMode } = React.useContext(ModeContext);
  // 组件内统一用 chatMode 指代当前工作模式（"task" | "dialog"），与 useBackendChat
  // 的入参同名，避免组件层引用到一个不存在的变量（chatMode）而抛 ReferenceError。
  const chatMode = mode;
  const { density, fontSize } = React.useContext(DisplayContext);
  const { flash } = React.useContext(FlashContext);
  const { toast } = React.useContext(ToastContext);
  const [genState, setGenState] = React.useState({ on: false, text: "" });
  // 生成中的实时输出速率（tok/s，来自后端 metrics 事件），结束/停止时清零
  const [genTps, setGenTps] = React.useState(0);
  const [activeId, setActiveId] = React.useState(null);
  // 最新会话 id 转发给 useBackendChat（避免 effect 闭包过期）：已有会话生成完成后由后端自动落盘
  const activeIdRef = React.useRef(null);
  activeIdRef.current = activeId;
  // 本轮生成的稳定标识：后端用它把生成交给独立作业（切页/多标签页不打断）。
  // 每「轮」在 onSend/onContinue 时重新生成，同一轮内 effect 重跑复用同一个。
  const streamIdRef = React.useRef(null);
  // 费用确认闸：用户确认成本后置 true，仅作用于紧接着的那一次请求（发送后复位）。
  const costConfirmedRef = React.useRef(false);
  // 网关会话 id（OpenCode Go/Zen 的 x-opencode-session）：每次「新对话」重新生成，
  // 同一会话内跨轮稳定。与业务 session_id 解耦，后端只用于请求头，不用于落盘。
  const gwSessionRef = React.useRef(null);
  if (gwSessionRef.current == null) gwSessionRef.current = newGwSessionId();
  // 实时镜像 msgs：hook 内函数式更新 + 组件体 onFinished 共用，保证续写/保存拿到最新消息
  const msgsRef = React.useRef([]);
  const [msgs, setMsgs] = React.useState([]);
  // 兜底同步：任何直接 setMsgs（onSend/onPickSession/onFork/…）都要让镜像跟上，
  // 否则流式/保存会对着旧会话的镜像操作（历史串会话、落盘错乱）。
  React.useEffect(() => { msgsRef.current = msgs; }, [msgs]);
  const [busy, setBusy] = React.useState(false);
  const [ctxOpen, setCtxOpen] = React.useState(false);
  const [listOpen, setListOpen] = React.useState(() => !mq("(max-width: 860px)"));
  const [auxOpen, setAuxOpen] = React.useState(() => !mq("(max-width: 1000px)"));
  // 记忆上次停留的控制台页签（程序化切到 activity 时不覆盖，仅用户点击时由 AuxPanel 写入）
  const [auxTab, setAuxTab] = React.useState(() => {
    try {
      const v = localStorage.getItem("wt_aux_tab");
      return ["activity", "params", "files", "procs"].includes(v) ? v : "params";
    } catch { return "params"; }
  });
  const focusActivity = React.useCallback(() => setAuxTab("activity"), []);
  // 控制台弹出为独立窗口（P2）
  const [auxPop, setAuxPop] = React.useState(false);
  const [popoutEl, setPopoutEl] = React.useState(null);
  const popWinRef = React.useRef(null);
  const [promptReq, setPromptReq] = React.useState(null);
  const [backendNote, setBackendNote] = React.useState("");
  const [multiSel, setMultiSel] = React.useState(null); // null=关闭, Set(index)
  const [starPanel, setStarPanel] = React.useState(false);
  const [searchPanel, setSearchPanel] = React.useState(false);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [searchType, setSearchType] = React.useState("message");
  const [searchResults, setSearchResults] = React.useState([]);
  const [searchBusy, setSearchBusy] = React.useState(false);
  const [fimPanel, setFimPanel] = React.useState(false);
  const [fimPrompt, setFimPrompt] = React.useState("");
  const [fimSuffix, setFimSuffix] = React.useState("");
  const [fimResult, setFimResult] = React.useState("");
  const [fimBusy, setFimBusy] = React.useState(false);
  const [variants, setVariants] = React.useState([]);
  const [variantPanel, setVariantPanel] = React.useState(false);
  const [timelinePanel, setTimelinePanel] = React.useState(false);
  const [cmdPanel, setCmdPanel] = React.useState(false);
  const [moreOpen, setMoreOpen] = React.useState(false);
  const moreRef = React.useRef(null);
  const [cmdQuery, setCmdQuery] = React.useState("");
  const [batchPanel, setBatchPanel] = React.useState(false);
  const [batchFiles, setBatchFiles] = React.useState("");
  const [batchTpl, setBatchTpl] = React.useState("请处理以下文件：{file}");
  // 长会话「回到最新」浮钮：向上翻阅后出现，避免手动拖到底
  const [showJump, setShowJump] = React.useState(false);
  // 拖拽文件到对话区时的高亮遮罩状态（全局 dragenter/leave 计数维护）
  const [dropActive, setDropActive] = React.useState(false);
  // 输入区实测高度：供「回到最新」浮钮定位（避免硬编码 bottom 遮挡多行输入/附件）
  const [composerH, setComposerH] = React.useState(0);
  const composerDockRef = React.useRef(null);
  React.useEffect(() => {
    const el = composerDockRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => setComposerH(el.offsetHeight || 0));
    ro.observe(el);
    setComposerH(el.offsetHeight || 0);
    return () => ro.disconnect();
  }, []);

  // 视口由宽变窄时自动收起内联侧栏/面板（转为抽屉，避免遮挡对话）；
  // 仅在跨越断点那一刻收起，不干扰用户在窄屏下主动打开。
  React.useEffect(() => {
    let wasNarrowList = mq("(max-width: 860px)");
    let wasNarrowAux = mq("(max-width: 1000px)");
    const onResize = () => {
      const narrowList = mq("(max-width: 860px)");
      const narrowAux = mq("(max-width: 1000px)");
      if (narrowList && !wasNarrowList) setListOpen(false);
      if (narrowAux && !wasNarrowAux) setAuxOpen(false);
      wasNarrowList = narrowList;
      wasNarrowAux = narrowAux;
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Esc 关闭已打开的抽屉（会话列表/控制台/上下文）——此前只能点遮罩或开关
  React.useEffect(() => {
    if (!active) return;  // 常驻挂载（切页只隐藏）时，隐藏页不响应全局快捷键
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (mq("(max-width: 860px)")) setListOpen(false);
      if (mq("(max-width: 1000px)")) setAuxOpen(false);
      setCtxOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  // ── 控制台弹出为独立窗口（P2）：把同一份 React 树 portal 到新窗口 ──
  // 复制主文档样式 + data-theme，主窗口不再内嵌渲染（面板“搬家”而非复制），
  // 因此活动/产物等实时状态无需跨窗口同步。
  React.useEffect(() => {
    if (!auxPop || !auxOpen) {
      if (popWinRef.current) {
        try { popWinRef.current.close(); } catch (e) { silentWarn(e, "ChatPage"); }
        popWinRef.current = null;
      }
      setPopoutEl(null);
      return undefined;
    }
    const win = window.open("", "wt-console", "width=580,height=940,menubar=no,toolbar=no,location=no,status=no");
    if (!win) { setAuxPop(false); return undefined; }  // 被拦截 → 回退内嵌
    popWinRef.current = win;
    try {
      win.document.title = "鲸语 · 控制台";
      document.querySelectorAll('link[rel="stylesheet"], style').forEach((node) => {
        win.document.head.appendChild(node.cloneNode(true));
      });
      win.document.documentElement.setAttribute("data-theme", document.documentElement.getAttribute("data-theme") || "");
      win.document.body.className = "wt-popout";
      const root = win.document.createElement("div");
      root.className = "aux-popout-root";
      win.document.body.appendChild(root);
      setPopoutEl(root);
    } catch (e) {
      silentWarn(e, "ChatPage");
      setAuxPop(false);
    }
    const iv = setInterval(() => {
      if (win.closed) {
        clearInterval(iv);
        popWinRef.current = null;
        setPopoutEl(null);
        setAuxPop(false);
      }
    }, 600);
    return () => {
      clearInterval(iv);
      // 依赖变化/卸载时关闭本 effect 打开的窗口（StrictMode 双调用下避免残留窗口）
      if (popWinRef.current === win) {
        try { win.close(); } catch (e) { silentWarn(e, "ChatPage"); }
        popWinRef.current = null;
      }
    };
  }, [auxPop, auxOpen]);

  // ── 活动镜像：把"最近一次工具链"喂给侧栏「🔧 活动」标签 ──
  // 取最新的 assistant 消息（含工具或正在流式）作为实时活动；工具执行从聊天流"搬"到侧栏，
  // 聊天正文只保留精简摘要。streaming=true 时侧栏自动切到活动标签并显示进行中。
  const liveActivity = React.useMemo(() => {
    // 单条 assistant 消息 → 步骤数组（含 args 原对象，供活动面板结构化渲染）
    const mapSteps = (last) => (last.tools || []).map((t) => ({
      tool: t.tool || "?",
      status: t.status || (last.streaming ? "running" : "done"),
      duration: t.duration,
      result: t.result,
      args: t.args,
      argsText: t.args ? JSON.stringify(t.args).slice(0, 200) : undefined,
    }));
    // 全量会话时间线：不再只保留「最近一次」，每一轮含工具调用的 assistant 消息都留档
    const turns = [];
    msgs.forEach((m, i) => {
      if (!m || m.role !== "assistant") return;
      if (!((m.tools && m.tools.length > 0) || m.streaming)) return;
      const steps = mapSteps(m);
      const failed = steps.filter((s) => s.status === "failed").length;
      turns.push({
        // 稳定任务标识：同一条 assistant 消息内不变，新消息即新任务 → 供活动面板重置展开态
        taskId: i,
        time: m.time || "",
        steps,
        streaming: !!m.streaming,
        failed,
        label: m.streaming ? "AI 正在执行" : failed ? "含失败步骤" : "已完成",
      });
    });
    if (!turns.length) return { steps: [], streaming: false, text: "", taskId: -1, turns: [] };
    const last = turns[turns.length - 1];
    return {
      steps: last.steps,
      streaming: last.streaming,
      text: msgs[last.taskId]?.text || "",
      taskId: last.taskId,
      label: last.streaming ? "AI 正在执行" : "最近一次工具调用",
      turns,
    };
  }, [msgs]);

  // ── 会话内「产物书架」：累计本会话所有 assistant 产出，去重、新→旧排好 ──
  // 不像"最近一次"那样每次新消息被冲掉——像书架上按顺序摆好的书，反复可看。
  const liveProducts = React.useMemo(() => {
    const seen = new Set();
    const list = [];
    // 从后往前扫，让"最近产出"排最前
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (!m || m.role === "user") continue;
      const textParts = [m.text || ""];
      (m.tools || []).forEach((t) => {
        if (t.result) textParts.push(String(t.result));
        // 工具「参数」里的目标路径同样是产物来源（write_file.path / html_to_pdf.output /
        // image_process.output / run_python 代码里写出的路径…）——只取路径类键，不整坨 JSON，
        // 避免把文件内容里引用的路径误当产物。
        const a = t.args;
        if (a && typeof a === "object") {
          for (const k of PRODUCT_PATH_KEYS) if (a[k]) textParts.push(String(a[k]));
          if (Array.isArray(a.paths)) textParts.push(a.paths.join("\n"));
          if (typeof a.code === "string") textParts.push(a.code);
        }
      });
      const paths = extractProducts(textParts.join("\n"), 0);
      for (const p of paths) {
        if (seen.has(p)) continue;
        seen.add(p);
        list.push({ path: p, name: String(p).split(/[\\/]/).pop(), at: i, from: m.role });
      }
    }
    return list;
  }, [msgs]);

  // ── 本会话统计：从消息的 usage/metrics 汇总（输入/输出/缓存 + 平均输出速率/TTFT）──
  const sessionStats = React.useMemo(() => {
    const usage = { prompt: 0, completion: 0, cache_hit: 0, cache_miss: 0 };
    let gen_ms = 0;
    let total_ms = 0;
    let ttft = null;
    let turns = 0;
    for (const m of msgs) {
      if (!m || m.role !== "assistant") continue;
      turns += 1;
      const mt = m.metrics || {};
      // 统一口径：优先累计 metrics（多轮正确），无则退回单轮 usage
      const u = (mt.prompt || mt.completion) ? mt : (m.usage || {});
      usage.prompt += u.prompt || 0;
      usage.completion += u.completion || 0;
      usage.cache_hit += u.cache_hit || 0;
      usage.cache_miss += u.cache_miss || 0;
      if (mt.gen_ms || mt.total_ms || mt.ttft_ms != null) {
        gen_ms += mt.gen_ms || 0;
        total_ms += mt.total_ms || 0;
        if (ttft == null && mt.ttft_ms != null) ttft = mt.ttft_ms;
      }
    }
    const tps = gen_ms > 50 ? Math.round((usage.completion / (gen_ms / 1000)) * 10) / 10 : 0;
    return { usage, gen_ms, total_ms, ttft, tps, turns };
  }, [msgs]);

  const doBatch = () => {
    const files = batchFiles.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!files.length) return;
    const tpl = batchTpl.includes("{file}") ? batchTpl : batchTpl + " {file}";
    const msg = `[批量任务] 请对以下 ${files.length} 个文件逐个执行同一指令：\n指令：${tpl}\n文件列表：\n${files.map((f) => `- ${f}`).join("\n")}`;
    setBatchPanel(false);
    setBatchFiles("");
    composerRef.current?.insertText(msg);
  };

  React.useEffect(() => {
    if (!active) return;  // 常驻挂载（切页只隐藏）时，隐藏页不响应全局快捷键
    const onKey = (e) => {
      if (e.ctrlKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdPanel(true);
        setCmdQuery("");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);
  // 顶栏「更多」菜单：点击外部 / Esc 关闭
  React.useEffect(() => {
    if (!moreOpen) return undefined;
    const onDoc = (e) => {
      if (moreRef.current && moreRef.current.contains(e.target)) return;
      setMoreOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setMoreOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [moreOpen]);

  const pendingRef = React.useRef({ text: "", images: [], files: [] });
  const historyRef = React.useRef([]);
  const stopSignalRef = React.useRef(null);
  const scrollRef = React.useRef(null);
  // ── 对话模式联网搜索开关（localStorage 持久化）──
  // 隐私模式/禁用存储下 getItem 抛 SecurityError，套 try/catch 防首次渲染崩溃
  const [webSearch, setWebSearch] = React.useState(() => {
    try { return localStorage.getItem("whaletalk.webSearch") === "1"; }
    catch { return false; }
  });
  const toggleWebSearch = React.useCallback(() => {
    setWebSearch((v) => {
      const nv = !v;
      try { localStorage.setItem("whaletalk.webSearch", nv ? "1" : ""); }
      catch { /* 存储不可用时仅本次会话生效 */ }
      return nv;
    });
  }, []);
  // ── 长会话窗口化渲染（P2）：默认只渲染最近 VIRT_WINDOW 条消息，
  // 顶部哨兵可见时增量加载更早的（替代全量渲染，长会话不卡）。──
  const VIRT_WINDOW = 60;
  const VIRT_LOAD = 40;
  const VIRT_EST_H = 90; // 未渲染条目的估算高度（px），用于顶部占位
  // 活动消息窗口上界（回合数）：状态里实际保留多少回合；更早的整回合丢弃
  // （已完整落盘，重开会话可查看）。防止 msgs 数组随会话长度无限增长。
  const MAX_LIVE_TURNS = 40;
  const [renderStart, setRenderStart] = React.useState(0);
  const atBottomRef = React.useRef(true);
  const topSentinelRef = React.useRef(null);
  const pendingScrollRef = React.useRef(null);
  const composerRef = React.useRef(null);
  // 指令库「应用」带入的指令内容：填入输入框并聚焦，用户补充内容后发送
  React.useEffect(() => {
    if (!applyPrompt) return;
    composerRef.current?.insertText(applyPrompt);
    onApplyDone?.();
  }, [applyPrompt, onApplyDone]);
  const resendIdxRef = React.useRef(null);
  // 编辑重发时暂存被编辑消息的附件（编辑文字不应丢原图/原文件）
  const editAttRef = React.useRef(null);
  const starsRef = React.useRef(new Set());
  const pinsRef = React.useRef(new Set());
  const genStateRef = React.useRef({ on: false, text: "" });
  const continueRef = React.useRef({ active: false, idx: -1 });

  // ── 拖拽文件到应用任意位置 → 交给输入区上传（图片→视觉，其它→路径给 AI）──
  // 全局监听：阻止浏览器「打开文件」默认行为（否则拖歪一点就跳走），并用计数
  // 抵消 dragenter/dragleave 在子元素间来回触发的抖动。仅在确实携带文件时接管。
  const dragDepthRef = React.useRef(0);
  // 拖入「文件路径」时复用的注入函数（在下方定义；用 ref 避开 effect 首渲染闭包）
  const onInjectFileRef = React.useRef(null);
  React.useEffect(() => {
    // 拖拽载荷两类：真实文件（Files）+ 控制台文件行拖出的路径（自定义 MIME）
    const WT_PATH_MIME = "application/x-whaletalk-path";
    const hasFiles = (e) => {
      const dt = e && e.dataTransfer;
      if (!dt) return false;
      try {
        const types = Array.from(dt.types || []);
        return types.indexOf("Files") !== -1 || types.indexOf(WT_PATH_MIME) !== -1;
      } catch (err) { return false; }
    };
    // 仅接管落在对话区（.chat-main）内的拖拽：其它区域的 file input（如大脑导入）
    // 保持浏览器原生行为，不被全局 preventDefault 干扰。
    const inChat = (t) => !!(t && t.closest && t.closest(".chat-main"));
    const onEnter = (e) => { if (!hasFiles(e) || !inChat(e.target)) return; e.preventDefault(); dragDepthRef.current += 1; setDropActive(true); };
    const onOver = (e) => { if (!hasFiles(e) || !inChat(e.target)) return; e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = "copy"; setDropActive(true); };
    const onLeave = (e) => {
      if (!hasFiles(e)) return;
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (dragDepthRef.current === 0) setDropActive(false);
    };
    const onDrop = (e) => {
      if (!hasFiles(e) || !inChat(e.target)) return;
      e.preventDefault();
      dragDepthRef.current = 0;
      setDropActive(false);
      const dt = e.dataTransfer;
      let path = "";
      try { path = (dt && dt.getData && dt.getData(WT_PATH_MIME)) || ""; } catch (err) { path = ""; }
      if (path) { onInjectFileRef.current?.(path); return; }
      const fs = dt && dt.files;
      if (fs && fs.length) composerRef.current?.addFiles(fs);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, []);

  // ── 粘贴图片/文件到对话区 → 输入区上传（普通文本粘贴不拦截，仍进输入框）──
  const onPasteFiles = (e) => {
    const dt = e.clipboardData;
    if (!dt) return;
    let files = [];
    if (dt.files && dt.files.length) files = Array.from(dt.files);
    else if (dt.items) {
      for (const it of dt.items) {
        if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); }
      }
    }
    if (files.length) {
      e.preventDefault();
      composerRef.current?.addFiles(files);
    }
  };

  const setGenStateThrottled = React.useCallback((s) => {
    const prev = genStateRef.current;
    if (prev.on === s.on && prev.text === s.text) return;
    genStateRef.current = s;
    setGenState(s);
  }, []);

  const { mode: dataMode, sessions, ctx, history, pickSession, refreshSessions, reload: reloadDataSources, setCtx, loadErr, peakInfo } = useDataSources();

  React.useEffect(() => {
    setBackendNote(
      dataMode === "offline"
        ? "后端未连接：请启动「鲸语 WhaleTalk」(web_app.py) 后刷新页面"
        : loadErr || ""
    );
  }, [dataMode, loadErr]);

  // 保存回调用 ref 转发最新版本：useBackendChat 的 effect 只依赖 [busy]，
  // 若直接传正文箭头函数，会捕获发送时刻的陈旧闭包（msgs/activeId/sessions 过期）。
  const onFinishedRef = React.useRef(null);
  const invokeFinished = React.useCallback(
    (payload) => onFinishedRef.current && onFinishedRef.current(payload),
    []
  );
  // 后端分配的会话 id 转发（新会话采用后端 id，落盘/停止与兜底落盘共用同一 id）
  const onSessionRef = React.useRef(null);
  onSessionRef.current = (sid) => {
    if (sid && !activeIdRef.current) setActiveId(sid);
  };

  useBackendChat({
    busy: dataMode === "backend" && busy,
    setBusy,
    setMsgs,
    msgsRef,
    pendingRef,
    historyRef,
    chatMode: mode,
    webSearch,
    quietMode,
    onFinished: invokeFinished,
    stopSignalRef,
    onPrompt: setPromptReq,
    setGenState: setGenStateThrottled,
    continueRef,
    sessionIdRef: activeIdRef,
    gwSessionRef,
    streamIdRef,
    costConfirmedRef,
    onSession: (sid) => onSessionRef.current && onSessionRef.current(sid),
    setGenTps,
    toast,
  });

  // 会话保存：每次 render 同步到 ref，保证 useBackendChat 用的是最新闭包
  onFinishedRef.current = ({ userText, msg, ok, isContinue, error, images = [], files = [] }) => {
    const saveChatFinished = async () => {
      if (ok) {
        toast("✅ 回复完成");
      } else if (error) {
        toast(`⚠️ 发送失败：${error}`);
        setBackendNote("⚠️ 发送失败：后端未响应，请确认「鲸语 WhaleTalk」服务在运行");
        return;
      }
      if (!ok) return;
      if (dataMode !== "backend") return;
      if (isContinue) {
        // 续写：用 msgsRef 实时镜像（含续写流式内容）重建完整消息链保存；
        // 续写不产生新 user 消息、msg 为空，直接以镜像消息链为准
        try {
          const updated = toSaveMessages(
            msgsRef.current.length ? msgsRef.current : msgs
          );
          await api.saveSession({
            id: activeId || undefined,
            name: activeSession?.title || userText.replace(/\s+/g, " ").slice(0, 24),
            messages: updated,
          });
          refreshSessions();
        } catch (e) { silentWarn(e, "ChatPage"); }
        continueRef.current = { active: false, idx: -1 };
        return;
      }
      // 用实参 msg（由 currentMsg() 从实时镜像取的流式终态）而非闭包 msgs（陈旧/可能为空）保存会话
      if (!msg || msg.role !== "assistant" || !msg.text) return;
      try {
        const calls = msg.tools.map((t, i) => ({
          id: `call_${i}`,
          type: "function",
          function: { name: t.tool, arguments: JSON.stringify(t.args || {}) },
        }));
        const toolMsgs = msg.tools.map((t, i) => ({
          role: "tool",
          tool_call_id: `call_${i}`,
          name: t.tool,
          content: String(t.result || "").slice(0, 4000),
        }));
        // 连续对话：若已有 activeId（历史会话/当前会话），写回同一会话并 append（后端合并旧消息）；
        // 仅当无 activeId（新对话）时才新建，名称取首条用户消息。
        const isExisting = !!activeId;
        const sid = await api.saveSession({
          id: activeId || undefined,
          append: isExisting,
          // 本次生成的 stream_id：后端据此把生成期「在制回合」就地替换为最终回合
          turn_id: streamIdRef.current || undefined,
          name: isExisting
            ? (activeSession?.title || userText.replace(/\s+/g, " ").slice(0, 24))
            : userText.replace(/\s+/g, " ").slice(0, 24),
          messages: [
            {
              role: "user",
              content: userText,
              ...(images.length ? { images } : {}),
              ...(files.length ? { files } : {}),
            },
            {
              role: "assistant",
              content: msg.text,
              reasoning_content: msg.think || "",
              ...(calls.length ? { tool_calls: calls } : {}),
              // 多段输出的分段锚点（段起点字符偏移 + 该段前工具步数）：重载后可还原因果标注
              ...(msg.segs && msg.segs.length ? { segs: msg.segs } : {}),
              // 用量/速率随消息落盘（历史会话回显；后端据此汇总会话级统计）
              ...(msg.usage ? { usage: msg.usage } : {}),
              ...(msg.metrics ? { metrics: msg.metrics } : {}),
            },
            ...toolMsgs,
          ],
          stars: [...starsRef.current].map((k) => {
            const [role, ...rest] = k.split("\u0000");
            return { role, content: rest.join("\u0000"), time: "" };
          }),
          pinned: [...pinsRef.current].map((k) => k.split("\u0000").slice(1).join("\u0000")),
        });
        if (sid) {
          setActiveId(sid);
          historyRef.current = trimHistory(buildHistory(
            msgsRef.current.length ? msgsRef.current : [userText, msg], chatMode
          ));
          refreshSessions();
        }
      } catch (e) { silentWarn(e, "ChatPage"); }
    };
    saveChatFinished();
    // 长会话内存上界（在**保存之后**执行，确保完整历史已落盘）：只保留最近
    // MAX_LIVE_TURNS 个回合的活动消息。窗口化渲染不回收消息对象，单轮长任务的
    // 上百条 tool 结果会一直驻留内存（实测某会话 820 条 / 1MB JSON）——这里做上界。
    // 只 slice 不改对象，保持 memo 引用语义；被裁掉的旧回合仍可重开会话查看。
    if (msgsRef.current && msgsRef.current.length) {
      const { msgs: capped, dropped } = capLiveWindow(msgsRef.current, { maxTurns: MAX_LIVE_TURNS });
      if (dropped > 0) setMsgs(capped);
    }
  };

  const onSend = async (text, attachments = []) => {
    // busy 一律拦截：busy 期间即使带 resendIdxRef 也会覆盖 pendingRef 且 effect 不重跑（新流不会启动），
    // 导致当前流结束后保存时 userText 错乱。编辑重发需等当前生成结束后再操作。
    if (busy) return;
    // 正常发送绝不是续写：显式复位，杜绝残留的 continue 标记把本次当成续写。
    continueRef.current = { active: false, idx: -1 };
    // 新消息 → 永久停止正在进行的朗读/自动朗读（真人对话：我开口，AI 就不该继续念）
    stopSpeak();
    // 高峰提示（每天首次发送；受「高峰提醒」开关与后端实时峰值判定控制）
    try {
      const today = new Date().toISOString().slice(0, 10);
      const last = localStorage.getItem("whaletalk.peak.notified");
      if (last !== today && peakInfo.warn && peakInfo.on) {
        toast("⏰ 当前为 DeepSeek 高峰时段，按高峰价计费（空闲时段为一半）");
        localStorage.setItem("whaletalk.peak.notified", today);
      }
    } catch (e) { silentWarn(e, "ChatPage"); }
    let base = msgs;
    let editedAtt = null;
    if (resendIdxRef.current != null) {
      // 编辑重发：删除该消息及之后；保留原消息附件（编辑文字不该丢掉原图/原文件）
      editedAtt = editAttRef.current;
      base = msgs.slice(0, resendIdxRef.current);
      resendIdxRef.current = null;
      editAttRef.current = null;
    }
    starsRef.current = new Set();
    pinsRef.current = new Set();
    if (dataMode !== "backend") {
      // 加载时后端恰好没起来 → 发送即重连一次；成功就继续发送，不再要求用户刷新页面。
      // （此前 dataMode 一旦为 offline 就被永久钉死，导致「输入无任何反应、前后端都无请求」。）
      toast("🔄 正在重连后端…");
      const online = reloadDataSources ? await reloadDataSources() : false;
      if (!online) {
        toast("⚠️ 后端未连接：请启动「鲸语 WhaleTalk」后重试");
        setBackendNote("后端未连接：请启动「鲸语 WhaleTalk」(web_app.py)");
        return;
      }
    }
    // 附件分流：图片走视觉链路（images），其余文件以路径随消息交给 AI 工具层（files）
    let images = (attachments || [])
      .filter((a) => a && a.path && a.kind !== "file")
      .map((a) => a.path);
    let files = (attachments || [])
      .filter((a) => a && a.path && a.kind === "file")
      .map((a) => ({ path: a.path, name: a.name, size: a.size }));
    // 编辑重发且未新选附件时，沿用被编辑消息原有的附件
    if (editedAtt && images.length === 0 && files.length === 0) {
      images = editedAtt.images || [];
      files = editedAtt.files || [];
    }
    pendingRef.current = { text, images, files };
    historyRef.current = buildHistory(base.filter((m) => !m.streaming), chatMode);
    // 新一轮生成：分配新的 stream_id（后端据此新建独立作业）
    streamIdRef.current = newStreamId();
    // 连续对话：保留已有消息（useBackendChat 在 base 上追加本轮 user+assistant），
    // 实现常规聊天记录连续滚动；只有「新对话」(onPickSession(null)) 才清空。
    setMsgs(base);
    setBusy(true);
    setBackendNote("");
  };

  const nowTime = () => nowClock();

  // 应用型插件执行结果：作为本地消息展示（local 标记——不落盘、不进模型历史）。
  // 关键：流式生成期间助手消息必须始终位于列表末尾（makePatchLast / currentMsg 等
  // 热路径都依赖 `m.length-1`），故此时把本地消息插在流式消息**之前**，绝不追加到末尾。
  const onPluginRun = (text) => {
    const out = String(text || "").trim();
    if (!out) return;
    setMsgs((m) => {
      const localMsg = { role: "assistant", text: out, time: nowTime(), local: true };
      const last = m[m.length - 1];
      if (last && last.streaming) return [...m.slice(0, -1), localMsg, last];
      return [...m, localMsg];
    });
  };

  const onInjectFile = async (path) => {
    try {
      const d = await api.readFile(path);
      if (d && d.content) {
        const note = d.truncated ? `\n[文件较大，已截断前 ${d.content.length} 字符]` : "";
        composerRef.current?.insertText(`[文件] ${String(path).split(/[\\/]/).pop()}:\n${d.content}${note}`);
      } else if (d && d.error) {
        composerRef.current?.insertText(d.error);
      }
    } catch (e) { silentWarn(e, "ChatPage"); }
  };
  onInjectFileRef.current = onInjectFile;

    const onContinue = (idx) => {
    if (busy || !msgs[idx] || msgs[idx].role !== "assistant") return;
    // 不再预构建历史：续写效果会从 msgsRef 原始消息重建到 idx（避免二次构建把内容清空）
    continueRef.current = { active: true, idx };
    streamIdRef.current = newStreamId();
    setBusy(true);
  };

  const onStop = () => {
    if (stopSignalRef.current) {
      try {
        stopSignalRef.current.abort();
      } catch (e) { silentWarn(e, "ChatPage"); }
    }
    // 同时通知服务端停止：生成在独立作业线程里跑，仅靠 abort 前端连接不会让
    // 服务端停——必须显式置位作业的停止句柄（长工具调用期间 SSE 无事件可写）。
    try {
      api.stopChat({
        gwSession: gwSessionRef.current,
        sessionId: activeIdRef.current,
        streamId: streamIdRef.current,
      }).catch(() => {});
    } catch (e) { silentWarn(e, "ChatPage"); }
    // 同步终结流式状态：把仍在 streaming 的 assistant 消息置为完成态，
    // 避免光标永久闪烁 / code-open 占位 / 操作条隐藏（停止不依赖 abort 竞态）。
    // updater 保持纯函数：msgsRef 由 useMsgs 同步 effect 统一维护。
    // 关键：同时复位续写标记——续写被「停止」打断时不会走到 finish()，否则标记会永久残留。
    continueRef.current = { active: false, idx: -1 };
    setMsgs((m) => m.map((x) => (x.streaming ? { ...x, streaming: false } : x)));
    setBusy(false);
    setGenStateThrottled({ on: false, text: "" });
    setGenTps(0);
    toast("⏹ 已停止生成");
  };

  const onPickSession = async (id) => {
    if (busy) return;
    // 切换/新建会话 → 更换网关会话 id（新对话上下文，路由亲和按会话走）
    // 同时复位续写标记：绝不把上一个会话残留的「续写中」带进新打开的会话。
    continueRef.current = { active: false, idx: -1 };
    gwSessionRef.current = newGwSessionId();
    setActiveId(id);
    setBackendNote("");
    if (dataMode !== "backend" || id == null) {
      setMsgs(id ? (history[id] || []).map((m) => ({ ...m, tools: [], streaming: false })) : []);
      return;
    }
    const got = await pickSession(id);
    if (got) {
      starsRef.current = new Set((got.stars || []).map((s) => `${s.role}\u0000${s.content}`));
      pinsRef.current = new Set((got.pinned || []).map((p) => `\u0000${p}`));
      setMsgs(got.messages.map((m) => {
        const key = `${m.role}\u0000${m.text || ""}`;
        return {
          ...m,
          starred: starsRef.current.has(key),
          pinned: pinsRef.current.has(key),
          time: m.time || "",
        };
      }));
      const u = got.usage || {};
      // 后端 usage_total 字段为 {prompt, completion, cache_hit, cache_miss}（非 OpenAI 的 *_tokens）
      if (u && (u.prompt || u.completion)) {
        setCtx((prev) => ({
          ...prev,
          usage: {
            prompt: u.prompt || 0,
            completion: u.completion || 0,
            cached: (u.prompt && u.cache_hit) ? `${((u.cache_hit / u.prompt) * 100).toFixed(1)}%` : "—",
            cost: "—",
          },
        }));
      }
    } else {
      setMsgs((history[id] || []).map((m) => ({ ...m, tools: [], streaming: false })));
    }
  };

  // 工作台「最近会话」直达：onPickSession 已定义，这里引用最新闭包
  React.useEffect(() => {
    if (!openSessionId) return;
    onPickSession(openSessionId);
    onOpenSessionDone?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openSessionId, onOpenSessionDone]);

  const onDeleteSession = async (id) => {
    try {
      await api.deleteSession(id);
      if (activeId === id) {
        setActiveId(null);
        setMsgs([]);
      }
      refreshSessions();
      toast("已删除会话");
    } catch {
      toast(`删除失败：${"后端未响应，请确认服务在运行"}`);
    }
  };

  const onBatchDeleteSessions = async (ids) => {
    try {
      await api.deleteSessionsBatch(ids);
      if (activeId && ids.includes(activeId)) {
        setActiveId(null);
        setMsgs([]);
      }
      refreshSessions();
      toast(`已删除 ${ids.length} 个会话`);
    } catch {
      toast("删除失败：后端未响应，请确认服务在运行");
    }
  };

  // ── 消息操作（对齐原程序右键菜单）──
  const markMsg = (idx, patch) => setMsgs((m) => m.map((x, i) => (i === idx ? { ...x, ...patch } : x)));

  const onStarMsg = (idx) => {
    const m = msgs[idx];
    if (!m) return;
    const key = `${m.role}\u0000${m.text || ""}`;
    const starred = !starsRef.current.has(key);
    if (starred) starsRef.current.add(key);
    else starsRef.current.delete(key);
    markMsg(idx, { starred });
  };

  const onPinMsg = (idx) => {
    const m = msgs[idx];
    if (!m) return;
    const key = `${m.role}\u0000${m.text || ""}`;
    const pinned = !pinsRef.current.has(key);
    if (pinned) pinsRef.current.add(key);
    else pinsRef.current.delete(key);
    markMsg(idx, { pinned });
  };

  const onQuoteMsg = (idx) => {
    const m = msgs[idx];
    if (!m) return;
    const lines = (m.text || "").split("\n").slice(0, 8);
    const quote = lines.map((l) => `> ${l}`).join("\n");
    composerRef.current?.insertText(`请结合以下内容回答：\n${quote}\n\n`);
  };

  const onForkMsg = (idx) => {
    if (busy) return;
    starsRef.current = new Set();
    pinsRef.current = new Set();
    continueRef.current = { active: false, idx: -1 };
    historyRef.current = buildHistory(msgs.slice(0, idx + 1), chatMode);
    setActiveId(null);
    setMsgs(msgs.slice(0, idx + 1));
    setBackendNote("分支会话：已从此处创建新会话");
  };

  const onEditMsg = (idx) => {
    const m = msgs[idx];
    if (!m) return;
    // 只允许编辑「用户」消息并重发；编辑助手消息会被当成新的 user 回合，语义错乱
    if (m.role !== "user") return;
    resendIdxRef.current = idx;
    editAttRef.current = (m.images && m.images.length) || (m.files && m.files.length)
      ? { images: m.images || [], files: m.files || [] }
      : null;
    composerRef.current?.insertText(m.text || "");
  };

  const doFim = async () => {
    if (!fimPrompt.trim() || fimBusy) return;
    setFimBusy(true);
    setFimResult("");
    try {
      const d = await api.fimComplete(fimPrompt, fimSuffix);
      setFimResult(d.result || "");
    } catch (e) {
      setFimResult(`⚠️ 失败：${e.message}`);
    }
    setFimBusy(false);
  };

  // ── A4 回复变体（重新生成存旧版，可浏览/恢复）──
  const saveVariant = (m) => {
    if (!m || m.role !== "assistant" || !m.text) return;
    const key = `whaletalk.variants.${activeId || "draft"}`;
    let list = [];
    try {
      list = JSON.parse(localStorage.getItem(key) || "[]");
    } catch (e) { silentWarn(e, "ChatPage"); }
    list.push({ text: m.text, think: m.think || "", ts: new Date().toISOString() });
    if (list.length > 20) list = list.slice(-20);
    try {
      localStorage.setItem(key, JSON.stringify(list));
    } catch (e) { silentWarn(e, "ChatPage"); }
  };

  const openVariants = () => {
    const key = `whaletalk.variants.${activeId || "draft"}`;
    try {
      setVariants(JSON.parse(localStorage.getItem(key) || "[]"));
    } catch {
      setVariants([]);
    }
    setVariantPanel(true);
  };

  const restoreVariant = (v) => {
    if (!msgs.length) return;
    setMsgs((m) => m.map((x, i) => (i === m.length - 1 ? { ...x, text: v.text, think: v.think, streaming: false } : x)));
    setVariantPanel(false);
    toast("已恢复该版本");
  };

  const onRegenerate = () => {
    if (busy) return;
    let lastUser = -1;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === "user") {
        lastUser = i;
        break;
      }
    }
    if (lastUser < 0) return;
    // 保存旧版到变体
    const lastAssistant = msgs[msgs.length - 1];
    if (lastAssistant && lastAssistant.role === "assistant") saveVariant(lastAssistant);
    const text = msgs[lastUser].text;
    const base = msgs.slice(0, lastUser);
    starsRef.current = new Set();
    pinsRef.current = new Set();
    continueRef.current = { active: false, idx: -1 };
    historyRef.current = buildHistory(base, chatMode);
    setActiveId(null);
    setMsgs(base);
    pendingRef.current = { text, images: msgs[lastUser].images || [], files: msgs[lastUser].files || [] };
    setBusy(true);
    setBackendNote("");
  };

  const onPinSession = async (id, pinned) => {
    try {
      await api.pinSession(id, pinned);
      refreshSessions();
    } catch (e) { silentWarn(e, "ChatPage"); }
  };

  // 费用确认后重发最后一条 user（不新增用户消息、不重置会话 id；空 assistant 占位已由 finish 移除）。
  const resendLastUser = () => {
    if (busy) return;
    const cur = msgsRef.current || msgs;
    let lastUser = -1;
    for (let i = cur.length - 1; i >= 0; i--) {
      if (cur[i].role === "user") { lastUser = i; break; }
    }
    if (lastUser < 0) return;
    const text = cur[lastUser].text;
    const base = cur.slice(0, lastUser);
    starsRef.current = new Set();
    pinsRef.current = new Set();
    continueRef.current = { active: false, idx: -1 };
    historyRef.current = buildHistory(base, chatMode);
    setMsgs(base);
    pendingRef.current = { text, images: cur[lastUser].images || [], files: cur[lastUser].files || [] };
    // 后端旧作业已结束，必须换新 stream_id 新建作业，否则会被当作订阅已完成作业。
    streamIdRef.current = newStreamId();
    setBusy(true);
    setBackendNote("");
  };

  // ── A7 多选消息模式（对齐原程序 multi-bar）──
  const toggleMulti = () => setMultiSel(multiSel === null ? new Set() : null);

  const toggleSelect = (idx) => {
    setMultiSel((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const multiDelete = async () => {
    if (!multiSel || !multiSel.size) return;
    const idxs = [...multiSel].sort((a, b) => b - a);
    if (!(await confirmDialog(`删除选中的 ${idxs.length} 条消息？`, { danger: true, okText: "删除" }))) return;
    setMsgs((m) => m.filter((_, i) => !multiSel.has(i)));
    starsRef.current = new Set();
    pinsRef.current = new Set();
    setMultiSel(null);
  };

  const multiExport = () => {
    if (!multiSel || !multiSel.size) return;
    const sel = msgs.filter((_, i) => multiSel.has(i));
    const md = sel.map((m) => (m.role === "user" ? `## 我\n\n${m.text || ""}` : `## 助手\n\n${m.think ? `（思考）${m.think}\n\n` : ""}${m.text || ""}`)).join("\n\n");
    navigator.clipboard.writeText(md).then(() => toast("已复制选中消息到剪贴板")).catch(() => {});
  };

  const multiStar = () => {
    if (!multiSel || !multiSel.size) return;
    setMsgs((m) => m.map((x, i) => {
      if (!multiSel.has(i)) return x;
      const key = `${x.role}\u0000${x.text || ""}`;
      starsRef.current.add(key);
      return { ...x, starred: true };
    }));
    setMultiSel(null);
    toast(`已收藏 ${multiSel.size} 条消息`);
  };

  // ── 收藏/固定查看面板 ──
  const toggleStarPanel = () => setStarPanel(!starPanel);

  const doSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearchBusy(true);
    try {
      const d = await api.searchSessions(searchQuery.trim(), { type: searchType });
      setSearchResults(d.results || []);
    } catch (e) { silentWarn(e, "ChatPage"); toast("搜索失败：后端未响应，请稍后重试"); }
    setSearchBusy(false);
  };

  const doSearchWithQuery = async (q) => {
    setSearchQuery(q);
    setCmdPanel(false);
    setSearchPanel(true);
    setSearchBusy(true);
    try {
      const d = await api.searchSessions(q);
      setSearchResults(d.results || []);
    } catch (e) { silentWarn(e, "ChatPage"); toast("搜索失败：后端未响应，请稍后重试"); }
    setSearchBusy(false);
  };

  const openSearchResult = async (r) => {
    setSearchPanel(false);
    setSearchQuery("");
    setSearchResults([]);
    const got = await pickSession(r.session_id);
    if (got) {
      setActiveId(r.session_id);
      starsRef.current = new Set((got.stars || []).map((s) => `${s.role}\u0000${s.content}`));
      pinsRef.current = new Set((got.pinned || []).map((p) => `\u0000${p}`));
      setMsgs(got.messages.map((m) => {
        const key = `${m.role}\u0000${m.text || ""}`;
        return { ...m, starred: starsRef.current.has(key), pinned: pinsRef.current.has(key), time: m.time || "" };
      }));
      // 搜索定位：目标在窗口外时先展开渲染窗口（渲染后由 pendingScrollRef 定位）
      const targetIdx = Number(r.index) || 0;
      const start = Math.max(0, Math.min((got.messages || []).length - VIRT_WINDOW, targetIdx - 5));
      pendingScrollRef.current = targetIdx;
      setRenderStart(start);
    }
  };

  const gotoMessage = (idx) => {
    if (!scrollRef.current) return;
    // 目标在窗口外：先展开渲染窗口再定位（渲染后由 pendingScrollRef 触发 scrollIntoView）
    if (idx < renderStart || idx >= msgs.length) {
      const start = Math.max(0, Math.min(msgs.length - VIRT_WINDOW, idx - 5));
      pendingScrollRef.current = idx;
      setRenderStart(start);
      return;
    }
    const el = document.querySelector(`[data-msg-idx="${idx}"]`);
    if (el) el.scrollIntoView({ behavior: mq("(prefers-reduced-motion: reduce)") ? "auto" : "smooth", block: "center" });
  };

  const onRenameSession = async (id, name) => {
    try {
      await api.renameSession(id, name);
      refreshSessions();
    } catch (e) { silentWarn(e, "ChatPage"); }
  };

  const onEditTags = async (id, tags) => {
    try {
      await api.tagSession(id, tags);
      refreshSessions();
    } catch (e) { silentWarn(e, "ChatPage"); }
  };

  const onExportSession = () => {
    import("../exporters.js").then(({ exportSession, exportSessionJson }) => {
      const meta = { model: activeSession?.model || "", name: activeSession?.title || "" };
      exportSession(msgs, meta);
      exportSessionJson(msgs);
    });
  };

  const onImportSession = async (raw) => {
    try {
      const { parseImportedText } = await import("../exporters.js");
      const parsed = parseImportedText(raw).filter((m) => m && ["user", "assistant", "system", "tool"].includes(m.role)).slice(0, 2000);
      if (!parsed.length) {
        toast("未解析到有效消息（支持 JSON 数组 / {\"messages\":[...]} / JSONL）");
        return;
      }
      const userMsg = parsed.find((m) => m.role === "user");
      const sid = await api.saveSession({
        name: (userMsg?.content || "导入会话").replace(/\s+/g, " ").slice(0, 24),
        messages: parsed.map((m) => ({ role: m.role, content: String(m.content || "") })),
      });
      if (sid) refreshSessions();
    } catch (e) { silentWarn(e, "ChatPage"); }
  };

  // ── 窗口化渲染：会话切换/消息加载时重置窗口到末尾 ──
  React.useEffect(() => {
    setRenderStart(Math.max(0, msgs.length - VIRT_WINDOW));
  }, [activeId]);
  React.useEffect(() => {
    if (msgs.length === 0) return;
    // 会话切换/消息删除后窗口越界：回退窗口覆盖末尾（防全部消息不渲染）
    setRenderStart((s) => (msgs.length <= s ? Math.max(0, msgs.length - VIRT_WINDOW) : s));
    // 新消息到来：用户在底部则贴底 + 窗口覆盖末尾；不在底部则保持阅读位置
    const sc = scrollRef.current;
    if (sc && atBottomRef.current) {
      sc.scrollTop = sc.scrollHeight;
      setRenderStart((s) => (s + VIRT_WINDOW < msgs.length ? Math.max(0, msgs.length - VIRT_WINDOW) : s));
    }
  }, [msgs]);
  // 顶部哨兵：滚动到窗口顶部时加载更早消息
  React.useEffect(() => {
    const sentinel = topSentinelRef.current;
    if (!sentinel || renderStart === 0) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0] && entries[0].isIntersecting) {
          setRenderStart((s) => Math.max(0, s - VIRT_LOAD));
        }
      },
      { root: scrollRef.current, rootMargin: "160px 0px" }
    );
    obs.observe(sentinel);
    return () => obs.disconnect();
  }, [renderStart, msgs.length]);
  // 定位等待：gotoMessage 展开窗口后执行 scrollIntoView
  React.useEffect(() => {
    if (pendingScrollRef.current == null) return;
    const idx = pendingScrollRef.current;
    pendingScrollRef.current = null;
    const t = setTimeout(() => {
      const el = document.querySelector(`[data-msg-idx="${idx}"]`);
      if (el) el.scrollIntoView({ behavior: mq("(prefers-reduced-motion: reduce)") ? "auto" : "smooth", block: "center" });
    }, 60);
    return () => clearTimeout(t);
  }, [renderStart]);

  const activeSession = sessions.find((s) => s.id === activeId);
  const isTask = mode === "task";

  return (
    <div className="chat-page">
      <SpeakingPill />
      <div className="chat-row">
        {listOpen && <div className="drawer-scrim scrim-list" onClick={() => setListOpen(false)} />}
        {listOpen && (
          <SessionList
            sessions={sessions}
            activeId={activeId}
            onPick={onPickSession}
            onClose={() => setListOpen(false)}
            onDelete={onDeleteSession}
            onPin={onPinSession}
            onRename={onRenameSession}
            onEditTags={onEditTags}
            onExport={onExportSession}
            onImport={onImportSession}
            onBatchDelete={onBatchDeleteSessions}
          />
        )}
        <div className="chat-main" onPaste={onPasteFiles}>
          {dropActive && (
            <div className="drop-overlay" aria-hidden="true">
              <div className="drop-overlay-inner">
                <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21.4 11.05l-9.19 9.19a5 5 0 01-7.07-7.07l9.19-9.19a3.5 3.5 0 014.95 4.95l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
                </svg>
                <div className="drop-overlay-title">松开即可添加附件</div>
                <div className="drop-overlay-sub">图片将作为视觉输入 · 其它文件随消息提供路径 · 控制台拖出的文件读入输入框</div>
              </div>
            </div>
          )}
          <div className="chat-header">
            <button className="icon-btn" title="会话列表" aria-label="会话列表" onClick={() => setListOpen(!listOpen)}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <div className="chat-header-title">
              <div className="cht-top">
                <span
                  className={`ch-live ${dataMode === "backend" ? "on" : "off"}`}
                  role="img"
                  aria-label={dataMode === "backend" ? "服务已连接" : "服务未连接"}
                  title={dataMode === "backend" ? "服务已连接" : "服务未连接"}
                />
                <b>{activeId ? activeSession?.title || "历史会话" : "新会话"}</b>
              </div>
              <span className="chat-header-sub">
                {backendNote
                  ? backendNote
                  : activeId
                  ? `${activeSession?.model || ""} · ${activeSession?.time || ""}`
                  : isTask
                  ? "任务模式：全部工具自动可用，目录内全自动"
                  : "对话模式：纯问答，不调用任何工具"}
              </span>
            </div>

            <div className="chat-header-right">
              {/* 主操作：模式切换 + 运行开关 */}
              <div className="ch-actions">
                <div className="mode-switch" role="group" aria-label="工作模式">
                  <button
                    className={`mode-btn ${!isTask ? "mode-on" : ""}`}
                    aria-pressed={!isTask}
                    onClick={() => isTask && switchMode("dialog")}
                  >
                    对话
                  </button>
                  <button
                    className={`mode-btn ${isTask ? "mode-on" : ""}`}
                    aria-pressed={isTask}
                    onClick={() => !isTask && switchMode("task")}
                  >
                    任务
                  </button>
                </div>
                {!isTask && (
                  <button
                    className={`web-switch ${webSearch ? "web-on" : ""}`}
                    onClick={toggleWebSearch}
                    title="对话模式联网搜索：开启后 AI 可实时搜索最新信息（天气/新闻/行情/网页），大幅减弱幻觉"
                  >
                    <span className={`web-dot ${webSearch ? "web-dot-on" : ""}`} />
                    <span className="web-label">联网</span>
                  </button>
                )}
                <button
                  className={`web-switch ${quietMode ? "web-on" : ""}`}
                  onClick={onToggleQuiet}
                  title="纯净对话：开启后不注入长期记忆/核心自我/大脑，AI 以全新姿态应答，也不会自动写入记忆（设置 → 高级 可同步配置）"
                >
                  <span className={`web-dot ${quietMode ? "web-dot-on" : ""}`} />
                  <span className="web-label">纯净</span>
                </button>
              </div>

              <span className="ch-sep" aria-hidden="true" />

              {/* 高频面板 */}
              <div className="ch-actions">
                <button className="icon-btn" title="控制台（参数/文件/进程）" aria-label="控制台（参数/文件/进程）" onClick={() => setAuxOpen(!auxOpen)}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2" />
                    <path d="M3 9h18M3 15h18M9 3v18" />
                  </svg>
                </button>
                <button className="icon-btn" title="上下文面板" aria-label="上下文面板" onClick={() => setCtxOpen(!ctxOpen)}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="4" width="18" height="16" rx="2" />
                    <path d="M3 9h18M9 4v16" />
                  </svg>
                </button>
              </div>

              {/* 低频工具溢出菜单 */}
              <div className="ch-more" ref={moreRef}>
                <button
                  className={`icon-btn ${moreOpen ? "is-on" : ""}`}
                  title="更多工具"
                  aria-label="更多工具"
                  aria-haspopup="menu"
                  aria-expanded={moreOpen}
                  onClick={() => setMoreOpen((v) => !v)}
                >
                  <Icon name="more" size={16} fill="currentColor" />
                </button>
                {moreOpen && (
                  <div className="ch-menu" role="menu">
                    <button role="menuitem" className="ch-menu-item" onClick={() => { setMoreOpen(false); setBatchPanel(true); }}>
                      <Icon name="layers" size={15} /><span className="ch-menu-label">批量任务</span>
                    </button>
                    <button role="menuitem" className="ch-menu-item" onClick={() => { setMoreOpen(false); setTimelinePanel(!timelinePanel); }}>
                      <Icon name="activity" size={15} /><span className="ch-menu-label">会话轨迹</span>
                    </button>
                    <button role="menuitem" className="ch-menu-item" onClick={() => { setMoreOpen(false); openVariants(); }}>
                      <Icon name="git-branch" size={15} /><span className="ch-menu-label">回复变体</span>
                    </button>
                    <button role="menuitem" className="ch-menu-item" onClick={() => { setMoreOpen(false); setFimPanel(true); }}>
                      <Icon name="code" size={15} /><span className="ch-menu-label">FIM 代码补全</span>
                    </button>
                    <button role="menuitem" className="ch-menu-item" onClick={() => { setMoreOpen(false); setSearchPanel(!searchPanel); }}>
                      <Icon name="search" size={15} /><span className="ch-menu-label">全局搜索</span>
                    </button>
                    <button role="menuitem" className={`ch-menu-item ${multiSel ? "is-on" : ""}`} onClick={() => { setMoreOpen(false); toggleMulti(); }}>
                      <Icon name="list" size={15} /><span className="ch-menu-label">多选消息</span>
                      {multiSel && <Icon name="check" size={13} className="ch-menu-check" />}
                    </button>
                    <button role="menuitem" className={`ch-menu-item ${starPanel ? "is-on" : ""}`} onClick={() => { setMoreOpen(false); toggleStarPanel(); }}>
                      <Icon name="star" size={15} /><span className="ch-menu-label">收藏与固定</span>
                      {starPanel && <Icon name="check" size={13} className="ch-menu-check" />}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div
            className="chat-scroll"
            ref={scrollRef}
            role="log"
            aria-live="polite"
            aria-relevant="additions text"
            data-density={density}
            style={{ fontSize: `${fontSize}px` }}
            onScroll={(e) => {
              const el = e.currentTarget;
              const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 120;
              atBottomRef.current = atBottom;
              setShowJump(!atBottom && el.scrollHeight > el.clientHeight + 240);
            }}
          >
            {multiSel && multiSel.size > 0 && (
              <div className="multi-bar">
                <span>已选 {multiSel.size} 条</span>
                <button className="msg-op" onClick={multiDelete}>🗑 删除选中</button>
                <button className="msg-op" onClick={multiExport}>📋 导出选中</button>
                <button className="msg-op" onClick={multiStar}>⭐ 收藏选中</button>
                <button className="msg-op" onClick={toggleMulti}>✕ 退出多选</button>
              </div>
            )}
            {msgs.length === 0 && (
              <div className="chat-empty">
                <div className="empty-whale" aria-hidden="true">🐳</div>
                <h1>{isTask ? "今天想做点什么？" : "随便聊聊"}</h1>
                <p>
                  {isTask
                    ? "看得见屏幕、听得见语音、动得了鼠标键盘与浏览器-115项能力随叫随到"
                    : "纯问答 · 不调用工具 · 适合聊天、翻译、写作、答疑"}
                </p>
                <div className="empty-suggest">
                  {(isTask
                    ? ["帮我调研一个主题并输出报告", "把这个文件夹整理成Markdown索引", "分析这张图片的内容", "帮我定时巡检一个网站"]
                    : ["用一句话介绍你自己", "翻译：Knowledge is power", "帮我写一首关于海的短诗", "帮我解释一个概念，比如 HTTP"]
                  ).map((s) => (
                    <button key={s} className="suggest-chip" onClick={() => onSend(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {renderStart > 0 && (
              <>
                <div ref={topSentinelRef} style={{ height: 1 }} />
                <div className="vir-spacer" style={{ height: renderStart * VIRT_EST_H }} />
              </>
            )}
            {msgs.slice(renderStart).map((m, i) => {
              const gi = renderStart + i;
              return (
                <div
                  key={gi}
                  data-msg-idx={gi}
                  className={`msg-wrap ${multiSel && multiSel.has(gi) ? "msg-wrap-selected" : ""}`}
                  role={multiSel ? "checkbox" : undefined}
                  aria-checked={multiSel ? multiSel.has(gi) : undefined}
                  tabIndex={multiSel ? 0 : undefined}
                  onClick={multiSel ? (e) => {
                    // 不吞掉消息内操作按钮（复制/收藏/引用…）的点击——否则点复制会顺带切换选中
                    if (e.target && e.target.closest && e.target.closest("button, a, input, textarea, select, .msg-ops")) return;
                    toggleSelect(gi);
                  } : undefined}
                  onKeyDown={multiSel ? (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleSelect(gi); } } : undefined}
                >
                  <Message
                    msg={m}
                    onResend={onSend}
                    onStar={() => onStarMsg(gi)}
                    onPin={() => onPinMsg(gi)}
                    onQuote={() => onQuoteMsg(gi)}
                    onFork={() => onForkMsg(gi)}
                    onEdit={() => onEditMsg(gi)}
                    onRegenerate={m.role === "assistant" ? onRegenerate : undefined}
                    onContinue={m.role === "assistant" && !m.streaming ? () => onContinue(gi) : undefined}
                    onFocusActivity={focusActivity}
                  />
                </div>
              );
            })}
          </div>

          {showJump && msgs.length > 0 && (
            <button
              className="jump-bottom"
              title="回到最新"
              style={composerH ? { bottom: composerH + 14 } : undefined}
              onClick={() => {
                const sc = scrollRef.current;
                if (sc) sc.scrollTop = sc.scrollHeight;
                atBottomRef.current = true;
                setShowJump(false);
              }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14M6 13l6 6 6-6" />
              </svg>
            </button>
          )}

          <div className="composer-dock" ref={composerDockRef}>
            <Composer ref={composerRef} busy={busy} onSend={onSend} onStop={onStop} isTask={isTask} onPluginRun={onPluginRun} />
          </div>
        </div>

        {ctxOpen && <div className="drawer-scrim scrim-ctx" onClick={() => setCtxOpen(false)} />}
        {ctxOpen && (
          <ContextPanel data={{ ...(ctx || {}), session: sessionStats }} loading={!ctx && !loadErr} err={loadErr || ""} onClose={() => setCtxOpen(false)} />
        )}

        {auxOpen && !auxPop && <div className="drawer-scrim scrim-aux" onClick={() => setAuxOpen(false)} />}
        {auxOpen && !auxPop && (
          <AuxPanel onClose={() => setAuxOpen(false)} onInjectFile={onInjectFile} activity={liveActivity}
            products={liveProducts}
            tab={auxTab} onTabChange={setAuxTab} onPopout={() => setAuxPop(true)} />
        )}
        {auxOpen && auxPop && popoutEl && createPortal(
          <AuxPanel onClose={() => { setAuxPop(false); setAuxOpen(false); }} onInjectFile={onInjectFile} activity={liveActivity}
            products={liveProducts}
            tab={auxTab} onTabChange={setAuxTab} onPopout={() => setAuxPop(false)} inPopout />,
          popoutEl
        )}
      </div>

      <StatusBar mode={mode} onSwitchMode={switchMode} generating={genState.on} generatingText={genState.text} tps={genTps} />

      <ConfirmGate
        req={promptReq}
        onRespond={async (payload) => {
          const isCost = promptReq && String(promptReq.type || "") === "confirm";
          setPromptReq(null);
          if (isCost) {
            // 费用确认：本地闸门，不走 /v1/respond（后端作业已结束）。确认则带
            // cost_confirmed 重发；取消则静默丢弃（已移除了空 assistant 占位）。
            if (payload && payload.confirm) {
              costConfirmedRef.current = true;
              resendLastUser();
            }
            return;
          }
          try {
            await api.respond(payload);
          } catch (e) { silentWarn(e, "ChatPage"); }
        }}
      />

      <BatchPanel
        open={batchPanel}
        onClose={() => setBatchPanel(false)}
        files={batchFiles}
        onFiles={setBatchFiles}
        tpl={batchTpl}
        onTpl={setBatchTpl}
        onDo={doBatch}
      />

      <CmdPanel
        open={cmdPanel}
        onClose={() => setCmdPanel(false)}
        query={cmdQuery}
        onQuery={setCmdQuery}
        onSearch={doSearchWithQuery}
        onNewChat={() => onPickSession(null)}
        onOpenSearch={() => setSearchPanel(true)}
        onGoWorkbench={onGoWorkbench}
        onOpenTimeline={() => setTimelinePanel(true)}
        onOpenVariants={openVariants}
        onOpenFim={() => setFimPanel(true)}
        onOpenStar={() => setStarPanel(true)}
        onExport={onExportSession}
        onGoSettings={onGoSettings}
      />

      <TimelinePanel
        open={timelinePanel}
        onClose={() => setTimelinePanel(false)}
        msgs={msgs}
        onGoto={gotoMessage}
      />

      <FimPanel
        open={fimPanel}
        onClose={() => setFimPanel(false)}
        prompt={fimPrompt}
        onPrompt={setFimPrompt}
        suffix={fimSuffix}
        onSuffix={setFimSuffix}
        result={fimResult}
        busy={fimBusy}
        onDo={doFim}
        onInsert={(text) => composerRef.current?.insertText(text)}
      />

      <VariantPanel
        open={variantPanel}
        onClose={() => setVariantPanel(false)}
        variants={variants}
        onRestore={restoreVariant}
      />

      <SearchPanel
        open={searchPanel}
        onClose={() => setSearchPanel(false)}
        query={searchQuery}
        onQuery={setSearchQuery}
        type={searchType}
        onType={setSearchType}
        results={searchResults}
        onResults={setSearchResults}
        busy={searchBusy}
        onBusy={setSearchBusy}
        onSearch={doSearch}
        onOpen={openSearchResult}
      />

      <StarPanel
        open={starPanel}
        onClose={toggleStarPanel}
        msgs={msgs}
        onStar={onStarMsg}
        onPin={onPinMsg}
        onGoto={gotoMessage}
      />
    </div>
  );
}