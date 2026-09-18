# 模块地图（v3.16.0 Web 版）

本文档描述鲸语 WhaleTalk 当前（v3.16.0，Web 架构）的模块构成与职责边界，供维护、重构与新增功能时定位。与旧 Tkinter 版（main.py）相关的拆分记录已随 Web 重构归档，不再维护。

> 规模口径（`tools/check_docs.py` 实测）：**162 个 Agent 工具**（11 组）· **99 /v1 端点** · 后端 61 个 pytest 文件 / 671 用例 · 前端 14 个 node 套件；源码约 6.2 万行（根目录 2.9 万 + `agent_tools/` 1.7 万 + `webui/src` 1.6 万）。

## 分层总览

```
web_app.py（唯一入口：浏览器 + 托盘 + 快捷方式 + 依赖自检）
    │
    ▼
api_server.py（本地 HTTP API：REST + SSE，99 /v1 端点）
    │
    ▼
deepseek_client.py（能力引擎：DeepSeekClient + 162 工具 + smart_tools）
    │
    ├─ 基础设施：permissions / security / crypto / stores / stats / tokens / persistence
    │              ＋ trust_kernel（信任内核：自我修改的声明 / 核对 / 回滚）
    │              ＋ degrade（退化日志：静默降级收口）
    │              ＋ context_providers（上下文装配 Provider 表）
    │              ＋ tool_hooks（工具钩子管线：横切关注点收口）
    │              ＋ egress（出网账本：入网有 SSRF 底线，出网亦可审计）
    │              ＋ memory_facade（记忆门面：血缘 / 作废 / 冲突提示）
    │              ＋ snapshot（写操作自动快照）/ insight（自我洞察）
    ├─ 工具底座：net_utils / search_utils / db_utils / pdf_utils / proc_utils / mdparse
    ├─ 配置体系：config_defaults / config_utils / profiles / themes / roles / templates / deps
    ├─ 扩展体系：plugins / user_tools / fetch_blocked（按需）/ mcp_server（MCP 出口）
    ├─ 大脑：brainkit / brain_api / genesis（创世化初始）
    └─ 子包：wechat_writer（公众号写作）/ webui（React 前端）/ agent_tools（工具域模块）/ tools（开发门禁）
```

## 模块清单

### 入口与 API 层

| 模块 | 职责 |
|---|---|
| `web_app.py`（924 行） | 唯一启动入口：启动本地 API、自动打开浏览器、系统托盘常驻、桌面/开始菜单快捷方式、开机自启、单实例、WebUI 自动构建（npm）、Python 依赖自检与自动安装 |
| `api_server.py`（8,707 行） | 本地 HTTP API（标准库 `ThreadingHTTPServer`，无 Flask）：会话/配置/上下文/工具/记忆/文件/进程/插件/指令库/工作台/大脑/TTS/审计/备份/更新等 **98 个 /v1 端点**（等 99 端点，含失败记忆生命周期 `/v1/failures/resolve|reopen|forget` 与技能结晶 `/v1/skills/crystallize`）；SSE 流式对话——**生成跑在独立后台作业线程**（`_ChatJob`/`_CHAT_JOBS`，见 TECH_NOTES §5.1）：切页/关标签/多标签页不打断，HTTP 线程只做订阅，无订阅者时作业兜底落盘；统一错误出口 `_fail`/`_fail_soft`（异常详情只落日志、前端收脱敏文案）；路径片段端点统一 `_valid_name` 校验；审批/询问双向通道；后台调度器 + 进程看门狗 + Webhook 接收端 + IM 轮询 |

### 能力引擎

| 模块 | 职责 |
|---|---|
| `deepseek_client.py`（5,100 行） | 能力引擎（**薄 facade**）：**统一模型** `deepseek-flash`（DeepSeek V4.1 Flash，原生多模态）客户端（thinking/多模态/流式/重试）+ `MODEL_ID`/`MODELS`/`LEGACY_MODEL_ALIASES`/`resolve_model`/`is_vision_model` 模型层、`DeepSeekClient.chat` 工具循环、六层工具注册表（`TOOLS`/`TOOL_CALL_MAP`）、smart_tools 智能调取（能力地图 + `activate_tools` 点菜 + 关键词预激活）、上下文压缩辅助、自我进化验证闸、记忆双向同步。**工具实现已全部迁出**至 `agent_tools/` |

