import React from "react";
import Message from "./Message.jsx";
import Composer from "./Composer.jsx";
import SessionList from "./SessionList.jsx";
import ContextPanel from "./ContextPanel.jsx";
import StatusBar from "./StatusBar.jsx";
import ConfirmGate from "./ConfirmGate.jsx";
import AuxPanel from "./AuxPanel.jsx";
import { FlashContext, ToastContext } from "./FlashToast.jsx";
import { ModeContext, DisplayContext } from "../App.jsx";
import * as api from "../api.js";
import { cleanForSpeech, splitSentences, enqueueSpeak, getVoiceConfig, onSpeechState, stopSpeak } from "../ttsUtil.js";

// 全局朗读状态浮标：合成中/播放中均有可见反馈，点击可停
function SpeakingPill() {
  const [st, setSt] = React.useState({ speaking: false, loading: false });
  React.useEffect(() => onSpeechState(setSt), []);
  if (!st.speaking && !st.loading) return null;
  return (
    <div
      onClick={() => st.speaking && stopSpeak()}
      title={st.speaking ? "点击停止朗读" : "正在合成语音…"}
      style={{
        position: "fixed", right: 18, bottom: 84, zIndex: 60,
        padding: "7px 14px", borderRadius: 999, cursor: st.speaking ? "pointer" : "default",
        fontSize: 12.5, fontWeight: 600, color: "var(--text, #eee)",
        background: st.speaking ? "linear-gradient(135deg,#0ea5e9,#2563eb)" : "rgba(120,130,150,.85)",
        boxShadow: "0 4px 14px rgba(0,0,0,.35)", userSelect: "none",
      }}
    >
      {st.speaking ? "🔊 正在朗读 · 点击停止" : "⏳ 正在合成语音…"}
    </div>
  );
}

// ── 多轮消息链构造（官方规范）────────────────────────
// tools 模式下必须完整回传：assistant(reasoning_content + tool_calls) → tool 结果
function buildMessageChain(msgs) {
  const out = [];
  for (const m of msgs) {
    if (m.role === "user") {
      out.push({ role: "user", content: m.text || "" });
    } else if (m.role === "assistant") {
      const am = { role: "assistant", content: m.text || "" };
      if (m.think) am.reasoning_content = m.think;
      if (m.tools && m.tools.length) {
        am.tool_calls = m.tools.map((t, i) => ({
          id: `call_${i}`,
          type: "function",
          function: { name: t.tool, arguments: JSON.stringify(t.args || {}) },
        }));
        out.push(am);
        m.tools.forEach((t, i) => {
          out.push({
            role: "tool",
            tool_call_id: `call_${i}`,
            name: t.tool,
            content: String(t.result || "").slice(0, 4000),
          });
        });
      } else {
        out.push(am);
      }
    }
  }
  return out;
}

