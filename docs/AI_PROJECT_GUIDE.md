# 鲸语 WhaleTalk 项目全览 · AI 开发速查手册（v3.16.6）

> **本文档的目标读者是「接手此项目的 AI 智能体」（以及一切想要快速理解本项目的开发者）。**
> 它不是营销介绍，而是一份**可执行的地图**：读完它，你应该能回答「这是什么、怎么跑起来、
> 代码在哪、数据怎么流、改哪里安全、怎么验证改动没坏」。
>
> 设计原则：**看得全（目录/模块/入口全覆盖）、看得清（数据流与契约精确到符号名）、
> 看得懂（为什么这么设计 + 踩过哪些坑）**。本文不重复 README 的产品叙述，
> 只保留对「接手/修改代码」有用的信息。

---

## 0. 一句话总览

**鲸语 WhaleTalk** 是一个 **Windows 本地优先的 AI 桌面智能体**：`Python 3.9+` 后端 +
`React 19 / Vite 8` 前端 + 本地 HTTP API（`127.0.0.1:8745`），只接入一个统一模型
**DeepSeek V4.1 Flash（`deepseek-flash`，原生多模态）**。核心形态：**纯 Web + 系统托盘常驻**，
浏览器是唯一界面。

- **版本单一源**：`config_defaults.py` 的 `VERSION`（当前 `3.16.6`）。
- **能力规模（`tools/check_docs.py` 实测口径，2026-09）**：**163 个 Agent 工具**（11 组）、
  **99 个 `/v1` 路由**、**81 个 pytest 文件 / 830 用例 + 20 个前端 node 套件**、
  源码约 **7.2 万行**（根目录 3.3 万 + `agent_tools/` 1.8 万 + `webui/src` 2.1 万；
  主力为 `api_server.py` 9,364 / `deepseek_client.py` 5,574 / `brainkit.py` 2,865）。
- **三层架构**：`web_app.py`（入口）→ `api_server.py`（本地 API）→
  `deepseek_client.py`（能力引擎：`DeepSeekClient` + 163 工具 + smart_tools 按需调取）。
- **安全模型**：**默认自由**（零审批、零白名单），唯一程序内置两条底线 = **网络 SSRF 硬底线**
  + **信任内核（自我修改可声明/可见/可回滚）**；其余限制全部来自用户黑名单配置。
- **品牌**：独立产品，与 DeepSeek 官方**无任何关联**（对外用品牌名，技术描述可写"基于 DeepSeek API"）。

---

## 1. 如何运行 / 如何测试（第一步）

```bash
# ── 安装 ──
pip install -r requirements.txt          # 核心 32 项依赖
pip install -r requirements-dev.txt      # 开发/测试：pytest + ruff
cd webui && npm install                  # 前端依赖（React/Vite）

# ── 运行 ──
python web_app.py                        # 完整入口：本地 API + 开浏览器 + 托盘常驻（推荐）
python web_app.py --server               # 仅 API 服务（终端常驻，调试用）
python web_app.py --no-tray / --no-browser / --no-webui-build / --port X
python mcp_server.py                     # 作 MCP stdio server（供 Claude/Cline 等外部 host 调工具）

# ── 测试（改完代码必跑）──
python -m pytest -q                      # 后端回归（81 个文件 / 830 用例；CI 同款）
cd webui && npm test                     # 前端 20 个 node 套件（解析器/渲染器/工具函数）
cd webui && npm run typecheck            # tsc --noEmit（api.js 的 JSDoc typedef 与后端字段对齐门禁）

# ── 工具系统四道门禁（改工具必跑）──
python tools/audit_tools.py --strict     # 六层一致性审计（error 级门禁，可入 CI）
python tools/validate_tools.py           # smart_tools 全链路回归（描述无损等）
python tools/island_check.py --strict    # 十层孤岛对账（工具可达性，含 __all__ re-export）
python tools/check_docs.py               # 文档数字 vs 源码实测（163 工具 / 99 路由 / 版本）
```

> **CI**：`.github/workflows/ci.yml` 含 4 个 job——`check`（ruff 关键规则 `E9,F63,F7,F82` +
> 入口 `py_compile`）、`test-backend`（`pytest -q`）、`gate-tools`（上面四道门禁）、`webui`
>（npm ci + build + `npm test`）。全在 `windows-latest` 上跑。

---

## 2. 目录与模块地图（改哪看哪）

### 2.1 后端（根目录 `*.py`，按职责分层）