> 演进建议：`deepseek_client.py` 已按「共享基建 → 六层注册表 → 客户端类」分层组织，但仍是单文件。可继续把共享基建按领域收拢，保留顶层薄 facade 做 re-export 兼容，用 `tools/audit_tools.py` 门禁护航。

### 基础设施（纯函数/低依赖，可独立复用）

| 模块 | 职责 |
|---|---|
| `permissions.py`（868 行） | 权限模型（默认自由）：blacklist（默认放行 + 用户黑名单，`blocklist_enabled` 一键开关，出厂仅预置 169.254.169.254）/ whitelist（旧模式可回退）；路径/命令/网络判定；审计日志（只记不拦） |
| `security.py` | 网络主机判定/`_safe_url`：blacklist 模式 = 用户 `network.blocklist` + **SSRF 硬底线**（`_hard_floor_reason`：私网段/链路本地/保留段默认拦截、域名做 DNS 解析防重绑定；回环默认放行，可 `network.allow_loopback=false` 加严；`block_private=false` 或 `blocklist_enabled=false` 可关）；旧 whitelist 模式保留更严的 `_is_private_host` 判断；信任白名单（CIDR/域后缀） |
| `crypto.py` | API Key DPAPI 加密（`dpapi:` 前缀 + base64），fail-closed（加密失败绝不写明文） |
| `stores.py`（476 行） | 本地 JSON 存取：最近产物/成功模式/失败模式/任务日志/长期记忆/定时任务（统一原子写）。**失败模式带生命周期（G18）**：`fingerprint` 指纹归并 + `hits` 复现计数 + `resolved` 消解状态 + 溢出归档 `failures_archive.json`；`failure_patterns_text` 只注入**未消解**项；`auto_resolve_on_success` 按「同工具连续 N 次成功」自动消解 |
| `skill_factory.py`（168 行） | 技能结晶（G14，纯函数 · 仅标准库）：把「重复出现的成功工具链」固化为可复用资产——`chain_signature` / `collect_chains` / `is_crystallizable`（链长≥3 且重复≥2）/ `crystallize`（产出参数化指令库草稿，路径脱敏）；由 api_server 在任务链落盘后自动触发 |
| `stats.py` | 用量统计（按天×模型累计）+ 官方峰谷定价费用估算（`price_for(model, day)` 按**用量发生日**选价；未收录模型名回落当前价） |
| `tokens.py` | token 估算（tiktoken o200k_base，缺省回退 1.5 字符/token，对象身份缓存） |
| `persistence.py` | 原子 JSON 写入（mkstemp 唯一临时文件 + os.replace） |
| `snapshot.py` | 文件/数据库写操作自动快照（P2）：写/编辑/重命名/数据库写前备份原内容到 `DATA_DIR/undo/`，可列出/恢复（`list_snapshots`/`restore_snapshot` 工具）；上限 200 条自动清理 |
| `trust_kernel.py`（1,322 行） | 信任内核（自我完整性）：让智能体对**自身授权代码**（permissions / security / crypto / snapshot / 自身）的改动**可声明 · 可见 · 可回滚**——`trust/baseline/` 可信副本（永不裁剪）+ `manifest.json` + `ledger.jsonl` + `incidents/` + `history/` + `quarantine/`；启动 `boot_check()`、工具层 `declare/commit`（经 tool_hooks 全局钩子，绝不阻断写入）、CLI `status/verify/diff/restore/accept/log`。**刻意不加限制**：能力一条不减，只让改动不可能悄悄发生（详见 [docs/信任内核.md](docs/信任内核.md)） |
| `degrade.py`（214 行） | 退化日志（P0-3）：全项目静默 `except: pass` 的**唯一收口**——按 key 归并计数 + 节流日志 + 原子落盘；**critical 分级**（只让影响回答质量的降级进注入文本）；**永不抛出** |
| `context_providers.py`（433 行） | 上下文装配（P0-2）：把「往系统提示里塞什么」变成**可枚举、可预算、可回执**的 Provider 表（name/priority/budget/critical/enabled/provide）；装配器统一排序、限预算截断、输出回执；失败走 `degrade` 而非静默。**注入内容与顺序与改造前一致**；回执经 `GET /v1/context` 暴露 |
| `tool_hooks.py`（345 行） | 工具钩子管线（P0-1）：横切关注点的收口接缝——pre/post 钩子表 + `wrap()`，在 `toolkit` **注册处**统一包装执行体，任何调用路径都逃不掉。内置 `trust_declare`/`trust_commit`（全局）、`snapshot`/`egress`（声明式）。钩子抛错吞掉 + `degrade` 留痕；`functools.wraps` 保证签名不变 |
| `egress.py`（324 行） | 出网账本（P1-A）：每一次**带内容出网**留痕（通道/目的地/字节数/摘要/结果）。**不阻断**；**默认不存明文**；达阈值向模型注入「出网留痕」。`EGRESS_SPECS` 声明式抽取——新增出网工具只需加一行。落盘 `DATA_DIR/egress.jsonl` |
| `memory_facade.py`（443 行） | 记忆门面（P1-B）：`memory.json` 唯一写入门面，为长期记忆补 **origin（血缘）+ confidence + status + supersede 双向链接**。① 血缘：非 user 来源注入时加 `〔推断〕`/`〔来自外部内容〕`标注；② 作废：同 `key` 取代旧条目——**只认显式 key，绝不按相似度**；相似度仅用于冲突提示。旧数据读时补默认值、不改写文件 |
| `insight.py`（356 行） | 自我洞察（纯函数 · 仅标准库）：`build_heatmap`（能力热力图：工具使用频率/任务链长度/失败率/技能结晶/预激活命中）· `build_self_report`（自我述职）· `render_report`（Markdown）。不依赖运行时，便于单测 |
| `memory_store.py`（250 行） | 统一记忆读取层（memory.json / knowledge_index / 大脑目录），供工具与 API 复用 |
| `mv_engine.py` | 自建 MV 引擎（不依赖任何外部程序）：`analyze`（BPM/节拍/能量）、`align_lyrics`（faster-whisper 词级 + 顺序短语映射的声学对轴）、`build_shots`（卡点分镜网格）、`render_frames`（确定性 PIL 帧）、`build_srt`、`verify_shots`；被 `agent_tools/tool_mv.py` 的 `mv_produce` 消费 |
| `genesis.py`（144 行） | 创世化初始：让 AI 完全自主设定自己的「前半生」，产出多版候选供选 |
| `app_utils.py` | 布尔转换、空壳目录判断、清理、干净退出标记、隐私日志 |
| `proc_utils.py` | 进程树终止（Windows taskkill /T，防孙进程残留） |
| `shared.py`（546 行） | cron 5 字段引擎（校验/匹配/错峰顺延）、峰谷定价判定、预算感知思考降档、本地路径正则、Windows OCR/Toast PowerShell 脚本、跨进程文件锁 `file_lock`、参数钳制 `clamp_*`、**工具域阈值与锁**（49 个工具阈值常量/锁统一归口，域模块 `from shared` 导入） |
| `themes.py` | 主题 token 定义（浅色/深色色板） |

