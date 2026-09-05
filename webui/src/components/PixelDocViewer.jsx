import React from "react";
import * as api from "../api.js";
import { silentWarn } from "../quiet.js";

// 像素级产物渲染（α 系列）
// - pdf : fetch blob → objectURL → <iframe>（浏览器原生渲染，真正页面像素）
// - docx: docx-preview renderAsync（按真实页面分页渲染）
// - pptx: pptx-preview init+load（全页预览 + 翻页/形状点选）
// 组件内部拉 /v1/files/raw（带 Bearer），不依赖后端 preview JSON 内容。
// 渲染库动态 import：仅在需要时加载，避免首屏体积膨胀与低端环境崩溃。

export default function PixelDocViewer({ path, ext }) {
  const [state, setState] = React.useState({ status: "loading", err: "" });
  const ref = React.useRef(null);
  const revoke = React.useRef(null);

  React.useEffect(() => {
    let alive = true;
    const container = ref.current;
    if (!container || !path) return undefined;
    setState({ status: "loading", err: "" });
    (async () => {
      try {
        const { ok, blob, error } = await api.fetchFileBlob(path);
        if (!alive) return;
        if (!ok || !blob) { setState({ status: "err", err: error || "无法读取文件" }); return; }
        const e = String(ext || "").toLowerCase();
        if (e === ".pdf") {
          const url = URL.createObjectURL(blob);
          revoke.current = url;
          container.innerHTML = "";
          const iframe = document.createElement("iframe");
          iframe.src = url;
          iframe.style.width = "100%";
          iframe.style.height = "520px";
          iframe.style.border = "0";
          iframe.style.borderRadius = "8px";
          container.appendChild(iframe);
          setState({ status: "ok" });
        } else if (e === ".docx") {
          container.innerHTML = "<div style='opacity:.7;font-size:12px;padding:8px'>正在按真实页面渲染 Word…</div>";
          const docxPreview = await import("docx-preview");
          const body = document.createElement("div");
          body.className = "docx-pixel";
          container.innerHTML = "";
          container.appendChild(body);
          await docxPreview.renderAsync(blob, body, undefined, {
            inWrapper: true, ignoreWidth: false, ignoreHeight: false,
            breakPages: true, experimental: true, className: "docx", useBase64URL: true,
          });
          if (alive) setState({ status: "ok" });
        } else if (e === ".pptx") {
          container.innerHTML = "<div style='opacity:.7;font-size:12px;padding:8px'>正在渲染 PPT…</div>";
          const pptxPreview = await import("pptx-preview");
          const box = document.createElement("div");
          container.innerHTML = "";
          container.appendChild(box);
          const buf = await blob.arrayBuffer();
          const viewer = pptxPreview.init(box, { width: 900, height: 560 });
          await viewer.load(buf);
          if (alive) { setState({ status: "ok" }); /* 渲染当前页 */ try { viewer.renderSingleSlide(0); } catch (e2) {} }
        } else {
          setState({ status: "err", err: "暂不支持该格式的像素渲染" });
        }
      } catch (err) {
        silentWarn(err, "PixelDocViewer");
        if (alive) setState({ status: "err", err: (err && err.message) || "渲染失败" });
      }
    })();
    return () => {
      alive = false;
      if (revoke.current) { try { URL.revokeObjectURL(revoke.current); } catch (e) {} revoke.current = null; }
      if (ref.current) ref.current.innerHTML = "";
    };
  }, [path, ext]);

  return (
    <div style={{ marginTop: 4 }}>
      {state.status === "loading" && <div style={{ opacity: .7, fontSize: 12 }}>正在加载文档渲染…</div>}
      {state.status === "err" && <div style={{ opacity: .8, fontSize: 12, color: "var(--danger,#c0392b)" }}>⚠ {state.err}</div>}
      <div ref={ref} style={{ marginTop: 4 }} />
    </div>
  );
}
