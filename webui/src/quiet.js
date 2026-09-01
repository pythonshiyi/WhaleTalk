// ── 静默异常收敛（P2-7）────────────────────────────────
// 替代裸 catch {}：前端错误不再无声无息，统一从此出口告警。
// 用法：catch (e) { silentWarn(e, "模块:功能"); }
// 对正常流程中的"可选操作失败"（如 localStorage 清理）也可调用——
// 告警仅 console.warn，不打断执行、不弹窗，纯诊断价值。

/**
 * 静默异常告警出口：捕获到的异常统一从此处 console.warn，不打断执行。
 * @param {unknown} e 捕获到的异常
 * @param {string} [label] 语义标签（建议"模块:功能"）
 */
export function silentWarn(e, label = "silent") {
  if (!e) return;
  if (typeof console !== "undefined" && console.warn) {
    const msg =
      typeof e === "object" && "message" in e && typeof e.message === "string"
        ? e.message
        : String(e);
    console.warn(`[quiet:${label}]`, msg);
  }
}
