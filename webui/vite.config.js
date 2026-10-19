import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// P2-2：dev/prod 拓扑统一——API 一律走相对路径。
// 生产：api_server 同源服务 dist（后端 8745）。
// 开发：vite dev server（5173）将 /v1、/health 代理到后端 8745，
//       前端无需直连端口，消除跨域与 dev/prod 行为不一致。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/v1": { target: "http://127.0.0.1:8745", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8745", changeOrigin: true },
    },
  },
  build: {
    target: "es2020",
    rollupOptions: {
      output: {
        // 手动分包：把「很少变动的运行时」与「常变的业务代码」拆开。
        // 之前所有依赖 + 首屏业务代码挤在同一个 index-*.js（约 500KB）里，
        // 任何一次业务改动都会让整包重新下载并重新解析。拆开后 react 运行时
        // 独立成块，升级代码时它仍可命中浏览器缓存（桌面端同源服务同样受益）。
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return "react";
          return "vendor";
        },
      },
    },
  },
});
