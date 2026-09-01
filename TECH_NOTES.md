# 鲸语 WhaleTalk 技术文档（Web 版 · v3.8.3）

本文档面向后续维护/开发的 AI 智能体，描述 Web 架构（v3.0+）下的系统结构、数据流、核心约定与踩坑记录。符号名为准，行号随代码演化漂移，本文档不承诺行号。

## 0. 品牌与版本

- 品牌：鲸语 WhaleTalk（独立产品，与 DeepSeek 官方无关联）。对外展示一律使用品牌名，技术描述可写"基于 DeepSeek API"。
- **版本单一源**：`config_defaults.VERSION`（当前 3.8.3）。备份产物 `WhaleTalk_v{version}_*.zip`；打包产物 `WhaleTalk.exe`。README/SECURITY 的版本表述须与该常量一致。
- 入口形态：**纯 Web + 托盘常驻**。浏览器是唯一界面；无 pywebview 原生窗口（desktop.py 已废弃）。

## 1. 项目概览

Windows 本地 AI 桌面智能体，深度适配 DeepSeek V4 API。核心能力：thinking 思考模式、135 项 Agent 工具（smart_tools 按需调取）、多模态视觉、百万 token 长上下文自动压缩、自我进化（提案分支 + git 分支实施）、鲸语大脑（跨会话灵魂）、插件体系（.wtplugin v2）、公众号自动写作。

- 运行时：Python 3.9+（开发 3.12），核心依赖仅 `openai` / `httpx`，其余全部可选（缺失自动降级提示）
- API 层：标准库 `http.server.ThreadingHTTPServer`（**无 Flask/无框架**）
- 前端：React 19 + Vite 8（`webui/`），无 UI 框架，纯 CSS 变量主题

## 2. 目录与模块职责

```
WhaleTalk/
├── web_app.py              # 唯一入口：API + 浏览器 + 托盘 + 快捷方式 + 依赖自检
├── api_server.py           # 本地 HTTP API（REST + SSE，79+ /v1 端点）
├── deepseek_client.py      # 能力引擎：DeepSeekClient + 135 工具 + smart_tools（约 1.31 万行；P0-1 拆分中，首批+第二批已迁出 14 工具，累计 −775 行）
├── agent_tools/            # 工具域模块包（P0-1 拆分）：tool_basic / tool_data / tool_media，@tool() 注册 + __all__ re-export
├── permissions.py          # 权限模型 v2（blacklist 默认放行 / whitelist 回退 / FULL_AUTO）
├── security.py             # SSRF 防护（云元数据永远拦截）
├── crypto.py               # API Key DPAPI 加密（fail-closed）
├── stores.py / stats.py / tokens.py / persistence.py   # 存储/统计/token 估算/原子写
├── shared.py               # cron 引擎、峰谷定价、OCR 脚本
├── config_defaults.py / config_utils.py / profiles.py  # 配置体系
├── roles.py / templates.py / themes.py / deps.py        # 角色/模板/主题/依赖
├── plugins.py / user_tools.py                           # 插件体系 / 自定义工具
├── brainkit.py / brain_api.py                           # 鲸语大脑 CLI + API 适配
├── net_utils.py / search_utils.py / db_utils.py / pdf_utils.py / mdparse.py  # 工具底座
├── fetch_blocked.py        # 被墙站点抓取（独立模块，按需启用，可剔除）
├── proc_utils.py / app_utils.py / backup.py            # 进程/应用工具/备份
├── wechat_writer/          # 公众号自动写作（采集→选题→写作→质检→草稿）
├── webui/                  # React 前端（dist/ 由 api_server 同源服务）
├── tools/                  # audit_tools.py（六层一致性审计）+ validate_tools.py（回归门禁）
├── sample_plugins/         # 10 个示例 .wtplugin 插件
└── WhaleTalk.spec / build_exe.bat / start.bat / backup.bat
```

完整模块职责见 [MODULES.md](MODULES.md)。

## 3. 启动流程（web_app.py）

