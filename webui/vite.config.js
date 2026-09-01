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
});
