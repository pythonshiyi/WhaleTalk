// ── 单次任务的「意图」：预算与边界 ──
// 为什么需要一个单独的概念：一次委托包含三样东西——目标（你说的那句话）、
// 预算（能跑多久/多少步）、授权（能不能动手）。后两样过去只存在于设置页，
// 而它们其实是**单次**决定：这次只想让它查一下，和这次让它改代码，边界不同。
//
// 设计边界（重要）：
//  · 预算/偏好**存本地**，不改全局配置；发送时作为请求字段带给后端（max_tool_rounds）。
//  · 授权沿用已有的 mode（task=可执行 / dialog=只答），不新造一套权限体系——
//    多一套权限就是多一个会说谎的地方。
//  · 时间预算只做**提醒**，不做阻断：没有后端时钟就无法真正中止，
//    与其假装能拦，不如老实提醒。

export const INTENT_KEY = "whaletalk.intent";

export const DEFAULT_INTENT = { maxRounds: 0, timeBudgetMin: 0 };

export const ROUND_PRESETS = [0, 8, 20, 50];

function storageOf(s) {
  if (s) return s;
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;          // 隐私模式访问会抛 SecurityError
  }
}

/** 归一化：非法值一律回到 0（不限），保证 UI 永远拿到可渲染的数字 */
export function normalizeIntent(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  const num = (v) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? Math.min(10000, Math.round(n)) : 0;
  };
  return { maxRounds: num(src.maxRounds), timeBudgetMin: num(src.timeBudgetMin) };
}

export function readIntent(storage) {
  const st = storageOf(storage);
  if (!st) return { ...DEFAULT_INTENT };
  try {
    const raw = st.getItem(INTENT_KEY);
    if (!raw) return { ...DEFAULT_INTENT };
    return normalizeIntent(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_INTENT };   // 损坏/不可读 → 默认，不影响发送
  }
}

export function writeIntent(patch, storage) {
  const st = storageOf(storage);
  const next = normalizeIntent({ ...readIntent(st), ...(patch || {}) });
  if (!st) return next;
  try {
    st.setItem(INTENT_KEY, JSON.stringify(next));
  } catch {
    /* 存储不可用时仅本次会话生效 */
  }
  return next;
}

/** 一句话摘要（显示在输入框下方，让「这次的边界」始终可见） */
export function intentSummary(intent, { taskMode = true } = {}) {
  const it = normalizeIntent(intent);
  const parts = [taskMode ? "可执行" : "只回答"];
  parts.push(it.maxRounds > 0 ? `≤${it.maxRounds} 步` : "步数不限");
  if (it.timeBudgetMin > 0) parts.push(`提醒 ${it.timeBudgetMin} 分钟`);
  return parts.join(" · ");
}

/** 请求字段：只有设了预算才带，避免给每次请求都塞无用字段。
 *  键名用前端 camelCase，由 api.streamChat 统一转 snake_case。 */
export function intentToRequest(intent) {
  const it = normalizeIntent(intent);
  return it.maxRounds > 0 ? { maxToolRounds: it.maxRounds } : {};
}
