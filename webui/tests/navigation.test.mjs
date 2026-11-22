// 信息架构回归门禁：
// 1. 一级导航固定为 6 个用户任务入口，不再回退成「按开发模块平铺」；
// 2. 侧栏文字常驻、可滚动，不再依赖 hover 展开覆盖页面；
// 3. 能力页的「查看全部」必须真的可展开，能力包页不得再使用未定义的旧 CSS 变量；
// 4. 旧的无入口页面（Tasks/Files/Evolution/System）不得重新混回 Pages.jsx。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(__dirname, "../src");
const COMP = path.join(SRC, "components");
const read = (rel) => fs.readFileSync(path.join(SRC, rel), "utf8");

describe("一级导航信息架构", () => {
  const sidebar = read("components/Sidebar.jsx");
  const css = read("app.css");
  const app = read("App.jsx");

  it("只保留六个一级入口，且每个入口标签唯一", () => {
    const ids = [...sidebar.matchAll(/\{ id: "([a-z]+)", label: "([^"]+)", icon: "([^"]+)" \}/g)];
    assert.deepEqual(ids.map((m) => m[1]), ["chat", "workbench", "create", "abilities", "agent", "settings"]);
    const labels = ids.map((m) => m[2]);
    assert.deepEqual(labels, ["会话", "工作台", "创作", "能力", "智能体", "设置"]);
    assert.equal(new Set(ids.map((m) => m[3])).size, ids.length, "六个入口必须使用不同图标");
  });

  it("不再把能力包 / 任务 / 通道 / 生命 / 自主作为一级入口", () => {
    for (const old of ["能力中心", "直播角色", "接入通道", "指令库", "角色", "大脑", "生命", "自主", "任务"]) {
      assert.ok(!new RegExp(`label: "${old}"`).test(sidebar), `一级导航不应再出现「${old}」`);
    }
    for (const oldKey of ["capabilities", "tasks", "prompts", "personas", "live", "channels", "brain", "life", "autonomy"]) {
      assert.ok(!new RegExp(`\\b${oldKey}: lazy`).test(app), `App 不应再注册旧页面 ${oldKey}`);
    }
  });

  it("侧栏文字常驻、可滚动，不靠 hover 覆盖层展开", () => {
    assert.ok(/\.sb-item-label\s*\{[^}]*display:\s*block/s.test(css), "导航文字应常驻显示");
    assert.ok(/\.sb-items\s*\{[^}]*overflow-y:\s*auto/s.test(css), "导航列表应可滚动");
    assert.ok(!/\.sidebar:hover\s+\.sb-inner/.test(css), "不应再用 hover 撑开覆盖层");
    assert.ok(/min-height:\s*0/.test(css.match(/\.sb-items\s*\{[^}]*\}/s)?.[0] || ""), "滚动容器需要 min-height:0");
    assert.ok(sidebar.includes("aria-current"), "当前页应有 aria-current 标记");
  });

  it("创作 / 智能体 / 工作台的二级页签接回一级容器", () => {
    assert.ok(app.includes('page === "create"') && app.includes("LazyPages.create"), "创作页应接入");
    assert.ok(app.includes('page === "agent"') && app.includes("LazyPages.agent"), "智能体页应接入");
    assert.ok(read("components/CreatePage.jsx").includes("PersonasPage embedded"), "创作页应嵌入角色页");
    assert.ok(read("components/BrainPage.jsx").includes("LifePage embedded"), "智能体应嵌入生命页");
    assert.ok(read("components/BrainPage.jsx").includes("AutonomyPage embedded"), "智能体应嵌入自主页");
    assert.ok(read("components/Pages.jsx").includes("<TasksPage embedded"), "工作台应嵌入任务记录");
  });
});

describe("能力页回归", () => {
  it("「查看全部」有真实展开逻辑，而不是无事件按钮", () => {
    const pages = read("components/Pages.jsx");
    assert.ok(pages.includes("setExpanded"), "应维护展开状态");
    assert.ok(/查看全部\s*\$\{d\.count\}/.test(pages), "应显示真实的查看全部数量");
    assert.ok(/expanded\[d\.name\]\s*\?/.test(pages), "应按展开状态渲染全部工具");
  });

  it("能力包页不再引用未定义的 --color-* 变量", () => {
    const cap = read("components/CapabilitiesPage.jsx");
    assert.ok(!/var\(--color-/.test(cap), "CapabilitiesPage 不应再使用未定义的 --color-* 令牌");
    assert.ok(cap.includes("var(--text-3)") && cap.includes("var(--border)"), "应使用主题令牌");
  });

  it("工具数量不再在界面里写死 115 / 135 / 169", () => {
    const files = [
      "components/ChatPage.jsx",
      "components/SettingsPage.jsx",
      "components/CapabilitiesPage.jsx",
      "components/Pages.jsx",
    ];
    for (const f of files) {
      const t = read(f);
      assert.ok(!/115\s*项能力|135\s*工具|内置工具（169\s*项）/.test(t), `${f} 仍有写死的旧工具数`);
    }
  });

  it("工作台最近产物兼容对象元数据，不出现 [object Object]", () => {
    const pages = read("components/Pages.jsx");
    assert.ok(pages.includes('f && typeof f === "object"'), "应兼容 /v1/situation 的对象形式");
    assert.ok(pages.includes("f.name || String(p).split"), "应从对象/字符串统一取文件名");
  });

  it("旧的无入口页面已移除", () => {
    const pages = read("components/Pages.jsx");
    for (const dead of ["export function FilesPage", "export function EvolutionPage", "export function SystemPage"]) {
      assert.ok(!pages.includes(dead), `Pages.jsx 不应保留无入口的 ${dead}`);
    }
    assert.ok(!pages.includes("function Combobox("), "无用的 Combobox helper 应删除");
  });
});