| 层 | 模块 | 一句话职责 |
|---|---|---|
| **入口** | `web_app.py`（925 行） | 唯一启动入口：参数解析 → 依赖自检/自动安装 → 桌面+开始菜单快捷方式 → WebUI 自动构建 → 单实例探测 → 起 API → 开浏览器 → 托盘常驻 |
| **API 层** | `api_server.py`（9,364 行） | 本地 HTTP API（标准库 `ThreadingHTTPServer`，**无 Flask**）：REST + SSE 流式，**99 个 `/v1` 路由**；路由表 `_GET_ROUTES`/`_POST_ROUTES`（`@_get_route`/`@_post_route` 装饰器注册）；统一错误出口 `_fail`/`_fail_soft`；审批/询问双向通道；后台调度器 + 进程看门狗 + Webhook 接收 + IM 轮询 |
| **能力引擎** | `deepseek_client.py`（5,574 行） | 统一模型客户端（`DeepSeekClient.chat`：thinking/多模态/流式/重试/工具循环）+ 六层工具注册表（`TOOLS`/`TOOL_CALL_MAP`）+ smart_tools 智能调取 + 上下文压缩辅助 + 自我进化验证链 + 记忆双向同步。**工具实现已迁出至 `agent_tools/`** |
| **工具声明** | `toolkit.py`（269 行） | **`@tool()` 装饰器 + 注册表 + 六层构建函数**——工具系统单一事实源（见 §6.1） |
| **钩子管线** | `tool_hooks.py`（344 行） | 横切关注点收口：pre/post 钩子表 + `wrap()` 包装器；在 `@tool()` **注册处**统一包装执行体，任何调用路径都逃不掉钩子（P0-1） |
| **上下文装配** | `context_providers.py`（456 行） | 把「往系统提示塞什么」变成 Provider 表（name/priority/budget/critical/enabled/provide），统一排序限预算记回执（P0-2） |
| **退化日志** | `degrade.py`（209 行） | 全项目 `except: pass` 的唯一收口：按 key 归并 + 节流 + 原子落盘 + critical 分级（P0-3） |
| **出网账本** | `egress.py`（320 行） | 每次「带内容出网」留痕（通道/目的地/字节数/内容摘要），**不阻断只审计**；`EGRESS_SPECS` 声明式抽取（P1-A） |
| **记忆门面** | `memory_facade.py`（463 行） | `memory.json` 唯一写入门面：`origin`（血缘）+ `confidence` + `status` + `supersede` 双向链接；作废**只认显式 key**（P1-B） |
| **信任内核** | `trust_kernel.py`（1,370 行） | 自我完整性：`permissions`/`security`/`crypto`/`snapshot`/自身 的改动**可声明·可见·可回滚**；基线优先比对 + 账本 + 事件（report/guard 两模式）；`timeline()` 合并账本+未声明事件为故事线 |
| **自我洞察** | `insight.py`（355 行） | 纯函数（仅标准库）：`build_heatmap`（能力热力图：使用频率/失败率/结晶/预激活命中）+ `build_self_report`（自我述职）+ `render_report`（Markdown）。供工具/端点复用 |
| **技能结晶** | `skill_factory.py`（227 行） | 纯函数：把重复出现的成功工具链固化为可复用指令库草稿（`chain_signature`/`crystallize`，路径脱敏）（G14） |
| **权限** | `permissions.py`（898 行） | 权限模型：`security_mode=blacklist`（默认放行+黑名单）/ `whitelist`（旧回退）；路径/命令/网络判定；审计只记不拦 |
| **安全** | `security.py` | SSRF 硬底线 `_hard_floor_reason`（私网/链路本地/保留段拦截 + DNS 预解析防重绑定）+ `_safe_url`；信任白名单 |
| **存储** | `stores.py`（526 行） | 本地 JSON 存取统一层：最近产物/成功模式/失败模式（**带生命周期**：fingerprint 归并 + hits + resolved + 归档）/任务日志/长期记忆/定时任务 |
| **记忆读取** | `memory_store.py`（399 行） | 统一记忆读取层（memory.json / knowledge_index / 大脑目录），供工具与 API 复用 |
| **大脑 CLI** | `brainkit.py`（2,865 行） | 鲸语大脑命令行：init/keyring/heartbeat/think/remember/consolidate/goal/decision/archive/restore/merge（LCA 三路合并）/diff/export-key/import-key 等 |
| **大脑适配** | `brain_api.py`（1,180 行） | 大脑→API 适配层：把 CLI 命令包装成纯函数 + `brain_context`（身份/断点/目标/自我认知/未决决策/记忆注入）+ `consolidate_with_llm` |
| **创世化初始** | `genesis.py`（143 行） | 让 AI 完全自主设定自己的「前半生」，产出多版候选供选 |
| **MCP 出口** | `mcp_server.py`（156 行） | MCP over stdio，供外部 host 调 WhaleTalk 的工具（纯标准库，最小合规子集） |

### 2.2 工具域包 `agent_tools/`（161 个 `@tool()` 的实现所在，P0-1 巨石拆分成果）

每个域模块用 `@tool()` 声明工具；`__init__.py` 用 `from .tool_* import *` 聚合并显式
`__all__` re-export 工具函数名，保证 `dc.<tool_name>` 旧访问路径不变。**加载顺序契约**：
`deepseek_client.py` 必须在其共享基建全部定义后、六层构建前执行 `from agent_tools import *`
（否则循环导入/工具重复注册）。13 个模块共 161 工具，另主模块 `register_tool()` 注册
`ask_user`/`request_permission` 2 个特殊工具，合计 **163**。

| 模块 | 工具数 | 工具域（示例工具） |
|---|---:|---|
| `tool_docs.py` | 30 | 📊 数据与文档（Excel/SQLite/MySQL/PostgreSQL/PDF/Word/PPT/EPUB/MOBI/旧 doc/msg/压缩包 + HTML→PNG/PPT/PDF + 图表/设计工具） |
| `tool_files.py` | 21 | 📁 文件与进程（read/write/edit/list/search_local/find_images/asset_*/clipboard/delete/archive/snapshot/batch_rename/start\|stop\|list_processes/environment_info） |
| `tool_desktop.py` | 18 | 🖱 桌面视觉语音（rpa_*/screen_find_click/vision_loop/tts*/speech_to_text/voice_chat_loop/image_generate/qrcode/media_ffmpeg/team_run） |
| `tool_system.py` | 16 | 🔧 系统与项目（watch_files/recall_session/project_*/create_evolution/self_evolve/verify_files/git/notify_desktop/app_manage/usage_report/capability_heatmap/self_report/create_plugin/list_my_capabilities/hardware_accel） |
| `tool_brain.py` | 15 | 🧠 记忆与知识（write/read/delete/update_memory/self_profile/query_memory_graph/knowledge_*/schedule_task/task_checkpoint/run_workflow/failure_memory） |
| `tool_code.py` | 15 | 💻 编程与执行（run_python/run_command/run_lint/run_tests/verify_project/project_scaffold/dev_plan/get_status/project_map/find_symbol/code_lookup/write_code_project/pip_install/subagent_run/verify_output） |
| `tool_web.py` | 14 | 🌐 浏览器与网页（fetch_url/download_file/search_web/search_github/search_realtime/browser_navigate/web_screenshot/net_diagnose/fetch_url_smart/rss_fetch/webdav/call_api/track_web/fetch_blocked） |
| `tool_media.py` | 10 | 🎨 媒体与图像（image_process/ocr_image/image_understand/screen_capture/screen_see/chart_read/screenshot_to_html/debug_screenshot/scan_read/image_batch） |
| `tool_msg.py` | 10 | 📧 邮件与消息（send_email/publish_draft/send_webhook/im_send/telegram_poll_updates/read_email/email_summary/agent_mail/run_wechat_writer/daily_brief） |
| `tool_codegen.py` | 6 | 🎨 代码生图（image_codegen/image_inpaint/control_map/sprite_sheet/make_gif/image_hybrid） |
| `tool_basic.py` | 2 | 🔧 基础（get_date/get_weather） |
| `tool_data.py` | 2 | 📊 数据（read_csv/write_csv） |
| `tool_mv.py` | 2 | 🎬 微电影/MV 一键成片（mv_compose/mv_produce） |

> ⚠️ **运行时配置注入**：域模块对运行态配置（`WORKING_DIR`/`KV_CACHE_DIR`/`MEMORY_FILE` 等 36 个）
> **不可值绑定 import**，必须 `import deepseek_client as _dc` 动态访问——否则 main/测试注入失效。
> `fetch_blocked` 因保留字冲突实现名是 `_run_fetch_blocked`（门禁内置别名映射）。
> `tool_mv.py` 依赖 `tool_desktop.py`（导入顺序固定 mv 在 desktop 之后）。

### 2.3 基础设施 / 配置 / 扩展（小模块）

