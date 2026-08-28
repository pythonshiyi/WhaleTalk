# 🐋 鲸语 WhaleTalk · AI 全能桌面智能体 / AI Desktop Agent

[![CI](https://github.com/pythonshiyi/WhaleTalk/actions/workflows/ci.yml/badge.svg)](https://github.com/pythonshiyi/WhaleTalk/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/pythonshiyi/WhaleTalk?color=blue)](https://github.com/pythonshiyi/WhaleTalk/releases)
[![官网](https://img.shields.io/badge/%E5%AE%98%E7%BD%91-whaletalk.top-0a84ff)](https://whaletalk.top/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)

> **中文为主 · English follows**（中文完整介绍 + 英文简版）

**鲸语 WhaleTalk v3.5.0** 是一个为 DeepSeek V4 API 深度优化的 Windows AI 智能体——不止聊天窗口，而是一个**看得见屏幕、听得见语音、动得了鼠标键盘、还能自我进化**的 AI 工作台。Web 重构后以 React 现代界面 + 本地 API 服务形态呈现：三套主题（星空/深海/北极）、控制台侧栏、产物直达、人工智能一键切换。

*WhaleTalk v3.5.0 is a Windows AI agent deeply optimized for the DeepSeek V4 API — rebuilt with a React frontend: modern UI, console sidebar, one-click artifact access, and self-evolution. WhaleTalk is an independent product brand with no affiliation to DeepSeek.*

> 🌐 **官网 / Website：**https://whaletalk.top/

## 📑 目录

- [🎉 v3.0 重大更新](#-v30-重大更新)
- [🧠 产品介绍](#-产品介绍)
- [🖥 核心功能](#-核心功能)
- [🏗 系统架构](#-系统架构)
- [🔧 安装与启动](#-安装与启动)
- [🕘 更新策略](#-更新策略)
- [👥 关于我们](#-关于我们)
- [🔒 安全与隐私](#-安全与隐私)
- [📄 文档](#-文档)
- [English Introduction](#english-introduction)
- [⚠️ 品牌与免责](#️-品牌与免责)

---

## 🎉 v3.0 重大更新

**从 Tkinter 桌面版重构为 Web 架构（重大版本）：**

- **全新 React 前端**（`webui/`）：现代视觉三主题、消息密度/字号控制台、产物一键直达、控制台侧栏（模型/思考档/场景/外观/功能开关集中管理）
- **统一 Web 入口**（`web_app.py`）：纯 Web + 托盘常驻——启动本地 API、自动打开浏览器、系统托盘驻留；桌面/开始菜单自动创建快捷方式（无窗口，双击即用）
- **本地 API 服务**（`api_server.py`，127.0.0.1:8745）：REST + SSE 流式，tool 全链路保留（记忆/失败模式/成功模式/项目上下文注入）
- **原生体验**：开机自启（无窗静默）、完成提示音与桌面通知、静默启动进托盘、单实例（重复启动只打开界面）
- **生产即真实**：无演示/假数据；后端不可用时界面明确提示错误，不做离线兜底

> 详见 [CHANGELOG.md](CHANGELOG.md) v3.0.0 节。

## 🧠 产品介绍

以「**Windows 本地 + DeepSeek V4 云推理**」为设计原点，把 V4 的 Agent 能力、多模态视觉、1M 长上下文、峰谷定价、前缀缓存优势转化为「开箱即用」的桌面体验：

- **看得见**：🖼 多模态视觉（图片理解/图表阅读/截图修复/OCR/扫码、屏幕截图）
- **说得出**：💬 对话/思考模式/语音合成（TTS）/朗读
- **做得了**：⚡ 118 项 Agent 工具（文件/代码/数据库/浏览器/邮件/媒体/桌面 RPA/应用管理），权限模型分层
- **会进化**：🧬 自我进化（提案分支、失败模式库、成功模式复用）
- **自疗**：🔁 失败模式沉淀 + 已知坑注入，AI 越用越聪明

技术底座：Python 3.9+ + React 前端 + 本地 API（openai/httpx）。

## 🖥 核心功能

### 🖼 多模态视觉 Agent

- **视觉模型支持**：`deepseek-v4-flash-vision-exp`（图片输入，自动切换/返回）
- **拖拽/粘贴/按钮三方传图**（JPEG/PNG/GIF/WebP，≤32MB）
- **屏幕视觉闭环**：`screen_see` 截图 + 图表理解，AI"看屏幕-自查-修正"
- **图像理解**：`image_understand` / `chart_read` / `screenshot_to_html` / `scan_read` / `debug_screenshot` / `image_batch`
- **视觉自审**：生成图片/图表后自动"看图审阅"，默认关节约成本

### 🖥 Web 界面（v3.0 新）

- **三主题**：星空（默认）/ 深海 / 北极冰，一处切换全局生效
- **控制台侧栏**：模型/思考档/场景/温度/Top-P/Seed/输出上限 + JSON/Beta/strict/工具开关 + 主题/密度/字号
- **产物直达条**：AI 回复中的文件路径（md/txt/xlsx 等）一键打开/定位所在文件夹/注入输入框
- **文件面板**：工作区树 + ⭐ 最近产物（打开/定位/注入三连），新产物实时跟出
- **消息体验**：流式 Markdown、思考卡片、工具卡片、收藏/固定/分叉/变体/续写、多选批量导出
- **会话管理**：多会话/标签/搜索/导入导出（JSON/JSONL）、历史库

### ⚡ Agent 工具链（118 项）

- **信息**：搜索（多引擎/分页/过滤/健康降级）、GitHub、实时热点（Hacker News）、网页抓取（含被墙站点代理通道）、RSS
- **执行**：沙箱 Python、终端/进程、pip 安装、浏览器自动化
- **数据**：SQLite/MySQL/PostgreSQL、CSV/Excel、图表、KV 存储（diskcache）、WebDAV
- **文档**：PDF 提取/生成、Word/PPT、二维码、音视频（ffmpeg）、Markdown
- **媒体**：图像生成/处理、语音转文字（whisper）、TTS
- **桌面 RPA**：pyautogui 鼠标/键盘/滚轮/截屏（防误触 failsafe）
- **自动化**：定时任务（错峰省费）、流程编排、任务检查点、知识库
- **v3.1 新增**：📦 应用管理 `app_manage`（winget/choco 装/卸/搜/升级，环境搭建闭环）· 🖱 视觉定位点击 `screen_find_click`（看图→定位→点击一步完成）· 🎙 实时语音对话 `voice_chat_loop`（听一句答一句）· 🤝 多智能体编排 `team_run`（协调者拆解+角色接力+共享黑板）· 🌐 网络自愈 `net_diagnose`/`fetch_url_smart`（分层诊断+自动走代理兜底）

### 🧬 自我进化

- 提案分支（`create_evolution`）：读代码 → 提改动建议 → 分支提案，绝不改原文件
- 失败模式库：工具失败自动去重沉淀（≤50 条），注入上下文引导规避
- 成功模式复用：已验证工具链注入，同类任务优先复用

### ✍️ 公众号自动写作

多信源采集（RSS+搜索+论坛）→ 选题（历史去重）→ 三阶段写作 → 质量门禁 → 草稿箱（只产草稿，发布权在你）。

## 🏗 系统架构

```
┌────────────── Web 前端（React / webui/）──────────────┐
│  ChatPage · Sidebar · AuxPanel(控制台) · ContextPanel │
│  文件面板 · 产物直达 · 主题/密度 · 设置中心            │
└──────────────────────┬───────────────────────────────┘
                       │ REST + SSE (http://127.0.0.1:8745)
┌──────────────────────▼───────────────────────────────┐
│                api_server.py （本地 API）             │
│  会话/配置/上下文/工具调用/记忆/文件/进程/状态/etc     │
├──────────────────────────────────────────────────────┤
│                  deepseek_client.py                  │
│  DeepSeek V4 客户端（thinking/多模态/tool/压缩/缓存） │
├──────────────────────────────────────────────────────┤
│  backend：permissions · stores · stats · crypto ·    │
│  plugins · wechat_writer · sprint · config_utils ... │
└──────────────────────────────────────────────────────┘
```

- **入口**：`web_app.py`（唯一入口）：启动本地 API + 自动打开浏览器 + 系统托盘常驻；`--server` 无头 API；`--no-tray`/`--no-browser` 可选
- **数据目录**：`C:\Users\<你>\Documents\WhaleTalk\`（配置/会话/记忆/统计；API Key 加密存储）
- **安全**：仅 127.0.0.1 监听 + Bearer token；工具权限黑白名单；SSRF 防护

## 🔧 安装与启动

```bash
# 方式一：双击 start.bat（自动创建虚拟环境并安装依赖）
# 方式二：
pip install -r requirements.txt
python web_app.py            # 启动本地服务 + 打开浏览器 + 托盘常驻（推荐）
python web_app.py --server   # 仅启动 API 服务（终端常驻，供远程/开发）
python web_app.py --no-tray  # 常驻但不启用系统托盘
# 方式三：双击 build_exe.bat 打包为 dist\WhaleTalk.exe（WhaleTalk.spec）
```

要求：**Python 3.9+，Windows 10/11**。首次启动会自动构建 WebUI：`web_app.py` 检测到 `webui/dist` 缺失或源码有更新时自动执行 `npm run build`（缺依赖先 `npm ci/install`），已构建则跳过；打包版 exe 前端已内置，无需 Node。前端单独开发：`cd webui && npm i && npm run dev` / `build`（可加 `--no-webui-build` 跳过自动构建）。

### 配置

1. 在 https://platform.deepseek.com 申请 API Key
2. 启动后在设置页「API Key」粘贴保存（或编辑 `config.json`）
3. 选择 `deepseek-v4-flash-vision-exp` 启用图像输入；「strict 工具模式（Beta）」自动启用 `/beta`

## 🕘 更新策略

- **版本线**：`v3.0.0`（Web 重构重大版本）起，同步镜像 `config_defaults.VERSION`（单一版本源）
- **更新源**：GitHub Releases（`https://api.github.com/repos/pythonshiyi/WhaleTalk/releases/latest`，或自定义 `update_url`）
- **更新方式**：
  - 应用内「关于/帮助 → 检查更新」（自动检测 GitHub 最新版）
  - 更新包支持 Ed25519 签名校验（配置 `update_public_key`）+ SHA-256 校验
  - 更新前自动备份 `backups/WhaleTalk_v<版本>_<时间戳>.zip`（一键回滚）
- **兼容性**：旧配置自动迁移；旧数据目录无缝升级
- **分支**：`main`（稳定版）· CI 自动跑 653+ 测试

## 👥 关于我们

鲸语 WhaleTalk 是一个**独立开发的个人作品**，关注「本地 AI 智能体」体验——

- **目标**：让 Windows 用户拥有一个真正"看得见、做得了、能进化"的 AI 助手，而非只是一个聊天窗口
- **理念**：本地优先（数据不出本机）、能力为王（118 工具）、自我进化（提案/失败沉淀）、成本透明（峰谷定价/缓存命中）
- **联系**：
  - 官网：https://whaletalk.top/
  - GitHub Issues：https://github.com/pythonshiyi/WhaleTalk/issues
  - 邮件：见 GitHub 仓库 Profile
- **感谢**：DeepSeek 团队的 V4 API 与开放文档；React/Vite 生态；所有开源依赖库

> 鲸语 WhaleTalk 与 DeepSeek **无任何关联**，是独立产品品牌。

## 🔒 安全与隐私

- **数据不出本机**：默认仅 127.0.0.1 监听；API Key 加密存储；隐私模式可关快照/会话/记忆/统计
- **工具控制**：文件权限白名单（`permissions.json`）、SSRF 防护（内网/元数据拦截）、审批闸门（高危工具）
- **安全承诺**：可执行文件拒绝直接打开；sandbox Python 禁用危险模块；上传/下载限额；zip 炸弹防护
- 详见 [SECURITY.md](SECURITY.md)

## 📄 文档

- [CHANGELOG.md](CHANGELOG.md)（版本历史）
- [TECH_NOTES.md](TECH_NOTES.md)（架构笔记）
- [MODULES.md](MODULES.md)（模块拆分清单）
- [CONTRIBUTING.md](CONTRIBUTING.md)（贡献指南）
- [docs/](docs/)（开发文档）

---

## English Introduction

WhaleTalk v3.5.0 is a Windows AI agent optimized for DeepSeek V4 — rebuilt as a **local-first Web architecture**: React frontend + local API (127.0.0.1:8745) with a pywebview desktop shell and system tray.

- **v3.0 highlights**: 3 themes (starfield/deepsea/arctic), console sidebar (model/thinking/scene/appearance), artifact one-click access, unified `web_app.py` entry (desktop/browser/headless)
- **Capabilities**: 118 Agent tools (files/browser/DB/mail/media/desktop RPA), vision (image/OCR/screenshots), speech (whisper/TTS), self-evolution (proposals/failure patterns), WeChat article writer
- **Stack**: Python 3.9+ + React (Vite) + local API (openai/httpx) · Windows 10/11

### Quick Start

```bash
pip install -r requirements.txt
python web_app.py          # Desktop window (pywebview + tray)
python web_app.py --server # Headless API at http://127.0.0.1:8745/
```

Get an API Key at https://platform.deepseek.com. Select `deepseek-v4-flash-vision-exp` for image input.

**Updates**: GitHub Releases (auto-check in-app); backups before update; migration old configs.

## ⚠️ 品牌与免责

鲸语 WhaleTalk 是独立产品，与 DeepSeek 官方**无任何关联**；不基于任何官方内部接口。请遵守当地法律法规，合理使用 AI 能力。