// ── 真实后端流式对话 ────────────────────────────────
function useBackendChat(busy, setBusy, setMsgs, pendingRef, historyRef, connRef, chatMode, onFinished, stopSignalRef, onPrompt, setGenState, continueRef) {
  const stopRef = React.useRef(false);

  React.useEffect(() => {
    if (!busy) return;
    let alive = true;
    stopRef.current = false;
    const userText = pendingRef.current.text;
    const images = pendingRef.current.images || [];
    const isContinue = continueRef && continueRef.current && continueRef.current.active;
    const continueIdx = isContinue ? continueRef.current.idx : -1;

    let msg = null;
    if (isContinue) {
      setMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, streaming: true } : x)));
    } else {
      msg = { role: "assistant", think: "", tools: [], text: "", streaming: true, time: new Date().toTimeString().slice(0, 8) };
      setMsgs((m) => [...m, { role: "user", text: userText, time: new Date().toTimeString().slice(0, 8) }, msg]);
    }
    if (stopSignalRef.current) stopSignalRef.current = new AbortController();

    const targetIdx = () => (isContinue ? continueIdx : -1);

    // ── 自动朗读（跟随设置 voice_config.auto_mode：off/sentence/full）──
    let voiceSettings = null;
    getVoiceConfig().then((v) => { voiceSettings = v; });
    let acc = "";            // 本轮流式全文累加
    let spokenCount = 0;     // 已入队句数（sentence 模式游标）
    const feedAuto = () => {
      if (!voiceSettings || voiceSettings.auto_mode !== "sentence") return;
      const all = splitSentences(cleanForSpeech(acc));
      while (spokenCount < all.length - 1) {  // 末句可能是半截，等下一包/收尾
        enqueueSpeak(all[spokenCount], voiceSettings);
        spokenCount += 1;
      }
    };

    (async () => {
      let done = false;
      const finish = (ok) => {
        if (done || !alive) return;
        done = true;
        setMsgs((m) => m.map((x, i) => (i === (isContinue ? continueIdx : m.length - 1) ? { ...x, streaming: false } : x)));
        setBusy(false);
        setGenState({ on: false, text: "" });
        // 自动朗读收尾：full 一次性读整段；sentence 补读最后半截句
        try {
          if (ok && voiceSettings && voiceSettings.auto_mode !== "off" && acc.trim()) {
            if (voiceSettings.auto_mode === "full") {
              enqueueSpeak(cleanForSpeech(acc), voiceSettings);
            } else {
              const all = splitSentences(cleanForSpeech(acc));
              while (spokenCount < all.length) {
                enqueueSpeak(all[spokenCount], voiceSettings);
                spokenCount += 1;
              }
            }
          }
        } catch {}
        onFinished && onFinished({ userText, msg, ok, isContinue });
      };
      try {
        const history = isContinue
          ? buildMessageChain((historyRef.current || []).slice(0, continueIdx + 1))
          : (historyRef.current || []).slice(-80);
        await api.streamChat(
          {
            messages: isContinue ? history : [...history, { role: "user", content: userText, ...(images.length ? { images } : {}) }],
            // 不传 thinking：后端 _chat_kwargs 使用 config.json 的 thinking（控制台/设置选择的档位即时生效）
            mode: chatMode,
            toolsEnabled: chatMode === "task",
            continue_prefix: isContinue,
          },
          {
            onReasoning: (t) => {
              if (!alive || stopRef.current) return;
              if (isContinue) {
                setMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, think: (x.think || "") + t } : x)));
              } else {
                msg.think += t;
                setMsgs((m) => [...m]);
              }
              setGenState({ on: true, text: "🤔 思考中…" });
            },
            onContent: (t) => {
              if (!alive || stopRef.current) return;
              acc += t;
              feedAuto();
              if (isContinue) {
                setMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, text: (x.text || "") + t } : x)));
              } else {
                msg.text += t;
                setMsgs((m) => [...m]);
              }
              setGenState({ on: true, text: "⏳ 等待模型响应…" });
            },
            onToolStart: ({ name, args }) => {
              if (!alive || stopRef.current) return;
              let parsed = args;
              try {
                parsed = typeof args === "string" && args ? JSON.parse(args) : args;
              } catch {}
              if (isContinue) {
                setMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, tools: [...(x.tools || []), { tool: name, args: parsed, status: "running" }] } : x)));
              } else {
                msg.tools.push({ tool: name, args: parsed, status: "running" });
                setMsgs((m) => [...m]);
              }
              setGenState({ on: true, text: "⚙ 正在执行「" + name + "」…" });
            },
            onTool: ({ name, result }) => {
              if (!alive || stopRef.current) return;
              if (isContinue) {
                setMsgs((m) => m.map((x, i) => {
                  if (i !== continueIdx) return x;
                  const tools = [...(x.tools || [])];
                  const card = [...tools].reverse().find((t) => t.tool === name && t.status === "running");
                  if (card) {
                    card.status = "done";
                    card.result = String(result || "").slice(0, 500);
                  } else {
                    tools.push({ tool: name, result: String(result || "").slice(0, 500), status: "done" });
                  }
                  return { ...x, tools };
                }));
              } else {
                const card = [...msg.tools].reverse().find((t) => t.tool === name && t.status === "running");
                if (card) {
                  card.status = "done";
                  card.result = String(result || "").slice(0, 500);
                } else {
                  msg.tools.push({ tool: name, result: String(result || "").slice(0, 500), status: "done" });
                }
                setMsgs((m) => [...m]);
              }
            },
            onToolDuration: ({ name, duration }) => {
              if (!alive || stopRef.current) return;
              if (isContinue) {
                setMsgs((m) => m.map((x, i) => {
                  if (i !== continueIdx) return x;
                  const tools = [...(x.tools || [])];
                  const card = [...tools].reverse().find((t) => t.tool === name && t.status === "done");
                  if (card) card.duration = duration;
                  return { ...x, tools };
                }));
              } else {
                const card = [...msg.tools].reverse().find((t) => t.tool === name && t.status === "done");
                if (card) card.duration = duration;
              }
            },
            onUsage: (u) => {
              if (alive) {
                if (isContinue) setMsgs((m) => m.map((x, i) => (i === continueIdx ? { ...x, usage: u } : x)));
                else msg.usage = u;
              }
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
              if (alive && !stopRef.current) onPrompt && onPrompt({ type: "ask", ...ev });
            },
            onApprovalRequest: (ev) => {
              if (alive && !stopRef.current) onPrompt && onPrompt({ type: "approval", ...ev });
            },
            onPermissionRequest: (ev) => {
              if (alive && !stopRef.current) onPrompt && onPrompt({ type: "permission", ...ev });
            },
            onDone: () => finish(true),
            onError: (e) => {
              if (!alive) return;
              setMsgs((m) => m.map((x, i) => (i === (isContinue ? continueIdx : m.length - 1) ? { ...x, text: (x.text || "") + "\n\n⚠️ 后端错误：" + e, streaming: false } : x)));
              setBusy(false);
              setGenState({ on: false, text: "" });
            },
          },
          stopSignalRef.current ? stopSignalRef.current.signal : undefined
        );
        finish(true);
      } catch (err) {
        if (!alive) return;
        setBusy(false);
        setGenState({ on: false, text: "" });
        try {
          onFinished?.({
            userText: pendingRef.current.text,
            msg: null,
            ok: false,
            isContinue: false,
            error: err.message || String(err),
          });
        } catch {}
      }
    })();

    return () => {
      alive = false;
      stopRef.current = true;
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

  React.useEffect(() => {
    (async () => {
      try {
        const ok = await api.checkBackend();
        if (!ok) {
          setMode("offline");
          setLoadErr("后端服务未连接：请启动「鲸语 WhaleTalk」(web_app.py) 后刷新页面");
          return;
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
        } catch {}
        try {
          const st = await api.getStatus();
          if (st) setPeakInfo({ on: !!st.peak_hour, warn: st.peak_warning !== false });
        } catch {}

        setLoadErr("");
      } catch {
        setMode("offline");
        setLoadErr("后端服务未连接：请启动「鲸语 WhaleTalk」(web_app.py) 后刷新页面");
      }
    })();
  }, []);

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
          const mapped = d.messages
            .map((m) => {
              if (m.role === "user") {
                return { role: "user", text: m.content };
              }
              // tool 结果消息：已按顺序归并进 assistant 的工具卡片，不单独渲染
              if (m.role === "tool" || m.role === "system") {
                return null;
              }
              const tools = (m.tool_calls || []).map((tc) => {
                let args = {};
                try {
                  args = JSON.parse(tc.function?.arguments || "{}");
                } catch {}
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
                };
              });
              return {
                role: "assistant",
                think: m.reasoning_content || "",
                tools,
                text: m.content,
                streaming: false,
              };
            })
            .filter(Boolean);
          // 兜底：历史上后端曾把 tool_calls 截到 16 条，孤儿 tool 消息挂到最后一个 assistant
          const lastAsst = [...mapped].reverse().find((x) => x.role === "assistant");
          if (lastAsst) {
            d.messages.forEach((mm, i) => {
              if (mm.role === "tool" && !usedToolIdx.has(i)) {
                lastAsst.tools.push({
                  tool: mm.name || mm.tool_call_id || "tool",
                  args: {},
                  status: "done",
                  result: String(mm.content || "").slice(0, 500),
                  duration: "—",
                });
              }
            });
          }
          return { messages: mapped, usage: d.usage_total, stars: d.stars, pinned: d.pinned, tags: d.tags };
        }
      } catch {}
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

  return { mode, sessions, ctx, history, pickSession, refreshSessions, setCtx, loadErr, peakInfo };
}