1. `_harden_stdio()`：stdout/stderr 非 UTF 编码改 replace（防 GBK 管道 UnicodeEncodeError 崩溃）
2. 参数解析：`--server`（无头 API）/ `--no-browser` / `--no-tray` / `--no-shortcuts` / `--no-webui-build` / `--install-shortcuts` / `--port` / `--no-deps-check`
3. `_ensure_python_deps()`：硬依赖（openai/httpx）缺失 → GUI 弹初始化进度窗同步安装（清华源）；软核心后台静默安装（前端轮询 `/v1/deps` 显示进度）；失败明确报错退出
4. 快捷方式：首次运行自动创建 桌面 + 开始菜单 `.lnk`（PowerShell WScript.Shell；清理旧版 `WhaleTalk.exe.lnk`）
5. `_ensure_webui_build()`：`webui/dist` 缺失或源码更新 → 自动 `npm ci/install && npm run build`（打包 exe 模式跳过）
6. `_serve_forever()`：单实例探测（`_probe_existing` 访问 `/v1/token`，已有实例只开浏览器）→ `api_server.start_server()` → 开浏览器（`silent_start` 时不弹）→ 托盘常驻（pystray）

## 4. API 端点地图（api_server.py）

认证：`Authorization: Bearer <token>`（HMAC 常量时间比较；token 来自 config.json 的 `inbound_token`，缺失则启动自动生成）。CORS 仅回显白名单 Origin（`_CORS_ALLOWED_ORIGINS`：127.0.0.1/localhost 的 8745/5173/5174）。请求体上限 1MB（`/v1/upload` 64MB）。

**GET**：health / v1/token / v1/sessions / v1/models / v1/deps / v1/update/check / v1/backup / v1/workflows / v1/checkpoint / v1/tasklog / v1/knowledge / v1/profiles / v1/audit / v1/approvals / v1/evolve_branches / v1/self_profile / v1/failures / v1/schedules / v1/services / v1/permissions / v1/prompts(+export) / v1/plugin_skills / v1/dir / v1/roles / v1/tools/<name> / v1/processes / v1/files / v1/tasks / v1/evolutions(+/<name>) / v1/status / v1/brain / v1/situation / v1/mode / v1/abilities / v1/memory / v1/plugins(+/<name>) / v1/context / v1/config(+reset) / v1/tts/audio/<fn> / v1/tts/voices / v1/sessions/<id>/messages

**POST**：v1/chat / v1/chat/stream（SSE）/ v1/brain / v1/deps/install（NDJSON 流式进度）/ v1/tools/<name>/invoke / v1/fim / v1/cleanup / v1/backup / v1/workflows / v1/checkpoint / v1/knowledge/search / v1/roles / v1/schedules / v1/services / v1/evolutions/apply|ignore / v1/evolve_branches/detail|merge|delete / v1/search / v1/plugin_studio/generate|install / v1/plugins / v1/plugin_market/install（P2：市场下载+校验安装）/ v1/profiles / v1/tts/synthesize / v1/mode / v1/respond / v1/config / v1/prompts(save/delete/reorder/import/use/restore_builtin) / v1/sessions(delete_batch/delete/pin/rename/tags) / v1/permissions / v1/dir / v1/files(open/opendir/read/preview) / v1/processes(stop/start) / v1/upload

**GET 补充**：v1/plugin_market（P2：远程市场索引 + 质量分级 tier + 已安装状态）

**兼容**：`/api/v1/...` 与 `/v1/...` 等价（Vite 代理）。

## 5. SSE 事件协议（POST /v1/chat/stream）

chunked 编码，帧格式 `data: {json}\n\n`。事件类型：