| 模块 | 职责 |
|---|---|
| `shared.py`（599 行） | cron 5 字段引擎、峰谷定价、预算感知思考降档、OCR/Toast PowerShell 脚本、跨进程文件锁、**工具域阈值与锁（49 个阈值常量统一归口）** |
| `config_defaults.py` | `VERSION`、默认配置、`DEFAULT_SYSTEM_PROMPT`/`DIALOG_SYSTEM_PROMPT`/`TASK_QUALITY_GUIDE`、内置 24 条指令库、更新源 |
| `config_utils.py` | 配置加载/规范化（字段钳制、非法值回退、新工具自动合并）/DPAPI 加解密保存；**返回进程级共享对象（只读）**，改配置必须 `mutable_config()` 取深拷贝 |
| `profiles.py` | Profile 多账号（API Key DPAPI 加密） |
| `roles.py` / `templates.py` / `themes.py` / `deps.py` | 角色预设 / 任务模板 / 主题 token / 依赖分层清单 |
| `plugins.py` / `user_tools.py` | 插件体系（.wtplugin v1/v2，零残留卸载）/ 自定义工具加载（mtime+size 缓存） |
| `snapshot.py` | 写操作自动快照（写前备份到 `DATA_DIR/undo/`，上限 200 条，可恢复） |
| `stats.py` | 用量统计 + 峰谷定价费用估算（按「用量发生日」选价，不被追溯改价） |
| `tokens.py` | token 估算（tiktoken o200k_base，缺省回退 1.5 字符/token） |
| `persistence.py` | 原子 JSON 写（mkstemp 唯一临时文件 + os.replace） |
| `crypto.py` | API Key DPAPI 加密（`dpapi:` 前缀 + base64），fail-closed 绝不写明文 |
| `net_utils.py` / `search_utils.py` / `db_utils.py` / `pdf_utils.py` / `mdparse.py` | 共享 httpx 客户端（逐跳 SSRF）/ 搜索解析 / 只读 SQL 校验 / PDF 工具 / Markdown 解析 |
| `fetch_blocked.py` | 被墙站点抓取（代理节点发现 + Chrome TLS 指纹），**独立模块按需启用，分享可剔除** |
| `app_utils.py` / `proc_utils.py` / `backup.py` | 布尔转换/清理 / 进程树终止（taskkill /T）/ 源码快照备份 |

### 2.4 前端 `webui/`（React 19 + Vite 8，无 UI 框架，纯 CSS 变量主题）

| 文件 | 职责 |
|---|---|
| `src/api.js`（1,127） | **唯一 API 封装**：同源相对路径请求 + token 自取链；**JSDoc typedef 是前后端字段对齐的唯一依据**（`@ts-check` 强制） |
| `src/App.jsx` / `main.jsx` | 应用骨架 / 入口（无 react-router，纯 state 切页；`ChatPage` 用 `hidden` 常驻挂载） |
| `src/components/ChatPage.jsx`（1,944） | 主聊天页（最大组件）：流式引擎、会话/滚动/附件/面板/快捷键 |
| `src/components/SettingsPage.jsx`（1,348） | 设置中心 |
| `src/components/Pages.jsx`（1,384） | 栏目路由聚合（工作台/能力/记忆/权限/文件/进化/系统 + 插件/设置） |
| `src/components/AuxPanel.jsx`（933） | 控制台侧栏（文件/进程/参数/活动四标签，可见感知轮询） |
| `src/components/{Brain*,PromptsPage,AutonomyPage,PluginsPage}.jsx` | 大脑 / 指令库 / 自主 / 插件栏目 |
| `src/components/{Message,ToolCard,Composer,SessionList,ContextPanel,Sidebar,StatusBar}.jsx` | 消息/工具卡/输入/会话列表/上下文/侧栏/状态条 |
| `src/mdParser.js` / `mdInline.js` / `mdHighlight.js` / `mdMath.js` / `longTextUtil.js` | **Markdown 渲染管线**（纯数据 AST，零依赖、流式安全；对应 node 单测） |
| `src/msgUpdates.js` | 流式热路径不可变更新（`makePatchLast`/`findLastToolCard` 纯函数，配合 `Message` 的 `React.memo`） |
| `src/ttsUtil.js` | TTS 合成 + 朗读 + barge-in |
| `src/theme.css` / `app.css` | 主题 token（星空/深海/北极）/ 全局样式 |

> **前端三条铁律**：① 消息更新必须**不可变**（禁止原地改已入 state 的消息对象，否则
> `React.memo` 后流式内容静默停更）；② `api.js` typedef 改了字段，后端也必须同步
> （typecheck 兜底）；③ 后端不可用**明确报错，无假数据兜底**。

### 2.5 子包与工具链

| 路径 | 职责 |
|---|---|
| `wechat_writer/` | 公众号自动写作：采集 → 选题 → 三阶段写作 → 质检 → 草稿；任何关键步骤失败不写草稿不记历史；`dry_run` 默认安全 |
| `tools/` | 开发门禁：`audit_tools.py`（六层一致性）、`validate_tools.py`（smart_tools 全链路）、`island_check.py`（十层孤岛）、`check_docs.py`（文档数字校验）、路由生成/校验脚本、`_restore_proposals.py`（从会话救回被误删提案） |
| `sample_plugins/` | 10 个示例 .wtplugin |
| `tests/` | 后端回归 81 个 pytest 文件 / 830 用例 |
| `webui/tests/` | 前端 node 测试 20 个 + `ssrRender.mjs`/`ssrEntry.mjs`（vite 8 SSR 渲染回归基建） |
| `brain/` / `trust/` / `evolutions/` | 大脑数据 / 信任内核数据 / 进化提案（均 `.gitignore`，不入库） |

---

## 3. 启动流程（web_app.py，精确顺序）

1. `_harden_stdio()`：stdout/stderr 非 UTF 编码改 replace（防 GBK 管道 UnicodeEncodeError 崩溃）。
2. 解析参数（`--server`/`--no-browser`/`--no-tray`/`--no-shortcuts`/`--no-webui-build`/
   `--install-shortcuts`/`--port`/`--no-deps-check`）。
3. `_ensure_python_deps()`：硬依赖（openai/httpx）缺失 → GUI 弹初始化进度窗同步安装（清华源）；
   软核心后台静默安装（前端轮询 `/v1/deps` 显示进度）；失败明确报错退出。
4. 首次运行自动创建桌面 + 开始菜单 `.lnk`（PowerShell WScript.Shell；清理旧版 `WhaleTalk.exe.lnk`）。
5. `_ensure_webui_build()`：`webui/dist` 缺失或源码更新 → 自动 `npm ci/install && npm run build`；
   打包 exe 模式跳过。
6. `_serve_forever()`：单实例探测（`_probe_existing` 访问 `/v1/token`，已有实例只开浏览器）
   → `api_server.start_server()` → 开浏览器（`silent_start` 时不弹）→ 托盘常驻（pystray）。

