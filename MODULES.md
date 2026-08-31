# 模块地图（v3.8.2 Web 版）

本文档描述鲸语 WhaleTalk 当前（v3.8.2，Web 架构）的模块构成与职责边界，供维护、重构与新增功能时定位。与旧 Tkinter 版（main.py）相关的拆分记录已随 Web 重构归档，不再维护。v3.6/v3.7 未改变模块布局，仅扩展能力（详见更新记录）。

## 分层总览

```
web_app.py（唯一入口：浏览器 + 托盘 + 快捷方式 + 依赖自检）
    │
    ▼
api_server.py（本地 HTTP API：REST + SSE，88 /v1 端点）
    │
    ▼
deepseek_client.py（能力引擎：DeepSeekClient + 135 工具 + smart_tools）
    │
    ├─ 基础设施：permissions / security / crypto / stores / stats / tokens / persistence
    ├─ 工具底座：net_utils / search_utils / db_utils / pdf_utils / proc_utils / mdparse
    ├─ 配置体系：config_defaults / config_utils / profiles / themes / roles / templates / deps
    ├─ 扩展体系：plugins / user_tools / fetch_blocked（按需）
    ├─ 大脑：brainkit / brain_api
    └─ 子包：wechat_writer（公众号写作）/ webui（React 前端）/ tools（开发门禁）
```

## 模块清单

### 入口与 API 层

| 模块 | 职责 |
|---|---|
| `web_app.py` | 唯一启动入口：启动本地 API、自动打开浏览器、系统托盘常驻、桌面/开始菜单快捷方式、开机自启、单实例、WebUI 自动构建（npm）、Python 依赖自检与自动安装 |
| `api_server.py` | 本地 HTTP API（标准库 `ThreadingHTTPServer`，无 Flask）：会话/配置/上下文/工具/记忆/文件/进程/插件/指令库/工作台/大脑/TTS/审计/备份/更新等 88 端点；SSE 流式对话；审批/询问/白名单双向通道；后台调度器 + 进程看门狗 + Webhook 接收端 + IM 轮询 |

### 能力引擎

| 模块 | 职责 |
|---|---|
| `deepseek_client.py` | 单体能力引擎（约 1.3 万行）：DeepSeek V4 客户端（thinking/多模态/流式/重试）、135 个 Agent 工具实现、工具注册表（`TOOLS`/`TOOL_CALL_MAP`）、smart_tools 智能调取（能力地图 + `activate_tools` 点菜 + 关键词预激活）、上下文压缩辅助、自我进化工具（`create_evolution`/`self_evolve` 四层验证闸：语法编译→lint→导入冒烟→测试）、代码结构定位 `code_lookup`（AST）、对话记忆自动写/删/改与大脑双向同步、自动记忆提炼（`auto_memory`） |

> 演进建议：`deepseek_client.py` 已按「工具实现 → 注册表 → 客户端类」分层组织，但仍是单文件。可按领域拆为 `tools/` 包（web/data/doc/media/system），保留顶层薄 facade 做 re-export 兼容，用 `tools/audit_tools.py` 门禁护航。

### 基础设施（纯函数/低依赖，可独立复用）

| 模块 | 职责 |
|---|---|
| `permissions.py` | 权限模型 v2：blacklist（默认放行+黑名单）/ whitelist（可回退）/ FULL_AUTO 完全智能；路径/命令/网络判定；审计日志（只记不拦） |
| `security.py` | SSRF 防护：内网/回环/保留段判定、云元数据永远拦截、DNS 重绑定防护、信任白名单（CIDR/域后缀） |
| `crypto.py` | API Key DPAPI 加密（`dpapi:` 前缀 + base64），fail-closed（加密失败绝不写明文） |
| `stores.py` | 本地 JSON 存取：最近产物/成功模式/失败模式/任务日志/长期记忆/定时任务（统一原子写） |
| `stats.py` | 用量统计（按天×模型累计）+ 官方峰谷定价费用估算 |
| `tokens.py` | token 估算（tiktoken o200k_base，缺省回退 1.5 字符/token，对象身份缓存） |
| `persistence.py` | 原子 JSON 写入（mkstemp 唯一临时文件 + os.replace） |
| `snapshot.py` | 文件/数据库写操作自动快照（P2）：写/编辑/重命名/数据库写前备份原内容到 `DATA_DIR/undo/`，可列出/恢复（`list_snapshots`/`restore_snapshot` 工具）；上限 200 条自动清理 |
| `app_utils.py` | 布尔转换、空壳目录判断、清理、干净退出标记、隐私日志 |
| `proc_utils.py` | 进程树终止（Windows taskkill /T，防孙进程残留） |
| `shared.py` | cron 5 字段引擎（校验/匹配/错峰顺延）、峰谷定价判定、预算感知思考降档、本地路径正则、Windows OCR PowerShell 脚本 |
| `themes.py` | 主题 token 定义（浅色/深色色板） |
| `tokens.py` | 见上（token 估算） |

### 配置体系

| 模块 | 职责 |
|---|---|
| `config_defaults.py` | 版本单一源（`VERSION`）、默认配置、系统提示词、任务质量指南、内置指令库（24 条模板）、更新源、默认快捷键 |
| `config_utils.py` | 配置加载/规范化（字段钳制、非法值回退、新工具自动合并）/DPAPI 加解密保存 |
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

