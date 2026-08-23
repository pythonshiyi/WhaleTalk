// ── 新一代界面演示数据（mock）────────────────────────────
// 架构约定：UI 层只依赖 api 对象（见 src/api.js），
// 演示阶段 api 由本文件的流式模拟器实现，接入真实后端时零改动。

export const sessions = [
  {
    id: "s1",
    title: "AI 芯片市场调研",
    time: "09:41",
    pinned: true,
    tag: "调研",
    model: "deepseek-v4",
    brief: "汇总了英伟达/AMD/自研芯片最新动态，形成五段式报告",
  },
  {
    id: "s2",
    title: "公众号：AI 基建与算力专题",
    time: "昨天",
    pinned: false,
    tag: "写作",
    model: "deepseek-v4",
    brief: "多信源采集 → 选题 → 三阶段写作 → 质量门禁 92 分",
  },
  {
    id: "s3",
    title: "WhaleTalk 重构：图形引擎选型",
    time: "昨天",
    pinned: false,
    tag: "开发",
    model: "deepseek-v4",
    brief: "对比 Tkinter/PySide/Web 三方案，输出技术备忘录",
  },
  {
    id: "s4",
    title: "数据库巡检脚本编写",
    time: "周一",
    pinned: false,
    tag: "数据",
    model: "deepseek-v4-flash",
    brief: "SQLite 只读校验 + 变更预览 + 表格格式化",
  },
  {
    id: "s5",
    title: "临时会话 · 随手一问",
    time: "周一",
    pinned: false,
    tag: "临时",
    model: "deepseek-v4",
    brief: "前缀缓存命中率如何保住 99%",
  },
];

export const navItems = [
  { id: "chat", label: "会话", icon: "chat" },
  { id: "workbench", label: "工作台", icon: "home" },
  { id: "abilities", label: "能力中心", icon: "grid" },
  { id: "plugins", label: "插件市场", icon: "puzzle" },
  { id: "memory", label: "记忆与知识", icon: "brain" },
  { id: "settings", label: "设置", icon: "gear" },
];

// ── 剧本：一次完整的演示对话（思考 → 工具 → 流式报告）────────
export const demoScript = [
  {
    type: "think",
    text: "用户想要 AI 芯片市场的最新动态与结构化报告。先并行检索多个信源，再抓取权威页面交叉验证，最后按 五维框架 输出。",
  },
  {
    type: "tool",
    name: "search_web",
    label: "多引擎联网搜索",
    args: { query: "AI 芯片市场 2026 最新动态", engines: ["bing", "baidu"], num: 8 },
    result: "6 个引擎返回 47 条结果，去重后 23 条有效；英伟达 B 系列扩产、国产算力政策、自研芯片三个方向热度最高",
    cost: "¥0.031",
  },
  {
    type: "tool",
    name: "fetch_url",
    label: "网页全文抓取",
    args: { url: "https://semi.org.cn/report/ai-chips-2026" },
    result: "抓取正文 8,214 字符 · 编码 utf-8 · 用时 1.2s",
    cost: "¥0.002",
  },
  {
    type: "tool",
    name: "write_md",
    label: "生成 Markdown 报告",
    args: { path: "D:/workspace/reports/AI芯片市场动态_20260823.md" },
    result: "已写入 2.4KB · 12 段 · 含 3 张数据表",
    cost: "¥0.000",
  },
  {
    type: "text",
    text: `# AI 芯片市场动态速览（2026-08-23）

## 一、市场总览

2026 年全球 AI 芯片市场规模预计 **1,180 亿美元**，同比增长 **34%**。增速主要由推理侧需求拉动——大模型从训练转向规模化部署，推理芯片出货量首次超过训练芯片。

| 厂商 | 主打产品 | 制程 | 2026 出货指引 |
|------|---------|------|--------------|
| 英伟达 | B200 / GB300 | 4nm | 上调 18% |
| AMD   | MI450X | 3nm | 上调 12% |
| 华为  | 昇腾 950 | 7nm | 供不应求 |
| 寒武纪 | 思元 690 | 5nm | 产能翻倍 |

## 二、三个值得关注的信号

1. **推理芯片占比反超**：推理 / 训练出货比首次突破 1.2，边缘侧小模型带动中低端需求。
2. **国产算力政策加码**：多个智算中心招标转向国产方案，国产份额预估从 22% 升至 31%。
3. **CoWoS 封装仍为瓶颈**：先进封装产能紧张，成为头部厂商扩产的最大约束。

## 三、风险提示

> 地缘政策与出口管制是最大变量；建议关注下季度英伟达财报电话会与国产替代订单落地节奏。

报告已保存至 \`D:/workspace/reports/\`，需要我进一步拆分到公众号文章或生成图表吗？`,
  },
];