### 配置体系

| 模块 | 职责 |
|---|---|
| `config_defaults.py` | 版本单一源（`VERSION`）、默认配置、系统提示词、任务质量指南、内置指令库（24 条模板）、更新源、默认快捷键 |
| `config_utils.py` | 配置加载/规范化（字段钳制、非法值回退、新工具自动合并）/DPAPI 加解密保存；`load_config()` 返回**进程级共享对象**（只读），「读—改—写」须用 `mutable_config()` 取深拷贝副本 |
| `profiles.py` | Profile 多账号配置读写（API Key DPAPI 加密） |
| `roles.py` | 内置角色预设（通用/智能体/翻译官/代码评审/面试官/润色/心理/周报） |
| `templates.py` | 任务模板与试玩任务库 |
| `deps.py` | 依赖清单（可选/自动安装/重型）+ 安装执行（清华源，状态供前端轮询） |

### 工具底座

| 模块 | 职责 |
|---|---|
| `net_utils.py` | 模块级共享 httpx 客户端、安全请求/流式请求（逐跳 SSRF 校验）、重定向拼接校验 |
| `search_utils.py` | 搜索解析（Bing/DDG/360 正则）、HTML 去标签、结果去重与安全过滤 |
| `db_utils.py` | 只读 SQL 校验（禁止关键字/分号注入）、UPDATE/DELETE 预览改写、表格格式化 |
| `pdf_utils.py` | PDF 页码范围解析、中文字体注册、Markdown→PDF 片段 |
| `mdparse.py` | Markdown 块/内联解析（纯函数，供渲染与工具复用） |
| `fetch_blocked.py` | 被墙站点抓取（代理节点发现 + Chrome TLS 指纹），**独立模块按需启用，分享可剔除** |

