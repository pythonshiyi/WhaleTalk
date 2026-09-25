// 消息列表的不可变更新工具（纯函数，无 React 依赖，便于单测）。
//
// 背景（性能关键）：ChatPage 此前在流式热路径上直接修改「已进入 state 的对象」
// ——msg.think += …、msg.tools.push(…)、card.status = "done"，再靠 updateMsgs
// 复制数组来触发重渲染。这样有两个问题：
//   ① 违反 React 不可变约定：任何父级 bail-out、或给 Message 加 React.memo 之后，
//      流式内容会静默停止刷新（对象引用没变，React 认为没变化）；
//   ② 每帧增量都要重渲染整条消息列表——长会话卡顿的根因。
// 统一走这里的不可变更新后，未变化的消息保持同一对象引用，配合 Message 的
// memo 即可让每帧只重渲染真正变化的最后一条。

// 找最后一张匹配的工具卡片（按名字 + 状态）：并发调用同名工具时取最近一张。
// 只做查找、不改对象。返回索引；未找到返回 -1。
export function findLastToolCard(tools, name, status) {
  const arr = tools || [];
  for (let i = arr.length - 1; i >= 0; i--) {
    const t = arr[i];
    if (t && t.tool === name && t.status === status) return i;
  }
  return -1;
}

// 优先按 tool_call_id 精确配对，退化时再按「名字 + 状态」找最近一张。
//
// 背景：SSE 的 tool_start / tool / tool_duration 现在都带后端 tool_call_id。
// 只按名字配对时，连续两次同名工具（如 read_file）的「开始/结果/耗时」会串到
// 相邻卡片上（导出 md 里 read_file 调用与结果错位即由此而来）。
export function findToolCard(tools, { id, name, status } = {}) {
  const arr = tools || [];
  if (id != null && id !== "") {
    for (let i = arr.length - 1; i >= 0; i--) {
      const t = arr[i];
      if (t && t.id === id) return i;
    }
  }
  return findLastToolCard(arr, name, status);
}

// 单条消息的体积估算（字符数）：正文 + 工具调用参数 + 工具结果。
function msgChars(m) {
  if (!m || typeof m !== "object") return 0;
  let n = typeof m.content === "string" ? m.content.length : 0;
  if (Array.isArray(m.tool_calls)) {
    for (const c of m.tool_calls) {
      const a = c && c.function && c.function.arguments;
      if (typeof a === "string") n += a.length;
    }
  }
  return n;
}

// 活动消息窗口（长会话内存上界）：只保留最近若干「回合」的完整消息，
// 更早的整回合直接丢弃——它们仍完整落盘（后端会话文件），需要时可重开会话查看。
//
// 为什么需要：窗口化渲染（VIRT_WINDOW）只约束 DOM 节点，**不回收消息对象**；
// 一次自主长任务单轮就能累积上百条 tool 结果（实测某会话 820 条 / 1MB JSON，
// 其中一条 assistant 含 123 个 tool_calls）。msgs 数组无上界 → 越用越占内存。
//
// 规则（与 trimHistory 同源，但这里是「状态里实际保留多少」，不是「送给模型多少」）：
//   1. 只在 user 消息（回合起点）处切；
//   2. 至少保留 maxTurns 个完整回合（不足则全保留）；
//   3. 正在流式的最后一条必须保留；
//   4. 绝不修改对象内容（只做 slice），不破坏引用相等的 memo 语义。
//
// 返回 { msgs, dropped }：dropped>0 表示发生了截断（调用方可据此提示/标记）。
export function capLiveWindow(msgs, { maxTurns = 40 } = {}) {
  const arr = Array.isArray(msgs) ? msgs : [];
  if (maxTurns <= 0 || arr.length === 0) return { msgs: arr, dropped: 0 };
  // 从后往前数回合起点（user）
  let turns = 0;
  let start = 0;
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i] && arr[i].role === "user") {
      turns += 1;
      if (turns > maxTurns) {
        start = i + 1;
        break;
      }
    }
  }
  // start 必须落在 user 上（若因边界落在中途，向前找到最近的 user 起点）
  if (start > 0) {
    while (start < arr.length && arr[start] && arr[start].role !== "user") start++;
  }
  if (start <= 0) return { msgs: arr, dropped: 0 };
  return { msgs: arr.slice(start), dropped: start };
}

// 历史裁剪：按「回合边界」截断，绝不从助手/工具片段中间切断。
//
// 背景（本次修复的根因）：任务模式下一轮可能产生几十上百条 tool 结果，
// 前端展开后单回合长度可达 90+ 条。旧实现 `slice(-80)` 会从中间腰斩——
// 丢掉回合开头的 user 指令，只剩一串没有归属的 tool 消息；后端
// `_sanitize_messages` 会把悬空 tool 全部丢弃，模型因此完全失忆、只能重新全览。
//
// 规则：
//   1. 只在 user 消息（回合起点）处截断；
//   2. 无条件保留最后一个回合（含本轮用户指令与全部工具链）；
//   3. 在 maxMessages / maxChars 预算内尽量多纳入更早的完整回合；
//   4. **原始任务**（首条 user）被裁掉时补回锚点——前端只是最后一道兜底，
//      真正的内容压缩由后端 `_compress_messages` 的结构化摘要完成（所以预算放得较宽，
//      只要不超 HTTP 请求体上限即可，后端会按轮次摘要而非硬裁剪）。
export function trimHistory(chain, { maxMessages = 3000, maxChars = 600000 } = {}) {
  const arr = Array.isArray(chain) ? chain : [];
  if (!arr.length) return arr;

  // 最后一个回合的起点（最后一条 user）；找不到则从头开始
  let mustStart = 0;
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i] && arr[i].role === "user") { mustStart = i; break; }
  }

  let start = arr.length;
  let msgs = 0;
  let chars = 0;
  for (let i = arr.length - 1; i >= 0; i--) {
    const nextMsgs = msgs + 1;
    const nextChars = chars + msgChars(arr[i]);
    if (nextMsgs > maxMessages || nextChars > maxChars) break;
    msgs = nextMsgs;
    chars = nextChars;
    start = i;
  }

  // 边界回退：start 必须落在 user 上，否则会把某个旧回合的尾部当成开头
  while (start < arr.length && arr[start] && arr[start].role !== "user") start++;
  // 预算连最后一个回合都装不下时，保底从该回合起点开始（宁可超预算也不丢指令）
  if (start > mustStart) start = mustStart;
  let out = start <= 0 ? arr : arr.slice(start);
  // 原始任务锚点：裁剪后若首条 user 被丢掉，补回一条（与后端「原始任务·不可裁剪」呼应）
  if (start > 0 && arr[0] && arr[0].role === "user" && out.length && out[0] !== arr[0]) {
    out = [arr[0], ...out];
  }
  return out;
}

// 构造「不可变替换最后一条消息」的函数（流式助手消息始终位于列表末尾）。
//
// updateMsgs 约定：接受 (prev => next) 或值，并同步更新实时镜像。
// 返回值语义：patchFn 返回同一引用（next === prev）时不产生新数组，
// 以便上层完全跳过重渲染。
export function makePatchLast(updateMsgs) {
  return (patchFn) => updateMsgs((m) => {
    if (!m || !m.length) return m;
    const i = m.length - 1;
    const next = patchFn(m[i]);
    if (next === m[i]) return m;
    const out = m.slice();
    out[i] = next;
    return out;
  });
}
