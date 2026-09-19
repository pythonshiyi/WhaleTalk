// 回归门禁：后端未连接时，前端必须能自恢复，不能把 offline 永久钉死。
//
// 历史缺陷：useDataSources 只在挂载时用 checkBackend（结果缓存）探测一次，失败即
// mode="offline" 且永不恢复；之后所有发送都被 `dataMode !== "backend"` 拦截，
// 表现为「输入任何内容点发送/回车都无反应，前后端都无请求」。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const jsx = fs.readFileSync(path.join(__dirname, "../src/components/ChatPage.jsx"), "utf8");

describe("后端离线自恢复", () => {
  it("用不缓存的 probeBackendHealth 做可重复探测", () => {
    assert.ok(jsx.includes("api.probeBackendHealth()"), "未使用不缓存的健康探测");
    assert.ok(jsx.includes("const loadAll = React.useCallback"), "缺少可重复加载入口");
  });

  it("监听后端恢复（断开→恢复）自动重载", () => {
    assert.ok(jsx.includes("api.watchBackend("), "未监听后端健康翻转");
    assert.ok(jsx.includes("was === false"), "缺少「离线→恢复」翻转判定");
  });

  it("onSend 在离线时先重连再发送", () => {
    const i = jsx.indexOf("const onSend = async (text");
    assert.ok(i >= 0, "onSend 应为异步（离线时可 await 重连）");
    const w = jsx.slice(i, i + 2200);
    assert.ok(w.includes("reloadDataSources"), "onSend 未在后端离线时尝试重连");
  });
});
