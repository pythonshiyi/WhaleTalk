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