### 扩展体系

| 模块 | 职责 |
|---|---|
| `plugins.py` | 插件体系（.wtplugin v1/v2）：校验/安装/卸载/停用（来源标记精确移除、零残留）、评分、requires 自检 |
| `user_tools.py` | 用户自定义工具加载（mtime+size 缓存） |
| `mcp_server.py`（157 行） | MCP over stdio 出口：供外部 host（Claude/Cline 等）调用 WhaleTalk 工具，纯标准库最小合规子集 |

### 鲸语大脑

| 模块 | 职责 |
|---|---|
| `brainkit.py`（2,863 行） | 大脑 CLI：init/keyring-setup/mount/unmount/heartbeat/think/remember/import-memory/consolidate（睡眠巩固）/goal/decision/evolution/archive/restore/merge/merge-resolve/status/list/diff/export-key/import-key；指纹防篡改、DPAPI 免密密钥体系、RSA 快照签名验签、LCA 三路合并、prune 豁免血缘引用快照、语义检索 |
| `brain_api.py`（984 行） | 大脑 → API 适配层（把 CLI 命令包装为 api_server 可调用的纯函数 + `brain_context` 注入——身份/断点/目标/自我认知/未决决策/记忆 + `consolidate_with_llm` LLM 提炼） |

### 子包

| 模块 | 职责 |
|---|---|
| `agent_tools/`（13 模块 · 159 工具） | 运行时工具域模块包（P0-1 巨石拆分完成，主文件 13,115 → 5,100 行）：<br>`tool_docs.py`（📊 数据与文档 · 30：Excel/SQLite/MySQL/PostgreSQL/PDF/Word/PPT/EPUB/MOBI/旧 doc/msg/压缩包 + HTML→PNG/PPT/PDF + 图表/设计工具）<br>`tool_files.py`（📁 文件与进程 · 21：read/write/edit/list/search_local/find_images/asset_*/clipboard/delete/archive/snapshot/batch_rename/start|stop|list_processes/environment_info）<br>`tool_desktop.py`（🖱 桌面与视觉语音 · 18：rpa_*/screen_find_click/vision_loop/tts*/speech_to_text/voice_chat_loop/image_generate/qrcode/media_ffmpeg/team_run）<br>`tool_system.py`（🔧 系统与项目 · 15：watch_files/recall_session/project_*/create_evolution/self_evolve/verify_files/git/notify_desktop/app_manage/usage_report/capability_heatmap/self_report/create_plugin/list_my_capabilities）<br>`tool_brain.py`（🧠 记忆与知识 · 15：write/read/delete/update_memory/self_profile/query_memory_graph/knowledge_*/schedule_task/task_checkpoint/run_workflow/failure_memory）<br>`tool_code.py`（💻 编程与执行 · 15：run_python/run_command/run_lint/run_tests/verify_project/project_scaffold/dev_plan/get_status/project_map/find_symbol/code_lookup/write_code_project/pip_install/subagent_run/verify_output）<br>`tool_web.py`（🌐 浏览器与网页 · 14：fetch_url/download_file/search_web/search_github/search_realtime/browser_navigate/web_screenshot/net_diagnose/fetch_url_smart/rss_fetch/webdav/call_api/track_web/fetch_blocked〔实现名 `_run_fetch_blocked`〕）<br>`tool_media.py`（🎨 媒体与图像 · 10：image_process/ocr_image/image_understand/screen_capture/screen_see/chart_read/screenshot_to_html/debug_screenshot/scan_read/image_batch）<br>`tool_msg.py`（📧 邮件与消息 · 10：send_email/publish_draft/send_webhook/im_send/telegram_poll_updates/read_email/email_summary/agent_mail/run_wechat_writer/daily_brief）<br>`tool_codegen.py`（🎨 代码生图 · 6：image_codegen/image_inpaint/control_map/sprite_sheet/make_gif/image_hybrid）<br>`tool_basic.py`（🔧 2：get_date/get_weather）· `tool_data.py`（📊 2：read_csv/write_csv）· `tool_mv.py`（🎬 1：mv_compose）<br>另主模块经 `register_tool()` 注册 2 个特殊工具（`ask_user`/`request_permission`）→ **162 个 Agent 工具** |
| `webui/` | React 前端（React 19 + Vite 8，无 UI 框架）：ChatPage/工作台/指令库/自主/大脑/插件/设置；`webui/dist` 由 api_server 同源服务。渲染链路（纯数据 AST）：`longTextUtil.js`（解除 `@long-text` 包装）→ `mdParser.js`（块级 AST，流式安全 `code-open`）→ `mdInline.js`（行内 tokens，独立 RegExp 防 lastIndex 破坏）→ `mdHighlight.js`（零依赖高亮）→ `mdMath.js`（LaTeX 子集）→ `components/Markdown.jsx`（消费 AST）。**长会话渲染**：`msgUpdates.js` 提供不可变更新，配合 `Message.jsx` 的 `React.memo` 实现每帧只重渲染最后一条 |
| `wechat_writer/` | 公众号自动写作：sources（多信源采集）/ topic（选题去重）/ writer（三阶段写作）/ quality（质检重试）/ output（草稿箱+存档）/ history / llm / config |
| `tools/` | 开发门禁：`audit_tools.py`（六层一致性，`--strict` 可入 CI）、`validate_tools.py`（smart_tools 全链路）、`island_check.py`（十层孤岛对账）、`check_docs.py`（文档数字 vs 源码实测） |
| `tests/` | 自举回归套件（61 个 pytest 文件 / 671 用例）：注册表六层一致性、域模块拆分 re-export、进化闸、失败生命周期、上下文装配、退化日志、出网账本、记忆门面、信任内核、大脑、安全/权限/网络等；`self_evolve` 验证链自动回退跑全量 |

