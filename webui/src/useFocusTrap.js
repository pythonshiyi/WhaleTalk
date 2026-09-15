import React from "react";

// 对话框焦点陷阱：打开时把焦点移入并限制 Tab 在容器内循环，关闭时归还给原焦点元素。
// 无依赖、无副作用泄漏；对 position:fixed 祖先同样适用（不做 offsetParent 过滤）。
const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useFocusTrap(ref, active = true) {
  React.useEffect(() => {
    if (!active) return;
    const el = ref.current;
    if (!el) return;
    const prev = document.activeElement;
    // 若容器内已有自动聚焦元素，则不抢焦点
    const t = setTimeout(() => {
      if (el.contains(document.activeElement)) return;
      const nodes = el.querySelectorAll(FOCUSABLE);
      if (nodes.length) nodes[0].focus();
    }, 0);
    const onKey = (e) => {
      if (e.key !== "Tab") return;
      const nodes = Array.from(el.querySelectorAll(FOCUSABLE));
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    el.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      el.removeEventListener("keydown", onKey);
      try {
        if (prev && typeof prev.focus === "function" && document.contains(prev)) prev.focus();
      } catch {
        /* 焦点归还失败不致命 */
      }
    };
  }, [ref, active]);
}

export default useFocusTrap;
