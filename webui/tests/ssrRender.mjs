// ── 通用 SSR 渲染工具（vite 8 · Rolldown 兼容）──────────────────
// vite 8 的 ssrLoadModule 会内联求值 CJS 依赖（react），导致双实例/require 未定义。
// 因此改用「vite ssr build → ESM bundle → node 原生 import」管线：
//   react / react-dom/server 标为 external，原生 import 与 CJS require 共享单例。
// 用法：import { renderMarkdown } from "./ssrRender.mjs";
import { build } from "vite";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const require = createRequire(path.join(root, "package.json"));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

let cached = null;

async function loadMarkdown() {
  if (cached) return cached;
  const result = await build({
    root,
    logLevel: "error",
    build: {
      ssr: true,
      write: false,
      minify: false,
      rollupOptions: {
        input: path.join(here, "ssrEntry.mjs"),
        external: ["react", "react-dom/server", "react/jsx-runtime", "react/jsx-dev-runtime"],
      },
    },
  });
  const code = result.output.find((o) => o.type === "chunk" && o.isEntry)?.code || result.output[0].code;
  // 临时 bundle 必须位于项目内，否则 node 原生 import 无法向上解析到 webui/node_modules/react
  const tmpDir = path.join(root, "tests", ".ssr_tmp");
  fs.mkdirSync(tmpDir, { recursive: true });
  const tmp = path.join(tmpDir, `ssr_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.mjs`);
  fs.writeFileSync(tmp, code);
  const mod = await import(pathToFileURL(tmp).href);
  cached = mod.default;
  return cached;
}

export async function renderMarkdown(props) {
  const Markdown = await loadMarkdown();
  return renderToStaticMarkup(React.createElement(Markdown, props));
}

export { React };