### 辅助脚本

| 文件 | 职责 |
|---|---|
| `bootstrap.py` | 跨平台引导器（替代原 start.bat/build_exe.bat）：建 `.venv` → 逐包安装核心依赖（死代理自动绕过、单包失败不阻断）→ 启动 `web_app.py`；`bootstrap.py check` 体检、`bootstrap.py build` 打包 |
| `backup.py` / `backup.bat` | 大版本更新前源码快照（`backups/WhaleTalk_v<版本>_<时间戳>.zip`） |
| `WhaleTalk.spec` | PyInstaller 打包配置（`python bootstrap.py build` 调用；前端内置，大型可选依赖排除） |

## 数据目录

运行数据位于 `C:\Users\<用户>\Documents\WhaleTalk\`：

| 路径 | 内容 |
|---|---|
| `config.json` | 配置（api_key 等敏感字段 DPAPI 加密） |
| `history/sessions/` | 会话文件（JSON）+ `sessions_index.json` 索引缓存 |
| `memory.json` | 长期记忆 facts（旧格式，v3.7 起自动兼容读取） |
| `memories/` | 见下方「鲸语大脑」数据说明（大脑目录位于源码同级 `brain/`） |
| `stats.json` | 用量统计 |
| `workspace/` | AI 产物工作目录 |
| `failures.json` / `failures_archive.json` / `patterns.json` | 失败模式库 / 归档 / 成功模式库 |
| `schedules.json` / `workflows.json` / `checkpoint.json` | 定时任务 / 流程 / 任务检查点 |
| `profiles.json` / `user_tools.json` / `prompts.json` | Profile / 自定义工具 / 指令库 |
| `undo/` | 写操作快照（snapshot.py，200 条轮转） |
| `archives/` | 上下文压缩归档 |
| `egress.jsonl` / `degradations.json` | 出网账本 / 退化日志（append-only） |
| `logs/` | 审计日志 actions.log 等 |

> 信任内核数据（源码同级 `trust/`，已入 `.gitignore`，不入库）：`baseline/` 可信副本、
> `manifest.json` 指纹索引、`ledger.jsonl` 声明式账本、`incidents/` 未声明改动事件、
> `history/` 已声明改动历史、`quarantine/`（guard 模式）、`last_check.json`、`STATUS.md`。
> 首次运行自动 bootstrap（以当时代码为可信基线），故新克隆无需携带。

> 鲸语大脑数据（源码同级 `brain/`）：`manifest.json`（指纹/状态）、`memories/memory.jsonl`（结构化记忆库）、`thinking_log/`（思考日志）、`archive/`（快照 `brain_v{n}.whale`）、`.keys/`（密钥，DPAPI 免密）、`.lineage.json`（血缘）、`self_model.json`（动态自我模型）、`goals.json`（目标）、`decisions.jsonl`（决策日志）；`.brain_active` 持久化当前大脑，多大脑分支见 `brain-dirs`。

## 演进建议

### 已完成（保留记录，勿重复立项）

- ~~**工具声明单一源**~~ ✅ `@tool()` 装饰器 + `register_tool()` 已统一 schema/实现/分组/动作短语/预激活关键字（六层由注册表生成，`audit_tools.py` 转为兜底门禁）。
- ~~**P0-1 工具执行管线**~~ ✅ `tool_hooks.py`：横切关注点在 **`toolkit` 注册处**统一包装执行体，任何调用路径都逃不掉钩子；信任声明与快照已从逐个工具手接改为钩子/声明。
- ~~**P0-2 上下文装配 Provider 化**~~ ✅ `context_providers.py`：9 个来源变成 Provider 表 + 预算 + 回执，失败走 degrade 而非静默。
- ~~**P0-3 退化日志**~~ ✅ `degrade.py`：静默降级的统一出口，critical 分级 + 自我状态提示。
- ~~**P1-A 出网账本**~~ ✅ `egress.py`：入网有 SSRF 底线、出网亦可审计——每次带内容出网留痕，默认不存明文；达阈值向模型注入「出网留痕」。经 `tool_hooks` 一个钩子 + 7 处声明接入，**工具体零改动**。
- ~~**P1-B 记忆单一门面**~~ ✅ `memory_facade.py`：长期记忆补 **origin（血缘）+ confidence + supersede 双向链接**；作废**只认显式 key，绝不按相似度**（实测依据见 [docs/出网账本与记忆门面.md](docs/出网账本与记忆门面.md)）。

### 待做（按杠杆排序）

1. **剩余横切关注点迁移进钩子（P0-1 第二步）**：`clamp_int` 参数钳制仍散在 6 个工具域模块 17 处，可改为 `@tool(clamp={...})` 声明 + pre 钩子；`batch_rename`/`database_execute` 的快照仍内联，可按需声明 `hooks=("snapshot",)`。**验收口径：工具函数体内不再出现横切调用。**
2. **记忆血缘的自动化**：`origin` 目前由写入方声明（`write_memory` 工具 + `_chat_harvest`）。可进一步在 `_wrap_external` 检出外部内容后、于**同轮**记忆写入时自动把 origin 判为 `web`。另可把 `similarity()` 换成向量语义信号以支持语义级冲突提示。
3. **显式 Runtime 对象**：工具域经 `import deepseek_client as _dc` 在调用时读可变全局，模块边界靠约定维持（`import agent_tools.tool_files` 直连会触发重复注册）。建议启动时构造一次 Runtime（路径/配置/锁），工具从 `runtime()` 取——测试可并行、无导入顺序陷阱。
4. **继续瘦身 `deepseek_client.py`**：主文件仍有 5,100 行，保留共享基建 + `_TOOL_ORDER` 等顺序常量 + 六层构建 + 薄 facade 即可，其余按领域继续收拢。配套能力已就绪（`toolkit.rebuild_layers` 多文件 AST、三门禁多文件扫描、`WhaleTalk.spec` 的 `collect_submodules`、`tests/test_tool_split.py`）。每拆一批跑 `tools/audit_tools.py --strict` + `tools/validate_tools.py` + `python -m pytest -q`。
5. **api_server 路由分层**：`api_server.py` 约 8,700 行 / 98 端点在单个 `_Handler` 内；`_match_routes` 已是路由表，可正式化为 `api/` 包（`test_api_routes.py` 护航）。
6. **版本与发布纪律**：把 CHANGELOG 里多批"未发版追加"收敛为真实 tag，并让 `check_docs.py` 校验 `VERSION` 与最新 CHANGELOG 标题一致——彻底消灭"构建与源码对不上"。
7. **前端 e2e**：`npm test` 覆盖解析器/渲染器/工具函数（14 个 .mjs），无组件交互与端到端；项目已依赖 Playwright，加一个冒烟（起服务 → 发一条 → 断言 SSE 与产物条）成本很低。
8. **测试资产**：按领域扩充分子级用例；`pytest tests/` 已接入 CI 与 `self_evolve` 验证链。

### 明确不做

- **不为"优雅"合并那 98 个端点**：CRUD 端点薄是特性不是缺陷。
- **不给 `write_file`/`run_python` 加拦截**：`run_python` 本就绕得过，工具层设卡只挡君子（详见 [docs/信任内核.md](docs/信任内核.md) 第 8 节）。
- **不合并 `snapshot.py` 与 `trust_kernel.py`**：两者生命周期语义不同（200 条轮转 vs 永不裁剪）。