### 鲸语大脑

| 模块 | 职责 |
|---|---|
| `brainkit.py` | 大脑 CLI：init/keyring-setup/mount/unmount/heartbeat/think/remember/import-memory/consolidate（睡眠巩固）/goal（目标）/decision（决策日志）/archive/restore/merge/merge-resolve/status/list/diff/export-key/import-key；指纹防篡改、DPAPI 免密密钥体系、RSA 快照签名验签、LCA 三路合并、语义检索（IDF 加权余弦 + 中文 bigram 分词） |
| `brain_api.py` | 大脑 → API 适配层（把 CLI 命令包装为 api_server 可调用的纯函数 + 大脑上下文注入 + `consolidate_with_llm` LLM 提炼） |

### 子包

| 模块 | 职责 |
|---|---|
| `wechat_writer/` | 公众号自动写作：sources（多信源采集）/ topic（选题去重）/ writer（三阶段写作）/ quality（质检重试）/ output（草稿箱+存档）/ history / llm / config |
| `webui/` | React 前端（React 19 + Vite 8，无 UI 框架）：ChatPage/工作台/指令库/自主/大脑/插件/设置；`webui/dist` 由 api_server 同源服务。渲染链路（v3.8.0 世界级渲染器）：`longTextUtil.js`（`unwrapLongText`，解除模型 `@long-text:`/`<long_text_quote>` 包装）→ `mdParser.js`（块级 AST：标题/段落/嵌套列表缩进树/任务框/嵌套引用/表格对齐/代码围栏/数学块/details 折叠/脚注定义/分隔线，流式安全的未闭合围栏 `code-open`）→ `mdInline.js`（行内 tokens：粗体/斜体/粗斜体/删除线/高亮/上下标/行内 code/行内公式/图片/链接/自动链接/脚注引用/转义，任意嵌套，独立 RegExp 实例防 lastIndex 破坏）→ `mdHighlight.js`（零依赖语法高亮：js/ts/jsx/py/bash/json/sql/html/css/c/cpp/java/go/rust/yaml 等，输出整体转义）→ `components/Markdown.jsx`（消费 AST 渲染 React DOM，流式 deferCode 机制）；对应单测 `tests/longTextUtil.test.mjs` + `tests/markdownRender.test.mjs`（vite 8 兼容 SSR 真实渲染回归，经 `tests/ssrRender.mjs` 打包管线） |
| `tools/` | 开发门禁：`audit_tools.py`（工具系统六层一致性审计，`--strict` 可入 CI）、`validate_tools.py`（smart_tools 全链路回归）、`island_check.py`（九层孤岛对账） |
| `tests/` | 自举回归套件（v3.7.2 起）：`test_registry.py`（工具注册表六层一致性断言）、`test_evolve_guard.py`（进化闸函数与回滚语义）、`test_code_lookup.py`；`self_evolve` 验证链自动回退跑全量，进化不得破坏回归 |

### 辅助脚本

| 文件 | 职责 |
|---|---|
| `backup.py` / `backup.bat` | 大版本更新前源码快照（`backups/WhaleTalk_v<版本>_<时间戳>.zip`） |
| `build_exe.bat` / `WhaleTalk.spec` | PyInstaller 打包 `dist\WhaleTalk.exe`（前端内置，大型可选依赖排除） |
| `start.bat` | 首次运行创建 .venv 并安装依赖后无窗启动 |

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
| `failures.json` / `patterns.json` | 失败模式库 / 成功模式库 |
| `schedules.json` / `workflows.json` / `checkpoint.json` | 定时任务 / 流程 / 任务检查点 |
| `profiles.json` / `user_tools.json` / `prompts.json` | Profile / 自定义工具 / 指令库 |
| `archives/` | 上下文压缩归档 |
| `logs/` | 审计日志 actions.log 等 |

> 鲸语大脑数据（源码同级 `brain/`）：`manifest.json`（指纹/状态）、`memories/memory.jsonl`（v3.7 结构化记忆库）、`thinking_log/`（思考日志）、`archive/`（快照 `brain_v{n}.whale`）、`.keys/`（密钥，DPAPI 免密）、`.lineage.json`（血缘）、`self_model.json`（动态自我模型）、`goals.json`（目标）、`decisions.jsonl`（决策日志）；`.brain_active` 持久化当前大脑，多大脑分支见 `brain-dirs`。

## 演进建议

1. **拆分 `deepseek_client.py`**：按领域拆为 `tools/` 包，顶层保留薄 facade（`TOOLS`/`TOOL_CALL_MAP` 等引用不变），每拆一批跑 `tools/audit_tools.py --strict` + `tools/validate_tools.py`。
2. **工具声明单一源**：引入 `@tool()` 装饰器统一声明 schema/实现/分组/动作短语/预激活关键字/审批级别，消除六层手工维护漂移（`audit_tools.py` 降级为兜底）。
3. **测试资产**：`tests/` 自举回归套件已建立（v3.7.2，28 用例：注册表一致性/进化闸/代码定位）；建议按领域扩充分子级 pytest 用例（工具/权限/存储），并将 `pytest tests/` 接入 CI 与 `self_evolve` 验证链（后者已自动回退全量）。
4. 保持"先纯函数/工具模块，再业务模块"的顺序。