| 事件 | 载荷 | 说明 |
|---|---|---|
| `reasoning` | `{text}` | 思考增量 |
| `content` | `{text}` | 正文增量 |
| `tool_start` | `{name, args}` | 工具开始 |
| `tool` | `{name, args, result}` | 工具完成（同时写 tasklog/审计） |
| `tool_duration` | `{name, duration}` | 工具耗时 |
| `usage` | `{prompt, completion, cache_hit, cache_miss}` | 用量（同时累计统计） |
| `compressed` | `{removed_turns, mode, archived_path}` | 上下文已压缩 |
| `error` | `{message}` | 错误 |
| `done` | `{}` | 正常结束（此前自动落盘会话） |

**审批/询问/白名单双向通道**：chat worker 需要用户交互时，通过 `_PENDING`（rid → Event + box）挂起并发送 SSE 事件（`ask`/`approval`/`permission`）；前端 `POST /v1/respond` 回传答案，`box` 填充后 `ev.set()` 唤醒。`_make_approval_cb`/`_make_ask_cb`/`_make_permission_cb` 在 api_server 组装。

## 6. 对话全链路

```
前端 Composer → POST /v1/chat/stream
  → _valid_messages（角色/长度/条数校验）
  → _sync_full_auto（同步权限 FULL_AUTO）
  → _client_from_cfg（Profile/模型/Key 解析）
  → _chat_kwargs（mode=task/dialog 决定 tools/pure_chat；thinking/温度/seed 透传）
  → _inject_system_messages（见 §7）
  → _compress_messages（见 §8）
  → DeepSeekClient.chat()（见 §9）
  → 结束：后端自动落盘会话（前端断连兜底）+ _record_tasklog + _notify_completed
```

## 7. 系统消息注入（_inject_system_messages）

按序拼装 `parts`，最后 join 为 `memory_text`（作为独立 system 消息追加在末尾，保持前缀稳定命中缓存）：

1. 系统提示词（`system_prompt`，pure_chat 用 `DIALOG_SYSTEM_PROMPT`）
2. 任务质量指南（`TASK_QUALITY_GUIDE`：计划先行/自检闭环/产物核验/态势感知/元认知/进化工具约束）
3. 长期记忆（`memory_enabled` 开启时，facts 最近 6 条）
4. 核心自我状态（`self_profile`，有实质内容才注入）
5. 鲸语大脑上下文（`brain_context`：身份 + 断点 + 近期记忆）
6. 当前工作目录（active_dir）
7. 失败模式（最近 3 条）+ 成功模式（最近 3 条）
8. 已装插件提示 + 项目任务记录（最近 3 条）

**缓存友好原则**：恒定内容（提示词/指南）在前，易变内容（记忆）在尾——官方硬盘缓存按前缀完整匹配命中。

## 8. 上下文压缩（_compress_messages）

- 触发：token > `max_context_tokens`（默认 40 万）或字符 > `max_context_chars`（默认 50 万）
- 按 user 消息切轮次，保留 `min_kept_turns`（默认 8）轮；被裁内容写 `archives/session_*.md`（隐私模式不写）
- LLM 摘要（thinking=none、max_tokens=1024）→ 摘要作为 system 消息插在最早保留消息之前；摘要失败回退硬裁剪
- **压缩后二次校验**（v3.5 T2）：仍超限 → 丢最老保留消息（保留首个 system）→ 截断超长单条（>6000 字符）→ 终极兜底只留最近 8 轮并截断
- token 估算：tiktoken o200k_base（缺省回退 1.5 字符/token），按消息对象身份 LRU 缓存（2048），压缩后 `clear_cache()` 防大消息滞留内存

## 9. Agent 工具循环（DeepSeekClient.chat）

核心方法：`chat(messages, scenario, thinking, tools_enabled, on_reasoning/on_content/on_tool/..., stop_event, ...)`。

