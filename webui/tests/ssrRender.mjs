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
  // SSR 构建可能产出**多个 chunk**（例如 quiet.js 被抽成共享块）。早先只写入口 chunk，
  // 一旦出现共享块就 `Cannot find module .../assets/quiet-xxx.js`——所以这里把全部
  // 产物按原相对路径落盘，再 import 入口。
  const tmpDir = path.join(root, "tests", ".ssr_tmp");
  fs.mkdirSync(tmpDir, { recursive: true });
  // 每次跑测都会新落一个目录：顺手清掉一小时前的，避免 .ssr_tmp 无限增长
  try {
    const cutoff = Date.now() - 3600 * 1000;
    for (const name of fs.readdirSync(tmpDir)) {
      const abs = path.join(tmpDir, name);
      if (fs.statSync(abs).mtimeMs < cutoff) fs.rmSync(abs, { recursive: true, force: true });
    }
  } catch { /* 清理失败不影响本次构建 */ }
  // 每次跑测都会新落一个目录：顺手清掉一小时前的，避免 .ssr_tmp 无限增长
  try {
    const cutoff = Date.now() - 3600 * 1000;
    for (const name of fs.readdirSync(tmpDir)) {
      const abs = path.join(tmpDir, name);
      if (fs.statSync(abs).mtimeMs < cutoff) fs.rmSync(abs, { recursive: true, force: true });
    }
  } catch { /* 清理失败不影响本次构建 */ }
  // 目录必须**只算一次**：每个 chunk 各用一个随机目录会让入口的静态 import 找不到兄弟块
  const runDir = path.join(tmpDir, `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`);
  let entry = "";
  for (const o of result.output) {
    if (o.type !== "chunk") continue;
    const f = path.join(runDir, o.fileName);
    fs.mkdirSync(path.dirname(f), { recursive: true });
    fs.writeFileSync(f, o.code);
    if (o.isEntry) entry = f;
  }
  if (!entry) throw new Error("[ssrRender] SSR 构建没有入口 chunk");
  const mod = await import(pathToFileURL(entry).href);
  cached = mod;
  return cached;
}

export async function renderMarkdown(props) {
  const mod = await loadMarkdown();
  const Markdown = mod.default;
  return renderToStaticMarkup(React.createElement(Markdown, props));
}

/** 渲染会话页（首屏主体）。用于抓「渲染期就抛」的崩溃，见 chatPageSmoke 测试 */
export async function renderChatPage(props = {}) {
  const mod = await loadMarkdown();
  return renderToStaticMarkup(React.createElement(mod.ChatPage, props));
}

/** 渲染过程时间轴（用于断言汇总/筛选/单步详情的真实 DOM 输出） */
export async function renderTracePanel(props) {
  const mod = await loadMarkdown();
  return renderToStaticMarkup(React.createElement(mod.TracePanel, props));
}

/** 渲染执行过程面板（用于断言折叠/展开的真实 DOM 输出，而不只测判据函数） */
export async function renderStepGroup(props) {
  const mod = await loadMarkdown();
  return renderToStaticMarkup(React.createElement(mod.StepGroup, props));
}

export { React };
