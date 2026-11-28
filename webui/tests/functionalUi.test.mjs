// 功能层回归：确保「已有后端能力」不会再次只躺在 API 里没有入口。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(__dirname, "../src");
const COMP = path.join(SRC, "components");
const read = (f) => fs.readFileSync(path.join(COMP, f), "utf8");

describe("功能入口接通", () => {
  it("任务记录：详情 / 重跑 / 运行中 / 搜索", () => {
    const t = read("TasksPage.jsx");
    for (const kw of ["jobDetail", "runningJobs", "onRetry", "search", "error_preview", "JobRow"]) {
      assert.ok(t.includes(kw), `TasksPage 应接入 ${kw}`);
    }
    assert.ok(read("Pages.jsx").includes("onRetry={(job) => onApply?.(job.user_text)}"), "工作台应把重跑接到输入框");
  });

  it("能力包：安装 / 重载 / 卸载 / 重置熔断", () => {
    const t = read("CapabilitiesPage.jsx");
    for (const kw of ["capabilityAction", "install", "reload", "unload", "reset_health"]) {
      assert.ok(t.includes(kw), `CapabilitiesPage 应接入 ${kw}`);
    }
  });

  it("备份：恢复入口与范围选择", () => {
    const t = read("SettingsPage.jsx");
    assert.ok(t.includes("restoreBackup"), "备份应调用 restoreBackup");
    assert.ok(t.includes('value="all"') && t.includes('value="data"'), "恢复应区分数据与全部");
  });

  it("定时任务：启停 / 立即运行 / 上次结果", () => {
    const t = read("Pages.jsx");
    assert.ok(t.includes("runSchedule"), "应接入 runSchedule");
    assert.ok(t.includes("last_status") && t.includes("立即运行"), "应显示运行状态并支持立即运行");
  });

  it("全局搜索 / 待办 / 资产 / 后台任务条", () => {
    const app = fs.readFileSync(path.join(SRC, "App.jsx"), "utf8");
    assert.ok(app.includes("CommandPalette") && app.includes('key === "k"'), "App 应绑定 Ctrl+K");
    assert.ok(app.includes("runningJobs") && app.includes("bg-jobs-bar"), "App 应显示后台任务条");
    assert.ok(read("InboxButton.jsx").includes("getSchedules"), "待办应收拢定时任务异常");
    assert.ok(read("CreatePage.jsx").includes("AssetsPanel"), "创作应包含资产页");
    assert.ok(read("AssetsPanel.jsx").includes("listFiles"), "资产页应接入文件列表");
  });

  it("刷新后仍能看到后台生成中的会话", () => {
    const t = read("ChatPage.jsx");
    assert.ok(t.includes("runningJobs") && t.includes("chat-bg-banner"), "ChatPage 应提示后台仍在生成");
    assert.ok(t.includes("同步进度"), "应能重新拉取在制回合");
  });
});