**关键约定**：
- `_sanitize_messages`：过滤空 content assistant、**悬空 tool_calls**、**孤立 tool 消息**（DeepSeek API 以 400 拒绝，最高频踩坑点）；历史保存/压缩/中断都可能产生
- 图片内联：会话带图而当前模型非视觉 → 本次请求自动切 `VISION_MODEL`；注入「图片须知」避免模型找路径
- thinking 档位：none/low/medium/high/max/auto；`none` 走 temperature/top_p，其余走 `reasoning_effort`；auto 按消息复杂度估算（简单任务自动关思考）
- 工具轮上限 `MAX_TOOL_ROUNDS`=100；空响应重试 1 次；同参数连续调用 3 次触发循环防护；计划连续拒绝 3 次终止；工具总超时 300s；停止后 1.5s 宽限期收已提交工具真实结果（防副作用重复执行）
- 工具并行：普通池 4 worker + 长任务池 2 worker（`_LONG_TOOL_NAMES` 36 个长工具走独立池）；交互工具（ask_user/request_permission）串行
- 工具结果 >4 万字符自动落盘（`_persist_long_result`），上下文只留路径 + 摘要
- `strict_tools`：工具 schema 严格模式（`_strictify_tools`）
- 视觉自审：`vision_self_review` 开启时图片产出工具自动调视觉模型审图，意见附回结果

**FIM 补全**：`fim_complete` 走 `/beta` 端点（按 base_url 缓存 client）。

## 10. smart_tools 智能调取（成本核心）

完全智能模式不再全量注入工具 schema（约 15k token），改为：
1. 常驻注入「能力地图」（`build_tool_index`：11 组分类 + 工具名 + 核心动作短语）
2. `activate_tools` 点菜工具（支持**按组激活**，`_TOOL_GROUP_NAME_MAP`）
3. chat 层关键词预激活（`_PREACTIVATE_HINTS` 25 组意图词，扫描最近一条 user 消息）
4. 激活后下一轮注入**压缩版 schema**（`compact_tools_list`：剥冗余括号、参数描述截断 40 字符）

**六层数据必须一致**（tools/audit_tools.py 审计）：`TOOLS` schema ↔ 函数签名 ↔ `TOOL_CALL_MAP` ↔ `_TOOL_ACTION_PHRASES` ↔ `TOOL_GROUPS` ↔ `_PREACTIVATE_HINTS` + `permissions.ACTION_TOOLS`。演进目标：`@tool()` 装饰器统一声明。

## 11. 权限与安全

**权限模型 v2**（permissions.py）：
- `security_mode = "blacklist"`（默认）：AI 默认拥有全部行动能力，黑名单明确禁止
- `whitelist`（旧模式可回退）：默认拒绝，白名单放行（dir/command/write）
- `FULL_AUTO` 完全智能：零审批零开关，黑名单仍生效
- 审计日志只记录不拦截（隐私模式关闭）；路径 `resolve()` 规范化防穿越；网络黑名单支持 IP/CIDR/`*.domain`

**安全纵深**：
- API Key DPAPI 加密（`crypto.py`）：加密失败 fail-closed（磁盘保留原密文，绝不写明文）
- SSRF（`security.py`）：内网/回环/保留段默认阻止（回环可配放行用于本地开发验证）；**云元数据 169.254.0.0/16 永远拦截（白名单不可豁免）**；DNS 重绑定防护（任一解析落内网即拦截）；搜索链接过滤保持严格（外部来源是注入源，回环不放行）
- CORS 白名单 + Bearer token + 仅 127.0.0.1 监听 + 请求体上限
- 沙箱 Python：AST 静态检查 + `-I -S` 隔离执行
- 进程：`kill_tree`（taskkill /T）防孙进程残留；服务停止清理全部子进程
- 文件：zip 炸弹防护、上传/下载限额、可执行文件拒绝直接打开

## 12. 数据与存储