export default function ChatPage({ onGoWorkbench, onGoSettings }) {
  const { mode, switchMode } = React.useContext(ModeContext);
  const { density, fontSize } = React.useContext(DisplayContext);
  const { flash } = React.useContext(FlashContext);
  const { toast } = React.useContext(ToastContext);
  const [genState, setGenState] = React.useState({ on: false, text: "" });
  const [activeId, setActiveId] = React.useState(null);
  const [msgs, setMsgs] = React.useState([]);
  const [busy, setBusy] = React.useState(false);
  const [ctxOpen, setCtxOpen] = React.useState(false);
  const [listOpen, setListOpen] = React.useState(true);
  const [auxOpen, setAuxOpen] = React.useState(true);
  const [promptReq, setPromptReq] = React.useState(null);
  const [backendNote, setBackendNote] = React.useState("");
  const [multiSel, setMultiSel] = React.useState(null); // null=关闭, Set(index)
  const [starPanel, setStarPanel] = React.useState(false);
  const [searchPanel, setSearchPanel] = React.useState(false);
  const [searchQuery, setSearchQuery] = React.useState("");
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
  const [cmdQuery, setCmdQuery] = React.useState("");
  const [batchPanel, setBatchPanel] = React.useState(false);
  const [batchFiles, setBatchFiles] = React.useState("");
  const [batchTpl, setBatchTpl] = React.useState("请处理以下文件：{file}");

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
    const onKey = (e) => {
      if (e.ctrlKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdPanel(true);
        setCmdQuery("");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const pendingRef = React.useRef({ text: "", images: [] });
  const historyRef = React.useRef([]);
  const connRef = React.useRef("auto");
  const stopSignalRef = React.useRef(null);
  const scrollRef = React.useRef(null);
  const composerRef = React.useRef(null);
  const resendIdxRef = React.useRef(null);
  const starsRef = React.useRef(new Set());
  const pinsRef = React.useRef(new Set());
  const genStateRef = React.useRef({ on: false, text: "" });
  const continueRef = React.useRef({ active: false, idx: -1 });
  const setGenStateThrottled = React.useCallback((s) => {
    const prev = genStateRef.current;
    if (prev.on === s.on && prev.text === s.text) return;
    genStateRef.current = s;
    setGenState(s);
  }, []);

  const { mode: dataMode, sessions, ctx, history, pickSession, refreshSessions, setCtx, loadErr, peakInfo } = useDataSources();
  connRef.current = dataMode;

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

  useBackendChat(
    dataMode === "backend" && busy,
    setBusy,
    setMsgs,
    pendingRef,
    historyRef,
    connRef,
    mode,
    invokeFinished,
    stopSignalRef,
    setPromptReq,
    setGenStateThrottled,
    continueRef
  );

  // 会话保存：每次 render 同步到 ref，保证 useBackendChat 用的是最新闭包
  onFinishedRef.current = ({ userText, msg, ok, isContinue, error }) => {
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
        // 续写：保存更新后的完整消息链（用实参 msg 而非陈旧闭包 msgs）
        try {
          const updated = buildMessageChain([
            ...(msgs.length ? msgs : []),
            { role: "user", text: userText, time: new Date().toTimeString().slice(0, 8) },
            msg,
          ]);
          await api.saveSession({
            id: activeId || undefined,
            name: activeSession?.title || userText.replace(/\s+/g, " ").slice(0, 24),
            messages: updated,
          });
          refreshSessions();
        } catch {}
        continueRef.current = { active: false, idx: -1 };
        return;
      }
      // 用实参 msg（流式引用对象，内容完整）而非闭包 msgs（陈旧/可能为空）保存会话
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
          name: isExisting
            ? (activeSession?.title || userText.replace(/\s+/g, " ").slice(0, 24))
            : userText.replace(/\s+/g, " ").slice(0, 24),
          messages: [
            { role: "user", content: userText },
            {
              role: "assistant",
              content: msg.text,
              reasoning_content: msg.think || "",
              ...(calls.length ? { tool_calls: calls } : {}),
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
          historyRef.current = buildMessageChain([
            ...(msgs.length ? msgs : []),
            { role: "user", text: userText, time: new Date().toTimeString().slice(0, 8) },
            msg,
          ]).slice(-80);
          refreshSessions();
        }
      } catch {}
    };
    saveChatFinished();
  };

  const onSend = (text, attachments = []) => {
    if (busy && !resendIdxRef.current) return;
    // 高峰提示（每天首次发送；受「高峰提醒」开关与后端实时峰值判定控制）
    try {
      const today = new Date().toISOString().slice(0, 10);
      const last = localStorage.getItem("whaletalk.peak.notified");
      if (last !== today && peakInfo.warn && peakInfo.on) {
        toast("⏰ 当前为 DeepSeek 高峰时段，按高峰价计费（空闲时段为一半）");
        localStorage.setItem("whaletalk.peak.notified", today);
      }
    } catch {}
    let base = msgs;
    if (resendIdxRef.current != null) {
      // 编辑重发：删除该消息及之后
      base = msgs.slice(0, resendIdxRef.current);
      resendIdxRef.current = null;
    }
    starsRef.current = new Set();
    pinsRef.current = new Set();
    if (dataMode !== "backend") {
      toast("⚠️ 后端未连接，发送已取消（请启动服务后刷新页面）");
      setBackendNote("后端未连接：请启动「鲸语 WhaleTalk」(web_app.py) 后刷新页面");
      return;
    }
    pendingRef.current = { text, images: attachments.map((a) => a.path) };
    historyRef.current = buildMessageChain(base.filter((m) => !m.streaming));
    // 连续对话：保留已有消息（useBackendChat 在 base 上追加本轮 user+assistant），
    // 实现常规聊天记录连续滚动；只有「新对话」(onPickSession(null)) 才清空。
    setMsgs(base);
    setBusy(true);
    setBackendNote("");
  };

  const nowTime = () => new Date().toTimeString().slice(0, 8);

  const onInjectFile = async (path) => {
    try {
      const d = await api.api("/v1/files/read", { method: "POST", body: JSON.stringify({ path }) });
      if (d && d.content) {
        const note = d.truncated ? `\n[文件较大，已截断前 ${d.content.length} 字符]` : "";
        composerRef.current?.insertText(`[文件] ${String(path).split(/[\\/]/).pop()}:\n${d.content}${note}`);
      } else if (d && d.error) {
        composerRef.current?.insertText(d.error);
      }
    } catch {}
  };

    const onContinue = (idx) => {
    if (busy || !msgs[idx] || msgs[idx].role !== "assistant") return;
    // 同步 historyRef 到该消息为止
    historyRef.current = buildMessageChain(msgs.slice(0, idx + 1));
    continueRef.current = { active: true, idx };
    setBusy(true);
  };

  const onStop = () => {
    if (stopSignalRef.current) {
      try {
        stopSignalRef.current.abort();
      } catch {}
    }
    setBusy(false);
    setGenState({ on: false, text: "" });
    toast("⏹ 已停止生成");
  };

  const onPickSession = async (id) => {
    if (busy) return;
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
      if (u && (u.prompt_tokens || u.completion_tokens || u.total_tokens)) {
        setCtx((prev) => ({
          ...prev,
          usage: {
            prompt: u.prompt_tokens || 0,
            completion: u.completion_tokens || 0,
            cached: u.prompt_cache_hit_tokens ? `${((u.prompt_cache_hit_tokens / (u.prompt_tokens || 1)) * 100).toFixed(1)}%` : "—",
            cost: "—",
          },
        }));
      }
    } else {
      setMsgs((history[id] || []).map((m) => ({ ...m, tools: [], streaming: false })));
    }
  };

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
    historyRef.current = buildMessageChain(msgs.slice(0, idx + 1));
    setActiveId(null);
    setMsgs(msgs.slice(0, idx + 1));
    setBackendNote("分支会话：已从此处创建新会话");
  };

  const onEditMsg = (idx) => {
    const m = msgs[idx];
    if (!m) return;
    resendIdxRef.current = idx;
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
    } catch {}
    list.push({ text: m.text, think: m.think || "", ts: new Date().toISOString() });
    if (list.length > 20) list = list.slice(-20);
    try {
      localStorage.setItem(key, JSON.stringify(list));
    } catch {}
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
    historyRef.current = buildMessageChain(base);
    setActiveId(null);
    setMsgs(base);
    pendingRef.current = { text, images: [] };
    setBusy(true);
    setBackendNote("");
  };

  const onPinSession = async (id, pinned) => {
    try {
      await api.pinSession(id, pinned);
      refreshSessions();
    } catch {}
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

  const multiDelete = () => {
    if (!multiSel || !multiSel.size) return;
    const idxs = [...multiSel].sort((a, b) => b - a);
    if (!window.confirm(`删除选中的 ${idxs.length} 条消息？`)) return;
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
      const d = await api.searchSessions(searchQuery.trim());
      setSearchResults(d.results || []);
    } catch {}
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
    } catch {}
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
      setTimeout(() => {
        const el = document.querySelector(`[data-msg-idx="${r.index}"]`);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
        else {
          const el2 = document.querySelector('[data-msg-idx="0"]');
          if (el2) el2.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }, 150);
    }
  };

  const gotoMessage = (idx) => {
    if (scrollRef.current) {
      const el = document.querySelector(`[data-msg-idx="${idx}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  const onRenameSession = async (id, name) => {
    try {
      await api.renameSession(id, name);
      refreshSessions();
    } catch {}
  };

  const onEditTags = async (id, tags) => {
    try {
      await api.tagSession(id, tags);
      refreshSessions();
    } catch {}
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
        alert("未解析到有效消息（支持 JSON 数组 / {\"messages\":[...]} / JSONL）");
        return;
      }
      const userMsg = parsed.find((m) => m.role === "user");
      const sid = await api.saveSession({
        name: (userMsg?.content || "导入会话").replace(/\s+/g, " ").slice(0, 24),
        messages: parsed.map((m) => ({ role: m.role, content: String(m.content || "") })),
      });
      if (sid) refreshSessions();
    } catch {}
  };

  React.useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs]);

  const activeSession = sessions.find((s) => s.id === activeId);
  const isTask = mode === "task";

  return (
    <div className="chat-page">
      <SpeakingPill />
      <div className="chat-row">
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
        <div className="chat-main">
          <div className="chat-header">
            <button className="icon-btn" title="会话列表" onClick={() => setListOpen(!listOpen)}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <div className="chat-header-title">
              <b>{activeId ? activeSession?.title || "历史会话" : "新会话"}</b>
              <span className="chat-header-sub">
                {backendNote
                  ? backendNote
                  : activeId
                  ? `${activeSession?.model || ""} · ${activeSession?.time || ""}`
                  : isTask
                  ? "🚀 任务模式：全部工具自动可用，目录内全自动"
                  : "💬 对话模式：纯问答，不调用任何工具"}
              </span>
            </div>
            <div className="chat-header-right">
                          <button className="icon-btn" title="批量任务" onClick={() => setBatchPanel(true)}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8 3H5a2 2 0 00-2 2v3M16 3h3a2 2 0 012 2v3M8 21H5a2 2 0 01-2-2v-3M16 21h3a2 2 0 002-2v-3" />
              </svg>
            </button>
            <button className="icon-btn" title="会话轨迹" onClick={() => setTimelinePanel(!timelinePanel)}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 2" />
              </svg>
            </button>
            <button className="icon-btn" title="回复变体" onClick={openVariants}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 6h16M4 12h16M4 18h7" />
              </svg>
            </button>
            <button className="icon-btn" title="FIM 代码补全" onClick={() => setFimPanel(true)}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
              </svg>
            </button>
            <button className="icon-btn" title="全局搜索" onClick={() => setSearchPanel(!searchPanel)}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <circle cx="11" cy="11" r="7" />
                <path d="M21 21l-4-4" />
              </svg>
            </button>
            <button className="icon-btn" title="多选消息" onClick={toggleMulti} style={{ color: multiSel ? "var(--brand-strong)" : undefined }}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" />
                </svg>
              </button>
              <button className="icon-btn" title="收藏与固定" onClick={toggleStarPanel}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
                  <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
                </svg>
              </button>
              <div className="mode-switch" title="工作模式">
                <button
                  className={`mode-btn ${!isTask ? "mode-on" : ""}`}
                  onClick={() => isTask && switchMode("dialog")}
                >
                  💬 对话模式
                </button>
                <button
                  className={`mode-btn ${isTask ? "mode-on" : ""}`}
                  onClick={() => !isTask && switchMode("task")}
                >
                  🚀 任务模式
                </button>
              </div>
              <span className={`header-chip ${dataMode === "backend" ? "header-chip-brand" : ""}`}>
                {dataMode === "backend" ? "已连接" : "未连接"}
              </span>
              <button className="icon-btn" title="控制台（参数/文件/进程）" onClick={() => setAuxOpen(!auxOpen)}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                  <path d="M3 9h18M3 15h18M9 3v18" />
                </svg>
              </button>
              <button className="icon-btn" title="上下文面板" onClick={() => setCtxOpen(!ctxOpen)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="4" width="18" height="16" rx="2" />
                  <path d="M3 9h18M9 4v16" />
                </svg>
              </button>
            </div>
          </div>

          <div className="chat-scroll" ref={scrollRef} data-density={density} style={{ fontSize: `${fontSize}px` }}>
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
                <div className="empty-whale">🐳</div>
                <h1>{isTask ? "今天想做点什么？" : "随便聊聊"}</h1>
                <p>
                  {isTask
                    ? "看得见屏幕、听得见语音、动得了鼠标键盘与浏览器——115 项能力随叫随到"
                    : "纯问答 · 不调用工具 · 适合聊天、翻译、写作、答疑"}
                </p>
                <div className="empty-suggest">
                  {(isTask
                    ? ["帮我调研一个主题并输出报告", "把这个文件夹整理成 Markdown 索引", "分析这张图片的内容", "帮我定时巡检一个网站"]
                    : ["用一句话介绍你自己", "翻译：Knowledge is power", "帮我写一首关于海的短诗", "帮我解释一个概念，比如 HTTP"]
                  ).map((s) => (
                    <button key={s} className="suggest-chip" onClick={() => onSend(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {msgs.map((m, i) => (
              <div
                key={i}
                data-msg-idx={i}
                className={`msg-wrap ${multiSel && multiSel.has(i) ? "msg-wrap-selected" : ""}`}
                onClick={multiSel ? () => toggleSelect(i) : undefined}
              >
                <Message
                  msg={m}
                  onResend={onSend}
                  onStar={() => onStarMsg(i)}
                  onPin={() => onPinMsg(i)}
                  onQuote={() => onQuoteMsg(i)}
                  onFork={() => onForkMsg(i)}
                  onEdit={() => onEditMsg(i)}
                  onRegenerate={m.role === "assistant" ? onRegenerate : undefined}
                  onContinue={m.role === "assistant" && !m.streaming ? () => onContinue(i) : undefined}
                />
              </div>
            ))}
          </div>

          <Composer ref={composerRef} busy={busy} onSend={onSend} onStop={onStop} isTask={isTask} />
        </div>

        {ctxOpen && (
          <ContextPanel data={ctx} onClose={() => setCtxOpen(false)} />
        )}

        {auxOpen && (
          <AuxPanel onClose={() => setAuxOpen(false)} onInjectFile={onInjectFile} />
        )}
      </div>

      <StatusBar mode={mode} onSwitchMode={switchMode} generating={genState.on} generatingText={genState.text} />

      <ConfirmGate
        req={promptReq}
        onRespond={async (payload) => {
          setPromptReq(null);
          try {
            await api.respond(payload);
          } catch {}
        }}
      />

            {batchPanel && (
        <div className="overlay-mask" onClick={() => setBatchPanel(false)}>
          <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
            <div className="overlay-head">
              <b>📦 批量任务</b>
              <button className="icon-btn" onClick={() => setBatchPanel(false)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="overlay-body">
              <div className="ctx-group-title">文件列表（每行一个路径）</div>
              <textarea className="fim-input" rows={5} placeholder={"C:/work/data1.csv\nC:/work/data2.csv"} value={batchFiles} onChange={(e) => setBatchFiles(e.target.value)} />
              <div className="ctx-group-title">指令模板（{file} 占位）</div>
              <input className="set-select set-combo" value={batchTpl} onChange={(e) => setBatchTpl(e.target.value)} />
              <div className="svc-actions">
                <button className="confirm-btn confirm-primary" onClick={doBatch} disabled={!batchFiles.trim()}>生成批量指令到输入框</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {cmdPanel && (
        <div className="confirm-mask" onClick={() => setCmdPanel(false)}>
          <div className="cmd-panel" onClick={(e) => e.stopPropagation()}>
            <input
              className="cmd-input"
              placeholder="输入命令或搜索会话…"
              autoFocus
              value={cmdQuery}
              onChange={(e) => setCmdQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setCmdPanel(false);
                if (e.key === "Enter") {
                  if (cmdQuery) doSearchWithQuery(cmdQuery);
                }
              }}
            />
            <div className="cmd-list">
              {[
                { icon: "💬", label: "新对话", act: () => { setCmdPanel(false); onPickSession(null); } },
                { icon: "🔍", label: "全局搜索", act: () => { setCmdPanel(false); setSearchPanel(true); } },
                { icon: "⏱", label: "定时任务（工作台）", act: () => { setCmdPanel(false); onGoWorkbench && onGoWorkbench(); } },
                { icon: "🕐", label: "会话轨迹", act: () => { setCmdPanel(false); setTimelinePanel(true); } },
                { icon: "🔄", label: "回复变体", act: () => { setCmdPanel(false); openVariants(); } },
                { icon: "✂", label: "FIM 代码补全", act: () => { setCmdPanel(false); setFimPanel(true); } },
                { icon: "⭐", label: "收藏与固定", act: () => { setCmdPanel(false); setStarPanel(true); } },
                { icon: "📋", label: "导出当前会话", act: () => { setCmdPanel(false); onExportSession(); } },
                { icon: "⚙", label: "设置", act: () => { setCmdPanel(false); onGoSettings && onGoSettings(); } },
              ]
                .filter((c) => !cmdQuery || c.label.includes(cmdQuery))
                .map((c, i) => (
                  <div className="cmd-item" key={i} onClick={c.act}>
                    <span>{c.icon}</span>
                    <span>{c.label}</span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      )}

      {timelinePanel && (
        <div className="overlay-mask" onClick={() => setTimelinePanel(false)}>
          <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
            <div className="overlay-head">
              <b>🕐 会话轨迹（{msgs.length} 条）</b>
              <button className="icon-btn" onClick={() => setTimelinePanel(false)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="overlay-body">
              <div className="timeline">
                {msgs.map((m, i) => (
                  <div key={i} className="tl-item" onClick={() => { setTimelinePanel(false); gotoMessage(i); }}>
                    <span className="tl-icon">{m.role === "user" ? "💬" : "🤖"}</span>
                    <span className="tl-role">{m.role === "user" ? "我" : "助手"}</span>
                    <span className="tl-text">
                      {String(m.text || m.think || "").slice(0, 50) || (m.tools && m.tools.length ? `🔧 ${m.tools.length} 个工具调用` : "")}
                    </span>
                    <span className="gs-time">{m.time || ""}</span>
                  </div>
                ))}
                {msgs.length === 0 && <div className="empty-tip">暂无消息</div>}
              </div>
            </div>
          </div>
        </div>
      )}

      {fimPanel && (
        <div className="overlay-mask" onClick={() => setFimPanel(false)}>
          <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
            <div className="overlay-head">
              <b>✂ FIM 代码补全（Beta）</b>
              <button className="icon-btn" onClick={() => setFimPanel(false)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="overlay-body">
              <div className="ctx-group-title">前缀（必填）</div>
              <textarea className="fim-input" rows={4} placeholder="代码前缀…" value={fimPrompt} onChange={(e) => setFimPrompt(e.target.value)} />
              <div className="ctx-group-title">后缀（可选）</div>
              <textarea className="fim-input" rows={2} placeholder="代码后缀…" value={fimSuffix} onChange={(e) => setFimSuffix(e.target.value)} />
              <div className="svc-actions">
                <button className="confirm-btn confirm-primary" onClick={doFim} disabled={fimBusy || !fimPrompt.trim()}>
                  {fimBusy ? "补全中…" : "▶ 补全"}
                </button>
              </div>
              {fimResult && (
                <div className="evo-file">
                  <div className="evo-file-head"><b>结果</b></div>
                  <pre>{fimResult}</pre>
                  <button className="msg-op" style={{ marginTop: 6 }} onClick={() => composerRef.current?.insertText(fimResult)}>↩ 插入输入框</button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {variantPanel && (
        <div className="overlay-mask" onClick={() => setVariantPanel(false)}>
          <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
            <div className="overlay-head">
              <b>🔄 回复变体（{variants.length} 版）</b>
              <button className="icon-btn" onClick={() => setVariantPanel(false)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="overlay-body">
              {variants.length === 0 && <div className="empty-tip">暂无变体——重新生成时旧回复自动保存</div>}
              {variants.map((v, i) => (
                <div className="star-item" key={i} onClick={() => restoreVariant(v)}>
                  <span className="star-role">第 {variants.length - i} 版</span>
                  <span className="star-text">{String(v.text).slice(0, 60)}</span>
                  <span className="gs-time">{new Date(v.ts).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {searchPanel && (
        <div className="overlay-mask" onClick={() => setSearchPanel(false)}>
          <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
            <div className="overlay-head">
              <b>🔍 全局搜索</b>
              <button className="icon-btn" onClick={() => setSearchPanel(false)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="overlay-body">
              <div className="global-search-bar">
                <input
                  className="set-select set-combo"
                  placeholder="跨全部会话搜索…（回车搜索）"
                  value={searchQuery}
                  autoFocus
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && doSearch()}
                />
                <button className="confirm-btn confirm-primary" onClick={doSearch} disabled={searchBusy || !searchQuery.trim()}>
                  {searchBusy ? "搜索中…" : "搜索"}
                </button>
              </div>
              <div className="global-search-results">
                {searchResults.map((r, i) => (
                  <div className="gs-item" key={i} onClick={() => openSearchResult(r)}>
                    <div className="gs-line1">
                      <b>{r.session_name}</b>
                      <span>{r.role === "user" ? "我" : "助手"}</span>
                      <span className="gs-time">{r.time}</span>
                    </div>
                    <div className="gs-snippet">{r.snippet}</div>
                  </div>
                ))}
                {searchQuery && !searchBusy && searchResults.length === 0 && <div className="empty-tip">无匹配结果</div>}
              </div>
            </div>
          </div>
        </div>
      )}

      {starPanel && (
        <div className="overlay-mask" onClick={toggleStarPanel}>
          <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
            <div className="overlay-head">
              <b>⭐ 收藏与固定</b>
              <button className="icon-btn" onClick={toggleStarPanel}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
              </button>
            </div>
            <div className="overlay-body">
              <div className="ctx-group-title">⭐ 已收藏消息（{msgs.filter((m) => m.starred).length}）</div>
              {msgs.filter((m) => m.starred).length === 0 && <div className="empty-tip">暂无收藏——hover 消息点 ⭐</div>}
              {msgs.map((m, i) => m.starred && (
                <div className="star-item" key={i} onClick={() => gotoMessage(i)}>
                  <span className="star-role">{m.role === "user" ? "我" : "助手"}</span>
                  <span className="star-text">{String(m.text || "").slice(0, 60)}</span>
                  <button className="msg-op" onClick={(e) => { e.stopPropagation(); onStarMsg(i); }}>取消</button>
                </div>
              ))}
              <div className="ctx-group-title">📌 已固定消息（{msgs.filter((m) => m.pinned).length}，压缩时保留进摘要）</div>
              {msgs.filter((m) => m.pinned).length === 0 && <div className="empty-tip">暂无固定——hover 消息点 📌</div>}
              {msgs.map((m, i) => m.pinned && (
                <div className="star-item" key={i} onClick={() => gotoMessage(i)}>
                  <span className="star-role">{m.role === "user" ? "我" : "助手"}</span>
                  <span className="star-text">{String(m.text || "").slice(0, 60)}</span>
                  <button className="msg-op" onClick={(e) => { e.stopPropagation(); onPinMsg(i); }}>取消</button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}