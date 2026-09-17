// 大脑页内导航总线（极简发布订阅）。
// 解决两类跨分区深链：① 实体图谱节点 → 记忆库按实体筛选；② 全局检索结果 → 跳到对应分区/记忆。
// 状态保持模块级单例，订阅者（BrainPage / MemoryPage）在挂载时订阅、卸载时退订。
const listeners = new Set();
let state = { entity: "", memoryId: "", tab: "", zone: "" };

function emit() {
  for (const fn of Array.from(listeners)) {
    try { fn({ ...state }); } catch (e) { /* 订阅者异常不影响发布方 */ }
  }
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getState() {
  return { ...state };
}

/** 按实体筛选记忆（图谱节点点击）→ 自动切到记忆库 */
export function setEntityFilter(entity) {
  state = { ...state, entity: entity || "", tab: "memory" };
  emit();
}

/** 清空实体筛选（保持当前分区） */
export function clearEntityFilter() {
  state = { ...state, entity: "" };
  emit();
}

/** 跳到某条记忆并高亮（全局检索命中） */
export function focusMemory(id) {
  state = { ...state, memoryId: id || "", tab: "memory" };
  emit();
}

/** 切换到指定顶层分区（cockpit / memory） */
export function focusTab(tab) {
  state = { ...state, tab: tab || "" };
  emit();
}

/** 切到指挥舱内的指定二级分区（overview / growth / continuity） */
export function focusZone(zone) {
  state = { ...state, zone: zone || "", tab: "cockpit" };
  emit();
}