- 数据目录：`C:\Users\<用户>\Documents\WhaleTalk\`（详见 MODULES.md）
- 原子写：`persistence.atomic_json_write`（mkstemp 唯一临时文件 + os.replace）贯穿全部 JSON 落盘
- 会话索引：`sessions_index.json` 持元数据 + 文件指纹（mtime_ns/size），列表接口毫秒级返回；写入路径钩子 `_index_session_locked` 同步更新
- 会话后端兜底落盘：`_handle_chat_stream` 结束后 `_sanitize_messages` 清洗再保存（防坏状态落盘）

## 13. 鲸语大脑（brainkit.py / brain_api.py）

- 数据：`brain/`（manifest 指纹 / identity / memories/ / self_model / thinking_log/ / evolution / heartbeat / archive/ / .keys/ / lineage / merge_log）
- 指纹防篡改：manifest 除 fingerprint 外 canonical JSON SHA-256；内容变化重算、brain_id 不变
- 免密密钥：MK 加密内容，MK 三重包裹（DPAPI 本地 / 口令 fallback / RSA-OAEP 跨躯体）；`export-key`/`import-key` 迁移仪式
- 分支合并：快照带血缘，`merge` 找 LCA 三路合并（日志行级并集、JSON 字段级、冲突逐条裁决）；`merge-resolve --keep ours|theirs|both|custom`
- 每日 22:00 由 api_server 调度器自动快照
- AI 对话自动注入大脑上下文（身份 + 断点 + 近期记忆）

## 14. 插件体系（.wtplugin v2）

- 五种形态：HTTP 工具 / 技能提示词 / 流程 / 场景 / **本地 Python 应用**（自带代码，`/触发词` 调用）
- 安装 = 写插件文件 + 合并进数据文件（条目带 `_source: plugin:<slug>` 标记）；卸载 = 精确移除本插件条目 + 删除代码目录（零残留）；停用 = 移除条目保留文件
- `requires` 依赖自检；权限声明 `permissions`（tools/files/net）
- 插件工坊：`/v1/plugin_studio/generate`（AI 生成）+ `/v1/plugin_studio/install`
- 在线市场：GitHub 索引 + SHA-256 校验安装；本地评分 ratings.json

## 15. 公众号写作（wechat_writer/）

主流程 `run_once`：采集（RSS + 搜索 + 论坛多信源，`enabled_groups` 控制）→ 选题（历史去重；用户指定主题跳过查重）→ 写作（大纲→正文→润色三阶段，LLM thinking 显式关闭）→ 质检（字数/事实/查重/标题门禁 + 重试）→ 草稿箱 + 存档 + 记历史。**任何关键步骤失败不写草稿、不记历史**（失败降级原则）。`dry_run` 默认安全。

## 16. 前端约定（webui/）

- 主题：CSS 变量（`data-theme` 属性 + localStorage `whaletalk.theme`）；星空/深海/北极三套
- token 获取链：localStorage → URL `?token=` → `/v1/token` 自取；后端不可用明确报错（**无假数据兜底**）
- 断连感知：`watchBackend` 5s 心跳探测，状态翻转回调；BackendBanner + 手动重连
- 消息链构造 `buildMessageChain`：tools 模式必须完整回传 assistant(reasoning_content + tool_calls) → tool 结果（官方规范）
- TTS：`ttsUtil.js`（合成 + 朗读 + barge-in 说话即打断，权限门控默认关闭）

### 16.1 Markdown 渲染管线（v3.8.0 世界级渲染器）

纯数据 AST 三层管线，零依赖、流式安全、所见即所得：

```
text → longTextUtil.unwrapLongText（解除 @long-text 包装）
     → mdParser.parseMarkdown → { blocks }            （块级 AST）
     → Markdown.jsx <Block> 分发                      （嵌套 ul/ol、任务框、引用、details、表格、代码、数学、脚注、分隔线）
     → mdInline.parseInline → tokens → <Tokens>       （行内任意嵌套）
     → mdHighlight.highlight（dangerouslySetInnerHTML，整体转义）