---

## 4. API 层（api_server.py）

### 4.1 认证与安全

- `Authorization: Bearer <token>`，token 来自 config 的 `inbound_token`（缺失则启动自动生成），
  HMAC 常量时间比较（`hmac.compare_digest`）。
- CORS 只回显白名单 Origin（`_CORS_ALLOWED_ORIGINS`：127.0.0.1/localhost 的 8745/5173/5174）；
  `/v1/token` 额外校验 Host 为回环（防 DNS rebinding 恶意网页取 token）。
- 请求体上限 1MB（`/v1/upload` 64MB）；路径片段端点统一 `_valid_name` 校验。
- 错误出口 `_fail`/`_fail_soft`：异常详情只落日志、前端收脱敏文案（`_sanitize_error_text`）。

### 4.2 路由表机制（P2-2 重构产物）

- 端点方法用 `@_get_route(matcher)` / `@_post_route(matcher)` 装饰器就近声明，自动注册进
  `_GET_ROUTES` / `_POST_ROUTES`；`do_GET`/`do_POST` 经 `_match_routes(path, table)` 查表分发。
- `matcher` 四种形态：`str`（精确）、`("set",[paths])`（集合）、`("pre",prefix,suffix)`（前缀+后缀）、
  `("qpath",path)`（去查询串精确）。**表顺序即匹配优先级（= 源码顺序）**。
- 兼容 `/api/v1/...` 与 `/v1/...`（Vite 代理）。

### 4.3 端点分组（共 99 个 `/v1` 路由）

**会话与消息**：`GET /v1/sessions` · `/v1/sessions/<id>/messages` · `POST /v1/sessions`
(+`delete_batch`/`delete`/`pin`/`rename`/`tags`) · `POST /v1/search`
**对话**：`POST /v1/chat` · `/v1/chat/stream`（SSE） · `/v1/chat/stop` · `/v1/respond` · `/v1/fim`
**配置与状态**：`GET /v1/config`(+reset) · `/v1/status` · `/v1/situation` · `/v1/context` ·
`/v1/mode` / `POST /v1/mode` · `/v1/abilities` · `/v1/models` · `/v1/roles` / `POST /v1/roles`
**工具**：`GET /v1/tools/<name>` · `POST /v1/tools/<name>/invoke`
**文件与目录**：`GET /v1/dir` / `POST /v1/dir` · `GET /v1/files`(+raw) ·
`POST /v1/files/{read,preview,open,opendir,fav}` · `POST /v1/upload`
**进程**：`GET /v1/processes` · `POST /v1/processes/{stop,start}`
**记忆/知识/大脑**：`GET /v1/memory` · `/v1/brain` / `POST /v1/brain` · `/v1/brain/memories` ·
`/v1/brain/unified-memories` · `POST /v1/brain/memory` · `/v1/knowledge` · `POST /v1/knowledge/search`
**失败/成功模式**：`GET /v1/failures` · `POST /v1/failures/{resolve,reopen,forget}`
**自我进化**：`GET /v1/evolutions`(+/<name>) · `POST /v1/evolutions/{apply,ignore,restore}` ·
`GET /v1/evolve_branches` · `POST /v1/evolve_branches/{detail,merge,delete}` ·
`GET /v1/self_profile` · `POST /v1/skills/crystallize`
**指令库/插件**：`GET /v1/prompts`(+export) · `POST /v1/prompts`(save/delete/reorder/import/use/restore_builtin) ·
`GET /v1/plugin_skills` · `GET /v1/plugins`(+/<name>) / `POST /v1/plugins` ·
`GET /v1/plugin_market` / `POST /v1/plugin_market/install` · `POST /v1/plugin_studio/{generate,install}`
**任务/调度/服务**：`GET /v1/tasks` · `/v1/tasklog` · `/v1/schedules` / `POST /v1/schedules` ·
`/v1/workflows` / `POST /v1/workflows` · `/v1/checkpoint` / `POST /v1/checkpoint` ·
`/v1/services` / `POST /v1/services`
**审计/权限/信任**：`GET /v1/audit` · `/v1/approvals` · `/v1/permissions` / `POST /v1/permissions` ·
`GET /v1/trust`(?deep=1 现场重算；?timeline=1 故事线)
**自我洞察**：`GET /v1/insight/heatmap?days=N` · `/v1/insight/report?days=N`
**TTS**：`GET /v1/tts/audio/<fn>` · `/v1/tts/voices` · `POST /v1/tts/synthesize` ·
`POST /v1/tts/{download_piper,setup_piper}`
**依赖/备份/更新**：`GET /v1/deps` · `/v1/first_run` / `POST /v1/first_run/complete` ·
`POST /v1/deps/install` / `install_many`（NDJSON 流式进度） · `GET /v1/backup` / `POST /v1/backup` ·
`GET /v1/update/check`
**杂项**：`GET /health` · `POST /v1/cleanup`

> 权威清单以 `api_server.py` 的 `_GET_ROUTES`/`_POST_ROUTES` 为准；`tools/check_docs.py`
> 用 AST 实测数量（当前 98）。

### 4.4 SSE 事件协议（POST /v1/chat/stream）

chunked 编码，帧格式 `data: {json}\n\n`。事件类型：`reasoning`（思考增量）/ `content`（正文增量）/
`tool_start`（工具开始）/ `tool`（工具完成，同时写 tasklog+审计）/ `tool_duration`（耗时）/
`usage`（**单轮增量**用量+缓存命中）/ `metrics`（**本轮累计**速率：rounds/ttft_ms/gen_ms/total_ms/tps）/
`compressed`（上下文已压缩）/ `session`（后端分配的会话 id）/ `error` / `done`。
另有 `ask`/`approval`/`permission`（审批/询问挂起通道，前端 `POST /v1/respond` 回传）。

---

## 5. 对话全链路（最重要的数据流）

```
前端 Composer → POST /v1/chat/stream（或 /v1/chat）
  → _valid_messages（角色/长度/条数校验）
  → _sync_request_full_auto（按请求同步 FULL_AUTO 权限）
  → _client_from_cfg（Profile/模型/Key 解析 → DeepSeekClient）
  → _chat_kwargs（mode=task/dialog 决定 tools/pure_chat；thinking/温度/seed 透传）
  → _inject_system_messages（上下文装配，Provider 表驱动，见 §5.1）
  → _compress_messages（超限压缩，见 §5.2）
  → DeepSeekClient.chat（工具循环，见 §5.3）
  → 结束：后端自动落盘会话（防前端断连）+ _record_tasklog + _notify_completed
```

