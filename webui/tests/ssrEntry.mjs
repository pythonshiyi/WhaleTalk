// ── SSR 测试入口：供 vite ssr build 打包，再被 node 原生 import ──
// 打包后 react / react-dom/server 保持 external，node 原生 import 与
// 测试侧 CJS require 共享同一模块实例（react 是 CJS，单例天然共享）。
import Markdown from "../src/components/Markdown.jsx";
export default Markdown;