```

关键约定：
- **AST 与渲染分离**：三个解析模块是纯 JS（node 可直跑单测），组件只做消费
- **流式安全**：未闭合代码围栏产出 `{t:'code-open'}`；组件 `deferCode=true` 时跳过，流结束由父组件补渲染；其余未闭合标记原样输出
- **安全**：所有文本先 token 化再渲染；代码高亮输出整体转义；`safeUrl` 白名单拒绝 `javascript:`；`<script>` 注入经 SSR 断言锁定
- **防 OOM 铁律**：`parseInline` 递归每层必须 `new RegExp(TOKEN_RE_SRC, "g")` 独立实例——共享全局正则会被内层 exec 破坏 `lastIndex` 导致死循环（历史 OOM 根因）；递归深度上限 5
- **SSR 测试基建（vite 8 特有）**：vite 8 的 `ssrLoadModule` 会经 module-runner 内联求值 CJS 依赖（react），导致双实例 `Invalid hook call`。正解：`tests/ssrRender.mjs` 用 vite **ssr build**（`build.ssr=true` + `write:false` + `rollupOptions.external` 声明 react/react-dom/server）打包组件为 ESM bundle 落盘项目内（必须位于项目内，否则 node 无法向上解析 react），再原生 `import()`——node 原生 import 与 CJS `require` 共享同一 react 单例（react 是 CJS 包）

## 17. 工程实践

- **CI**（.github/workflows/ci.yml）：`check`（ruff 关键规则 E9/F63/F7/F82 + 入口 py_compile）· `test-backend`（`pytest tests/`，28 用例，依赖 `requirements-dev.txt` 锁 pytest 版本）· `webui`（npm ci + build + `npm test` 三个 node 套件）· 门禁 job（`tools/audit_tools.py --strict` / `tools/validate_tools.py` / `tools/island_check.py` / `tools/check_docs.py`）。pytest 的 `addopts=-p no:asyncio` 在 `pyproject.toml` 固化，本地与 CI 行为一致
- **本地门禁**：`tools/audit_tools.py`（六层一致性，error 级 `--strict` 返回非 0；warn 级仅提示）· `tools/validate_tools.py`（smart_tools 全链路：能力地图/compact/schema 可序列化/描述 ≤130 字/数组参数带 items）· `tools/island_check.py`（九层孤岛对账）· `tools/check_docs.py`（README/TECH_NOTES/MODULES 数字与源码一致，`--fix` 自动修正）
- **依赖**：`deps.py` 分层（硬依赖同步安装 / 自动安装后台 / 重型可选）；清华源镜像（`WHALETALK_PIP_MIRROR` 可覆盖）
- **打包**：`build_exe.bat` → PyInstaller（WhaleTalk.spec：webui/dist + sample_plugins 内置；playwright/faster-whisper/PyMuPDF 等大型依赖排除）
- **备份**：`backup.py` 源码快照（compresslevel=1；排除 .venv/dist/backups/.git 等）
- **更新**：GitHub Releases 检测；Ed25519 签名（`update_public_key`）+ SHA-256 校验；更新前自动备份可回滚

## 18. 踩坑记录（Web 版）

1. **DeepSeek API 400 三连**：空 content 的 assistant、悬空 tool_calls、孤立 tool 消息——`_sanitize_messages` 必须在前置清理；流中断/停止后必须补齐 tool 结果或移除半截 tool_calls
2. **array 参数缺 items → 400**：`_patch_array_items` 每次请求前递归兜底（含自定义/插件工具）
3. **JSON 输出非法**：官方有概率返回非 JSON，应用层解析失败自动重试一次（`json_retried`）
4. **流式中途断线**：已有增量送达 UI 时不整体重试（避免重复显示）；无增量时重试一次
5. **缓存命中**：SSE 流式必须显式 `stream_options.include_usage`；prompt 前缀稳定（system 恒前、可变记忆置尾）
6. **线程安全**：`stats.record_usage` 读-改-写全程持锁；`_TOOL_CHAIN_LOCK` 保护跨会话工具链；`ThreadPoolExecutor` 必须 daemon 化（CPython 3.9+ 无法事后设置，`_DaemonThreadPool` 复刻 `_adjust_thread_count`）
7. **CORS 攻击链**：恶意网页若拿到 CORS 头即可读本地 token 调本地 API——白名单必须只回显本机可信 Origin
8. **SSRF**：云元数据（169.254.169.254）永远不可豁免；搜索结果链接来自外部，是注入源（回环不放行）；DNS 重绑定需逐 IP 校验
9. **Windows 路径/编码**：GBK 管道下中文/emoji 日志会 UnicodeEncodeError → `_harden_stdio` replace 兜底；bat 用 `chcp 65001`；.ps1 快捷方式脚本写 `utf-8-sig`
10. **打包路径**：PyInstaller frozen 模式 `__file__` 指向 _MEIPASS 临时目录——配置/数据用 exe 所在目录（`_runtime_dir`），只读资源（webui/dist、sample_plugins）用原始模块目录（`_ORIG_DIR`）
11. **工具超时**：无内部超时的工具会卡死整轮 → `_TOOL_TOTAL_TIMEOUT` 300s 兜底，超时如实标记不等待
12. **停止语义**：停止后副作用已发生的工具（发信/写文件/启进程）要拿真实结果写回历史，写"已中断"会让模型下轮重试同参数造成重复执行

## 19. 演进建议

1. `deepseek_client.py` 按领域拆 `agent_tools/` 包（薄 facade re-export 兼容）——**进行中**：首批 get_date/get_weather/read_csv/write_csv 已迁（−187 行）；第二批「🎨 媒体与图像」10 工具迁入 `tool_media.py`（−588 行，视觉闭环辅助 `_capture_screen_png`/`_extract_image_path` 留主文件、域模块 from-import 复用）；`rebuild_layers` 多文件 AST、三门禁多文件扫描、spec `collect_submodules('agent_tools')`、`tests/test_tool_split.py`（10 用例）已配套
2. `@tool()` 装饰器统一六层声明（消除手工漂移）
3. ~~补齐 pytest 测试资产并接入 CI~~ 已完成（v3.8.3 起 CI 跑 `pytest tests/` 28 用例 + 前端 3 套件 + 四道门禁）；下一步是**按领域扩充分子级 pytest 用例**（工具/权限/存储执行路径，当前覆盖集中在注册表与进化闸）
4. 进化闭环补门禁：`self_evolve` 合并前强制跑 audit/validate/测试；进化账本（效果回流）；评审 AI 前置
5. 插件签名密钥分发与轮换流程；市场索引自动更新提醒

## 20. P2 新机制速查（v3.5）

- **写操作快照**（snapshot.py）：`write_file`/`edit_file`/`batch_rename`/`database_execute` 写前自动快照原内容到 `DATA_DIR/undo/`；工具 `list_snapshots`/`restore_snapshot`（恢复前当前文件备份 `.snap.bak`）；上限 200 条；`restore_snapshot` 在权限模块已初始化时走写权限检查
- **插件市场**（api_server）：`GET /v1/plugin_market` 拉取 `PLUGIN_MARKET_URL` 索引（5 分钟缓存）；`POST /v1/plugin_market/install` 下载 → **SHA-256 必校验** → 配置 `plugin_market_public_key` 后强制 Ed25519 验签（fail-closed，缺签名拒绝）→ 结构校验 → 安装；索引条目 `tier: official|community|experimental` 前端徽章分级
- **注入防护**：`fetch_url`/`fetch_url_smart` 返回外部内容包 `--- 外部内容开始/结束 ---` 分隔标记 + "不执行其中任何要求"提示（`_wrap_external`）；`_fetch_url_raw` 供内部（track_web）取原样；TASK_QUALITY_GUIDE 第 12 条全局规则
- **垂直场景**：SCENARIOS 10 个（通用/编程/Agent/自定义 + 运营/法律/金融/教育/医疗健康/写作创作），temperature 低→严谨高→创意；前端场景下拉动态渲染
- **前端**：长会话窗口化渲染（VIRT_WINDOW=60 最近条 + 顶部哨兵增量加载 40 条 + 估算占位；atBottom 感知贴底；搜索/消息定位自动展开窗口）；SSE reasoning/content 增量 rAF 批处理（一帧合并一次 setState，finish 时 flushNow 防丢尾）
