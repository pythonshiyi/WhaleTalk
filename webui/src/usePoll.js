// ── 统一的轮询钩子：页面不可见时暂停，可见时立刻补一次 ──
// 为什么需要它：状态栏 8s、侧栏 10s、工作台 30s、安装进度 2.5s 各自 setInterval。
// 用户把窗口切到后台（或最小化）后，这些请求与随之而来的 React 重渲染仍在空转：
// 白耗 CPU/网络、笔记本掉电，回来时还一次性堆叠多个过期响应。
//
// 三个约定：
//  1. document.hidden 时不发请求（切回时立刻补一次，避免数据陈旧）；
//  2. 用 setTimeout 链而不是 setInterval——上一次没回来就不再叠加请求；
//  3. 卸载时清定时器并置 alive=false，晚到的响应不会 setState（防内存泄漏告警）。

import React from "react";

const isHidden = () => typeof document !== "undefined" && document.hidden;

/**
 * @param {() => any} fn 轮询任务（可为 async；抛错由调用方自行处理）
 * @param {number} intervalMs 间隔毫秒
 * @param {{immediate?: boolean, enabled?: boolean}} [opts]
 *   immediate=false 首次不立刻执行；enabled=false 完全不轮询（如面板/页签未激活）
 * @returns {{pause: () => void, resume: () => void}} 手动控制（如正在流式输出时暂停）
 */
export function usePoll(fn, intervalMs, opts = {}) {
  const { immediate = true, enabled = true } = opts;
  const fnRef = React.useRef(fn);
  fnRef.current = fn;
  const timerRef = React.useRef(null);
  const pausedRef = React.useRef(false);

  React.useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;

    const clear = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    const schedule = () => {
      clear();
      if (!alive || pausedRef.current || isHidden()) return;
      timerRef.current = setTimeout(run, Math.max(250, intervalMs));
    };

    const run = async () => {
      timerRef.current = null;
      if (!alive || pausedRef.current) return;
      // 不可见时不发请求，**也不挂下一次定时器**：彻底静默，等 visibilitychange 唤醒。
      // （之前是「隐藏时仍按间隔醒来再判断」——没流量，但每 8s 白醒一次。）
      if (isHidden()) return;
      try {
        await fnRef.current();
      } finally {
        schedule();
      }
    };

    const onVisible = () => {
      if (!alive || pausedRef.current) return;
      if (isHidden()) {
        clear();            // 隐藏：停掉定时器
        return;
      }
      if (immediate) run();  // 回到前台：立刻补一次，随后由 finally 恢复节奏
      else schedule();
    };

    // 首跑：可见才跑（后台标签页打开就不该发请求）
    if (!isHidden()) {
      if (immediate) run();
      else schedule();
    }
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      alive = false;
      clear();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs, immediate, enabled]);

  const pause = React.useCallback(() => {
    pausedRef.current = true;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);
  const resume = React.useCallback(() => {
    if (!pausedRef.current) return;
    pausedRef.current = false;
    if (!isHidden() && timerRef.current === null && fnRef.current) {
      fnRef.current();
    }
  }, []);

  return React.useMemo(() => ({ pause, resume }), [pause, resume]);
}

export default usePoll;