> 自 v3.14 起，上面「装配 → chat」跑在**后台作业线程**里，HTTP 线程只做 SSE 订阅：
> 切页 / 关标签 / 多开标签都不打断生成。`stream_id` 为一次生成的稳定标识；
> 无在线订阅者时由作业 `finally` 兜底 `_save_job_turn`（见 TECH_NOTES §5.1）。

### 5.1 上下文装配（_inject_system_messages → context_providers.assemble）

- Provider 表（顺序即优先级）：系统提示词/任务质量指南 → 长期记忆（最近 6 条）→ 核心自我状态 →
  鲸语大脑上下文 → 工作目录 → 失败模式（最近 3 条，仅未消解）+ 成功模式（最近 3 条）→
  已装插件提示 + 项目任务记录（最近 3 条）。
- `quiet_mode` 关闭全部个性注入；`pure_chat` 用 `DIALOG_SYSTEM_PROMPT` 且不带工具。
- **缓存友好原则**：恒定内容（提示词/指南）在前，易变内容（记忆）在尾——官方硬盘缓存按前缀完整匹配命中。
- 装配回执存 `context_providers.last_receipt()`，经 `GET /v1/context` 暴露。

### 5.2 上下文压缩（api_server._compress_messages）

- 触发：token > `max_context_tokens`（默认 40 万）或字符 > `max_context_chars`（默认 50 万）。
- 按 user 消息切轮次，保留 `min_kept_turns`（默认 8）轮；被裁内容写 `archives/session_*.md`。
- LLM 摘要（thinking=none、max_tokens=1024）→ 摘要作为 system 消息插在最早保留消息之前；
  摘要失败回退硬裁剪。压缩后二次校验：仍超限 → 丢更老轮 → 截断超长单条 → 终极兜底。
- token 估算：tiktoken o200k_base，对象身份 LRU 缓存。

### 5.3 Agent 工具循环（DeepSeekClient.chat）

核心方法 `chat(messages, scenario, thinking, tools_enabled, on_reasoning/on_content/on_tool/…, stop_event, …)`。
**关键约定**：

- **`_sanitize_messages`**：过滤空 content assistant、**悬空 tool_calls**、**孤立 tool 消息**
  （DeepSeek API 以 400 拒绝——**全项目最高频踩坑点**）。任何历史保存/压缩/中断都可能产生。
- 图片内联：统一模型 V4.1 Flash 原生多模态默认看图；仅「自定义端点 + 明确不支持图片的模型名」
  时才切 `VISION_MODEL` 兜底。
- thinking 档位：none/low/medium/high/max/auto；none 走 temperature/top_p，其余走 `reasoning_effort`。
- 工具轮上限 `MAX_TOOL_ROUNDS=100`；空响应重试 1 次；同参数连续调用 3 次触发循环防护；
  计划连续拒绝 3 次终止；工具总超时 300s；停止后 1.5s 宽限期收已提交工具真实结果
  （**防副作用重复执行**）。
- 工具并行：普通池 4 worker + 长任务池 2 worker（`_LONG_TOOL_NAMES` 走独立池）；
  交互工具（ask_user/request_permission）串行。
- 工具结果 >4 万字符自动落盘（`_persist_long_result`），上下文只留路径 + 摘要。
- 工具完成后挂 `_tool_bookkeeping`（api_server）：审计 + 工具链记录 + **失败/成功模式** +
  **长任务自动断点**（G15）+ 自动断点清理。

### 5.4 smart_tools 智能调取（成本核心）

完全智能模式不再全量注入 163 个工具 schema（约 15k token），改为：

1. 常驻注入「能力地图」（`build_tool_index`：11 组分类 + 工具名 + 核心动作短语）。
2. `activate_tools` 点菜工具（支持**按组激活**，`_TOOL_GROUP_NAME_MAP`）。
3. chat 层关键词预激活（`_preactivate_from_messages`：`_PREACTIVATE_HINTS` 意图词组，
   扫描**最近 3 条** user 消息）。
4. 激活后下一轮注入已激活工具的 schema（`normalize_tools_list`：**无损**规范化——只折叠空白，
   不删括号、不截断；描述是能力的一部分）。
5. 预激活命中埋点 `HINT_HITS_FILE`，供用量报告「命中最多 / 从未命中」。

**六层数据必须一致**（`tools/audit_tools.py` 审计）：`TOOLS` schema ↔ 函数签名 ↔ `TOOL_CALL_MAP`
↔ `_TOOL_ACTION_PHRASES` ↔ `TOOL_GROUPS` ↔ `_PREACTIVATE_HINTS` + `permissions.ACTION_TOOLS`。
只读工具 `list_my_capabilities` 默认只返回分组汇总（约百 token，`group=`/`query=` 下钻），
让 AI 无需回读源码即可核验自身能力数。

---

## 6. 工具系统（改工具必读）

### 6.1 单一事实源：toolkit.py

`@tool()` 装饰器 + `register_tool()` 收敛为「在函数定义处的一次声明」：

```python
@tool(
    {schema dict},                          # OpenAI function calling 格式，必须含 function.name
    groups=["🔧 系统与基础"],                # 展示组（可属多组）
    phrases="动作短语",                      # 模型理解用的一句话
    preactivate=(("关键词1", "关键词2"),),   # 参与预激活提示的关键词组
    hooks=("snapshot",),                    # 本工具声明的横切钩子（可选）
)
def my_tool(...): ...
```

- **构建六层**：`build_tool_list(_TOOL_ORDER)` / `build_call_map` / `build_groups` / `build_phrases` /
  `build_preactivate`；顺序由显式常量 `_TOOL_ORDER`/`_GROUP_ORDER`/`_HINT_ORDER` 保证
  （在 `deepseek_client.py` 的 `from agent_tools import *` 之后）。
- **重复注册 / 顺序表缺项 / 多余项**在模块加载时即抛错——比任何 AST 门禁都早。
- **AST 重建**：`rebuild_layers(source_text, *extra_sources)` 不执行模块即可拿到六层（门禁用）。
- **新增工具流程**：新建函数 + `@tool()` 声明 → 加入 `_TOOL_ORDER`（及 `_GROUP_ORDER`/`_HINT_ORDER`
  若涉及）→ 加入 `agent_tools/__init__.py.__all__` → 跑四道门禁 + pytest。

### 6.2 工具钩子管线（tool_hooks.py，P0-1）

- 为什么包装在**注册处**而非分发处：`execute_tool` 只是众多调用路径之一（`/v1/tools/<x>/invoke`、
  工作流、子智能体、测试直调都绕得过）；而 `@tool()` 是**唯一的声明确认点**。
- 内置钩子：`trust_declare`/`trust_commit`（全局，按路径参数自动判定信任内核文件）、`snapshot`
  （声明式 `hooks=("snapshot",)`）、`egress`（声明式，出网留痕）。