// ── 历史会话静态内容（切换查看）────────────────────────
export const historyMessages = {
  s1: [
    { role: "user", text: "帮我调研一下 AI 芯片市场最新动态，出一份报告" },
    {
      role: "assistant",
      text: `# AI 芯片市场动态速览（2026-08-23）\n\n## 一、市场总览\n\n2026 年全球 AI 芯片市场规模预计 **1,180 亿美元**，同比增长 **34%**。\n\n| 厂商 | 主打产品 | 2026 出货指引 |\n|------|---------|--------------|\n| 英伟达 | B200 / GB300 | 上调 18% |\n| AMD | MI450X | 上调 12% |\n\n报告已保存至 \`D:/workspace/reports/\`。`,
    },
  ],
  s2: [
    { role: "user", text: '围绕"AI 基建"写一篇公众号文章草稿' },
    {
      role: "assistant",
      text: `# AI 基建，正在经历一场"供给侧革命"\n\n> 本文已通过质量门禁（**92 分**）：结构完整、事实有源、无敏感表述。\n\n**草稿已存至公众号草稿箱**，发布权在你——需要调整标题党程度或补充数据源吗？`,
    },
  ],
  s3: [
    { role: "user", text: "对比 Tkinter、PySide6、Web 三方案做图形引擎选型" },
    {
      role: "assistant",
      text: `# 图形引擎选型备忘\n\n| 方案 | 表现力 | 后端复用 | 工期 |\n|------|--------|---------|------|\n| Tkinter | 低 | 100% | — |\n| PySide6 | 中 | 100% | 2~3 月 |\n| Web + pywebview | **高** | 90% | 3~4 月 |\n\n**结论**：优先 Web 路线——渲染瓶颈是 Tkinter 的天花板，换引擎才能根治"难看、不流畅、扩展难"。`,
    },
  ],
  s4: [
    { role: "user", text: "写一个 SQLite 巡检脚本，要只读、带变更预览" },
    {
      role: "assistant",
      text: "已完成：\n- \`readonly_stmt\` 只读 SQL 校验（拒绝多语句/写操作）\n- \`db_preview_sql\` 变更预览（行数/列映射/影响估计）\n- \`table_to_md\` 表格格式化输出\n\n全部 8 项测试通过。",
    },
  ],
  s5: [
    { role: "user", text: "前缀缓存命中率怎么保住 99%？" },
    {
      role: "assistant",
      text: "三条：**1)** 能力地图缓存键改为内容指纹，深拷贝不失效；**2)** 消息头部（系统提示 + 工具定义）稳定不变；**3)** 思考成本剥离，只缓存稳定前缀。当前命中率实测 ~99%。",
    },
  ],
};

export const toolCallDefs = {
  search_web: { icon: "search", color: "var(--brand)" },
  fetch_url: { icon: "globe", color: "var(--ai)" },
  write_md: { icon: "doc", color: "var(--ok)" },
  run_python: { icon: "code", color: "var(--warn)" },
};

// 上下文面板数据
export const contextData = {
  tools: [
    { name: "search_web", desc: "多引擎联网搜索", state: "on" },
    { name: "fetch_url", desc: "网页全文抓取", state: "on" },
    { name: "read_file", desc: "文件读取", state: "on" },
    { name: "write_md", desc: "Markdown 生成", state: "on" },
    { name: "database_query", desc: "数据库只读查询", state: "off" },
    { name: "send_email", desc: "邮件发送", state: "off" },
  ],
  memory: [
    { id: "MEM#1024", text: "用户偏好 Markdown 五段式报告结构", tag: "偏好" },
    { id: "MEM#0987", text: "报告默认保存到 D:/workspace/reports/", tag: "习惯" },
    { id: "MEM#0912", text: "上次失败：批量图片任务超时 → 应小并发", tag: "经验" },
  ],
  usage: { prompt: 4120, completion: 1860, cached: "99.2%", cost: "¥0.47" },
};

export const statusBar = {
  model: "deepseek-v4",
  thinking: "medium",
  context: "1M",
  budget: "¥12.4 / ¥100",
  mode: "标准",
};