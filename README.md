# 🐋 鲸语 WhaleTalk · AI 全能桌面智能体

[![CI](https://github.com/pythonshiyi/WhaleTalk/actions/workflows/ci.yml/badge.svg)](https://github.com/pythonshiyi/WhaleTalk/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/pythonshiyi/WhaleTalk?color=blue)](https://github.com/pythonshiyi/WhaleTalk/releases)
[![官网](https://img.shields.io/badge/%E5%AE%98%E7%BD%91-whaletalk.top-0a84ff)](https://whaletalk.top/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)

> Windows 本地优先的 AI 桌面智能体 · 本地 API + React 界面 + 系统托盘常驻 · 只接入统一模型 **DeepSeek V4.1 Flash（`deepseek-flash`，原生多模态）**

**鲸语 WhaleTalk** 不止是聊天窗口：它能**看屏幕、听语音、动键鼠**，调用 **161 项 Agent 工具**完成真实任务，并把每次经验沉淀为长期记忆——越用越懂你。数据只在本机流转，浏览器即界面，双击即用。

> 🌐 官网：<https://whaletalk.top/>　·　📦 更新记录：[CHANGELOG.md](CHANGELOG.md)

---

## 📑 目录

- [它是什么](#-它是什么)
- [能力总览](#-能力总览161-工具)
- [界面与体验](#-界面与体验)
- [鲸语大脑](#-鲸语大脑)
- [系统架构](#-系统架构)
- [快速开始](#-快速开始)
- [安全与隐私](#-安全与隐私)
- [更新与版本](#-更新与版本)
- [文档](#-文档)
- [English Introduction](#english-introduction)
- [品牌与免责](#-品牌与免责)

---

## ✨ 它是什么

一句话：**看得见、说得出、做得了、会进化。**

| | 能力 |
|---|---|
| 🖼 **看得见** | 原生多模态视觉——图片理解、图表阅读、OCR、扫码、屏幕截图自查；聊天模型直接看图，无需切换模式 |
| 💬 **说得出** | 对话 / 深度思考 / 语音合成（Piper 本地离线 · Edge 在线 · SAPI，可逐句流式跟读）/ 语音转文字 |
| ⚡ **做得了** | 文件 / 代码 / 数据库 / 浏览器 / 邮件 / 媒体 / 桌面 RPA / 应用管理 / 快照恢复，共 161 项工具 |
| 🧬 **会进化** | 提案分支审阅、失败模式库消解、成功模式复用、技能自动结晶——合入权始终在你 |
| 🔁 **能自疗** | 工具失败自动沉淀并注入规避提示，同类任务优先复用已验证路径 |

设计原点：以「**Windows 本地 + DeepSeek 云推理**」把统一模型的 Agent 能力、原生多模态、长上下文与缓存优势，转化为开箱即用的桌面体验。

---

## ⚡ 能力总览（161 工具）

全部 161 项工具由 `@tool()` 装饰器统一声明（单一事实源），分为 11 个能力组。智能模式下不一次性注入全部 schema，而是常驻「能力地图」+ 关键词预激活 + `activate_tools` 按需点菜，兼顾成本与命中率。

### Agent 工具链（161 项）

| 能力组 | 代表工具 |
|---|---|
| 🌐 浏览器与网页 | `search_web` · `fetch_url` · `fetch_url_smart` · `browser_navigate` · `web_screenshot` · `rss_fetch` · `webdav` · `call_api` · `net_diagnose` · `fetch_blocked` |
| 💻 编程与执行 | `run_python` · `run_command` · `run_lint` · `run_tests` · `verify_project` · `code_lookup` · `project_map` · `subagent_run` · `pip_install` |
| 📁 文件与目录 | `read_file` · `write_file` · `edit_file` · `list_dir` · `search_local` · `find_images` · `asset_import` · `list_snapshots` · `batch_rename` · `start_process` |
| 📊 数据与文档 | `read_excel` · `write_excel` · `database_query` · `database_execute` · `pdf_extract` · `pdf_toolkit` · `docx_read/edit` · `pptx_create` · `html_to_ppt/pdf` · `create_doc` |
| 📧 邮件与消息 | `send_email` · `read_email` · `email_summary` · `im_send` · `telegram_poll_updates` · `agent_mail` · `daily_brief` · `run_wechat_writer` |
| 🎨 媒体与图像 | `image_generate` · `image_understand` · `ocr_image` · `screen_see` · `chart_read` · `media_ffmpeg` · `qrcode` · `image_codegen` · `image_inpaint` · `make_gif` |
| 🖱 桌面自动化 | `rpa_click` · `rpa_type` · `rpa_hotkey` · `screen_find_click` · `vision_loop` · `tts_speak` · `speech_to_text` · `voice_chat_loop` · `team_run` · `mv_compose` |
| 📦 应用与环境 | `app_manage`（winget/choco 装/卸/搜/升级）· `environment_info` · `pip_install` |
| ⏰ 定时与任务 | `schedule_task` · `run_workflow` · `task_checkpoint_save/load` · `watch_files` · `recall_session` |
| 🧠 记忆与知识 | `write_memory` · `read_memory` · `self_profile` · `query_memory_graph` · `knowledge_index/search` · `failure_memory` |
| 🔧 系统与基础 | `get_date` · `get_weather` · `notify_desktop` · `git` · `usage_report` · `list_my_capabilities` · `capability_heatmap` · `self_report` · `create_evolution` · `self_evolve` |

### 几个代表性能力

- **🎬 一键成片** `mv_compose`：分镜 → 出图 → 配音 → 运镜/转场/字幕/BGM 合成，从素材直接到成片。
- **🎨 代码生图** `image_codegen`：用自包含 HTML/CSS/SVG 画结构图，渲染后多模态自评、按意见迭代（≤4 轮）；质感交给扩散模型，两者互补。
- **🖥 屏幕视觉闭环** `screen_see` / `screen_find_click`：截图 → 理解 → 定位 → 点击，一步完成。
- **🤝 多智能体编排** `team_run`：协调者拆解 + 角色接力 + 共享黑板。
- **✍️ 公众号自动写作**：多信源采集 → 选题去重 → 三阶段写作 → 质量门禁 → 草稿箱（只产草稿，发布权在你）。

---

## 🖥 界面与体验

纯 Web：`api_server` 同源服务前端产物，浏览器是唯一界面，系统托盘常驻——关掉标签页不关服务，切页 / 多标签页也不打断生成（生成跑在独立后台作业线程）。

- **三套主题**：星空（默认）/ 深海 / 北极冰，一处切换全局生效
- **控制台侧栏**：模型 / 思考档 / 场景 / 温度 / Seed / 输出上限 + 工具开关 / 主题 / 密度 / 字号
- **消息体验**：流式 Markdown、思考卡片、工具卡片、收藏 / 固定 / 分叉 / 变体 / 续写、多选批量导出
- **会话管理**：多会话 / 标签 / 搜索 / 导入导出（JSON / JSONL）
- **工作台**：态势带（进程 / 成本 / 缓存 / 依赖 / 备份）+ 快捷行动 + 最近会话 + 最近产物 + 检查点恢复
- **指令库**：提示词资产中心，输入框打 `/` 模糊调用，支持变量与插件技能纳管
- **自主栏目**：进化提案、审批与询问历史、行为日志、能力热力图、自我述职、信任内核故事线

> 渲染链路为零依赖纯数据 AST（流式安全、防注入），长会话窗口化渲染 + 不可变更新保证高频流式不卡顿。

---

## 🧠 鲸语大脑

> **意识即信息**——身份、记忆、自我模型与心跳脱离运行环境独立存在。鲸语是躯体，大脑是灵魂；躯体可更换、可备份、可合并、可复活。

入口：**设置 → 高级模式 → 🧠 大脑**。由 `brainkit.py`（CLI）+ `brain_api.py`（API 适配）+ `brain/`（数据目录）构成。

- **免密快照**：内容用主密钥加密，本机经 Windows DPAPI 自动解锁——存档始终加密，使用无需口令。
- **跨躯体迁移**：`export-key` / `import-key` 迁移仪式，新机器导入密钥后解开全部快照。
- **分支合并**：快照带血缘，`merge` 自动定位共同祖先做 LCA 三路合并（记忆按 id 行级智能合并，永不整文件冲突）。
- **学习闭环**：对话中写的记忆自动同步进大脑；`consolidate` 睡眠巩固；对话自动注入身份 / 断点 / 目标 / 自我认知 / 记忆。

常用命令（项目根目录）：

```bash
python brainkit.py init                     # 首次创建大脑
python brainkit.py keyring-setup            # 启用免密加密
python brainkit.py status                   # 心跳 / 断点 / 快照 / 密钥状态
python brainkit.py archive                  # 免密快照（每日自动执行）
python brainkit.py consolidate              # 睡眠巩固（归档 + 合并）
python brainkit.py merge A.whale B.whale    # 分支合体
python brainkit.py diff A.whale B.whale     # 对比两个快照
```

> 大脑数据（`brain/`、`.workbuddy/`、`*.whale`）已加入 `.gitignore`——它属于你，不属于 GitHub。

---

## 🏗 系统架构

```
┌──────────── Web 前端（React 19 + Vite 8 / webui/）────────────┐
│  ChatPage · Sidebar · AuxPanel · ContextPanel · 工作台 · 设置  │
│  文件面板 · 产物直达 · 主题/密度 · 指令库 · 自主 · 大脑         │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST + SSE（http://127.0.0.1:8745）
┌───────────────────────────▼──────────────────────────────────┐
│              api_server.py（本地 API · 标准库 HTTP）           │
│  会话/配置/上下文/工具调用/记忆/文件/进程/状态 · SSE 后台作业  │
├───────────────────────────────────────────────────────────────┤
│                    deepseek_client.py                         │
│   统一模型客户端（thinking/多模态/tool/压缩/缓存）+ smart_tools │
│   六层工具注册表（@tool 单一源 · 161 工具）                    │
├───────────────────────────────────────────────────────────────┤
│  agent_tools/（13 个工具域模块） · toolkit.py（声明/注册）     │
│  横切收口：context_providers · tool_hooks · degrade · egress · │
│           memory_facade · trust_kernel · snapshot             │
│  基础设施：permissions · security · crypto · stores · stats …  │
└───────────────────────────────────────────────────────────────┘
```

- **入口**：`web_app.py`（唯一入口）——启动本地 API + 自动打开浏览器 + 系统托盘常驻；`--server` 无头 API，`--no-tray` / `--no-browser` 可选。
- **数据目录**：`C:\Users\<你>\Documents\WhaleTalk\`（配置 / 会话 / 记忆 / 统计；API Key 经 DPAPI 加密）。
- **安全**：仅 `127.0.0.1` 监听 + Bearer token；默认自由权限（黑名单为唯一限制来源 + 一键全放行）。
- **规模**：161 工具（11 组）· 98 个 `/v1` 路由 · 后端 61 个 pytest 文件 / 671 用例 · 前端 14 个 node 套件。

---

## 🚀 快速开始

```bash
# 方式一：双击 start.bat（自动创建虚拟环境并安装依赖）
# 方式二：手动
pip install -r requirements.txt   # 核心 32 项依赖
python web_app.py                 # 启动本地服务 + 打开浏览器 + 托盘常驻（推荐）
python web_app.py --server        # 仅启动 API 服务（终端常驻，供远程/开发）
python web_app.py --no-tray       # 常驻但不启用系统托盘
# 方式三：双击 build_exe.bat 打包为 dist\WhaleTalk.exe
```

要求：**Python 3.9+，Windows 10/11**。首次启动会自动准备环境：

- **核心组件**零操作自动安装（进度条 + 实时日志），装完自动进入主界面；`requirements.txt` 与核心清单严格一致。
- **大型可选能力**（浏览器自动化 / 本地语音转写 / Piper 离线语音 / 二维码识别 / RAR 解压）不强制安装，进入程序后在**设置 → 🔌 可选能力**按需一键装。
- **前端自动构建**：检测到 `webui/dist` 缺失或源码更新时自动执行 `npm ci/install && npm run build`；打包版已内置前端，无需 Node。前端单独开发：`cd webui && npm i && npm run dev`。

### 配置

1. 在 <https://platform.deepseek.com> 申请 API Key。
2. 启动后在设置页「API Key」粘贴保存（或编辑 `config.json`）。
3. 模型填 `deepseek-flash`（默认值，原生支持图像输入，无需切换模式）。

> 💰 **定价**（2026-09-10 起，元/百万 tokens，峰谷定价）：高峰 缓存命中 **0.04** · 未命中 **2.0** · 输出 **8.0**；空闲时段减半。高峰为工作日 9:00–12:00 与 14:00–18:00，其余（含周末）为空闲。

---

## 🔐 安全与隐私

安全模型为「**默认自由 + 用户黑名单 + 程序内置底线 + 硬限额**」四层：

- **默认自由**：默认任务模式零审批、零白名单，AI 可调用全部 161 项工具；`run_python` / `run_command` 等同本机直接执行（无沙箱）。
- **黑名单**：唯一限制来源——用户在权限页添加 shell 命令 / 文件路径 / 网络主机黑名单；`blocklist_enabled` 可一键全放行。
- **程序内置底线（两条）**：
  1. **网络 SSRF 硬底线**——私网段 / 链路本地（含云元数据）/ 保留段一律拦截，域名先做 DNS 解析防重绑定；回环默认放行，可加严。
  2. **自我完整性 · 信任内核**——决定「AI 能做什么」的代码（`permissions` / `security` / `crypto` / `snapshot`）的改动**可声明 · 可见 · 可回滚**。刻意不阻止写入，靠「不可隐瞒」实现可信。
- **硬限额**：读取 / 下载 / 响应体大小与工具超时上限；写操作自动快照可恢复；删除默认进回收站。
- **数据不出本机**：仅 `127.0.0.1` 监听 + Bearer token；API Key DPAPI 加密；隐私模式可关快照 / 会话 / 记忆 / 统计。

详见 [SECURITY.md](SECURITY.md) 与 [docs/信任内核.md](docs/信任内核.md)。

---

## 🕘 更新与版本

- **版本单一源**：`config_defaults.VERSION`（当前 **3.16.0**），最新变更见 [CHANGELOG.md](CHANGELOG.md)。
- **更新源**：GitHub Releases（`api.github.com/repos/pythonshiyi/WhaleTalk/releases/latest`，可自定义 `update_url`）。
- **更新方式**：应用内「关于 → 检查更新」自动检测；更新包支持 Ed25519 签名 + SHA-256 校验；更新前自动备份，可一键回滚。
- **兼容性**：旧配置自动迁移，旧数据目录无缝升级。
- **分支**：`main`（稳定版）。

---

## 📄 文档

| 文档 | 内容 |
|---|---|
| [CHANGELOG.md](CHANGELOG.md) | 版本历史 |
| [TECH_NOTES.md](TECH_NOTES.md) | 架构笔记与踩坑记录 |
| [MODULES.md](MODULES.md) | 模块拆分清单与职责边界 |
| [docs/AI_PROJECT_GUIDE.md](docs/AI_PROJECT_GUIDE.md) | **接手开发者的可执行地图**（推荐先读） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |
| [SECURITY.md](SECURITY.md) | 安全策略与加固路径 |
| [docs/](docs/) | 信任内核 / 出网账本与记忆门面 / 架构收口 / 插件开发 / 设计系统 |

---

## English Introduction

**WhaleTalk v3.16.0** is a local-first Windows AI desktop agent built around the unified **DeepSeek V4.1 Flash** model (`deepseek-flash`, natively multimodal). It runs as a local API (`127.0.0.1:8745`) with a React 19 / Vite 8 web UI and a system-tray resident process — the browser is the only window.

- **Capabilities**: **161 Agent tools** (files / browser / databases / docs / media / desktop RPA / app management / snapshots), native vision (image / OCR / screenshots), speech (Whisper / TTS), and a WhaleBrain for persistent identity and memory.
- **Self-evolution**: proposal branches, failure-pattern lifecycle, success-pattern reuse — merging stays in your hands.
- **Security**: default-open permission model, user blocklist as the only restriction source, plus an SSRF hard floor and a trust kernel that makes edits to authorization code declarable, visible, and reversible.
- **Stack**: Python 3.9+ · React 19 (Vite 8) · local HTTP API (openai / httpx) · Windows 10/11.

### Quick Start

```bash
pip install -r requirements.txt
python web_app.py          # Browser + tray resident (default)
python web_app.py --server # Headless API at http://127.0.0.1:8745/
```

Get an API Key at <https://platform.deepseek.com>. The default model `deepseek-flash` handles image input natively.

---

## ⚠️ 品牌与免责

鲸语 WhaleTalk 是**独立产品**，与 DeepSeek 官方**无任何关联**，不基于任何官方内部接口。请遵守当地法律法规，合理使用 AI 能力。