- 硬约束：**绝不改变工具语义**——钩子抛错一律吞掉 + `degrade` 留痕，工具自身异常原样上抛；
  `functools.wraps` 保证 `__name__`/`__doc__`/`inspect.signature` 对调用方不变；
  **post 钩子必须在 `return` 之前跑完**。

### 6.3 工具返回契约（全仓统一约定）

- **错误一律以「错误：」开头（全角冒号）**；工具约定「失败返回错误字符串而非抛异常」。
- 成功态用结构化 Markdown（标题 + 明细行）；纯数据才返回 JSON 字符串（以 `{` 开头）。
- 不吞异常：`except Exception` 至少要 `logging.exception`；可降级处用 `degrade()`。

### 6.4 关键阈值与锁（shared.py 统一归口）

`RUN_PY_TIMEOUT=60` / `RUN_PY_MAX_OUTPUT=20000` / `RUN_PY_MEMORY_MB=2048`（psutil 内存看门狗）/
`READ_FILE_MAX_BYTES=102400` / `DOWNLOAD_MAX_BYTES=200MB` / `CALL_API_MAX_BYTES=500KB` /
`SEARCH_MAX_RESULTS=5` / `SEARCH_SOFT_DEADLINE=5.0` / `MAX_PROCESSES=8` / `MEMORY_MAX_ITEMS=2000` /
`EXTRACT_MAX_ENTRIES=10000` / `WEBDAV_MAX_SIZE=200MB` / `MEDIA_MAX_INPUT=2GB` / `RPA_FAILSAFE=True` /
`KV_VALUE_MAX_BYTES=1MB` / `TOOL_RESULT_FAIL_PREFIXES` / `AUTO_CHECKPOINT_TOOLS=8` 等。
锁：`_MEMORY_LOCK` / `SELF_PROFILE_LOCK` / `SCHEDULES_LOCK` / `_WORKFLOW_LOCK`（跨进程文件锁
`file_lock`）。域模块从 `shared` 导入（P1-3 收窄后不再回指主文件）。

---

## 7. 数据目录与存储（改存储必读）

