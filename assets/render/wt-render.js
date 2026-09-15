/* ═══════════════════════════════════════════════════════════════
   WhaleTalk 渲染引导 · wt-render.js
   在无头浏览器里把「声明式图表/公式」渲染成静态内容，然后置就绪标志，
   供渲染管线等待（document.fonts.ready + data-wt-ready）。
   支持（按需，库不在则跳过）：
     · Mermaid  → <pre class="mermaid"> 或 <div class="mermaid">…</div>
     · ECharts  → <div class="wt-chart" data-echarts='{…option}'></div>
                  或 <div class="wt-chart"><script type="application/json">…</script></div>
     · KaTeX    → <span class="tex">E=mc^2</span> / <div class="tex tex-block">…</div>
   全部本地加载、零网络；失败不阻塞，最后一定置位就绪标志。
   ═══════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  function ready() {
    try {
      window.__WT_READY__ = true;
      document.documentElement.setAttribute("data-wt-ready", "1");
    } catch (e) { /* ignore */ }
  }

  function renderKatex() {
    if (!window.katex) return;
    document.querySelectorAll(".tex, .tex-block").forEach(function (el) {
      if (el.getAttribute("data-tex-done")) return;
      var display = el.classList.contains("tex-block");
      try {
        katex.render(el.textContent || "", el, { displayMode: display, throwOnError: false });
        el.setAttribute("data-tex-done", "1");
      } catch (e) { /* 保留原文 */ }
    });
  }

  function renderEcharts() {
    if (!window.echarts) return;
    document.querySelectorAll(".wt-chart").forEach(function (el) {
      if (el.getAttribute("data-ec-done")) return;
      var raw = el.getAttribute("data-echarts");
      if (!raw) {
        var s = el.querySelector('script[type="application/json"]');
        if (s) raw = s.textContent;
      }
      if (!raw) return;
      try {
        var opt = JSON.parse(raw);
        var chart = echarts.init(el, null, { renderer: "svg" });
        chart.setOption(opt);
        el.setAttribute("data-ec-done", "1");
      } catch (e) { /* 保留占位 */ }
    });
  }

  function renderMermaid(done) {
    if (!window.mermaid || !document.querySelector(".mermaid")) { done(); return; }
    try {
      mermaid.initialize({ startOnLoad: false, securityLevel: "loose", theme: "default" });
      var p = mermaid.run({ querySelector: ".mermaid", suppressErrors: true });
      if (p && typeof p.then === "function") { p.then(done).catch(done); } else { done(); }
    } catch (e) { done(); }
  }

  function boot() {
    try { renderKatex(); } catch (e) { /* ignore */ }
    try { renderEcharts(); } catch (e) { /* ignore */ }
    renderMermaid(function () {
      // 留一帧给布局/字体，再置就绪
      requestAnimationFrame(function () { setTimeout(ready, 60); });
    });
  }

  if (document.readyState === "complete") { boot(); }
  else { window.addEventListener("load", boot); }
})();
