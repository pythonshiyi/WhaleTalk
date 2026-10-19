// ── 执行过程的展示策略（纯函数，便于测试）──
// 设计取舍：一整轮的「执行过程」默认折叠，只在两种情况下展开：
//   1) 有步骤失败 —— 失败必须第一眼看到，不能藏在折叠里；
//   2) 正在执行   —— 用户需要看到进度（否则会以为卡住了）。
//
// 关键细节：判定只在**组件挂载时**做一次（见 StepGroup 的 useState 惰性初始化），
// 不随 streaming 状态实时重算。原因是一轮里工具是分段的：第 1 个工具跑完、模型
// 思考、第 2 个工具才启动——中间存在「全部 done」的瞬间。若实时重算，面板会
// 在两次工具调用之间反复折叠/展开，闪烁得非常难受。
//
// 于是行为变成：实时那一轮保持展开（挂载时 running），历史会话重新打开时折叠。

export const RAIL_PREF_KEY = "whaletalk.railOpen";

/** 该执行组挂载时是否默认展开（不含用户偏好；偏好由 StepGroup 叠加） */
export function shouldOpenByDefault(steps) {
  const list = (steps || []).filter((t) => t && t.tool);
  return list.some((t) => t.status === "failed" || t.status === "running");
}

/**
 * 折叠态下的工具名摘要：`web_search · read_file +2`
 * 目的：折叠不等于信息丢失——用户不展开也能知道「它干了什么类型的事」。
 */
export function railPreview(steps, max = 3) {
  const names = [];
  for (const t of steps || []) {
    if (!t || !t.tool) continue;
    const n = String(t.tool);
    if (!names.includes(n)) names.push(n);
  }
  if (!names.length) return "";
  const head = names.slice(0, max).join(" · ");
  return names.length > max ? `${head} +${names.length - max}` : head;
}

// ── 用户偏好：手动展开过一次，就记住「我习惯看过程」 ──
// 折叠是默认值，不是教条。用户手动展开后，历史会话也按展开呈现——这比让他
// 每轮点一次更符合直觉。存储不可用（隐私模式）时静默退化为默认行为。

function storageOf(s) {
  if (s) return s;
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;          // 存储被禁用时访问会抛 SecurityError
  }
}

/** 读取「总是展开执行过程」偏好 */
export function readRailPref(storage) {
  const st = storageOf(storage);
  if (!st) return false;
  try {
    return st.getItem(RAIL_PREF_KEY) === "1";
  } catch {
    return false;
  }
}

/** 记录偏好；传 false 表示用户主动收起，恢复「默认折叠」 */
export function writeRailPref(open, storage) {
  const st = storageOf(storage);
  if (!st) return;
  try {
    if (open) st.setItem(RAIL_PREF_KEY, "1");
    else st.removeItem(RAIL_PREF_KEY);
  } catch {
    /* 存储不可用时仅本次生效 */
  }
}

/** 合并后的初始展开判定：失败 > 用户偏好 > 是否在执行中 */
export function initialRailOpen(steps, pref = false) {
  const list = (steps || []).filter((t) => t && t.tool);
  if (list.some((t) => t.status === "failed")) return true;   // 失败永远展开
  return pref || shouldOpenByDefault(list);
}