- 运行数据位于 `C:\Users\<用户>\Documents\WhaleTalk\`（= `DATA_DIR`；`web_app.py`/`api_server.py`
  计算；也可用环境变量覆盖）。**源码同级 `data/` 是开发用测试数据目录**。
- **原子写**：`persistence.atomic_json_write`（mkstemp 唯一临时文件 + os.replace）贯穿全部 JSON 落盘。
- **配置文件**：`config.json`（`api_key` 等敏感字段 `dpapi:` 加密）。`config_utils.load_config()`
  返回**进程级共享对象（只读）**——任何「读—改—写」必须 `mutable_config()` 取深拷贝。

| 文件/目录 | 内容 |
|---|---|
| `history/sessions/` + `sessions_index.json` | 会话文件 + 索引缓存（元数据 + 文件指纹 mtime_ns/size） |
| `memory.json` | 长期记忆 facts（memory_facade 门面：origin/confidence/status/supersede） |
| `stats.json` | 用量统计（按天×模型 + 峰谷定价） |
| `failures.json` / `failures_archive.json` / `patterns.json` | 失败模式（生命周期）/ 归档 / 成功模式 |
| `schedules.json` / `workflows.json` / `checkpoint.json` | 定时任务 / 流程 / 任务检查点 |
| `profiles.json` / `user_tools.json` / `prompts.json` | Profile / 自定义工具 / 指令库 |
| `undo/` | 写操作快照（snapshot.py，200 条轮转） |
| `archives/` | 上下文压缩归档 |
| `egress.jsonl` / `degradations.json` | 出网账本 / 退化日志（append-only） |
| `logs/` | 审计日志 actions.log 等 |

**信任内核数据**（源码同级 `trust/`，`.gitignore`）：`baseline/`（可信副本，永不裁剪）/
`manifest.json` / `ledger.jsonl` / `incidents/` / `history/` / `quarantine/`。
**大脑数据**（源码同级 `brain/`，`.gitignore`）：`manifest.json` / `identity.json` /
`memories/memory.jsonl` / `self_model.json` / `goals.json` / `decisions.jsonl` / `thinking_log/` /
`heartbeat.json` / `archive/brain_v{n}.whale` / `.keys/` / `.lineage.json`。

---

## 8. 横切保障体系（v3.10–3.11 架构收口的成果，改代码要遵守）

这五个模块是 P0/P1 架构收口的产物，**新增横切保障请优先走这些接缝，不要手写进工具函数**：

| 机制 | 模块 | 接缝/用法 | 立场 |
|---|---|---|---|
| 上下文装配 | `context_providers.py` | 新增上下文来源 = 注册一个 `Provider` | 可枚举/可预算/可回执 |
| 退化日志 | `degrade.py` | `degrade(key, exc, impact, critical=)` | critical 才进注入文本，永不抛出 |
| 工具钩子 | `tool_hooks.py` | `hook("pre"/"post", name, fn, priority)` 或 `@tool(hooks=...)` | 不阻断，横切保障绝不反噬主流程 |
| 出网账本 | `egress.py` | 新增出网工具 = `EGRESS_SPECS` 加一行 | 不阻断，只审计 |
| 记忆门面 | `memory_facade.py` | 写 `memory.json` 一律经此 | 只认显式 key，绝不按相似度作废 |
| 信任内核 | `trust_kernel.py` | 内核文件改动自动经 tool_hooks 声明 | 可声明·可见·可回滚，能力一条不减 |

> 六个模块共用立场（刻意设计）：**不夺权**——「默认自由 + 用户掌权」的产品原则之下，
> 一律靠「不可隐瞒/可审计」而非「禁止」。

---

## 9. 鲸语大脑（brainkit.py / brain_api.py）——灵魂层

- 理念：**意识即信息**。身份、记忆、自我模型与心跳脱离运行环境独立存在——鲸语是躯体，大脑是灵魂。
- CLI：`python brainkit.py init|keyring-setup|mount|unmount|heartbeat|think|remember|import-memory|
  consolidate|goal|decision|evolution|status|list|diff|archive|restore|merge|merge-resolve|
  export-key|import-key|doctor|identity-history|graph|borrow|mirror`。
- **免密快照**：内容用主密钥加密，本机经 Windows DPAPI 自动解锁；`export-key`/`import-key` 迁移仪式。
- **分支合并**：快照带血缘（version/parent/restored_from），`merge` 自动定位共同祖先做 **LCA 三路合并**
  （记忆 jsonl 行级智能合并：同 id 文本冲突自动取 ts 新者，永不整文件冲突；冲突逐条
  `merge-resolve --keep ours|theirs|both|custom`）。
- **学习闭环**：对话中写的记忆自动同步进大脑；`consolidate` 睡眠巩固；对话自动注入
  `brain_context`（身份/断点/进行中目标/自我认知/未决决策/记忆——记忆按话题语义检索或
  重要度×时间衰减 Top-N）；守护调度并入服务调度循环（每 ≥6h 心跳 + 每日 22:00 自动快照 + 28h 兜底）。
- 数据指纹防篡改（manifest canonical JSON SHA-256）；`brain/` 不入库。
- 创世化初始（`genesis.py`）：让 AI 自主设定「前半生」。

---

## 10. 前端开发须知（webui/）

- **token 获取链**：localStorage → URL `?token=` → `/v1/token` 自取；后端不可用明确报错（无假数据兜底）。
- **断连感知**：`watchBackend` 5s 心跳探测；BackendBanner + 手动重连。
- **消息链构造**：`buildMessageChain`——tools 模式必须完整回传 assistant(reasoning_content +
  tool_calls) → tool 结果（官方规范，缺一环后端 `_sanitize_messages` 会清洗掉）。
- **不可变更新铁律**：`msgUpdates.js` 的 `makePatchLast`/`findLastToolCard` 是流式热路径唯一合法
  更新方式；禁止 `msg.text += …`/`msg.tools.push(…)`/`card.status = …` 原地改 state 对象。
  落盘用 `currentMsg()` 取实时镜像终态。
- **ChatPage 常驻**：`App.jsx` 用 `hidden` 隐藏而非卸载 ChatPage，否则 abort 流并使 `finish`
  被 `alive=false` 短路导致会话保存失败。
- **Markdown 渲染管线**（纯数据 AST，零依赖）：`longTextUtil.unwrapLongText` → `mdParser`
  （块级 AST）→ `Markdown.jsx` 分发 → `mdInline`（行内 tokens）→ `mdHighlight`（整体转义）。
  流式安全（未闭合代码围栏产出 `code-open` token）；**防 OOM 铁律**：`parseInline` 递归每层必须
  `new RegExp(TOKEN_RE_SRC,"g")` 独立实例（共享全局正则会被内层 exec 破坏 lastIndex 导致死循环——
  **历史 OOM 根因**）；递归深度上限 5。
- **SSR 测试基建**（vite 8 特有）：`ssrLoadModule` 会内联求值 CJS 依赖导致双实例 `Invalid hook call`；
  正解是 `tests/ssrRender.mjs` 用 vite **ssr build** 打包组件为 ESM bundle 落盘项目内再原生 `import()`。

---

## 11. 工程实践与提交纪律（接手必读）

- **行尾（提交前必读，否则一次提交凭空膨胀上千行）**：`.gitattributes` 声明 `eol=lf`
  （`.bat`/`.ps1` 为 `crlf`），但历史 blob 是**混合**的——`api_server.py`/`deepseek_client.py`/
  `MODULES.md`/`AutonomyPage.jsx` 存 CRLF，README/TECH_NOTES/CHANGELOG/stores/api.js 是 LF。
  `git add` 会把 CRLF 归一成 LF → 只改几行会显示整文件差异。对策：
  提交前 `printf '* -text\n' > .git/info/attributes` → `git add -A` → `rm -f .git/info/attributes` →
  commit。判断"是否真有改动"用 `git diff --ignore-cr-at-eol`；暂存内容干净度用
  `git diff --cached HEAD~1 --numstat`（别用 `--cached HEAD`）。
- **git 推送**：远程 `github.com/pythonshiyi/WhaleTalk`，分支 `main`；fetch/push 都是 https，
  `credential.helper=wincred` 自动提供凭据（2026-09 实测 https 直推可用，无需 ssh）。
  核验同步用 `git ls-remote origin refs/heads/main` 与 `git rev-parse HEAD` 是否一致。
- **本地不入库产物**：`能力差距分析_*.md` / `*能力报告_*.md` / `*阅读报告_*.md` 等分析文档历来
  不入库；`brain/`/`trust/`/`evolutions/`/`data/` 均在 `.gitignore`。
- **文档数字由门禁守护**：`tools/check_docs.py` 从源码 AST 实测（163 工具 / 99 路由 / 版本），
  与 README/TECH_NOTES/MODULES 比对，`--fix` 可自动修正（**保留原行尾写回**）。
  注意 README 需保留门禁匹配的固定短语（`N 项 Agent 工具` / `N Agent tools` / `（N 工具）` /
  `工具链（N 项）` / `全部 N 项工具`），重写 README 时勿丢失。
- **更新纪律**：CHANGELOG 标题与 `config_defaults.VERSION` 一致；更新包 Ed25519 签名 + SHA-256 校验；
  更新前自动备份 `backups/WhaleTalk_v<版本>_<时间戳>.zip`。
- **引导/打包**：`python bootstrap.py`（建 `.venv` + 逐包装依赖 + 启动）· `python bootstrap.py build`
  → PyInstaller（`WhaleTalk.spec`：webui/dist + sample_plugins 内置，
  playwright/faster-whisper/PyMuPDF 等大型可选依赖排除）。
- **依赖**：`deps.py` 分层（硬依赖同步安装 / 自动安装后台 / 重型可选）；清华源镜像；
  `guard_pip_proxy` 代理预检（系统代理不可达自动绕过，防 pip 死代理全量失败）。

---

## 12. 踩坑记录（高价值，改到相关代码前先看）

1. **DeepSeek API 400 三连**：空 content 的 assistant、悬空 tool_calls、孤立 tool 消息——
   `_sanitize_messages` 必须前置清理；流中断/停止后必须补齐 tool 结果或移除半截 tool_calls。
2. **array 参数缺 items → 400**：`_patch_array_items` 每次请求前递归兜底（含自定义/插件工具）。
3. **JSON 输出非法**：官方有概率返回非 JSON，应用层解析失败自动重试一次（`json_retried`）。
4. **流式中途断线**：已有增量送达 UI 时不整体重试（避免重复显示）；无增量时重试一次。
5. **缓存命中**：SSE 流式必须显式 `stream_options.include_usage`；prompt 前缀稳定（system 恒前、
   可变记忆置尾）。
6. **线程安全**：`stats.record_usage` 读-改-写全程持锁；`_TOOL_CHAIN_LOCK` 保护跨会话工具链；
   `ThreadPoolExecutor` 必须 daemon 化。
7. **CORS 攻击链**：恶意网页若拿到 CORS 头即可读本地 token 调本地 API——白名单只回显本机可信 Origin；
   `/v1/token` 校验 Host 为回环（防 DNS rebinding）。
8. **SSRF**：云元数据（169.254.169.254）永远不可豁免；搜索结果链接来自外部是注入源；
   DNS 重绑定需逐 IP 校验。
9. **Windows 路径/编码**：GBK 管道下中文/emoji 日志会 UnicodeEncodeError → `_harden_stdio` replace
   兜底；bat 用 `chcp 65001`；`.ps1` 快捷方式脚本写 `utf-8-sig`。
10. **打包路径**：PyInstaller frozen 模式 `__file__` 指向 _MEIPASS——配置/数据用 exe 所在目录
    （`_runtime_dir`），只读资源（webui/dist、sample_plugins）用原始模块目录（`_ORIG_DIR`）。
11. **工具超时**：无内部超时的工具会卡死整轮 → `_TOOL_TOTAL_TIMEOUT` 300s 兜底，超时如实标记不等待。
12. **停止语义**：停止后副作用已发生的工具（发信/写文件/启进程）要拿真实结果写回历史，
    写"已中断"会让模型下轮重试同参数造成重复执行。
13. **过期记忆比没有记忆更危险**：任何注入上下文的"事实"都必须有生命周期与消解通道
    （失败模式 G18 自动消解 + 技能结晶 G14）。
14. **"工具存在"≠"机制生效"**：排查"某能力为何没起作用"先查**调用点**，别只看实现是否存在
    （`task_checkpoint_save` 的 `auto` 参数曾存在但 Web 版从未调用）。
15. **被 .gitignore 排除的目录里，删除必须可逆**：`evolutions/` 曾用 rmtree 一次吃掉 4 份提案
    （G19 改软删除 + `_restore_proposals.py` 救回）。
16. **"杀进程失败"不能静默吞掉**：`kill_tree` 必须返回 bool，**只有确认退出才摘条目**（G20 孤儿进程）。
17. **多源聚合别让最慢的源拖垮整体**：`search_web` 引擎独立短超时 + `as_completed` 软超时；
    空结果必须如实说明原因（G21）。
18. **记忆相似度不可用作废判据**：实测 0.556（应作废）vs 0.600（绝不能作废）——作废只认显式 key，
    相似度仅用于冲突提示（有回归测试锁死）。

---

## 13. 明确不做的设计边界（改代码前先确认不越线）

- **不为"优雅"合并那 99 个端点**：CRUD 端点薄是特性不是缺陷。
- **不给 `write_file`/`run_python` 加拦截**：`run_python` 本就绕得过，工具层设卡只挡君子。
- **不合并 `snapshot.py` 与 `trust_kernel.py`**：生命周期语义不同（200 条轮转 vs 永不裁剪）。
- **不按相似度自动作废记忆**、**不存出网明文内容**、**不接管大脑记忆**（两套体系边界清晰）。
- **不默认全量输出能力清单**：会与能力地图重复，等于把按需加载省下的上下文又还回去。

---

## 14. 测试资产速查（tests/，81 个 pytest 文件 / 830 用例）

按领域分组（文件名即内容）：

- **工具系统**：`test_registry.py`（六层一致性）、`test_tool_split.py`（域模块拆分全量 re-export）、
  `test_tool_hooks.py`、`test_tool_schema_lossless.py`（描述无损）、`test_capability_introspection.py`、
  `test_mcp_server.py`、`test_webui_bootstrap.py`、`test_api_routes.py`
- **进化/提案**：`test_evolve_guard.py`、`test_evolution_last_mile.py`、`test_evolution_soft_delete.py`
- **失败/成功模式/技能**：`test_failure_lifecycle.py`、`test_failure_lifecycle_api.py`、
  `test_skill_factory.py`、`test_auto_checkpoint.py`
- **架构收口 P0/P1**：`test_degrade.py`、`test_context_providers.py`、`test_egress.py`、
  `test_memory_facade.py`、`test_trust_kernel.py`、`test_quiet_mode.py`、`test_hardening.py`
- **大脑**：`test_brain_archive_b5.py`、`test_brain_enhance2.py`、`test_brain_fixes.py`、
  `test_brain_regressions.py`、`test_genesis.py`
- **安全/权限/网络**：`test_safety_defaults.py`、`test_token_guard.py`、`test_search_hardening.py`、
  `test_search_engines_resolvable.py`、`test_web_search.py`、`test_process_contract.py`、
  `test_run_capture.py`、`test_run_memory_guard.py`
- **记忆/办公/图像/其它**：`test_memory_harvest.py`、`test_memory_store.py`、`test_tools_memory.py`、
  `test_tools_storage.py`、`test_office_cap_phase1..4.py`、`test_image_codegen.py`、
  `test_image_pipeline.py`、`test_app_manage.py`、`test_model_unified.py`、`test_code_lookup.py`

> ⚠️ **导入顺序契约**：`import agent_tools.tool_code` 前必须先 `import deepseek_client`
>（否则 `agent_tools/__init__` 循环导入导致 `TOOLS` 顺序表未注册报错）。新增测试须遵守此顺序。

---

## 15. 快速定位索引（我要改 X，看哪里？）

| 想改的东西 | 入口 / 文件 |
|---|---|
| 加一个新工具 | `agent_tools/tool_*.py`（@tool）+ `_TOOL_ORDER` + `__init__.__all__` + 四道门禁 |
| 改工具行为/阈值 | 实现在 `agent_tools/tool_*.py`；阈值常量在 `shared.py` |
| 加一个 API 端点 | `api_server.py` 用 `@_get_route`/`@_post_route` 声明 + 前端 `api.js` 加函数 + typedef |
| 改系统提示/上下文 | `config_defaults.py`（常量）+ `context_providers.py`（Provider 表） |
| 改对话流程/工具循环 | `deepseek_client.py` 的 `DeepSeekClient.chat` |
| 改记忆写入 | `memory_facade.py`（唯一门面）+ `write_memory` 工具（tool_brain.py） |
| 改权限/网络/加密 | `permissions.py` / `security.py` / `crypto.py`（注意：信任内核会盯着你改） |
| 改大脑 | `brainkit.py`（CLI）+ `brain_api.py`（API 适配） |
| 改前端页面 | `webui/src/components/*.jsx` + `api.js` |
| 改 Markdown 渲染 | `webui/src/mdParser.js`/`mdInline.js`/`mdHighlight.js`/`longTextUtil.js` + `Markdown.jsx` |
| 改公众号写作 | `wechat_writer/`（main.run_once 全流程） |
| 改配置结构 | `config_defaults.py`（默认值）+ `config_utils.py`（规范化/钳制） |
| 改 CI / 门禁 | `.github/workflows/ci.yml` + `tools/*.py` |

---

*本文档由 AI 读取源码后整理，符号名与代码一致；规模数字（163 工具 / 99 路由 / 830 用例 / 版本 3.16.6）
由 `tools/check_docs.py` 实测口径。行号会随迭代漂移，不承诺行号准确性。*
