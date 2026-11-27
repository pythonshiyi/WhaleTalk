import React from "react";

// P7 体验·浮层互斥：任一模态/抽屉打开时，其它已打开的浮层自动关闭。
// 起因：真实可用性巡检里命令面板遮罩没关掉，后续所有点击被拦截，
// 用户会以为「点了没反应 / 程序卡死」。用轻量事件总线统一协调，
// 各组件只需注册 hook，不用改动原有的打开触发点。
const EVT = "wt:overlay-open";

/** 广播「某个浮层刚打开」。 */
export function announceOverlay(name) {
  try {
    window.dispatchEvent(new CustomEvent(EVT, { detail: { name: String(name || "") } }));
  } catch (e) {
    // 非浏览器环境或 CustomEvent 不可用时退化为不互斥，不影响功能
    try { console.warn("[overlayBus]", e); } catch (_) {}
  }
}

/**
 * 注册一个浮层：自己由关变开时广播；别的浮层打开时自动关闭自己。
 * @param {string} name 唯一标识
 * @param {boolean} open 当前是否打开
 * @param {()=>void} close 关闭回调（应幂等）
 */
export function useOverlayExclusive(name, open, close) {
  const openRef = React.useRef(open);
  const closeRef = React.useRef(close);
  openRef.current = open;
  closeRef.current = close;
  const prev = React.useRef(open);

  React.useEffect(() => {
    if (open && !prev.current) announceOverlay(name);
    prev.current = open;
  }, [open, name]);

  React.useEffect(() => {
    const onOpen = (e) => {
      const other = e && e.detail ? e.detail.name : "";
      if (other && other !== name && openRef.current) closeRef.current();
    };
    window.addEventListener(EVT, onOpen);
    return () => window.removeEventListener(EVT, onOpen);
  }, [name]);
}
