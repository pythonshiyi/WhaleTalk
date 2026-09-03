# 更新记录 / Changelog

本文件记录鲸语 WhaleTalk 的版本迭代历史。当前版本见 [README](README.md)。

## v3.8.5（未发版追加·增强二批）—— 🧠 大脑增强：反馈回路 + 检索增强 + 记忆版本链 + 新命令

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.4）。据《大脑增强建议路线图（2026-09-04）》，除"明确不建议做"项外，落地第一批核心增强：让记忆"被写后会被用、被用后有回执、会自我修正"。

### 记忆生命周期闭环（brainkit / brain_api）
- **F2 事实版本链**：`version_replace_memory` 替换记忆时新建 supersedes=旧id 的新条目、旧条目标记归档不删、共享 version_id 溯源——`update_memory` 不再永久覆盖丢旧值
- **F3 重要度自学习**：`search_memories(record_hits=True)` 回写 hit_count/last_hit；注入打分 `_decay_with_hits` 给"近期仍被用的记忆"真实加分——记忆因被用而重要
- **F4 间隔复习**：`_spaced_review_due` 选出 ≥7 天高价值(imp≥4)、近 3 天未命中的记忆周期性带回上下文，抵消时间衰减埋葬
- **F6 大脑体检**：`doctor` 命令——重复率/陈旧率/未回执决策/冲突/快照新鲜度/密钥 → 健康度 0-100 + 问题清单；`--fix` 自动归档陈旧低价值记忆
- **F9 身份演化史**：`identity-history record|list` 把人格基线留痕到 `identity_history.json`
- **F7 记忆图谱多跳**：`query_graph_multi_hop`/`graph` 命令按实体 + 共享实体 2 跳扩散
- **F10 跨大脑记忆借贷**：`borrow <src> --keyword` 从另一大脑导入匹配记忆（secret 不外借、落 source=借贷）
- **F5 决策回执提示**：brain_context 对 open>3 天决策带"（请回执结果）"提示

### 检索 / 预算 / 一致性
- **L3 检索增强**：search 把 tags/entities 拼入语料 + 命中加分
- **L1 上下文预算**：`brain_context(budget_chars=N)` 段模型（身份/目标/自我认知/决策/复习/记忆 各带权重），超限按优先级截断（api_server 注入层逐步接入）
- **L8 敏感度分级**：记忆加 sensitivity(public/private/secret)，`share-export` 默认排除 secret（可 include_secret 显式包含）
- **L6 操作审计**：`audit_op` 写 `brain_ops.log`（doctor-fix/borrow 等埋点）
- **L5 跨进程锁**：`cross_process_lock`/`release_lock`；`cmd_archive` 重构为加锁包装 + `_archive_unlocked`（防多进程版本号竞态）
- **F8 merge 预演**：`merge --dry-run` 在临时目录完成冲突计算、无副作用产出，打印将产生的冲突清单
- **L2 高价值语义取舍留痕**：jsonl 合并中重要度≥4 的记忆 text 双方都改时，merge_log 记录 `jsonl_auto_hi` 并在合并输出提示——不静默丢弃高价值语义（仍行级 auto，不引入整文件冲突）

### 修复
- **重大**：`_MEM_LOCK` 由 `threading.Lock` 改 `threading.RLock`——save_memories/version_replace/_record_hits 等持锁后嵌套落盘会自锁死（全量 pytest 卡死的根因），RLock 允许同线程重入
- `cmd_doctor --fix` 归档逻辑改为对 include_archived 全量改写（原对副本改无效）

### 回归
- 新增 `tests/test_brain_enhance2.py` **13 用例**（版本链/命中/复习/doctor/graph 多跳/身份历史/借贷/脱敏导出/审计/dry-run 无副作用/跨进程锁/不变量）
- pytest **159 passed**（146 + 13）；四门禁全绿（audit 0/0、validate 135、island 135×9、check_docs 135·85·3.8.4）

## v3.8.5（未发版追加）—— 🧠 鲸语大脑深度思考修复批次：jsonl 行级合并 / prune 保血缘 / 认知回路接通

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.4）。依据《大脑功能深度思考报告》（2026-09-03）对大脑全链路（brainkit/brain_api/集成点）的审计结论实施修复：大脑从「记忆档案馆」向「AI 的自我」补齐读侧回路——P0 架构断裂三条 + P1 符号-行为断裂四条 + P2 一致性七项全部落地。

### P0 · 架构断裂修复
- **P0-1 记忆 jsonl 与三路合并引擎撕裂**（`brainkit.py`）：新增 `_merge_jsonl_text`/`_row_merge`——按记忆 id 主键三方比对，任一分支保留的键并入（记忆不丢）、单边改动取改动方、同 id 双方均改走字段级融合（text 冲突自动取 ts 更新者，merge_log 记录 `jsonl_auto` 计数）；`.jsonl` 经 `_merge_file` 正确分派，**不再整文件标量冲突**（旧版整文件冲突在 `merge-resolve` 中被拒绝，防截断样本毁库）
- **P0-2 prune 与血缘自相矛盾**：`_protected_snapshot_versions()` 从 lineage（last_archived/restored_from/ancestors）与 merge_log（a/b/lca）汇总血缘引用版本，`_prune_snapshots()` 滚动清理时一律豁免并打印——LCA 祖先不再被 7 日滚动删除，merge 不静默降级双路
- **P0-3 记忆写路径一致性加固**：`remember_structured` 改「读-查重-原子 append」（O(1)，不再整文件重写）；`save_memories` tmp + `os.replace` 原子写 + `_MEM_LOCK` 进程内互斥；`_brain_sync_update` 精确全文匹配优先、子串兜底（防宽泛关键词误更无关记忆）；`_brain_sync_delete` 保持与 memory.json 侧一致的子串全删语义

### P1 · 认知回路接通（写后无读 → 写后必读）
- **P1-1 自我模型 / 决策日志进入推理上下文**：`brain_context()` 新增注入「自我认知 · 我知道/我不确定/我的局限」（各 2 条，诚实声明防幻觉）与「未决决策」（open 状态 2 条）——自省结果首次塑造对话行为；`brain_status` 增 `open_decisions`/`self_model_source`
- **P1-2 对话按话题检索**：api_server chat 组装以最近一条用户消息尾部文本作 query 调 `brain_context(query=…)`——闲置的语义检索通道接通（兼容旧签名/外部桩的 TypeError 兜底）
- **P1-3 时间语义修复**：新增 `_ts_epoch`（任意偏移 → UTC epoch），search 空查询排序、consolidate 天数归档、brain_context 排序全部改 epoch 口径——历史混写 +08/+01 不再失真
- **记忆排序破陈旧固化**：`brain_context` 无 query 时改「重要度 × 时间衰减」打分（半衰期 30 天）取 Top-N，高重要度旧记忆不再永久霸占注入位

### P2 · 一致性打磨
- **调度收敛**：心跳（≥6h）/兜底快照（>28h）并入 `scheduler_loop`（`_brain_guard_tick`），删除独立守护线程 `_start_brain_guard`——单一调度源；每日 22:00 快照钩子共享时间轴防重复
- **会话生命周期对齐**：`stop_server()` 收尾自动 heartbeat（「服务停止，记忆已落盘」），下次启动断点续接
- **merge 血缘续链**：合并结果 `.lineage.json` 记录双亲/祖先并集（原为清空），合体后可继续分支演化、再次以双亲为 LCA
- **演化账本可用**：新增 `record_evolution`/`cmd_evolution`（add/list，adopted/proposed）+ brain_api `evolution-record` 动作；注释明确与主程序 self_evolve（能力自举）双轨关系；cmd_init 预置记录无 title 的列表显示兜底
- **self_model 模板如实化**：baseline.limits 补 jsonl 行级合并语义与 prune 血缘豁免语义；`cmd_status` 增自我模型源/待回执决策行

### 回归
- 新增 `tests/test_brain_fixes.py` **15 用例**（jsonl 三路并集/单边改/双边 ts 裁决/删除不传播、prune 血缘豁免、原子 append+去重、自我模型+决策注入、时间衰减排序、混时区 epoch、演化账本、merge-resolve 拒绝 jsonl）
- 修复 `test_quiet_mode.py` 收集期副作用：stub 归还 brain_context/_memory_full（此前 monkeypatch 在 pytest 收集阶段污染同进程后续测试）
- pytest **146 passed**（131 + 15）；四门禁全绿（audit error 0 · warn 0 / validate 135 / island 135×9 无孤岛 / check_docs 135 工具 · 85 路由 · 3.8.4）；quiet_mode 门控脚本通过

## v3.8.4（2026-09-03）—— 🏷 正式固化「未发版追加」系列：版本号 3.8.3 → 3.8.4

**正式发版**：`config_defaults.VERSION` 3.8.3 → **3.8.4**。v3.8.3（2026-08-31）以来代码审查驱动的全部「未发版追加」提交首次固化并打 tag（下方各「v3.8.3（未发版追加）」段 + 本节 P1/P2 收尾即为本版本内容明细）；「默认自由，法无禁止皆可为」安全模型随 README / SECURITY.md / CHANGELOG 最终对齐。**本版本不引入任何默认限制回退**——只让用户可控机制（黑名单/审批清单，均默认 off）真正生效。

### 新增收尾：审查报告风险清单 P1/P2 全量闭环（代码此前已随提交落地，此处留档）
- **P1-1**：禁命令黑名单激活——`run_command`/`start_process` 接入 `permissions.check_shell`（此前黑名单空转）；**P1-2**：出厂 `network.blocklist` 预置云元数据 `169.254.169.254` 单点底线（`blocklist_enabled=False` 一键全放行时整体跳过）；**P1-3**：49 个工具域阈值常量/锁下沉 `shared.py`，域包半循环静态导入面 174 → 123；**P1-4**：文档与安全现实统一（README/SECURITY/CONTRIBUTING/TECH_NOTES/MODULES 三层表述 + `check_docs` 12 条 STALE_TEXT 防回潮门禁）；**P1-5**：`/v1/config/reset` GET 副作用分支删除，恢复默认只经 POST
- **P2-1**：零调用返回契约 helper `fmt_err/fmt_ok/fmt_json` 移除；**P2-2**：`do_GET` 46 分支 if/elif 路由表化（`@_get_route` + qpath matcher，端点口径 79 → 85）；**P2-3**：前端 API 收编——`api.js` 全量 JSDoc 类型化命名导出、组件零裸路径（新门禁 `noBareApiCalls.test.mjs` 递归断言 `api.api(` 不存在）、`localStorage/location/history` 收敛 SSR 安全层；**P2-4**：`fetch_blocked` 工具名别名接线入迁移/审计门禁；**P2-6**：`run_python` 移除 `with_site` 死参数 + `PIP_ALLOWLIST_NOTICE` 幽灵文案清理；**P2-8**：`/v1/token` 端点加固——有 Origin 须匹配白名单、无 Origin 校验 Host 回环（防 DNS rebinding，判定矩阵 7 用例）；**P2-9**：工具系统审计 **warn 13 → 0**——8 工具 schema 描述去除「需安装/可选依赖/未安装时提示」门槛措辞（改为点名依赖库的陈述式，依赖缺失返回安装指引的真实行为不变），`KNOWN_ALIASES` 登记即豁免（未登记差异仍按 error 拦截）

### 固化内容（下方「v3.8.3（未发版追加）」各段，明细见各段不再重复）
P0-1 巨石拆分三批收官（117 工具迁入 `agent_tools/`，主文件 13,115 → 4,484 行）、D/L/C 三层优化 16 项、A1/A6 返回契约统一与执行链路测试固化、前端全量审查修复（2 P0 + 6 P1 + 12 P2）、audit_preactivate_hints 六层审计补全、恢复默认自由（任务模式无限权限 + 黑名单主导一键开关）、app_manage 跨平台包管理器自动探测、browser_navigate 多窗口/多页签句柄管理。

### 回归
- pytest **131 passed**；四门禁全绿（audit `--strict` error 0 · warn 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 85 路由 · 3.8.4）；前端 `npm test` / `typecheck` / `build` 全绿

## v3.8.3（未发版追加）—— 🧹 修复批次：权限默认值纠偏 + 工具域常量下沉 + 安全文档统一（P1-1 → P1-4）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。代码审查报告 P1 级四条与配套文档债务一次性收口；语义全部保持「默认自由，法无禁止皆可为」——本批次**不引入任何默认限制**，只让已有用户可控机制真正生效、让文档与代码现实一致。

### 代码修复（此前已随提交落地，此处留档）
- **P1-1 禁命令黑名单激活**：`run_command`/`start_process` 此前绕过 `permissions.check_shell`（黑名单空转）；接入后用户配置的 shell 黑名单真正生效
- **P1-2 默认网络底线**：出厂 `network.blocklist` 预置云元数据地址 `169.254.169.254`（唯一几乎所有合法场景都不会访问的内网地址）；`blocklist_enabled=False` 一键全放行时整体跳过——底线可被用户主动解除
- **P1-3 工具域常量下沉 `shared.py`**：49 个工具域阈值常量/锁从 `deepseek_client.py` 迁移归口（按 8 个语义组），dc 顶部 re-export 保旧路径（对象身份一致）；`agent_tools/*` 不再回指 dc 顶层常量，`tool_basic` 归零 dc 依赖，消除域包半循环依赖的静态导入面（174 → 123）

### P1-4 文档与安全现实统一（本提交）
- **README**：「安全与隐私」重写为「默认自由 + 黑名单 + 硬限额」三层表述；删除幽灵声明（sandbox Python 禁用危险模块 / zip 炸弹防护 / 可执行文件拒绝直接打开——均无对应代码）；架构图补 `agent_tools/`、`toolkit.py`、`shared.py` 层
- **SECURITY.md**：支持版本表 3.5.x → 3.8.x；「run_python 静态 AST + `-I -S` 沙箱」改为无沙箱直通语义；SSRF 表述改为双模式（blacklist 只拦用户黑名单 / 旧 whitelist 才严格判断）；删「新增工具接入审批流」旧要求
- **CONTRIBUTING.md**：审批语义改为「默认零审批，确需加严才登记 `approval_actions`」；修正 `permissions.py` 职责行（移除无来源的「双工作线程池」）；提交示例更新
- **TECH_NOTES.md / MODULES.md**：权限与安全章节、`security.py`/`permissions.py` 模块描述同步双模式语义
- **`tools/check_docs.py`**：`STALE_TEXT` 新增 12 条安全表述门禁——上述过时片段一旦回潮即被 CI 拦截（D6 元债务闭环）
- `security.py` 内部 docstring 版本错标（v3.9+ → v3.8.3+）修正

### 回归
- pytest **116 passed**；`check_docs` 实测 135 工具 · 79 路由 · 3.8.3 全绿（含新增 STALE_TEXT 门禁）

## v3.8.3（未发版追加）—— 📦 app_manage 跨平台包管理器自动探测（修复"无管理器不可用"）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。此前 app_manage 仅支持 winget/choco，本机无任一管理器时（managers 全 ❌）完全不可用；改造为按平台自动探测完整工具链，并支持一条命令引导安装 Scoop（免管理员）——"装软件"在任何机器上都能闭环。

### 变更（agent_tools/tool_system.py）
- **跨平台探测链**：Windows winget→scoop→choco、macOS brew、Linux apt/dnf/pacman/apk，按平台优先级自动选择；`source` 枚举同步扩展到 9 个管理器（auto/winget/scoop/choco/brew/apt/dnf/pacman/apk）
- **Scoop 专项**：scoop 是 PowerShell 函数 + shims（无独立 exe）——除 shutil.which 外补 SCOOP 环境变量 / `~/scoop/shims` 目录兜底；`.cmd` shim 经 `cmd /c`、`.ps1` 经 `powershell -File` 包装执行（避免 WinError 193）
- **action=bootstrap（新增）**：无任何包管理器时可直接引导安装 Scoop（免管理员）；managers 动作输出各管理器缺失时的精确安装指引
- **跨平台 list**：Windows 注册表枚举，q 过滤无命中且已装 Scoop 时自动补 `scoop list` 便携应用（不写注册表）；macOS/Linux 走 brew/apt/dnf/pacman/apk 的 list 命令过滤（截 80 条）
- **统一 argv 模板**：search/install/uninstall/upgrade 每管理器独立参数——winget `--id` + 失败按名称回退重试、choco `-y`、Linux 系统管理器非 root 自动补 `sudo` 前缀；upgrade 语义 = 列出可升级（winget upgrade / scoop status / choco outdated / brew outdated / apt list --upgradable…），dnf 退出码 100 视为正常

### 回归
- 新增 `tests/test_app_manage.py` 10 用例：平台优先级表、九包管理器 argv 模板、scoop `.cmd` shim 包装（cmd /c）、目录兜底探测（SCOOP 环境变量 / ~/scoop）、schema 枚举与 Python 签名一致性
- 真实冒烟（本机确无 winget/scoop/choco，即原"不可用"场景）：managers 输出全部缺失 + bootstrap 引导；list(q=python) 正常返回 11 项；source=brew 提示可执行路径

## v3.8.3（未发版追加）—— 🌐 browser_navigate 多窗口/多标签页签句柄管理

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。此前浏览器为"单共享页面"模型：跨页面任务只能不断 open 覆盖当前页，多窗口/多标签（对照页面、弹窗窗口）无法管理。改造为共享上下文多页签模型，页签/窗口可句柄级操作。

### 变更
- **基建（deepseek_client.py）**：非 persistent 模式由 `browser.new_page()`（每次独立上下文、登录态不共享）改为单一 BrowserContext 多页签模型（`ctx.new_page()`，弹窗/多标签共用 cookie/登录态）；persistent 模式页签枚举统一走 `ctx.pages`
- **页签辅助（新增）**：`_browser_pages`（枚举全部窗口/页签，含 window.open 弹窗）、`_browser_active_page`（激活页签，失效自动回退相邻）、`_browser_new_page`、`_browser_switch_to`（#编号/URL/标题关键字三路匹配）、`_browser_close_page`（默认关激活页，关闭后自动接管相邻页签）、`_browser_match_page`
- **工具层（agent_tools/tool_web.py）**：browser_navigate 新增 action——`tabs`（列出全部页签，▶ 标记当前激活）、`new_tab`、`switch_tab`、`close_tab`（`handle` 参数 = #编号/URL/标题）、`back`/`forward`/`reload`；open/click/type/fill/submit/select/get_text 改为作用于当前激活页签；schema 新增可选 `handle` 参数、url 不再必填
- 描述收紧至 130 字门禁内（validate_tools compact 校验通过）

### 回归
- 四门禁全绿：audit `--strict` error 0（warn 13，描述超长类清零）/ validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3
- pytest **116 passed**（106 + 新增 test_app_manage 10 用例）

## v3.8.3（未发版追加）—— 🚀 恢复默认自由：任务模式无限权限 + 黑名单主导（一键开关）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。回应用户对 P1-5「安全默认值」的纠偏：任务模式应回归「法无禁止皆可为」——默认 0 限制，限制只来自黑名单（带一键开关），不再由程序默认强加审批。

### 变更
- **默认任务模式（无限权限）**：`config_defaults.full_auto` 默认 `False → True`——全新安装/恢复默认即进入任务模式（全 135 工具、零审批、黑名单主导）；`App.jsx` 初始 `mode` 同步改回 `task`（启动后仍从后端读真实模式覆盖）
- **审批清单默认清空**：`permissions.DEFAULT_PERMISSIONS.approval_actions` 由 21 项高危动作 → `[]`（零审批，法无禁止皆可为）；迁移路径（v1→v2）同样不再强制写入。审批机制保留——需要时用户可在「权限」页自行添加工具名（可选限制，非默认）
- **黑名单一键总开关（新增）**：`blocklist_enabled`（默认 `True`，黑名单默认空 = 0 限制）——`check_shell` / `check_filesystem` / `check_network_host` 三处黑名单检查统一受开关控制；`False` = 一键全放行（连黑名单也不拦）。权限页新增「🔓 一键全放行 / 🛡 启用黑名单限制」按钮，`/v1/permissions` API 支持读写
- **首次启动向导**：说明文案由「🛡 安全默认已启用（对话模式+高危审批）」改为「🚀 默认任务模式（法无禁止皆可为）：黑名单为唯一限制来源，可在权限页添加黑名单/审批清单（均带一键开关），或随时切换对话模式」
- **`_config_reset`**：恢复默认 = 默认任务模式（full_auto=True），同步权限模块
- **杂项**：`SettingsPage`「工具库与权限」入口描述 115 → 135 工具（过时文案修正）

### 保留的安全底线（非"限制"，是"保护"）
> ⚠️ 本段原稿（SSRF 请求层硬拦内网/元数据、run_python 静态危险检查"按设计放行"）已被后续提交推翻，以下为对齐现实后的表述：
> - `222beec` 移除所有内置限制（run_python 去 `-S` 与静态拦截、网络放开内网/回环）；`29f063e` 清理 image_generate 残留的 `allow_loopback=False`。
> - 现网语义：默认 `blacklist` 模式下 `security._safe_url` **只拦用户 `network.blocklist`**（无独立请求层 SSRF 硬拦），出厂默认仅预置 `169.254.169.254` 一项（P1-2 迁移为初始网络黑名单），受 `blocklist_enabled` 总开关控制；旧 `whitelist` 模式才保留严格 SSRF 判断（内网/回环/链路本地阻止、云元数据不可豁免）。
> - `run_python` 现等同本机 `python -c` 直通（无静态危险检查、无 `-I -S`）——"无限制模式"即设计本身，由用户显式授权的无限权限承担风险。

### 回归
- 新增/重写 `tests/test_safety_defaults.py`（13 用例）：默认自由断言（full_auto=True / approval_actions=[] / blocklist_enabled=True）、审批机制保留（用户配置清单后仍走回调/拒绝拦截/FULL_AUTO 跳过）、**黑名单开关三域验证**（shell/filesystem/network 关闭即全放行）、`_config_reset` 回默认任务模式、前端初始 mode 与向导文案静态检查
- 后端冒烟：全新安装加载链确认默认 `full_auto=True / approval_actions=[] / blocklist_enabled=True`，`check_shell`/`check_filesystem`/`request_approval(delete_file)` 全部默认放行，网络黑名单出厂仅预置云元数据地址 `169.254.169.254`（blacklist 模式唯一默认底线，可增删/一键全放行整体跳过；旧 whitelist 模式才恢复严格 SSRF 判断）
- 全量 pytest **105 passed**；四门禁全绿（audit `--strict` error 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3）；前端 `npm run typecheck` + 三套件全绿

## v3.8.3（未发版追加）—— 🤖 AI 自我进化提案实现：audit_preactivate_hints（六层审计补全）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。采纳并实现鲸语 AI 自提的进化提案 `evolutions/audit_preactivate_hints_20260901`——为 `tools/audit_tools.py` 补上六层注册表审计中唯一缺失的第 6 层 `_PREACTIVATE_HINTS` 覆盖检查。

### 变更（仅 `tools/audit_tools.py`，纯只读审计逻辑，零业务风险）
- **六层解包取用第 6 层**：新增 `hints = layers["_PREACTIVATE_HINTS"]`（此前该层从未被取用/检查）
- **新增「预激活未覆盖」error 级检查**：建议参与预激活的每个工具必须至少声明一组 preactivate 关键词，防「工具加进系统但忘了挂预激活关键词」的静默漂移——与「不在任何分组」「短语表缺失」同等被 `--strict` 拦截
- **豁免名单**：`ask_user` / `request_permission`（交互回调工具，有意不参与关键词预激活）+ `activate_tools`（分组/短语已豁免，同步豁免）
- **新增 `_hint_membership` 辅助函数**：判断工具名是否命中任一组预激活关键词成员
- **报告完整性**：txt 头部统计行与 JSON 增加 `hint_count`（预激活组数）
- **顺带清理**：删除 `evolutions/` 下 6 个自检冒烟测试残留目录（`iso_test_*` ×5 + `t1_*` ×1），仅保留真实提案

### 验证
- 正向：`audit_tools.py --strict` → **error 0**（warn 15 与基线一致，零新增误报）
- 负向：AST 剥离 `get_date` 的 `preactivate` 声明重建六层 → 精确命中「预激活未覆盖」1 条，无额外误报；三个豁免工具确认不在 hints 中（豁免必要且正确）
- 回归：pytest **102 passed**；四门禁全绿（audit `--strict` error 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3）

## v3.8.3（未发版追加）—— 🖥 前端全量审查修复批次（2 P0 + 6 P1 + 12 P2）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。三路并行逐行审查全部 27 个组件 + 8 个模块（~1.17 万行）后的一次性修复批次。核心是聊天流式链路两处 P0 级缺陷与设置页两处 P1 级崩溃，其余为缺防御/死代码/竞态清理。

### 变更
- **P0-1 停止生成整条链路修复**（`ChatPage.jsx`）：`AbortController` 因 `if (stopSignalRef.current)` 恒 false 而永不创建——改为 `!stopSignalRef.current || signal.aborted` 才新建；`onStop` 同步把 streaming 消息置为完成态并同步 `msgsRef` 镜像（此前停止后光标永久闪烁、`code-open` 代码块永远渲染为占位、操作条永久隐藏）
- **P0-2 后端错误被当成功**（`ChatPage.jsx`）：`onError` 未置 `done` 终结标志，流关闭后 `finish(true)` 继续执行误弹"✅ 回复完成"并保存带错误文本的会话——改为错误即终结（`done = true` + 冲刷 rAF + 按失败路径回调）
- **P1-1 设置页白屏**（`SettingsPage.jsx`）：停止序列输入框 `onChange` 把字符串写进 `cfg.stop`，重渲染 `(cfg.stop || []).join(",")` 对字符串调 `.join` 抛 TypeError——改为 onChange 暂存字符串、onBlur 才解析为数组，value 处加 `Array.isArray` 防御
- **P1-2 浏览器可见开关失效**（`SettingsPage.jsx`）：`browser_headless` 未取反（`!!cfg` 写回原值）——改为 `!cfg.browser_headless`
- **P1-3 批量任务面板打开即崩**（`ChatPanels.jsx`）：`{file}` 引用未定义变量——改为字面量 `{'{file}'}`
- **P1-4 Composer 打断挂起死代码**（`Composer.jsx`）：`if (!v || busy) return` 前置短路使"busy 时发送即打断挂起"分支永不执行——删除 busy 短路，恢复设计行为
- **P1-5 参数对象渲染崩溃**（`ToolCard.jsx`）：参数值为对象时 `String(v)` 长度 ≤26 走原值分支，React 抛 "Objects are not valid as a React child"——无条件 `String(v)`
- **P1-6 插件页缺字段崩溃**（`PluginsPage.jsx`）：`p.permissions.tools.length` / `files.length` 与详情弹窗 `d.tools/skills/workflows/files` 均无空数组防御——统一 `|| []`
- **P1-7 停止被误报为失败**（`ChatPage.jsx`）：catch 未区分 `AbortError`（修复 P0-1 后用户停止将走进 catch 弹"⚠️ 发送失败"）——`AbortError`/code 20 直接 return
- **P1-8 localStorage 崩溃面**（`ChatPage.jsx`）：`whaletalk.webSearch` 读写无 try/catch，隐私模式下 `useState` 初始化器抛 SecurityError 首帧崩溃——套 try/catch 给默认值
- **P2 批量**：ChatPage rAF 清理（防卸载后 setState/多冒半帧）、webSearch 编辑重发 busy 一律拦截、key_hint 短密钥整体打码；Composer auto_send 清附件 + 加载 effect alive 守卫；ttsUtil 死代码收敛、`playTestTone` blob URL 泄漏、`primeAudio` 过早置位；exporters `esc` 补引号转义；ContextPanel 硬编码计数改动态 + 缺字段防御；Pages 域卡片 `d.tools` 防御；FirstRunPage batch_done 同步 `failedKeys`（重试失败项不再空跑）；PromptsPage 保存剔除 UI 私有 `tagsText` 字段；BrainBlock 快照版本号统一 `String()` 比较；SessionList 拖拽卸载兜底清理全局监听；App `toggleQuiet` 副作用移出 updater

### 回归
- 前端：`npm run typecheck`（tsc --noEmit）✅ · 三套件（longTextUtil / apiStreamChat / markdownRender）✅ · `npm run build`（vite）✅
- 后端：pytest **102 passed**；四门禁全绿（audit `--strict` error 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3）

## v3.8.3（未发版追加）—— 🧪 A6 执行链路测试固化（tests/test_run_capture.py）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。A6 抽 `_run_capture` 后，五个执行工具（run_python / run_command / run_lint / run_tests / pip_install）的执行路径此前仅靠临时冒烟脚本覆盖，本轮固化为正式 pytest 用例，防止后续改动回归。

### 变更
- 新增 `tests/test_run_capture.py` **22 用例**：`_run_capture` 六条路径（成功 / stderr 合并 / 非零退出 / 超时 kill 进程树 / 截断标记 / cwd 生效）+ 五工具参数校验与统一执行路径（成功/无输出/错误堆栈/超时文案/超长拦截/空参、退出码透传、blacklist 命中拒绝、pytest 单文件、ruff 优雅降级、pip 选项注入与非法字符拦截）
- 测试导入顺序备忘：必须**先 `import deepseek_client`**（顶层完整构建六层注册表并加载 agent_tools），直接 `from agent_tools.tool_code import ...` 会触发 `__init__` 循环导入报 `TOOLS 顺序表含未注册工具`

### 回归
- pytest **102 passed**（80 原有 + 22 新增）；四门禁全绿（audit `--strict` error 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3）

## v3.8.3（未发版追加）—— 🧹 收尾批次：返回契约统一（A1）+ 公共执行辅助抽取（A6）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。对 C 系列遗留项的收尾：工具返回契约全库核对归一 + `tool_code.py` 五处重复的「spool 输出 + 超时 kill + 截断」内联模式收敛为单一公共辅助。

### 变更
- **A1** 返回契约统一：`daily_brief` 失败返回补 `错误：` 前缀（`简报生成失败` → `错误：简报生成失败`，与其余工具失败前缀一致）；全库 AST 扫描确认其余疑似裸文案（~103 处）均为嵌套辅助函数内部返回 / `return None` 分支，非工具契约出口，无需改动
- **A6** 抽 `_run_capture` 公共执行辅助（`agent_tools/tool_code.py`）：统一「`SpooledTemporaryFile` 1MB 限流防 OOM + 超时 `_kill_tree` 进程树 + 抛 `TimeoutError(timeout)` + 头部截断 `[输出已截断]` + `CREATE_NO_WINDOW` + 可传 `cwd`」；`run_python` / `run_command` / `run_lint` / `run_tests` / `pip_install` 五工具全部改走该辅助，删除各函数内联副本（净 −80 行重复代码），超时/截断/无输出文案行为不变

### 回归
- pytest **80 passed**；四门禁全绿（audit `--strict` error 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3）；前端三套件（longTextUtil / apiStreamChat / markdownRender）+ `tsc --noEmit` 全过；A6 定向冒烟 **10/10**（五工具成功/失败/无输出/退出码路径 + `_run_capture` 超时 kill、截断标记、pip 选项注入拦截）

## v3.8.3（未发版追加）—— 🔧 设计/逻辑/能力三层优化批次（D1-D4 + L1-L8 + C1-C8 共 16 项）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。对全部 135 工具做三层审查后的一次性修复批次：设计缺陷（返回值格式统一/读操作留痕/进程间文件锁/参数校验收敛）+ 逻辑短板（输出相似度升级/记忆近重复合并/SQL 强制 LIMIT 与超时/写操作行数保护/进程内增量索引/批量替换/编码探测/权限分级）+ 能力缺口（HTML 正文提取/Excel 追加多表/图表多系列与字体探测/实时搜索多源/下载校验和/Webhook 签名/点击轮询等待/桌面通知静音时长）。

### 变更
- **D1** 工具返回值格式统一：成功/失败前缀规范化；**D2** 读操作调用留痕（audit）；**D3** 关键文件进程间文件锁；**D4** `shared.clamp_int` 参数钳制收敛
- **L1** `verify_output` 相似度升级（difflib）；**L2** `write_memory` 近重复合并（difflib ratio≥0.85 + 数字集合冲突 ×0.5 强惩罚 + 实体别名归并）；**L3** `database_query` 强制 LIMIT（`db_utils.force_limit` 剥尾注释）+ SQLite 15s 语句超时（`set_progress_handler`）；**L4** `database_execute` 写操作行数保护（`DB_EXECUTE_MAX_ROWS=10000`，预览超限拒绝，无 WHERE 的 UPDATE/DELETE 入口拒绝）；**L5** `search_local` 进程内增量索引（mtime/size 零 IO 命中，单次刷新预算 200 文件，>512KB 不索引，>64KB/400 行截断实时补扫）；**L6** `edit_file` 批量替换 `replacements` 参数（JSON 数组，单项不匹配跳过）；**L7** `read_file` 编码探测（BOM→UTF-8→GB18030→BIG5→latin-1，非 UTF-8 追加 `[编码：xxx]` 提示）；**L8** RPA 只读操作（rpa_screenshot/rpa_screen_size）降级不入审批，写类仍双清单
- **C1** `fetch_url` HTML 正文提取（纯标准库：剔除 script/nav/footer 等噪音块 + 块级标签换行 + 实体解码，JSON/纯文本原样，过短回退）；**C2** `write_excel` 追加模式 `mode=append` + 多表 `sheets` 参数（dict 行/数组行类型保留）；**C3** `chart_data` 多系列（`[{"name","data"}]`，调色板 + legend）+ 跨平台中文字体探测（Windows/macOS/Linux）；**C4** `search_realtime` 多源（hn/github/bilibili/stackoverflow，v2ex 因网络不可达换 B站热门，均实测连通）；**C5** `download_file` 可选 `expected_sha256` 下载后校验（不匹配删文件报错）；**C6** `send_webhook` HMAC-SHA256 签名（`X-Timestamp`+`X-Signature` 头，配置支持 `{"url","secret"}` 对象）；**C7** `rpa_click` 点击前等待（`wait_sec` 延迟 / `wait_pixel` 像素颜色轮询超时不点击）；**C8** `notify_desktop` 静音 `silent` + 时长 `duration`（Toast XML audio/duration 注入）

### 回归
- pytest **80 passed**；四门禁全绿（audit `--strict` error 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3）；前端三套件（longTextUtil / apiStreamChat / markdownRender）+ `tsc --noEmit` 全过；冒烟 29 项全过（本轮 8 个改动工具注册/签名/schema 逐项核对 + run_workflow 注入回归 + 路由/入口导入）

## v3.8.3（未发版追加）—— 🧩 P0-1 巨石拆分收官（第三~八批，117 工具全部迁入 agent_tools/）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。一次性规划 + 连续执行剩余全部批次，P0-1 拆分路线收官：`deepseek_client.py` 由 13,115 行瘦身至 **4,484 行**（−8,631），主文件仅剩「共享基建 + 六层注册表 + 薄 facade」。

### 变更
- **新增 8 个域模块（117 工具）**：
  - `tool_docs.py`（📊 数据与文档，19）：database_query_mysql / database_query_postgres / read_excel / epub_read / mobi_read / doc_read / msg_read / archive_list / write_excel / chart_data / database_query / database_execute / pdf_extract / pdf_create / docx_read / pptx_read / secret_store / kv_store / create_doc
  - `tool_web.py`（🌐 浏览器与网页，14）：fetch_url / download_file / search_web / search_github / search_realtime / browser_navigate / web_screenshot / net_diagnose / fetch_url_smart / rss_fetch / webdav / call_api / track_web / **fetch_blocked**（保留字冲突，实现名 `_run_fetch_blocked`，审计/迁移门禁内置别名映射）
  - `tool_code.py`（💻 编程与执行，15）：run_python / run_command / run_lint / run_tests / verify_project / project_scaffold / dev_plan / get_status / project_map / find_symbol / code_lookup / write_code_project / pip_install / subagent_run / verify_output
  - `tool_files.py`（📁 文件与进程，17）：read_file / write_file / edit_file / list_dir / search_local / clipboard_get / clipboard_set / delete_file / archive_files / extract_archive / list_snapshots / restore_snapshot / batch_rename / start_process / stop_process / list_processes / environment_info
  - `tool_brain.py`（🧠 记忆与定时任务，14）：write/read/delete/update_memory / self_profile / query_memory_graph / knowledge_index / knowledge_search / schedule_task / list_schedules / cancel_schedule / task_checkpoint_save / task_checkpoint_load / run_workflow
  - `tool_msg.py`（📧 邮件与消息，10）：send_email / publish_draft / send_webhook / im_send / telegram_poll_updates / read_email / email_summary / agent_mail / run_wechat_writer / daily_brief
  - `tool_system.py`（🔧 系统与项目，12）：watch_files / recall_session / project_info / read_project_file / create_evolution / self_evolve / verify_files / git_tool / notify_desktop / app_manage / usage_report / **create_plugin**
  - `tool_desktop.py`（🖱 桌面与视觉语音，18）：rpa_screen_size / rpa_click / rpa_type / rpa_hotkey / rpa_move / rpa_scroll / rpa_screenshot / screen_find_click / vision_loop / tts_save / tts_speak / tts_stop / speech_to_text / voice_chat_loop / image_generate / qrcode / media_ffmpeg / team_run
- **运行时注入配置动态化**：迁移后域模块不再对 `WORKING_DIR` / `KV_CACHE_DIR` / `SECRETS_FILE` / `MEMORY_FILE` / `MEMORY_ENABLED` / `EVOLUTIONS_DIR` / `PLUGIN_PATHS` / `EMAIL_CONFIG_FILE` 等 36 个可变配置做值绑定 import，一律 `import deepseek_client as _dc` 属性访问——main / api_server / 测试注入新值后**立即生效**（修复了值绑定导致注入失效的回归）
- **`agent_tools/__init__.py`**：聚合 11 个域模块 `import *`，`__all__` 覆盖全部 117 个拆分工具名（`dc.kv_store` / `dc.create_plugin` / `dc._run_fetch_blocked` 等旧访问路径不变）
- **主文件工具定义清零**：`@tool` / `register_tool` 装饰的工具函数全部迁出，主文件 AST 扫描 0 个残留

### 回归
- `tests/test_tool_split.py` 扩展 7 用例（共 17 用例）：全量 117 工具 re-export + 归属断言（逐工具 `__module__` 匹配域模块）+ 六层完整性（135 = TOOLS/ORDER/CALL_MAP）+ `fetch_blocked` 别名实现 + **主文件无工具定义 AST 断言** + 各域代表性工具参数校验分支（不联网/不依赖三方库）
- pytest **80 passed**（74 原有 + 6 新增）；四门禁全绿（audit `--strict` error 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3）；前端三套件（longTextUtil / apiStreamChat / markdownRender）全过；修复 `test_quiet_mode.py` 模块级 stub 未恢复的隔离瑕疵（末尾归还 `self_profile` 真实实现）

## v3.8.3（未发版追加）—— 🧩 P0-1 巨石拆分第二批（媒体/文档域）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。P0-1 路线第二批：把「🎨 媒体与图像」域的 10 个工具从 `deepseek_client.py` 迁入 `agent_tools/tool_media.py`，复用首批验证的「共享符号梳理 + 门禁护航」拆分模式。

### 变更
- **新增 `agent_tools/tool_media.py`**（🎨 媒体与图像，10 工具）：`image_process` / `ocr_image` / `image_understand` / `screen_capture` / `screen_see` / `chart_read` / `screenshot_to_html` / `debug_screenshot` / `scan_read` / `image_batch`——装饰器 + 函数体原样迁出；顶层 `from-import` 依赖符号：deepseek_client（`VISION_MODEL` / `IMAGE_EXTENSIONS` / `get_active_client` / `is_vision_model` / `_detect_image_mime` / `_safe_stream` / `_capture_screen_png`）+ security（`_safe_url`）+ shared（`OCR_IMAGE_PS`）+ permissions，均位于主文件导入点之前（加载顺序契约）
- **`deepseek_client.py` 薄化 −588 行**（13,703 → 13,115）：主文件保留视觉闭环辅助符号 `_capture_screen_png` / `_extract_image_path` / `_IMAGE_FILE_PATH_RE` / `_IMAGE_PATH_TRAIL` / `_IMAGE_PRODUCING_TOOLS`（vision_loop / RPA 自查 10829/10873/10929 与进化审核 12924/12927 仍直接调用），域模块经 re-import 复用
- **`agent_tools/__init__.py`**：聚合 `from .tool_media import *`，`__all__` 扩展至 14 工具名（`dc.image_understand` 等旧访问路径不变）

### 回归
- `tests/test_tool_split.py` 扩展 4 用例（共 10 用例）：媒体工具 re-export + 归属 `agent_tools.tool_media`、六层完整性、视觉辅助符号保留在主文件、参数校验分支行为回归（不依赖 PIL/网络）
- pytest **74 passed**（70 原有 + 4 新增）；四工具门禁全绿（audit `--strict` error 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3）；进程内冒烟确认 TOOLS 仍 135、媒体 10 工具六层同源、`image_generate`/`qrcode` 等仍归属主文件

## v3.8.3（未发版追加）—— 🧩 P0-1 巨石拆分首批（agent_tools/ 域模块）

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。P0-1 路线的第一批落地：把 `deepseek_client.py`（13,889 行巨石）的工具定义按域拆出，建立可复制、门禁护航的拆分模式。

### 变更
- **新增 `agent_tools/` 运行时工具域包**：`tool_basic.py`（🔧 系统与基础：`get_date`/`get_weather`）、`tool_data.py`（📊 数据与文档：`read_csv`/`write_csv`）——装饰器 + 函数体从主文件原样迁出，顶层 `@tool()` 注册；`__init__.py` 聚合并显式 `__all__` re-export 工具名，**`dc.get_date` 等旧访问路径不变**（外部仅经 `TOOL_CALL_MAP` 动态调用，无业务代码直接依赖）
- **`deepseek_client.py` 薄化 −187 行**：保留全部共享基建（常量/辅助/import 别名）+ `_TOOL_ORDER`/`_GROUP_ORDER`/`_HINT_ORDER` 顺序常量 + 六层构建；在共享基建定义完成后、六层构建前执行 `from agent_tools import *`（加载顺序契约：域模块顶层 `from deepseek_client import WEATHER_TIMEOUT` 可安全解析）
- **`toolkit.rebuild_layers` 支持多文件**：`rebuild_layers(source_text, *extra_sources)`——@tool/register_tool 跨文件收集，顺序常量只从主文件顶层读取（门禁的 AST 重建与实跑等价）
- **三门禁多文件适配**：`audit_tools.py`（函数签名收集 + 六层重建均扫主文件 + `agent_tools/`）、`island_check.py`（`defined` 函数集合同样多文件）、`validate_tools.py`（六层重建传多文件）
- **`WhaleTalk.spec`**：`hiddenimports` 增加 `collect_submodules('agent_tools')`——PyInstaller 无法静态发现 `import *` 的子模块，不收集则打包后工具注册缺失

### 回归
- 新增 `tests/test_tool_split.py`（6 用例）：re-export 可见性、六层完整性（TOOLS/GROUPS/PHRASES/PREACTIVATE + CALL_MAP 指向同一函数对象）、行为回归（get_date 日期格式 / get_weather 参数校验分支 / CSV 往返）、`rebuild_layers` 多文件重建与运行时六层**顺序级一致**
- pytest **70 passed**（64 原有 + 6 新增）；四工具门禁全绿（audit `--strict` error 0 / validate 135 全链路 / island 135×9 层无孤岛 / check_docs 135 工具 · 79 路由 · 3.8.3）；进程内冒烟确认 TOOLS 仍 135、`write_csv` 归属 `agent_tools.tool_data`、CALL_MAP 指向同一函数对象

## v3.8.3（未发版追加）—— 🛡 P1-5 安全默认值落地

**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3）。将「初始即完全智能（零审批）」反转为「安全默认（对话模式 + 高危审批）」。

### 变更
- **`config_defaults.full_auto` 默认 `True → False`**：新装用户初始为 💬 对话模式，不再零审批；老用户 config.json 显式值不受影响
- **`permissions.DEFAULT_PERMISSIONS.approval_actions` 预填 21 项高危动作**（run_command / run_python / pip_install / delete_file / batch_rename / extract_archive / restore_snapshot / send_email / database_execute / start_process / stop_process / write_code_project / publish_draft / create_plugin / rpa_* 六项）——blacklist 模式下清单内动作弹审批确认，**此前默认为空导致审批空转**；v1→v2 迁移路径同步获得该安全基线
- **`api_server._config_reset`**：不再强制保留 `full_auto=True`，恢复默认 = 回到安全默认，并同步权限模块 FULL_AUTO
- **前端**：`App.jsx` 初始 `mode` 由 `task` → `dialog`（启动后仍从后端读真实模式覆盖）；`FirstRunPage` 首次启动向导底部新增「🛡 安全默认已启用」确认说明（高危操作需确认、可随时切任务模式）

### 回归
- 新增 `tests/test_safety_defaults.py`（10 用例）：默认值断言、approval_actions 高危预填与低危不打扰、blacklist 清单内走审批/清单外放行、FULL_AUTO 跳过审批、审批拒绝拦截、`_config_reset` 回安全默认、前端初始 mode 与向导说明静态检查
- pytest **64 passed**（54 原有 + 10 新增）；前端 `npm test` / `npm run typecheck` / `npm run build` 全绿；四工具门禁（audit/validate/island/check_docs）全绿；进程内冒烟确认默认 `mode=dialog / full_auto=False`

## v3.8.3（未发版追加）—— 🧹 P2 打磨批次（P2-1 ~ P2-8 全部落地）

在 v3.8.3 基础上的低优先级打磨项全量收敛，**版本号不变**（`config_defaults.VERSION` 仍为 3.8.3），涉及后端重构 + 前端优化 + 文档/门禁配套。

### 后端
- **P2-8 `do_POST` 路由表化**：829 行 if/elif 链收敛为**轻量路由表 + 装饰器**。`@_post_route(matcher)` 在端点方法处就近注册（exact/set/pre 三种 matcher），`_POST_ROUTES` 顺序即优先级；`do_POST` 仅 14 行（鉴权 → 去前缀 → 查表 → `getattr` 分发 → 404 兜底）。51 条路由 / 51 个 `_p_*` 方法，经行为等价验证（169 路径 0 不一致、51 方法体逐行一致）后写回；`tools/check_docs.py` 端点统计兼容新装饰器写法；新增 `tests/test_api_routes.py`（8 用例）锁定路由表结构与查表行为，防止回退内联 if/elif
- **P2-3 大脑血缘图真 LCA**：`brain_api.py`/`brainkit.py` 由版本号集合交集启发式改为存完整血缘图求真 LCA
- **P2-4 `self_model` 双来源优先级标记**：静态模板与 LLM 校准不再互相覆盖

### 前端
- **P2-1 数学公式轻量排版**：自研零依赖渲染（`mdMath.js`）
- **P2-2 vite proxy 统一相对路径**：`vite.config.js` 配 `server.proxy`，dev/prod 拓扑一致
- **P2-6 `Inline` 组件加 `React.memo`**：长文重复解析消除
- **P2-7 前端 `catch` 静默吞错加告警**：各组件 `catch {}` 至少 `console.warn`

### 其他
- **P2-5 非 Windows 免密不可用补文档**（`SECURITY.md`）
- 全量回归：pytest **54 passed**（46 原有 + 8 新增路由用例）、前端 `npm test`/`npm run typecheck` 通过、四工具门禁（audit/validate/island/check_docs）全绿

## v3.8.3（2026-08-31）—— 🧘 纯净对话：一键关闭整套个性能力

新增「纯净对话」总开关——AI 随时挂着「大脑」（长期记忆 + 核心自我状态 + 鲸语大脑上下文三路注入），用户想安静执行一段不被打扰的对话时，记忆/大脑的存在可能干扰最终结果。现在一键即可让 AI 以全新姿态应答。

### 三路个性上下文（此前只有一路能关）
- **长期记忆**（`memory.json` 最近 6 条事实注入）——已有 `memory_enabled` 开关（设置 → 高级 → 🧠 长期记忆）
- **核心自我状态**（`self_profile` 跨会话连续自我注入）——**此前无条件注入、无开关**
- **鲸语大脑上下文**（`brain_context` 身份/断点/进行中目标/近期记忆 Top4 注入）——**此前无条件注入、无开关**

### 新能力
- **「纯净对话」总开关**（对话页 header 模式切换旁，随时一键切换；设置 → 通知与安全 → 🧘 纯净对话 双入口共享同一状态，localStorage + config.json 双持久化）
- 开启后**注入全停**：三路个性上下文（长期记忆/核心自我/大脑）全部不注入，AI 只带基础系统提示，以全新姿态应答
- 开启后**回写全停**：对话结束不再自动提炼写入长期记忆（`_chat_harvest` 跳过）
- 与既有开关正交：`memory_enabled` 只管记忆一路，`quiet_mode` 是总闸；任务模式同样生效

### 实现
- `config_defaults` 新增 `quiet_mode: False` 默认配置；`VERSION → 3.8.3`
- `api_server._chat_kwargs` 透传 `body.quiet_mode`（请求级覆盖，body 优先、cfg 兜底）
- `api_server._inject_system_messages` 增加 `quiet_mode` 参数：开启时跳过记忆/自我/大脑三路注入（关闭时保持原行为）
- `api_server._handle_chat`：`quiet_mode` 时跳过 `_chat_harvest` 对话回写（流式路径本就无回写，只需注入门控）
- 前端：`App.jsx` 挂全局 `quietMode` state（localStorage `whaletalk.quietMode`）双入口共享；`ChatPage.jsx` header 加「纯净对话」开关 + 发送 body 带 `quiet_mode`；`SettingsPage.jsx` 通知与安全页加「🧘 纯净对话」Toggle（联动保存 `quiet_mode` 配置）；`api.js` `streamChat` 解构 + 请求体补 `quiet_mode`（吸取 v3.8.2 静默丢字段教训，三处一致）

### 测试
- 新增 `tests/test_quiet_mode.py`（15 组断言）：开启后三路全停、关闭时正常注入、`memory_enabled` 与 `quiet_mode` 正交、`_chat_kwargs` body/cfg 优先级、任务模式同样生效
- `apiStreamChat.test.mjs` 新增 4 组 `quiet_mode` 透传断言（共 13 组全绿）
- vite build 通过；pytest 既有用例全绿

## v3.8.2（2026-08-31）—— 🐛 修复联网开关失效：web_search 字段在 api 层被静默丢弃

**症状**：对话模式打开「联网搜索」开关后，AI 仍回答「我的知识截止日期是 2024 年 7 月，无法获取实时信息」——开关看似生效（UI 点亮）但实际未起作用。

**根因**：`ChatPage.jsx` 已把 `web_search: chatMode === "dialog" && webSearch` 传入 `api.streamChat()`，但 `webui/src/api.js` 的 `streamChat` **解构参数列表与 `JSON.stringify` 请求体都遗漏了 `web_search` 字段** → 参数被静默丢弃，后端 `_chat_kwargs` 读不到开关状态（回退默认 `False`），`pure_chat` 分支不注入搜索工具，模型自然回答「没有联网能力」。v3.8.1 测试覆盖了后端注入逻辑，但前端字段透传层无测试，漏洞漏网。

**修复**：
- `webui/src/api.js`：`streamChat` 解构新增 `web_search` 参数，请求体 `JSON.stringify` 补上 `web_search`（保持与 `tools_enabled` 一致的 camelCase→snake_case 透传约定）
- 新增 `tests/apiStreamChat.test.mjs`（9 组断言，mock fetch + localStorage 直测 `streamChat`）：`web_search:true/false` 原样进请求体、未传时不带字段、`toolsEnabled→tools_enabled` 映射不回退、SSE 事件（reasoning/content/done）正常分发——**锁定请求体字段透传，杜绝同类静默丢字段**

### 验证
- 前端测试全绿：`apiStreamChat` 9 组 + `longTextUtil` 8 组 + `markdownRender` 36 组
- 端到端实测（对话模式 + `web_search:true`）：模型连续调用 `search_realtime`（Hacker News）与 `search_web`（中文新闻聚合），返回真实当日新闻（日本第 23 轮核污染水排海 / 上合组织比什凯克峰会 / 美军袭击伊朗拉腊克岛等），`vite build` 通过

## v3.8.1（2026-08-31）—— 🌐 对话模式联网搜索：实时信息一键开启

对话模式新增「联网搜索」开关——纯对话也能搜到最新信息（天气/新闻/行情/网页/文献），**大幅减弱幻觉、扩展能力边界**。

### 新能力
- **对话模式「联网搜索」开关**（header 模式切换旁，仅对话模式显示，绿色圆点指示状态，localStorage 持久化）：开启后，对话模式的请求注入 `search_web`（Bing+360+DuckDuckGo 聚合）+ `search_realtime`（Hacker News 实时热点）两个**纯搜索只读工具** + 联网使用提示（实时/事实类问题先搜再答、信息不足可换词翻页、搜索失败如实说明不编造）
- **克制注入**：仅注入联网搜索工具，**不引入其余 100+ 工具**——对话模式保持纯粹；工具卡片展示/流式安全全部复用既有能力（search_web 结果如标题/链接/摘要实时显示在对话流）
- **任务模式不受影响**：任务模式本就含全部工具，开关不向其注入任何额外工具

### 实现
- `deepseek_client.chat()` 新增 `web_search` 参数；`pure_chat` 分支开启时注入 `WEB_SEARCH_TOOLS` + `WEB_SEARCH_HINT`（不写回历史，仅本轮生效）
- `api_server._chat_kwargs` 透传 `body.web_search`（前端 → 后端全链路）
- `config_defaults` 新增 `web_search: False` 默认配置
- 前端 `ChatPage.jsx`：联网开关 state（持久化 `whaletalk.webSearch`）+ header UI + 发送 body 带 `web_search`（仅对话模式发送）；`app.css` 新增开关样式（绿色点亮动效）

### 测试
- 新增 `tests/test_web_search.py`（8 组断言，mock SDK 流式层真实调用 `chat()`）：只注入联网工具、注入提示、对话流正常、纯对话默认不传 tools、工具不可用优雅降级、任务模式不受影响/不额外注入
- pytest 28 用例回归全绿；前端 vite build 通过（vite 8.2.2）

## v3.8.0（2026-08-31）—— 🚀 世界级 Markdown 渲染器：所见即所得

手写渲染器全面升级为「解析器 + 渲染器」分离的世界级架构——AI 长回复里的一切格式（嵌套列表/表格对齐/代码高亮/任务框/折叠/脚注/公式/删除线/高亮）**完整还原、所见即所得**。

### 新架构：三层纯数据管线（零依赖 · 流式安全）
- **`webui/src/mdParser.js`（块级 AST）**：标题 1-6 / 段落 / **嵌套列表缩进树**（无限层级）/ **任务列表**（`- [x]`）/ **嵌套引用**（`>` 层次）/ **表格对齐**（`:---`/`:---:`/`---:` → 左/中/右）/ 代码围栏（```` ``` ```` 与 `~~~`，`lang="math"` 走数学块）/ **details 折叠**（`<details><summary>`）/ 脚注定义 / 分隔线；未闭合围栏产出 `code-open` 供流式延迟
- **`webui/src/mdInline.js`（行内 tokens）**：粗体/斜体/粗斜体/删除线 `~~…~~`/高亮 `==…==`/上标/下标/行内 code（单双反引号）/行内公式 `$…$`/图片/链接/自动链接（裸 URL/尖括号/邮箱）/脚注引用/转义，**任意嵌套组合**（深度上限 5，防栈溢出）；每层递归独立 RegExp 实例，杜绝共享 `lastIndex` 死循环（历史 OOM 根因）
- **`webui/src/mdHighlight.js`（零依赖语法高亮）**：js/ts/jsx/tsx/python/bash/shell/json/sql/html/xml/css/c/cpp/java/go/rust/yaml/toml 等 20+ 语言，规则顺序化（注释/字符串 > 数字 > 关键字 > 内建 > 类型 > 属性），**输出整体转义**后注入（SSR 验证零 HTML 泄漏）
- **`components/Markdown.jsx` 重写**：消费 AST 渲染 React DOM——嵌套 `<ul>/<ol>`、任务 checkbox、嵌套 `<blockquote>`、`<details>`、对齐感知表格（表头/单元格均走行内渲染）、代码块带语言标签 + 一键复制 + 语法高亮、脚注锚点互链、数学块；全部文本先解析为纯数据再渲染，杜绝 HTML/`javascript:` 注入

### 修复与加固
- **历史缺陷修复**：表格单元格、表头、列表项、引用内、折叠区内全部接入行内渲染（`code`/`bold`/链接在任意位置生效）
- **流式安全保持**：`deferCode=true` 时未闭合代码围栏跳过渲染，流结束后由父组件补齐；未闭合标记原样输出不吞内容
- **安全边界**：`<script>` 注入转义可见、`javascript:` 链接白名单拒绝（SSR 断言锁定）

### 测试（node 直跑全绿）
- `tests/markdownRender.test.mjs` 重写为 **36 组断言**：多表格共享引用回归（2 表格/7 行/内容完整性）+ 全类型渲染（标题/行内全家族/表格对齐/嵌套列表/任务框/引用/折叠/高亮/数学/脚注/分隔线）+ 流式安全 + 空输入 + 注入防护 + `javascript:` 拒绝
- **测试基建升级（vite 8 兼容）**：`tests/ssrRender.mjs` 用 vite ssr build 把组件打包为 ESM（react 保持 external）再原生 import 渲染——绕开 vite 8 `ssrLoadModule` 内联求值 CJS 导致的双实例问题（`Invalid hook call`）
- `longTextUtil` 8 用例回归全绿；仓库 pytest 回归不涉及（纯前端）

### 三主题适配
- `theme.css` 每主题新增 8 个 `--hl-*` 高亮变量（关键字/字符串/注释/数字/函数/类型/属性/标签），代码块恒为深底、亮色系保证对比度
- `app.css` 新增：嵌套列表、任务框、details 折叠箭头动效、脚注、数学块、mark 高亮、图片圆角等样式

## v3.7.3（2026-08-31）—— 🛠 表格渲染修复：多表格共享引用致整段消失

### 修复（P0 · 聊天消息渲染排版）
- **多表格内容整段消失**：Markdown 渲染器把多组表格的 `<Table rows={rows}>` 全部绑定到同一个 `rows` 数组引用，flush 时 `rows.length = 0` 清空共享数组，React 实际渲染时所有表格读到空数组 → 长回复里**所有表格渲染为空 `<table>`，内容完全不可见**（v3.0.0 起的历史缺陷；v3.7.2 解除模型长文本包装后，长回复中的表格集中暴露此问题）
- **修复**：`webui/src/components/Markdown.jsx` 每个 Table 渲染前对 `rows` 做 `rows.slice()` 快照复制，各表格互不串扰
- **顺带**：表格单元格 `<td>` 接入行内渲染（反引号 code/加粗/链接生效），与正文样式一致，不再显示字面反引号

### 测试
- 新增 `webui/tests/markdownRender.test.mjs`（SSR 真实渲染回归：多表格数量/表格行数/行内容完整性/列表/标题 5 组断言），node 直跑全绿
- `longTextUtil` 8 用例、仓库 pytest 28 用例回归全绿；vite 构建产物更新为 `index-CFMw110d.js`

## v3.7.2（2026-08-31）—— 📜 长文本渲染修复：模型包装格式解除

### 修复（P0 · 聊天消息渲染排版）
- **模型长文本包装导致排版崩溃**：部分大模型（DeepSeek 长文本生成路径）对超长回复会回带 `@long-text:"..."` 元信息前缀 + `<long_text_quote>...</long_text_quote>` 包裹的 JSON 消息链。前端原先将其当普通文本渲染，JSON 内转义符 `\n` 无法还原为真实换行，导致**表格整段消失、列表退化为裸点、加粗失效**——长回复"部分内容渲染不出来"
- **新增 `webui/src/longTextUtil.js`（`unwrapLongText`）**：渲染前解除包装——优先提取包裹内 JSON 消息链「最后一条 assistant」的 `content`（支持数组/单对象/`{messages:[...]}`/非规范 JSON 对象裸拼，含 `\n` `\"` `\\` `\u` 转义还原）；无法解析时剥标签与前缀保留正文；干净文本原样返回零副作用
- **接线四层**：`Markdown.jsx` 渲染前统一解除（实时流式/历史会话/文件预览全覆盖）；`ChatPage.buildMessageChain` 存档与请求历史解除（不再把包装垃圾写进会话/回传模型）；`Message.jsx` 复制与朗读解除（复制得到可用正文、朗读不读 JSON 壳）
- 修复后与真实落盘内容**逐字节一致**（端到端验证：5392 字符原样还原，12 标题/157 表格行/10 列表项/13 加粗全可渲染）

### 测试
- 新增 `webui/tests/longTextUtil.test.mjs`（8 用例：单行 JSON 包装/美化数组/裸 JSON/非 JSON 剥壳/流式半截/空值透传/单对象），node 直跑全绿
- 仓库 pytest 28 用例回归全绿；vite 构建产物 `index-Cp3GQMPt.js` 已上线（服务端实测返回新哈希）

## v3.7.1（2026-08-31）—— 🛡 进化防瞎闸：四层验证链 + 代码结构定位


### P0 · 自我进化验证链升级（防「瞎进化」核心）
- **四层验证闸**：`self_evolve` 验证从「lint + 测试」升级为「py_compile 语法编译 → ruff lint → import 冒烟 → pytest」串行闸，任何一级失败立即回滚，杜绝「改完就以为成功」
- **import 冒烟**：改动涉及的可导入模块必须能被独立子进程导入（抓未定义引用/循环导入/初始化错误）
- **pytest 全量回退**：改动无测试文件时自动跑仓库 `tests/` 全量——进化从此不得破坏既有回归（防「改了 A 炸了 B」）

### P1 · 自举回归套件（tests/）
- **tests/ 目录建立**：`test_registry.py`（工具注册表六层一致性核心断言：TOOLS/TOOL_CALL_MAP/TOOL_GROUPS/BUILTIN/ACTION_TOOLS/schema/可调用性）+ `test_evolve_guard.py`（进化闸函数与回滚语义：编译/冒烟/测试跳过逻辑、create_evolution 分支隔离、self_evolve 坏补丁回滚与好补丁保留分支）+ `test_code_lookup.py`
- 套件 28 用例全绿（venv pytest 9.1.1）；`_evolve_tests` 从「空转」变「有效」

### P2 · 编程能力增强
- **新增 `code_lookup` 工具**：AST 级代码结构定位（def 定义/class 类/call 调用点/import 导入来源，返回「文件:行号 摘要」），改代码前先查定义与调用点，避免改 A 炸 B；已入默认启用集（BUILTIN_TOOL_NAMES）
- 新增工具全链路注册：TOOLS(135)/TOOL_CALL_MAP/TOOL_GROUPS/_TOOL_ACTION_PHRASES/_PREACTIVATE_HINTS/_TOOL_DOMAIN，三大门禁全过（audit/validate/island 零孤岛）

### 修复
- `code_lookup` import 分支误遍历 `ast.Global/Nonlocal` 的字符串 names（AttributeError 崩溃）→ 限定仅处理 Import/ImportFrom

## v3.7.0（2026-08-30）—— 🧠 大脑进化：从容器到会学习的灵魂

### P0 · 记忆系统重构
- **记忆结构化**：`memories/memory.jsonl`（id/ts/type/importance/tags/entities/relations/source/archived），旧 md 自动兼容读取；`remember_structured` 同文本去重
- **对话记忆自动入脑**（核心闭环）：deepseek_client `write_memory/update/delete` 与大脑记忆**双向同步**——对话中写的记忆自动进大脑，删改同步
- **语义检索**：本地 IDF 加权余弦 + 中文按字/双字 bigram 分词；`brain_context` 无查询按「重要度×最新」注入，有查询按相关性 Top-N（带 `[类型·重要度]` 标记）
- **前端记忆库可编辑**：新增/编辑/删除/★标记重要/搜索（`GET /v1/brain/memories` + `POST /v1/brain/memory`）

### P1 · 思考与自我
- **睡眠巩固**：`consolidate` 命令——低重要度旧记忆归档 + 同类型相似合并（本地版）；brain_api `consolidate_with_llm` LLM 提炼增强；前端一键巩固按钮
- **目标系统**：`goals.json`（add/list/update/delete/进度），**对话自动注入进行中目标**；前端目标管理区
- **动态自我模型**：`self-refresh` 用 LLM 基于真实工具能力+记忆+目标重写 knows/unknowns/limits（无 key 优雅跳过）
- **决策日志**：`decisions.jsonl`（决策/理由/预期/结果回执），`decision add/list/resolve` 命令

### P2 · 自动化闭环
- **对话回写**：chat 完成后台提炼「用户偏好/决定/事实」自动写记忆入脑（`auto_memory` 配置开关）
- **自动心跳 + 定时快照**：大脑守护线程（每 6h 心跳、每 24h 自动归档，密钥就绪才归档）
- **快照签名**：RSA 私钥签名 `.whale`（签名块追加文件尾），恢复/合并验签，篡改拒绝（`--force` 可强过）
- **快照 diff + 前端**：`diff` 命令（文件级 + 记忆/日志行级 + JSON 字段级）；指挥舱快照「对比」按钮；融合冲突裁决 UI（已有）

### P3 · 生态
- **大脑分享**：脱敏导出（身份+记忆精华，不含密钥）`whale_share.json`；导入并入记忆
- **多大脑**：`.brain_active` 持久化切换 + `brain-dirs/brain-switch`，指挥舱切换 UI（含分支/备份大脑）

### 修复
- `_read_snapshot_to_dir` 同名快照解包互相覆盖（路径哈希隔离）；`_query_get` 不存在改用 parse_qs；brain_api 缺 `import json`；`MEMORY_SOURCE_DIR` 未定义（import-memory 潜伏 NameError）

## v3.6.5（2026-08-30）—— 🐛 移除大脑初始化硬编码的示例记忆

- **问题**：`brainkit.py` 的 `cmd_init`（大脑初始化）硬编码写入一条「用户正在推进『博视』报价目录整理」的示例记忆——**任何新用户初始化大脑都会被自动导入**，且违反大脑自身「诚实：不假装记得没记过的事」原则。
- **修复**：初始化不再预置任何具体记忆，改为中性文案「大脑初始化完成，近期记忆为空，等待首次记录」；同时清理帮助文本中的业务示例（报价/博视 → 通用示例）。
- 已同步清理本机 `brain/memories/` 中已写入的示例记忆。

## v3.6.4（2026-08-30）—— 🐛 修复：桌面快捷方式无图标 / 指向旧路径

- **根因 1（无图标）**：`_create_shortcuts` 的 `$s.IconLocation = "path",0` —— 逗号在引号**外**，PowerShell 视为数组赋值被静默忽略 → 快捷方式永远用默认图标。修复为 `"path,0"`（逗号在引号内）。
- **根因 2（指向旧路径/未重建）**：`_shortcuts_exist` 只检查文件名存在，旧项目（DeepSeek_Assistant 时代）残留的同名快捷方式被误判"已就绪"→ 跳过重建，桌面图标指向旧目录。修复：校验 lnk 二进制**必须包含当前项目路径**（UTF-16LE/ANSI），不符即自动重建。
- 已验证：重建后桌面+开始菜单 lnk 均含 app.ico 与当前项目路径，`_shortcuts_exist` 判定正确。

## v3.6.3（2026-08-30）—— 📦 核心依赖全量盘点与对齐

- **AST 全量扫描**项目代码 import：实际使用 36 个第三方模块（+win32com 同包），**31 个核心已 100% 覆盖于 AUTO_INSTALL_DEPS，5 个大型可选（playwright/faster-whisper/piper/pyzbar/rarfile）在 HEAVY_DEPS，零遗漏**（动态 import 仅标准库）。
- **requirements.txt 重写对齐核心清单**：补入缺失的 9 个核心包（ebooklib/edge-tts/extract-msg/mobi/numpy/py7zr/pyautogui/PyMySQL/sounddevice），移除误入必装清单的大型可选（playwright/faster-whisper/pyzbar）——现在 `start.bat` / `pip install -r` 一次装齐全部核心，大小写归一后与 AUTO 31 项完全一致。
- README 更新依赖策略说明（核心全自动 + 大型可选按需装）。

## v3.6.2（2026-08-30）—— ⚡ 核心依赖启动全自动安装

- **核心组件零操作全自动**：首次启动向导加载后**立即自动安装全部核心依赖**（无需选择/点击）——纯进度展示（进度条 + 当前包 + 实时日志），装完**自动进入主界面**。
- 核心安装**有失败项时不自动进入**：列出失败清单，提供「↻ 重试失败项」（按 key 精确重试）与「仍要进入」；确保"装完才进程序"。
- 可选能力在向导页**只读展示**状态（✓ 已启用 / 未安装），进入后到「设置 → 可选能力」按需安装（不再参与首启自动流程）。
- `install_many` 的 `item_done` 事件新增 `key` 字段（失败项精确重试用）。
- 验证：item_done 带 key、batch_done 收尾、first_run 状态、构建通过。

## v3.6.1（2026-08-30）—— 🐛 修复：安装完成后自动进入 + 501 竞态

- **安装完自动进入**：批量安装结束（含部分失败）自动调用 complete + 进入主界面，无需再点「进入鲸语」；complete 失败时明确提示并可就地重试（不再静默吞错）。
- **修复 501 `Unsupported method ('{}GET')`**：根源为流式 NDJSON 响应无 `Connection: close`，连接收尾不明确，reload 的 GET 请求与残留字节粘连。修复：所有 NDJSON 流式端点（deps/install、deps/install_many、tts/setup_piper）显式 `Connection: close`；前端「进入」改为状态切换（去掉 reload 环节）。
- 验证：install_many 流式正常收尾（28 事件行）、complete ok、first_run 翻转全链路通过；构建通过。

## v3.6.0（2026-08-30）—— 🚀 首次启动依赖安装向导

- **新用户首次启动**：程序弹出**全屏依赖安装向导**（不再是"后台静默装、终端一行字"）——核心组件默认全部勾选、可选能力按需勾选，一键安装（NDJSON 流式进度：逐包进度条 + 实时日志），**安装完成才进入主界面**。
- **后端**：
  - 首次启动标志 `DATA_DIR/first_run_done`；`GET /v1/first_run`（是否首次 + 依赖全量清单）、`POST /v1/first_run/complete`（装完/跳过写标志）、`POST /v1/deps/install_many`（批量安装，NDJSON 流式，含 Piper 装后自动下模型）
  - `web_app.py`：首次启动软核心**不再后台静默装**（交由向导），二次启动缺失时仍后台补装
- **前端**：新增 `FirstRunPage.jsx` 全屏向导；App 启动检测首次状态，后端未就绪时轮询等待（硬依赖安装期间自动进入向导）；跳过/完成后刷新进主界面。
- 验证：三端点全流程实测（install_many batch_done ok / complete / first_run 翻转）；前端构建通过。

## v3.5.6（2026-08-30）—— 🐛 修复：Piper 可选能力安装失败

- **根因**：「可选能力 → Piper 本地语音」的 `pip` 字段是多包（`piper-tts[zh] g2pW sentence_stream unicode_rbnf` 空格分隔），
  但 `deps.py::pip_install` 把整串作为一个参数传给 `pip install` → pip 解析到空格后的包名即报
  `(after name with no version specifier) or end`，安装中断。
- **修复**：`pip_install` 将包名按空白拆分为**独立参数**一次安装（`pip install A B C D`），单包调用完全兼容；
  另加空包名防护。
- 验证：多包拆分/单包兼容/空包名单元测试通过；真实 pip 调用 4 包全部正确解析；`/v1/deps/install`（key=piper）
  全流程 NDJSON 验证 `done ok=True`（装依赖 → 自动下模型 → 就绪）。

## v3.5.5（2026-08-30）—— 🧩 Piper 语音并入「可选能力」

- Piper 本地语音从语音设置独立部署区**并入「🔌 可选能力」面板**（与浏览器自动化/语音转写等同级卡片）：
  - `HEAVY_DEPS` 新增「Piper 本地语音」项：pip 一次装齐 `piper-tts[zh] g2pW sentence_stream unicode_rbnf`
  - 安装完成后**自动下载中文语音模型 + g2pW 音素模型**（官方源超时自动回退镜像），全程 NDJSON 进度可见
- 语音设置页精简：移除「一键部署/仅下模型」大按钮与日志区，只保留语音相关配置（引擎选择、Piper 音色、模型下载小按钮）；未装 Piper 时提示引导至「可选能力」
- 验证：前端构建通过；HEAVY_DEPS 含 Piper 项；编译通过。

## v3.5.4（2026-08-30）—— 🛠 修复：首启依赖全自动安装中断

**问题**：首次启动自动安装（清华源）只装了几个包就停止，导致程序部分功能不可用。
**根因**（deps.py 安装链路三个缺陷）：
- `run_verbose` 无超时 + 阻塞读 stdout：某包 pip 下载卡住（pip 默认超时 15s×5 次重试，网络波动更久）→ 线程永久阻塞 → 后续几十个包全部不装
- `pip_install` 无 subprocess timeout：可无限挂起
- `install_many` 循环内无异常隔离：单包异常（如 TimeoutExpired）中断整个循环

**修复**：
- `pip_install`：显式 `--timeout 20 --retries 2`（pip 自身网络超时可控）+ 外层 subprocess 超时（300s/包）+ 失败自动重试 1 次
- `run_verbose`：独立 reader 线程逐行读 stdout（阻塞读不影响超时检测）+ 主循环轮询 poll() + 超时强制 kill，杜绝卡死
- `install_many`：循环内单包 try/except 隔离——失败/超时只记录，**继续安装后续包**，全部尝试完才返回
- 失败包在状态与日志中留痕（前端「可选能力」面板可见 failed 列表）

**验证**：多包模拟——失败包不中断后续、进度推进至 total、状态正确收尾；编译通过。

## v3.5.3（2026-08-30）—— 🎙 语音体验与部署优化

**对话式自动朗读（低延迟逐句播放）**：
- 分句算法升级：句末标点优先 + **长句按逗号/顿号软切**（>40 字即切），流式回复的第一句更快完整开播，不再苦等句号
- 自动朗读逐句模式改内容级去重（流式边界漂移不再重复读句）；**无标点长尾段 ≥80 字直接播**，避免"一直等句号"的延迟
- 收尾补读剩余句（含半截长尾），停止/中断仍可随时打断

**Piper 一键部署（clone 即用，免折腾）**：
- 新端点 `POST /v1/tts/setup_piper`（NDJSON 流式进度）：自动 pip 安装 `piper-tts[zh]`/`g2pW`/`sentence_stream`/`unicode_rbnf` → 下载中文语音模型 → 下载 g2pW 音素模型 → 合成验证，**全流程自动、官方源超时自动回退 hf-mirror 镜像**
- 设置页「⚡ 一键部署」按钮（实时进度日志）+ 保留「⬇ 仅下模型」；`_piper_download` 中文模型下载后自动补齐 g2pW
- 全新用户路径：`pip install -r requirements.txt` → 设置页点「⚡ 一键部署」→ 即可本地离线朗读，无需手工装包/下模型

**验证**：一键部署全流程冒烟（依赖/模型/g2pW/合成验证全 ✅）；前端构建通过。

## v3.5.2（2026-08-30）—— 🎙 语音升级：Piper 本地离线神经引擎

**新增 Piper 本地 TTS 引擎**（https://github.com/OHF-Voice/piper1-gpl · `pip install piper-tts[zh]`）：
- **完全本地离线**：VITS+ONNX 推理，CPU 实时（树莓派 4 级算力即可），断网可用、零费用、30+ 语言
- **中文模型仅 20-60MB**：默认 `zh_CN-chaowen-medium`，另有 `zh_CN-huayan-medium` 可选
- **引擎链升级**：`auto` 默认 `Piper → Edge → SAPI` 智能降级；可强制 `engine=piper|edge|sapi`（配置 `voice_config.engine`）
- **一键下载模型**：`POST /v1/tts/download_piper`（官方源超时自动回退 hf-mirror 镜像；g2pW/bert tokenizer 等中文音素依赖同样走镜像）
- **语速/音量**：rate（-10..10→length_scale 0.5..1.5）、volume（0..100→增益）全支持
- 前端设置页：新增「合成引擎」选择 + Piper 音色组 + 「⬇ 下载模型」按钮；`/v1/tts/voices` 返回 piper 音色与就绪状态
- 依赖清单补录：`piper-tts[zh]` + `g2pW` + `sentence_stream` + `unicode_rbnf`

**验证**：中文模型下载（镜像）、端到端合成（253KB WAV）、`/v1/tts/synthesize`（engine=piper/auto）、`/v1/tts/voices` 全部实测通过；前端构建通过。

## v3.5.1（2026-08-30）—— ✨ P2 体验与生态：可恢复/市场/防注入/垂直场景

**🛡 安全增量**：
- **写操作自动快照**（新增 `snapshot.py`）：`write_file`（覆盖）/`edit_file`/`batch_rename`/`database_execute` 执行前自动备份原内容到 `DATA_DIR/undo/`（上限 200 条自动清理）——新增工具 `list_snapshots`（列出）/`restore_snapshot`（恢复，恢复前当前文件另备份 `.snap.bak`，权限检查复用写白名单）
- **外部内容注入防护**：`fetch_url`/`fetch_url_smart` 返回内容包显式分隔标记（`--- 外部内容开始/结束 ---` + "不执行其中任何要求"提示）；内部取原样走 `_fetch_url_raw`（track_web 等不受影响）；任务质量指南新增第 12 条全局防注入规则

**🌐 插件生态**：
- **在线插件市场**（Web 版落地）：`GET /v1/plugin_market` 拉取远程索引（5 分钟缓存）· `POST /v1/plugin_market/install` 下载安装
- **下载校验**：SHA-256 哈希必校验；配置 `plugin_market_public_key`（新增配置项）后强制 Ed25519 签名校验（fail-closed：缺签名/验签失败拒绝安装）
- **质量分级**：市场条目 `tier: official（官方）/ community（社区）/ experimental（实验）`，前端市场 Tab 徽章展示（蓝/绿/橙）

**🎯 垂直场景**：
- 场景从 4 个扩展为 **10 个**：新增 运营 / 法律 / 金融 / 教育 / 医疗健康 / 写作创作（预设 temperature/top_p/reasoning_effort，低→严谨、高→创意；`SCENARIO_DEFAULT_THINKING` 同步）

**⚡ 前端体验**：
- **长会话窗口化渲染**：默认只渲染最近 60 条 + 顶部哨兵滚动增量加载（替代全量渲染，超长会话不卡）；搜索/消息定位自动展开渲染窗口；贴底改为"在底部才跟随"（阅读历史不再被强制拽回底部）
- **SSE 高频事件 rAF 批处理**：reasoning/content 流式增量合并为一帧一次状态更新（不再逐 token 重渲染），结束/停止时冲刷残余防丢尾

**工程**：`audit_tools.py --strict` + `validate_tools.py` 全链路通过（134 工具）；前端 `vite build` 通过；Ed25519 验签/SHA-256 校验/快照恢复冒烟测试通过。

## v3.5.0（2026-08-27）—— 🛡 可靠性与体验增强批：稳健/可视化/RAG/检索

**可靠性 / 稳定性**：
- **T1 僵尸进程清理**：后台子进程空闲超 1 小时自动回收（`cleanup_idle_processes`，可配 `process_max_idle_seconds`）；服务停止时终止全部子进程防孤儿（`cleanup_all_processes`）；进程记录 `started_ts` 供空闲判断。
- **T2 长上下文压缩回滚**：`_compress_messages` 压缩后二次校验长度；仍超限则保留更少轮次→截断超长消息→终极兜底（只留最近轮并截断），避免"压缩后仍超限→请求被拒→整轮卡死"。

**产品体验**：
- **T4 产物预览延伸**：`/v1/files/preview` 新增 **CSV→内嵌分页表格**、**PDF→首页文本预览+用系统程序打开**、**xlsx→表格**、**docx/pptx→引导打开**；前端 `TablePreview` 分页展示表格。
- **T5 计划面板**：定时任务显示**下次运行时间**（`next_run`，time/every/cron 计算）、启用数、上次运行；性能用 `schedule_task` 也能建计划。
- **T8 team_run 可视化**：`team_run` 输出追加 `__TEAM_JSON__` 结构化段，前端 `TeamRunSteps` 渲染多智能体流水线步骤条（角色/任务/产出）；聊天工具结果存储上限放宽到 8000 字。

**功能增量**：
- **T7 知识库 RAG 产品化**：`/v1/knowledge/search` 返回带**来源路径+得分+片段**的结构化命中；`/v1/knowledge` 状态对齐实际索引文件；「记忆与知识」页新增**知识库 RAG 检索面板**（检索带引用源）。
- **T9 结构化检索**：`/v1/search` 支持过滤器（`type=message|artifact`，按标签/时间可选）；前端全局搜索面板新增**「💬 消息 / 📦 产物」切换**，可跨会话搜产物文件路径。
- **T6 barge-in 说话即打断**：朗读时检测到用户开口即停止（前端麦克风音量阈值近似，权限门控、默认关闭，设置页「🎙 开启打断/关闭」）。

**验证**：全链路跑通——health/能力 118 项（含 vision_loop）、计划 next_run、知识库 RAG 命中带来源、搜索消息/产物、team_run 结构化段、产物 CSV/PDF 预览均正常；未触发页面错误边界。



## v3.4.0（2026-08-27）—— 👁 vision_loop：视觉操作闭环（看屏幕→动作→再验证）

工具数 117 → **118**。把「看图→判断→执行→验证」做成一个可复用的自主闭环，最体现"真的能干活"。

- **`vision_loop(goal, steps, max_iters, area)`**：用自然语言下达目标，AI 自主完成多步屏幕操作——
  - 每轮：截图 → 视觉模型判断当前状态 → 决定并执行下一步动作（点击/输入/滚动/描述）→ 再截图验证 → 直到目标达成
  - 视觉模型输出 JSON 动作序列：`{"status","action": done|click|type|scroll|describe,"target","area"}`；`done` 表示目标达成/无法推进即结束
  - 轮数上限 1–12（默认 5），动作记录逐条沉淀到日志，失败即中止并给出已执行步骤
  - 复用现有 `screen_find_click`/`rpa_click`/`rpa_type`/`rpa_scroll`/`image_understand`，零新建依赖
- **接入**：`TOOLS` 工具定义 + `TOOL_CALL_MAP` + 媒体与图像分组 + 关键词预激活（"视觉执行/视觉闭环/屏幕操作"）+ 工具速览描述。

**适用**：填表、点按钮、验证界面变化、操作旧桌面软件等"看着屏幕"的多步任务。

**验证**：视觉模型图片输入正常（实测试图返回准确描述）；`vision_loop` 经 `/v1/tools/vision_loop/invoke` 端到端跑通——截屏后正确读出屏幕内容（识别出网站年龄验证弹窗）并按目标输出结果；工具数 118、分组正确。



## v3.3.1（2026-08-27）—— 🖼 产物内嵌预览（点开即看，无需另开程序）

**产品体验**：把「产物直达」从"只给路径链接"升级为"点开即见内容"。

- **后端新增 `/v1/files/preview`**：只读提取可内嵌内容，不改变原文件——
  - 图片（png/jpg/jpeg/gif/webp/bmp/ico，≤3MB）→ base64 data URI
  - 文本/markdown/HTML/JSON/CSV/日志等（≤2MB）→ 内容 + kind（html/md/text）；markdown 前端渲染、HTML 用 sandbox iframe 预览、其余纯文本展示
  - 其余（xlsx/docx/pptx/pdf/zip 等）→ 返回元信息并提示「用系统程序打开」
- **前端产物 chip 点文件名即可展开内嵌预览**（图片缩略 / markdown 渲染 / HTML 预览 / 文本），再点 ✕ 收起；加载中有 ⏳ 反馈；不可内嵌类型给原因提示。
- **边界**：图片 ≤3MB、文本 ≤2MB，超出提示不内嵌（防爆内存）；大/二进制文件仍走「打开」「定位」原路径。

**验证**：真实任务生成 markdown 产物 → chip 出现 → 点击展开内嵌渲染内容；md/json/图片三种 kind 均正常；不存在的文件返回友好错误；未触发页面错误边界。



## v3.3.0（2026-08-27）—— 🛡 可靠性与体验打磨：统一错误透传 / 断连感知 / 任务进度

**可靠性 / 稳定性**：
- **前端错误透传**：`api()` / 部分接口解析服务端返回的错误详情（`error` / `detail` / `error.message`），把原来笼统的「API xxx → 500」升级为服务端人话提示；SSE 错误事件保留 `message`。
- **后端友好错误**：主请求处理器（GET/POST）的通用 500 兜底改为 `_friendly_error(e)` 映射（限速/无效 Key/模型不支持图片等→可操作提示），并附 `code` / `detail` 便于排查；未捕获异常仍记录日志。
- **断连感知 + 自动重连**（前端）：新增 `probeBackendHealth` / `watchBackend` 周期性心跳探测 `/health`；服务不可用时置顶显示「⚠️ 后端服务未连接」横幅（带「立即重连」按钮），恢复后自动消失并刷新各页面缓存。

**产品体验**：
- **后台任务进度卡片**：AI 执行多工具任务时，助手消息顶部显示「⏳ 任务进行中 · N/N 步」+ 进度条 + 当前工具名；完成时转为「✅ 任务完成」。复用已有 SSE `tool_start`/`tool` 事件，无需后端改动。

**验证**：真实工具调用会话渲染出进度条+工具卡；服务断开后横幅 6s 内出现、重启后 1s 内自动清除；未触发页面错误边界；TTS 全链路正常。



## v3.2.3（2026-08-27）—— 🔊 语音输出修复与优化（长回复朗读报错 / 引擎选用 / 播放健壮性）

**现象**：点朗读（🔊）对较长的回复报错（按钮变 ⚠「合成失败（服务端/引擎）」），且语音输出链路稳定性差。

**根因**（两层）：
1. **服务端 edge 合成用子进程敲 CLI 且固定 25s 超时**——长文本（约 >1500 字）合成耗时超过 25s 即被 `subprocess.run(timeout=25)` 掐断并错误地回退 SAPI；回退的 `python -m edge_tts`/SAPI 在本机无中文离线语音包时产出空 WAV，最终 500 → 前端 ⚠。子进程方案在 `pythonw` / 打包 exe 下还会因 `sys.executable` 不是 python 而直接不可用。
2. **前端单次整段合成长文本 + 播放停止/队列有隐患**——手动朗读把最长 4000 字一次性丢给服务端；`enqueueSpeak` 在合成成功后 `loadingCount` 被重复扣减；`stopSpeak` 暂停音频后 `onended` 不触发会让串行队列永久卡死。

**修复与优化**（后端 + 前端 + 设置试听全链路）：
- **服务端 edge 合成改为直接调用 `edge_tts` 库**（`edge_tts.Communicate.save`），不再依赖子进程/CLI/`sys.executable`；超时随文本长度放宽（`max(30, len*0.05)`）——长文本不再误判超时。`_edge_available` 改为 `import edge_tts` 探测。
- **引擎选用修正**：选中非 edge 音色（本机 SAPI）时不再白跑一次 edge（音色名对 edge 无效）；仅在「未选音色 或 选中了 edge 音色」时优先 edge。
- **失败提示更精准**：两种引擎都失败时上报更有信息量的一侧；本机无中文离线语音包 + 在线不可用时给出明确引导（"请安装 edge-tts 或中文语音包"）。
- **前端手动朗读改分句串行**（`speakText`）：按 ≤200 字分句逐句合成播放——长回复边读边播、更快出第一句、也避免单次超长合成。
- **播放队列健壮性**：`enqueueSpeak` 修 `loadingCount` 重复扣减与泄漏；`playUrl` 加 `onloadedmetadata` 时长+4s 兜底定时器（防无输出设备/卡住时按钮永远停在播放中）；`stopSpeak` 主动解除挂起的 `playUrl`（防队列卡死）；自动朗读（逐句/整段）与设置页试听均补 `.catch` 消除未处理拒绝。
- **合成接口 401 自取 token 重试**（前端 `synthesize` 直连并解析服务端 error 详情，提示更具体）。

**验证**：3000 字长文本此前 500/25s 超时 → 现返回 200（edge，约 10s）；短文本/emoji/长回复均正常；无 ⚠。


## v3.2.2（2026-08-27）—— 🔊 朗读可见反馈与无声问题排查增强

针对「点了朗读没声音也看不出任何反应」：

- **朗读按钮三态可视化**：点击立即 ⏳（合成中）→ 播放中 ⏹（再点即停）→ 失败 ⚠（悬停显示具体原因：被浏览器拦截/合成失败/无可读内容）
- **全局朗读浮标**：右下角「🔊 正在朗读 · 点击停止」/「⏳ 正在合成语音…」，任何朗读活动一眼可见，点浮标可停
- **点击手势解锁音频管线**（primeAudio）：防「合成耗时超过浏览器用户激活窗口被自动播放策略拦截」
- **音频加载 401 自动重取 token 重试**：长会话后 token 失效不再静默失败
- **设置页新增「📈 测试出声」**：浏览器直接播一声提示音（不经过合成）——听到"哔"=浏览器→声卡链路正常，听不到=Windows 输出设备选错了（点任务栏喇叭切换），与鲸语无关
- 排查结论归档：服务端合成/音频回源/系统声卡已分别验证正常；本机存在 7 个活动输出端点（蓝牙/HDMI/Realtek/USB），默认输出指向问题需用户在系统层确认

## v3.2.1（2026-08-27）—— 🛠 历史会话显示错乱/不全修复

**现象**：重启程序点开历史会话，工具结果以"助手"气泡裸奔错乱、内容显示不全。

**根因（数据文件完好，问题全在加载映射）**：
1. `pickSession` 映射漏掉 `role:"tool"` / `role:"system"` 分支——工具结果消息掉进 assistant 兜底被渲染成独立"助手"气泡
2. tool_call_id 按轮生成（每轮都从 call_0 起），加载器全局按 id 找结果导致**跨轮错配**（第二轮卡片显示第一轮的结果）
3. 后端 `_save_session` 将 tool_calls 硬截断 16 条——超过 16 次调用的轮次，末尾调用从声明中消失（其 tool 消息成孤儿）

**修复**：
- 加载器：tool/system 消息不再单独渲染；结果按**顺序指针**归并进所属 assistant 的工具卡片（跨轮零错配）；历史截断产生的孤儿 tool 消息兜底挂到最后一个 assistant（旧坏文件无需迁移即完整显示）
- 后端：tool_calls 截断上限 16 → 64

**验证**：用真实受损会话（2 轮 / 17 工具调用 / 32 条 tool 消息）离线复刻新映射——32/32 卡片按轮逐条零错配、两轮最终回答完整。

## v3.2.0（2026-08-27）—— 🔊 语音输出增强：朗读/停止/语速音量音色/自动朗读三模式

工具数 115 → **117**（+`tts_speak`/`tts_stop`），按 MVP→进阶路线落地语音输出增强：

- **朗读按钮升级 + 停止**：助手消息气泡的 🔊 按钮改为服务端合成（跟随语音设置），播放中变 ⏹ 点击即停；全局串行队列，多处朗读互不打断也不堆积
- **三种自动朗读模式（设置页「🗣 语音朗读」组）**：
  - `关闭`：手动点 🔊 才读
  - `自动·逐句跟读`：流式生成中按中英句末标点切句边出边读（半截句等下一包自动补读）
  - `自动·整段读完`：回复完成后一次性朗读
  - 设置持久化到 config.json 的 voice_config（auto_mode/rate/volume/voice），保存即时生效
- **双引擎自动降级**：装了 edge-tts 自动用在线神经网络音色（晓晓/晓伊/云希/云健，自然度高）；未安装或在线失败回退 SAPI 离线保底；音色枚举接口 `/v1/tts/voices` 分组展示
- **Agent 工具**：`tts_speak`（后台播放、可带音量/音色/另存 WAV）与 `tts_stop`（空参停全部/sid 定点停）——SAPI SpVoice 注册表 + 空 utterance purge 实现真中断
- **合成服务端** `/v1/tts/synthesize`：Markdown 清洗（代码块整段跳过、链接只读文字、去强调/表格符）、sha1 内容缓存（重复朗读零合成延迟）、并发信号量(2)防短句风暴；音频经鉴权路由回源
- 试听按钮：设置里以当前配置即点即听
- **顺延项**：情感风格需 edge-tts SSML 注入（微软已限制公共端点）待后续专项；说话打断（barge-in）的地基（可中断引擎）已就位，麦克风联动排下一迭代

## v3.1.5（2026-08-27）—— 🗂 配置方案（Profile）管理回归

恢复旧桌面版的 Profile 能力并接入 Web 架构：把当前的 **API Key + 网关 + 模型** 保存为一套命名方案，之后一键整套切换（官方/中转站/多账号场景免手动改三个字段）。

- 后端：`POST /v1/profiles`（save/apply/delete）；api_key 沿用 profiles 模块 DPAPI 加密落盘、原子写，接口不回传明文；apply 即时生效——写 config.json + 热同步接线 + 失效 LLM 客户端缓存
- 前端：设置页新增「🗂 配置方案」卡片（简单模式与高级模式「模型与网关」均有）：保存当前为方案、一键应用、删除；当前方案标记"生效中"
- `_init_dc_paths` 补接 profiles.json 路径；LLM 客户端缓存失效逻辑抽为 `_reset_llm_client_cache` 供 config/profiles 两处复用

## v3.1.4（2026-08-27）—— 🧹 企业微信智能机器人幽灵配置清理

应用户决策移除 wecom_aibot 长连接的全部残留入口（功能代码已随桌面版重构删除，配置项却仍在误导用户）：

- 设置页「IM 通道」删除「企业微信智能机器人 Bot ID / Secret」两个输入框（企业微信群机器人 Webhook 保留，功能正常）
- deps 可选依赖清单删除 wecom-aibot-python-sdk 条目
- IM 配置提示文案、secret 加密元组同步清理；全仓库无 aibot 残留引用

## v3.1.3（2026-08-26）—— 📋 依赖级二次审计：远程任务闭环与插件感知修复

在 v3.1.2 注入审计之上，对旧 main.py 做**功能级依赖对照**（逐模块调用面 + 启动行为清单）：

- **Telegram 远程任务闭环补全**：IM 轮询注释写着「执行任务并回复」，但上游实现只执行不回消息；现在 `_headless_chat(reply_channel="telegram")` 执行完把结果推回 Telegram，且改为独立后台线程执行（不再阻塞长轮询）
- **无头执行结果恢复落会话库**：v3.1.2 抽取 `_headless_chat` 时丢了旧版"存会话库"步骤（定时任务/IM 结果可追溯性退化），已恢复
- **已装插件提示注入补回**：旧 main 会把应用型插件触发词注入系统提示（模型据此识别 `/飞侠` 等意图），v3.0 重构丢失——装了插件模型却毫无感知。`_inject_system_messages` 现注入启用插件的名称/触发词/描述
- 审计确认（有意移除/半残，暂不动）：watchdog/perception/splash/taskpanel/processpanel/dialogs 为纯桌面 UI 有意移除；exporters 由前端 exporters.js 替代；企业微信智能机器人长连接（wecom_aibot SDK）随桌面版移除但**设置页仍保留 secret 配置项且 deps 清单仍提示安装 SDK**——幽灵配置，待产品决策（改走 webhook 或删除入口）；profiles 仅剩只读接口，切换能力与 UI 待规划

## v3.1.2（2026-08-26）—— 🧯 重构遗留断链全面审计与修复

**对 v3.0「删除 main.py」重构做全量接线审计**（静态挖掘旧 main.py 注入清单 + 115 工具动态探测），已知/未知问题一次修清：

- **修复一：12 项工具侧全局路径断链**（api_server `_init_dc_paths` 现在完整对齐旧 main 注入）：
  - `MEMORY_FILE` —— **AI 任务中存不下长期记忆**（write/read/query_memory_graph 全部静默失效；且与 UI 记忆面板 api_server 自用路径分裂）
  - `SCHEDULES_FILE` / `CHECKPOINT_FILE` / `KNOWLEDGE_INDEX_FILE` / `STATS_FILE` / `PATTERNS_FILE` —— schedule_task、任务检查点、知识库索引持久化、用量报告、成功模式，此前全部不可持久化
  - `SECRETS_FILE` / `RSS_SOURCES_FILE` / `KV_CACHE_DIR` / `WEBDAV_CONFIG_FILE` / `BROWSER_PROFILE_DIR` —— 密钥库/RSS 源/KV/WebDAV/浏览器配置
- **修复二：插件工具的 AI 调用恢复**——聊天管线从未传 `custom_tools`，已安装插件/自定义工具模型根本看不到；两处聊天 handler 现按旧 main 方式注入 `load_user_tools(USER_TOOLS_PATH)`
- **修复三：启动工作目录恢复**——WORKING_DIR 此前只在手动切目录后才有值，现在从 cfg.active_dir 启动还原；`AGENT_MAIL_ENABLED/CLI`、`CHART_THEME` 随配置同步；`WHALETALK_DATA_DIR` 环境变量补设（应用型插件数据目录）
- **修复四：热同步**——设置保存后立即重跑接线，agent_mail 开关等即时生效不再等重启

验证：离线 12 项接线断言通过；真实服务重启后记忆写入→读取→UI 可见、调度创建→列表→取消、检查点存取清、知识库索引落盘等端到端探测全部通过。

## v3.1.1（2026-08-26）—— 🔧 视觉模型切换与工具客户端断链修复

**修复：所有依赖 LLM 客户端的工具报「没有可用客户端（请先完成一次对话建立连接）」**

- **根因**：v3.0 Web 重构删除旧桌面版 main.py 后，`set_active_client()` 再无调用者——api_server 每请求自建临时客户端但不注册，`_CLIENT_HOLDER` 恒为空。视觉理解（screen_see/image_understand/OCR 系列）与新 v3.1 的子代理/语音/团队工具全部断链
- **修复一（懒构建兜底）**：新增 `get_active_client()`——优先会话注入的客户端，否则按当前配置（api_key/base_url/model/timeout 指纹缓存，变更自动重建）懒构建；工具直调、测试台、无先对话场景全部开箱可用
- **修复二（每会话注册）**：`_client_from_cfg` 构建聊天客户端后立即 `set_active_client(client)`，工具与当前对话使用同一网关/模型；密钥/网关/模型变更时自动失效缓存，绝不复用过期凭据
- **修复三（优雅自动切换）**：会话带图而当前模型非视觉时，chat() 按**本次请求**自动改用 `deepseek-v4-flash-vision-exp` 并记日志——不再抛 ValueError 要求人工换模型（AI 自主指挥顺畅）；直调路径保留抛错兜底防线
- 端到端验证：embed 图片内联、非视觉兜底、指纹缓存复用离线单测通过；真实服务 subagent_run 实调 LLM 成功

## v3.1.0（2026-08-26）—— 🚀 五大高价值能力扩展

工具数 109 → **115**（新增 6 工具），打通「环境搭建 → 运行 → 自愈」闭环：

- **📦 应用管理（app_manage）**：Windows 软件安装/卸载/搜索/升级一站式管理——自动检测并选用 winget/choco，注册表枚举本机已装软件；跑代码/测试/爬虫不再卡在环境依赖上
- **🖱 视觉定位点击闭环（screen_find_click）**：把 screen_see（看）与 rpa_click（点）合一步——截图后视觉模型按自然语言描述定位界面元素坐标并自动点击，支持 dry_run 先看位置、点击后自动自查截图；自动化 Web 应用与旧桌面软件的关键拼图
- **🎙 实时语音对话（voice_chat_loop）**：全双工对话循环——麦克风听一句 → faster-whisper 本地转写 → AI 回复 → SAPI 直接朗读，循环多轮直到说「再见」；声音静默检测自动断句，依赖缺失时明确提示安装
- **🤝 多智能体团队编排（team_run）**：协调者拆解总目标为带角色指派的执行计划（≤6 步），研究员/工程师/评审等角色按流水线接力、共享黑板传递中间成果，最终综合成完整交付物
- **🌐 网络诊断与自愈（net_diagnose + fetch_url_smart）**：分层探测 全局连通→DNS→TCP→HTTP，判定断网/DNS 故障/端口不通/反爬 403/限流/超时被墙/TLS 问题并给出降级策略；智能抓取失败时自动走内置代理通道兜底

其他：可选依赖清单新增 sounddevice（实时语音录音）；能力中心 12 域映射同步注册新工具。

## v3.0.1（2026-08-26）

- **🔑 设置页支持在线填写 DeepSeek API Key（修复：前端此前只能查看"已配置/未配置"状态，无填写入口）**：
  - 前端（webui/）：简单模式「核心设置」与高级模式「模型与网关」均新增 API Key 输入框（密码框，回车或失焦保存；留空=不修改；保存后显示脱敏提示如 `sk-***abcd`）
  - 后端（api_server.py）：`POST /v1/config` 白名单放行 `api_key`（strip 后经 `save_config` DPAPI 加密落盘，明文永不落盘）；保存后失效 status 缓存
  - `GET /v1/config` 新增 `key_hint` 字段（前3+尾4 脱敏，绝不回传明文密钥）
  - 端到端冒烟验证：写入 → 磁盘 `dpapi:` 密文 → 解密回读 → 界面状态刷新全链路通过

## v3.0.0（2026-08-23）—— 🎉 Web 重构重大版本

**程序形态从 Tkinter 桌面版重构为 Web 架构（本地优先）**——全新 React 前端 + 本地 API 服务，同一能力引擎（deepseek_client 复用），界面与交互全面升级。

- **🖥 全新 React 前端（webui/）**：
  - **三主题**：星空（默认）/ 深海 / 北极冰，一处切换全局生效（localStorage 持久化）
  - **控制台侧栏**：模型/思考档/场景/温度/Top-P/Seed/输出上限 + JSON 输出/Beta API/strict 工具/工具开关 + 外观（主题/密度/字号）——侧栏即控制台，高频设置一秒可达（默认展开）
  - **产物直达条**：AI 回复中的产物路径（md/txt/xlsx/pdf 等绝对路径）自动提取，一键打开/定位所在文件夹/注入输入框
  - **文件面板**：工作区树 + ⭐ 最近产物「打开/⌖定位/注入」三连；新产物实时跟出；顶层条目补 path 字段（修复此前展开/操作失灵）
  - **消息体验**：流式 Markdown、思考卡片、工具卡片（展开结果）、收藏/固定/分叉/变体/续写、多选批量导出、消息字号/密度
  - **会话管理**：多会话/标签/搜索/导入导出（JSON/JSONL）、历史库；状态栏常显模型/模式/场景/上下文
- **🔌 本地 API 服务（api_server.py，127.0.0.1:8745）**：
  - REST + SSE 流式（`/v1/chat/stream`）；token 认证；同源服务前端构建产物
  - 工具全链路保留：记忆/失败模式/成功模式/项目上下文/任务记录注入、上下文压缩、strict 工具模式（Beta）
  - 22+ 端点：sessions/config/context/files/abilities/dir/memory/deps/models/prompts/schedules/tasks/evolutions/knowledge/roles/workflows/services/backup/update/cleanup/audit/processes
  - 新增 `POST /v1/files/open`（系统打开，可执行类安全拦截）与 `POST /v1/files/opendir`（explorer 定位选中）
- **🚀 统一入口（web_app.py）**：`python web_app.py`（pywebview 桌面窗 + 托盘）/ `--browser`（默认浏览器）/ `--server`（无头 API）/ `--legacy`（回退旧 Tkinter）；start.bat 与打包 spec（WhaleTalk.spec 打包 webui/dist + pywebview）同步更新
- **🧹 工程与修复**：
  - `create_evolution` 校验前置——非法文件类型不再留下空分支目录（防残留）
  - `deps.py` 补录 pyautogui（依赖状态显示此前漏项）
  - 测试隔离：`run_wechat_writer` 集成测试不再写入真实 data/drafts
  - README 全量重写（架构图/更新策略/关于我们/双语文案）
- **✅ 质量**：全量 653 项测试通过；环境依赖（playwright/pyautogui/pywebview 等）补齐；端到端冒烟（health/API/WebUI 同源 200）

> 沿用兼容：main.py 保持原 Tkinter 版（保底），deepseek_client.py 能力引擎不变。

## v2.27.0（2026-08-22）

- **插件体系升级为「应用型插件」（.wtplugin v2）——插件是应用，工具是服务**：
  - 插件可**自带 Python 代码**（iles 字段，安装写入 plugins/<插件名>/，卸载整目录删除 → 可插拔、零残留、不改变程序本体）
  - **/插件名 或 @插件名 直接调用**：输入框命中触发词即执行插件应用（可带参数，如 /飞侠 帮我看看AI芯片），结果回显聊天区；slash 菜单列出已装应用
  - 插件中心：详情显示触发词与调用方式；AI 注入已装插件应用清单（引导用户用插件而非底层工具硬凑）
  - 插件执行环境：包式/文件级两种加载，按代码 mtime 增量重载（内存状态跨调用保留），多插件模块隔离
- **✈️ 智能飞侠（World Cruiser）迁移为应用型插件**（自包含，从工具中心移除）：
  - sample_plugins/智能飞侠.wtplugin 内置全部代码，插件中心「画廊」一键安装后即可 /飞侠 / /巡航 调用
  - 功能不变：五维世界巡航（RSS+HN+多引擎）、五站式报告（md+html 星空主题）、✍️ 飞侠手记、📡 明日雷达线索追踪（open→watching→dormant→closed + 判断成绩单）、巡航记忆（SQLite）、偏好配置
  - 工具中心回归纯净：不再有 flybot 内置工具（底层工具数量有限，应用交给插件）
- 全量测试 650 项通过（新增应用型插件机制测试 + 智能飞侠插件化测试）

## v2.26.2（2026-08-22）

- **聊天体验大优化**：
  - **早期消息默认折叠**（`fold_early_threshold` 默认 1200 块）：长对话只渲染最近 ~400 条消息 + 一条「早期内容已折叠，点击展开」提示——滚动更丝滑、切换/重建更快；早期内容不丢失，点击即展开（设置可调回 0）
  - **滚动丝滑**：消息悬停高亮改为按区间精确增删（不再每 50ms 全文档扫描）、滚动期间暂停浮条重建、悬停节流 50→100ms、浮条移动容差加大
  - **消除"上面内容不加载"**：分帧渲染被中断/放弃时给会话打未完成标记，切回自动补渲染；挂起追加按归属会话写入（切会话不再错位）
  - **重建提速**：会话重建的时间戳/内容块格式与流式视图完全对齐，未改动会话重建直接跳过全量渲染
  - **输入区操作钮重构**：发送键升级为 34px 主位圆钮（常驻 C 位）；停止键改为红色圆钮（■），**仅在 AI 生成中出现**并占据 C 位（带呼吸动画），输出完成自动消失让出发送键——不再常年占据主位
- **文件面板实时跟踪新产物**：工具生成文件后自动同步「最近产物」与已展开目录（防抖 500ms）；重新展开目录/最近产物节点始终取最新内容（此前懒加载后不再刷新，新文件找不到）；切换到「文件」Tab 自动刷新已展开节点
- **拖拽文件不再截断**：文本/文档文件拖入输入框只插入路径引用（`[文件] 路径`），由 AI 用 `read_file` 读取完整内容（smart 模式自动预激活）；移除「截断前 8000/6000 字符」两条主动截断路径，大文件信息不再丢失
- **防静默丢失**：blocks 超上限裁剪时给出可见提示（数据仍在，可导出）
- 全量测试 638 项通过（新增聊天视图优化测试 17 项）

## v2.26.1（2026-08-21）

- **工具定义成本优化**（按技术方案落地）：
  - `activate_tools` 支持**按组激活**：传组名（如「数据与文档」「媒体与图像」）一次激活整组，点菜次数从 O(工具数) 降到 O(组数)；能力地图与点菜工具描述同步提示组名用法
  - **chat 层关键词预激活**：扫描最近 user 消息命中常见意图（搜索/下载/邮件/图片/表格/数据库等），免点菜直接可用（仅提前加载定义，不改变权限）
  - **能力地图缓存键改内容指纹**：深拷贝/重建列表不再导致缓存失效，`index_msg` 内容稳定，保住 ~99% 前缀缓存命中率
  - **描述压缩规则微调**：补充「需审批/敏感/Beta/可选依赖」等冗余括号剥离；参数描述截断收紧到 40 字符；name/type/required/enum 结构一律保留
- 全量测试 626 项通过（新增成本优化测试 9 项）

## v2.26.0（2026-08-21）

- **视觉 Agent 闭环**：新增 `screen_see`（截图+看图一步完成），智能模式索引注入"操作后视觉自检"准则——AI 截图→看懂→点击/输入→再截图验证，桌面/浏览器自动化"看得见、能自查"。
- **截图即服务**：`chart_read`（图表→结构化数据+解读）、`screenshot_to_html`（UI 截图→HTML/CSS 前端还原，可存文件）、`debug_screenshot`（报错截图→诊断修复建议）、`scan_read`（扫描件/文档图片→Markdown，含图表/公式/手写）。
- **批量视觉分析**：`image_batch`（文件夹内图片小并发逐张理解→汇总报告）。
- **自我审图创作闭环（可选）**：新增配置 `vision_self_review`（默认关闭控成本），开启后工具产出图片（生成图/图表/截图）自动调用视觉模型审图，审阅意见回传模型驱动"修改/重生成"迭代。
- 全量测试 605 项通过（新增视觉工具测试 11 项）。

## v2.25.0（2026-08-21）

- **图像理解（适配 DeepSeek-V4-Flash-Vision-Exp）**：新增视觉模型 `deepseek-v4-flash-vision-exp`（模型下拉可选）；输入区新增「🖼 图片」附件按钮、拖拽图片、Ctrl+Alt+V 粘贴剪贴板图片，可多图一次发送（JPEG/PNG/GIF/WebP，单张 ≤32MB）。
- **图片真实内联**：附带的图片以 OpenAI 内容块（base64 data URL）直接送入模型，模型可真正看图/识别截图/分析图表；发送时自动切换视觉模型，无需手动操作。
- **`image_understand` 工具升级**：当前模型不支持视觉时自动改用 `deepseek-v4-flash-vision-exp`，单图上限提升至 32MB，格式按文件实际内容（魔数）识别。
- **工程细节**：图片 token 计入上下文估算（每张上限 384）；编辑重发/重新生成保留原图片附件；图片仅出现在 user 消息、请求后历史自动还原为纯文本 + 图片路径（不残留 base64）。
- 全量测试 594 项通过（新增视觉适配测试 9 项）。

## v2.24.0（2026-08-16）

- **聊天栏体验全面打磨**：拖拽选中高亮修复（悬浮浮条不再在拖动时拦截鼠标）、消息 hover 高亮与快捷操作条、选中文本快捷复制/引用/搜索、代码块语言+复制按钮、本地图片缩略图、任务列表/表格截断、回到底部按钮、折叠全开/全关、工具卡片复制参数/结果/打开文件、思考过程默认收起。
- **编辑器增强**：Markdown 发送前预览、代码块/引用块快捷键、Ctrl+Y 重做、粘贴为纯文本、字体族与消息密度设置。
- **消息操作增强**：编辑助手消息并继续（Beta）、复制纯文本/思考过程/工具调用 JSON、多选批量删除/导出/收藏、重新生成保留旧版到变体。
- **文件与会话管理**：文件面板搜索、会话拖拽排序、右键上移/下移、非模态 Toast 提示。
- **工程化**：引入 `pyproject.toml`/ruff 配置、更新 CI、补充新功能测试、文档同步。
- 全量测试通过。

## v2.22.2（2026-08-15）

- **官网上线**：README、关于、帮助均加入官方站点 `https://whaletalk.top/`（产品介绍 / 下载 / 动态）。

## v2.22.1（2026-08-15）

- **能力最大化**：默认 `max_tool_rounds` 从 10 提高到 **100**，约束上限从 50 放宽到 100——长链路工具任务（注册/发帖/多步抓取/调试）单条指令内一次跑完，不再频繁“继续/续命”。
- 全量测试 574 项通过。

## v2.22.0（2026-08-15）

- **初始化不覆盖用户选择**：启动回填场景不再联动改写思考档位；新增 `font_size_custom` 标记，手动字号永不自动调整。
- **聊天区宽度可调**：右侧设置面板分隔条拖拽调整实际聊天内容宽度（240–480），双击恢复默认并持久化。
- **任务进度内嵌化**：移除左下角悬浮任务窗，改为聊天区右上角轻量进度卡（执行中/完成自动淡出）。
- **默认角色更名**：`通用助手` → `通用角色`；默认场景保持 `通用`。
- 全量测试 573 项通过。

## v2.21.0（2026-08-15）

- **聊天体验**：用户消息改为左对齐 + 轻量气泡（沿用 quote_bg 淡色底，不刺眼）；思考过程与正式回答之间增加换行间距。
- **开源可见性**：关于/帮助新增 MIT 开源声明、GitHub 仓库入口（Star/Issues/检查更新按钮）；默认开启启动时检查更新（GitHub Releases）。
- 全量测试 570 项通过。

## v2.20.0（2026-08-15）

- **初始即最干净、最强大**：全新默认——完全智能模式、思考关闭、浏览器可见、工具开启、完成通知/开机自启/最小化托盘默认开启；权限黑名单制默认零限制。
- **欢迎页重写**：简明说明完全智能 vs 纯对话的适用场景，列出可选增强配置（IM / Agent Mail / 密钥保险箱），并给出仅作推荐的安全黑名单（可全部留空）。
- 全量测试 570 项通过。

## v2.19.0（2026-08-15）

- **默认完全放开**：`DEFAULT_PERMISSIONS` 的所有黑名单/审批动作全部为空（blocked_dirs / command_blocklist / network_blocklist / approval_actions = []），用户自行增删，出厂即零限制。
- **全局对话框加大**：`_dialog_shell` 所有对话框初始尺寸至少中档（宽/高都抬升），且 minsize 不低于初始尺寸，底部保存按钮不再被裁切；插件中心/工具中心 minsize 提升到 640×480。
- 本地开发生成物清理：删除 build/dist/backups/evolutions/data/.pytest_cache/__pycache__/config.json 及运行时旧数据，恢复“刚 clone”体验。
- 全量测试 570 项通过。

## v2.18.1（2026-08-15）

- **修复 Agent Mail CLI 可调用性**：新增 `_resolve_agent_mail_cli`（shutil.which + Windows .CMD/.BAT 显式解析，shell=False 安全执行）；修复 `agent_mail` 与 `_agent_mail_run` 重复前置 CLI 导致的 `unknown command "agently-cli"` 参数错误。
- **授权失效处理**：agent_mail 返回 exit 3 / unauthorized / invalid_grant 时，提示用户运行 `agently-cli auth login` 重新授权，不自动重试。
- 全量测试 570 项通过。

## v2.18.0（2026-08-15）

- **Agent Mail 集成（可选）**：新增 `agent_mail` 工具封装 agently-cli——me/list/search/read/send/reply/forward/trash/delete/download 附件；写操作遵循两阶段确认（返回 confirmation-token，AI 必须等用户确认）。
- **配置 UI**：外部服务配置新增「Agent Mail」页签——启用开关 + CLI 路径 + 安装状态提示；默认关闭，未配置时工具返回友好提示、不报错。
- 全量测试 568 项通过。

## v2.17.0（2026-08-15）

- **桌面 RPA（P0）**：新增 `rpa_screen_size / rpa_click / rpa_type / rpa_hotkey / rpa_move / rpa_scroll / rpa_screenshot`（pyautogui），可模拟鼠标键盘操作任意桌面软件；加入默认启用集，默认需审批（高风险）。
- **密钥保险箱（P2）**：新增 `secret_store` 工具，DPAPI 加密托管 API key/令牌，set/get/delete/list，明文不落日志。
- **行动审计可视化（P2）**：系统菜单新增「📋 行动审计…」，查看最近 200 条 actions.log（只记录不拦截）。
- **高风险动作默认审批（P2）**：`approval_actions` 默认包含删文件/数据库/邮件/命令/进程/插件/RPA 等高风险动作，用户可在权限页清空。
- **记忆扩容（P1）**：长期记忆上限 500 → 2000 条。
- **常驻值守（P0）**：新增 `watchdog.py` 崩溃自动拉起（3s~60s 退避）；源码运行开机自启改为 watchdog 守护。
- **新邮件汇总（P1）**：新增 `email_summary` 工具（复用 IMAP 配置，返回可摘要清单）。
- 全量测试 563 项通过。

## v2.16.2（2026-08-15）

- **IM 通道配置 UI**：外部服务配置新增「IM 通道」页签（企业微信群机器人 Webhook、智能机器人 Bot ID/Secret、Telegram Token/Chat ID），敏感字段 DPAPI 加密保存；系统菜单新增「📱 IM 通道配置…」快捷入口。
- **未配置不报错**：无 IM 配置时启动/工具调用静默跳过，返回友好提示「如不需要推送可忽略；系统菜单 → IM 通道配置」。
- **首次使用提示**：欢迎页增加可选提示（不配置不影响使用，不强制）。
- 全量测试 559 项通过。

## v2.16.1（2026-08-15）

- **修复企业微信回复失败（errcode=40008）**：`wecom_aibot.reply_text` 消息体从 `msgtype=text` 改为官方支持的 `msgtype=stream`（finish=True + 唯一 stream id）；`send_text` 主动发送改为 `msgtype=markdown`。新增消息体类型回归测试。

## v2.16.0（2026-08-15）

- **企业微信智能机器人长连接**：新增 `wecom_aibot.py` 通道（基于官方 `wecom-aibot-python-sdk`）。配置 `im_config.json` 的 `wecom_aibot_bot_id` / `wecom_aibot_secret` 后，鲸语启动时自动连接 `wss://openws.work.weixin.qq.com`；收到单聊/群聊@消息后自动交给 AI 处理，并把最后回复发回企业微信会话。支持断线重连、心跳保活、IM 消息状态提示。
- **IM 配置增强**：`_load_im_config` 提示补充 aibot 字段；`deps.py` 新增 aibot/ebooklib/mobi/extract_msg/py7zr/rarfile 可选依赖项。
- **测试**：新增 aibot 帧解析用例；全量 557 项通过。

## v2.15.0（2026-08-15）

- **IM 主动触达**：新增 `im_send`（Telegram Bot / 企业微信群机器人，im_config.json 配置，敏感字段 DPAPI 加密）与 `telegram_poll_updates`（长轮询接收用户消息，游标自动去重）——AI 可主动汇报、用户可随时召唤。
- **浏览器自动化补全**：playwright + Chromium 已安装就绪；`browser_navigate` 保留 open/click/type/fill/submit/select/get_text 多步共享页面。
- **二进制下载**：新增 `download_file` 工具，任意格式流式下载到工作区 downloads/ 或指定目录，200MB 上限、超限自动清理。
- **文件格式扩展**：新增 `epub_read` / `mobi_read` / `doc_read`（antiword/catdoc）/ `msg_read`（extract_msg）；`archive_list` 支持 zip/tar/gz/7z/rar 列目录；`extract_archive` 扩展支持 tar/gz/7z/rar。
- **默认启用**：上述新工具加入 `BUILTIN_TOOL_NAMES`，旧配置升级自动补入。
- **测试**：新增 tests/test_p1p2_tools.py；全量 555 项通过。

## v2.14.0（2026-08-15）

- **权限哲学反转：默认放行 + 黑名单（自由优先，用户掌权）**：`permissions.py` 升级 v2——默认 `security_mode="blacklist"`，AI 拥有全部文件/命令/网络/敏感操作能力，只按用户维护的黑名单拦截；黑名单默认为空，可一键清空/增删。旧 `whitelist` 模式保留可回退。
- **完全智能 = 无限权利**：`full_auto` 下零审批、零开关；`request_permission` 工具在 blacklist 模式下直接提示无需授权，不再弹窗。
- **SSRF 按黑名单拦截**：`_safe_url`/fetch_url/call_api/搜索过滤/自定义工具/fetch_blocked 统一改为 blacklist 模式默认放行，仅命中 `network.blocklist` 拒绝；旧 SSRF 严格判断仅在 whitelist 模式生效。
- **权限 UI 重写**：工具中心「权限」页签改为安全模式切换 + 黑名单管理（禁止目录/命令/网络/审批动作）+ 旧白名单配置折叠保留；概览卡显示黑名单统计。
- **旧配置迁移**：v1 权限文件自动迁移到 v2——过去的 blocked_dirs/shell.blocklist/云元数据 SSRF 保留为初始黑名单，其余全部放行。
- **测试**：SSRF/权限测试适配新模式并新增黑名单放行/黑名单拦截/迁移/旧模式回退用例；全量 543 项通过。

## v2.13.0（2026-08-15）

- **新一轮修复与增强**：①修复 Ctrl+Backspace 多行删除范围错误（字符偏移不再误当行号）；②`call_api` 响应改为流式读取（超 500KB 即断，防大响应内存峰值），WebDAV 上传改为流式发送（不再整文件读入内存）；③`fetch_blocked` 增加 DNS 重绑定 SSRF 校验（域名解析落内网/元数据即拦截）；④`permissions.save` 改为原子写；⑤`_http_client` 增加关闭标记，退出竞态不再重建新客户端；⑥长耗时工具（公众号写作/数据库/WebDAV/下载等）与普通工具线程池隔离，避免慢任务占满快速池；⑦`read_excel` 确认只读模式（read_only=True）；⑧插件中心新增「市场」页签（远程索引浏览/下载/SHA-256 校验安装）；⑨新增自定义主题（颜色 token 可视化配置并即时应用）；⑩新增快捷键自定义（根窗口快捷键可改、立即生效、可恢复默认）；⑪更新包下载后支持 SHA-256/签名校验（防篡改）；⑫`dialogs.py` 按主题拆为 `dialogs/` 包（about_help / data_stats / workspace / session / productivity）。全量测试 537 项通过。
- **隐私安全**：修复隐私模式仍写快照/会话/成功模式/任务记录/最近产物的问题；敏感配置（inbound_token / image_api_key / 邮件 / 数据库 / Webhook）改为 DPAPI 加密存储。
- **SSRF**：新增逐跳重定向 SSRF 校验；`call_api` 默认禁止回环；`rss_fetch` 增加 SSRF/本地文件校验；`image_generate` 下载地址校验。
- **沙箱**：`run_python` AST 检查阻断别名导入、`from os import *`、`from importlib import import_module`、`globals/vars/getattr` 反射等更多绕过。
- **可靠性**：`_pending_send` 改为队列，连续发送不再丢消息；退出保存会话改为同步落盘；`ensure_client` 加锁；多处消息快照加锁。
- **工具正确性**：strict 模式不再破坏自由对象/强制可选参数必填；`call_api`/自定义工具响应限流；解压/打包增加 zip 炸弹与总量限制；CSV/PDF 改为惰性读取；MySQL 增加慢查询超时；`run_tests` pytest 支持指定路径；子代理增加重试；`_SEARCH_HEALTH` 加锁。
- **测试**：修复无效断言、环境依赖跳过、CI 增加 Python 3.9；新增 run_python 绕过与 SSRF 重定向回归用例。全量 525 项通过。
- **后续加固**：WebDAV 下载改为流式写盘；`_drain_ui_queue` 重构为逐消息容错；侧栏宽度常量统一走 `LAYOUT`；移除自证式布局测试。全量 524 项通过。
- **模块拆分**：新增 `uiutils.py`（CappedList/MAX_BLOCKS/index_num）、`security.py`（SSRF 校验函数）、`db_utils.py`（只读 SQL 校验 / 变更预览 / 表格格式化）、`persistence.py`（原子 JSON 写入）、`pdf_utils.py`（页码范围 / 中文字体 / Markdown 转 PDF 片段）、`proc_utils.py`（进程树终止）、`net_utils.py`（共享 HTTP 客户端 / 重定向校验）、`search_utils.py`（搜索解析 / 去重 / 安全过滤）、`layout.py`（布局常量）、`themes.py`（主题 token）、`deps.py`（可选依赖清单）、`config_defaults.py`（默认配置 / 系统提示词 / 内建工具 / 行为指令 / 更新源 / 场景思考默认值）、`roles.py`（内置角色）、`templates.py`（任务模板 / 试玩任务）、`app_utils.py`（布尔转换 / 空壳目录 / 清理 / 干净退出 / 隐私日志）、`render_utils.py`（流式 Markdown 切分 / 代码块切分）、`ui_utils.py`（菜单销毁）、`migration.py`（旧数据迁移）、`user_tools.py`（自定义工具加载缓存）、`profiles.py`（Profile 配置读写）、`config_utils.py`（配置加载 / 规范化 / 保存）、`stores.py`（最近产物 / 模式 / 失败 / 任务日志 / 记忆 / 调度等 JSON 存取）、`session_utils.py`（会话 ID 工具）与 `dialogs.py`（关于/帮助/余额/用量统计/依赖状态/失败模式/任务记录/检查点/最近产物/工作目录/数据清理/工作区文件树/历史会话库/命令面板/上下文详情/会话轨迹/批量任务/FIM/回复变体/收藏消息/自我进化提案/功能建议/自我审查/插件安装引导/欢迎页对话框）；`shared.py` 新增 `defer_until` / `budget_thinking`；`exporters.py` 新增 `build_markdown`；新增 `MODULES.md` 记录模块拆分清单。`main.py` / `deepseek_client.py` 改为导入复用，为后续继续拆分打下基础。

## v2.12.11（2026-08-15）

v2.12.10 之后的 13 个提交汇总（源码包与本地代码对齐）：

- **搜索能力**：search_web 增强（num/offset/since/until/site 参数 + 多引擎聚合去重）、搜索引擎池化（新增 360 搜索 + 健康度管理）、search_realtime 实时通道（Hacker News 热点/搜索）、search_github 垂直源、rss_fetch 精选预置源
- **能力扩展**：call_api 万能接口（SSRF 防护 + 内网白名单）、自动经验复盘、system_status 系统自检
- **修复**：fetch_blocked 分发签名、search_web site/offset 参数保证生效、流式 usage 统计恒为 0、设置面板布局、滚轮速度、启动窗口几何
- **品牌**：GitHub 仓库更名为 WhaleTalk，更新机制接入新仓库
- **文档**：README 重写为正式产品介绍，CHANGELOG 独立归档

## 开源后新增（2026-08）

| 提交 | 说明 |
|------|------|
| 47fe961 | call_api 限制放宽：超时 180s / 响应 500KB / 请求头 16 个；新增 call_api_allowed_hosts 内网白名单（SSRF 防护保持） |
| cbe05e6 | 能力增强：call_api 通用 API 工具（SSRF 防护）、自动经验复盘、system_status 系统自检 |
| 02af26f | P1/P2：search_realtime 实时通道（Hacker News）、rss_fetch 精选预置源 |
| 7bddefe | P0 修复：fetch_blocked 分发签名错误、search_web site/offset 参数保证生效 |
| b66ea80 | 搜索引擎池化：新增 360 搜索、引擎健康度管理、search_github 垂直源 |
| ad909ad | search_web 增强：num/offset/since/until/site 参数 + 双引擎并行聚合去重 |
| dc22647 | 启动默认最大化显示（铺满工作区天然居中） |
| aab1157 | 修复启动窗口几何超屏（偏右下方/底部在屏外） |
| 1c85778 | 修复设置面板收起再展开后输入框变宽、挤压右侧栏 |
| 3c72fb0 | 滚轮滚动速度修复（每格 1 行 → 3 行） |
| 2809032 | 修复流式对话累计输入/输出恒为 0（openai SDK 2.x usage 捕获） |
| 1bf500f | 同步本地主版本 v2.12.10 完整功能至开源仓库 |

## v2.x
| 版本 | 说明 |
|------|------|
| 2.12.10 | **修复公众号写作在思考模型下的 LLM 调用失败**：`run_wechat_writer(topic=...)` 报「LLM 调用失败：模型返回空内容」——切换 `deepseek-v4-pro` 等**思考模型**后，响应 `content` 为空、推理在 `reasoning_content`，wechat_writer 内部 LLM 封装（llm.py）只读 `content` → 空内容。修复：①请求显式 `thinking: disabled`（写作/选题场景内容直出），旧端点 400 时自动降级重试；②响应解析回退 `reasoning_content` 兜底；③**用户显式指定主题（topic_override）跳过历史查重**——去重只约束自动选题，用户决策优先（此前 LLM 精判误伤"DeepSeek Harness"指定主题）；全量测试 475 通过；实测 dry-run 完成（2585 字、质检 100 分） |
| 2.12.9 | **信源深度扩展 + 按需加载的「飞机包」能力**：①**信源扩充 4 组**——国内论坛组（v2ex / 虎扑 / 吾爱破解）、国际论坛组（reddit / lobste.rs / slashdot / phoronix）、被墙论坛组（linux.do / hostloc / 龙空）、微博贴吧组（RSSHub 公共实例），`enabled_groups` 机制控制启用（**默认仅启用国内可达组**，被墙组需显式加入并配合 use_blocked）；②**fetch_blocked 工具**（按方案文档落地 + 增强）：自动发现 mihomo/clash 订阅缓存 HTTP 节点（并发测速选最快 + **节点池 10 分钟 TTL 缓存**）→ curl_cffi Chrome 指纹过 Cloudflare → 无 curl_cffi 自动降级标准库 TLS-in-TLS；**SSRF 防护内建**（回环放行、内网/元数据阻止）；③**合规设计**——fetch_blocked.py 独立模块，**不进默认启用集**（工具中心勾选才启用），分享/开源剔除该文件即无此能力，规避传播翻墙软件风险；④**公众号写作集成**——`run_wechat_writer` 新增 `use_blocked` 参数；sources.py 被墙源直连超时/失败后**自动经代理升级重试**（linux.do/v2ex 等素材实测进入素材池）；全量测试 470 通过 |
| 2.12.8 | **修复公众号写作能力降级**：核心工具 `run_wechat_writer`（采集→选题→LLM 写作→质检→存草稿箱）与 `publish_draft` **不在默认启用集 BUILTIN_TOOL_NAMES**（同类 `daily_brief` 却在），v2.12.5 移除场景包后（创作包原含 publish_draft）标准模式失去公众号能力——模型不再调用工具，退化为普通对话写作（无采集/选题/质检链路），即"能力变差"。修复：两工具加入默认启用集（安全属性：只产草稿不发布，发布权在用户；permissions 白名单已含 publish_draft），normalize 升级合并自动为所有旧配置补入；用户 config 已即时修复；dry-run 文案修正（明确"未写入草稿箱"）；回归测试（默认集/升级合并/schema 完整）；dry-run 实测链路完好（2489 字、质检 100 分）；全量测试 451 通过 |
| 2.12.7 | **修复工具 schema 400（missing field 'items'）**：4 个内置工具（write_csv.rows / write_excel.data / chart_data.data / subagent_run.tasks）的 array 参数缺 `items` 字段，DeepSeek API 直接 400 拒绝整轮请求（`阅读程序` 等场景复现）。已补齐 items（结构化类型或宽松 `{}`）；并新增**递归兜底 `_patch_array_items`**——每次请求前扫描所有工具（含用户自定义工具/插件工具）schema，`type: array` 缺 `items` 自动补齐，防御第三方 schema 遗漏导致的同类 400；深层嵌套（object 属性里的 array）同步覆盖；strict 模式 `_strictify_schema` 本就保留 items 无影响；全量测试 448 通过 |
| 2.12.6 | **SSRF 防护分层（消除本地开发阻挡）**：修复 `已阻止访问内网/回环地址` 阻挡 `http://localhost:3000` 等本地开发验证——**回环（localhost/127.0.0.0/8/::1）默认放行**（本地服务器验证是最高频正当场景）；**内网/链路本地/保留网段仍阻止**，但工具中心 → 权限页签新增「SSRF 信任主机」白名单（IP/主机名/CIDR 网段，如 192.168.1.0/24、NAS），保存即时生效（写 config.json + 运行时同步）；**云元数据 169.254.169.254 永远硬阻止（白名单不可豁免）**；DNS 重绑定防护保留（域名解析落内网仍拦截，解析到回环按本地验证放行）；**搜索链接过滤保持严格**（结果来自外部，是 SSRF 注入源，回环不放行）；自定义工具 endpoint 回环放行（用户注册的本地服务）；修复工具中心底部「保存更改」回调被误清空的隐藏 bug（上轮保存按钮虽显示但回调丢失，本次彻底生效）；全量测试 445 通过 |
| 2.12.5 | **自主模式 = 任务能力 + 工具中心保存修复**：①**删除场景包（办公/开发/创作）**——"任务能力"与自主模式语义重叠，完全多余：**完全智能 = 全部工具**（运行时自动启用内置+自定义全部工具，为开发/创作而生）、**纯对话 = 无工具**（不传工具 schema）、**标准 = 按工具中心勾选**；运行时语义不污染 enabled_tools 配置（切换模式不再覆盖手动工具配置）；②**配套提示词设计**——新增内置「智能体」角色（目标先行 / 规划执行 / 产物落地 / 验证闭环 / 结果汇报，思考档 max），完全智能模式切换时状态栏提示建议应用；③**修复工具中心无保存按钮**——工具设置/权限页签面板化后保存回调被丢弃（勾选改动关窗即丢），新增底部「💾 保存更改」统一保存栏（收集两个面板保存回调，面板内提示未保存即不生效的语义）；工具设置页签在完全智能/纯对话模式下显示"勾选仅作为标准模式默认集"；④工具中心概览新增「任务能力」卡片（当前模式语义常显）；⑤代码输出建议从"启用开发场景包"改为"切换完全智能模式"；⑥`_apply_plugin_scenario` 注释/文案同步（与自主模式分层一致）；全量测试通过 |
| 2.12.4 | **UI 提示校正 + Bug 修复 + 状态可视化**：①**状态栏角色常显**——右段新增「🎭 角色名」（模型 · 角色 · 场景 · 思考），当前人格全程可见；角色识别加缓存（状态栏高频调用防读盘，用户角色保存时失效）；②**场景包确认弹窗**——应用前列影响清单（工具数/思考档/写文件/命令/审批 + 人格保持不变），取消回退下拉；③**修复场景包空值误导**——选空仅清除标记，提示改为「已退出场景包（当前工具/权限保持不变）」而非虚假的"已恢复手动配置"；④**修复插件场景覆盖人格**——与三层分层一致：插件场景只应用任务能力（工具/思考），建议人格提示词**单独弹窗询问**（拒绝保持当前人格）；⑤**修复 OCR 结果丢失**——OCR worker 曾走已删除的语音队列（"speech"）导致识别结果无人消费，改为独立 "ocr" 队列 + 输入框插入；⑥**过时路径文案校正**——菜单重构后所有"工具 → 权限/插件/流程/进化"提示更新为新路径（🛠 工具中心 → 权限、🧩 插件中心、自动化 → 流程管理）；权限拒绝提示（permissions.py）同步更新；⑦欢迎/关于 "90+" → "100+" Agent 工具；⑧错峰图标 ⛰ → 🌙；strict 菜单 label 修正（自动启用 Beta API）；⑨删除使用中的角色时提示"显示为自定义"；⑩工具中心概览新增「人格」卡片；全量测试 433 通过 |
| 2.12.3 | **角色管理完整化 + 三层职责分离**：①**角色 CRUD**——用户自定义角色（user_roles.json）新增/编辑/删除/分类，内置角色只读（可复制内容到新增）；「角色与提示词」对话框左侧按「内置/我的角色」分组展示（分类二级），当前角色 ✅ 高亮，右侧预览（描述/思考档/提示词全文）+「✍ 自定义」编辑；②**三层职责分离消除冲突**——**场景包不再写 system_prompt**（此前办公/开发/创作直接覆盖人格提示词，与角色冲突）：场景包=任务能力（工具/权限/思考档）、角色=人格（system_prompt）、场景=模型参数（温度/采样）；设置面板各分组加职责说明（"场景=模型采样参数"、"场景包=任务能力（不影响人格）"、"人格=系统提示词"）；③`apply_role` 支持用户角色；`_current_role_name` 在合并集（内置+用户）匹配；④测试——用户角色 CRUD 持久化、识别、应用、场景包不覆盖人格；全量测试 426 通过 |
| 2.12.2 | **角色与提示词统一（消除重复入口）**：①概念模型明确——**角色 = 系统提示词预设**，两者操作同一字段；②**统一对话框**「角色与提示词」——左侧列表（7 预设 + ✍ 自定义，当前角色 ✅ 高亮）、右侧详情（描述/思考档/提示词全文预览；自定义可直接编辑）；③**当前角色自动识别** `_current_role_name`（提示词与预设完全一致 → 角色名，否则"自定义"）；④**应用点可视化**——设置面板新增「AI 人格」组常显「🎭 当前角色：X」，角色应用/自定义保存/启动均联动；⑤**入口收敛**——删除独立「系统提示词」对话框（edit_system_prompt -52 行）与能力扩展菜单的「角色库」重复入口，设置菜单「AI 行为」唯一入口 + 设置面板直达；⑥`apply_role`/`_apply_custom_prompt` 统一走缓存警示 + 状态联动；⑦测试——角色识别（预设/自定义）、对话框结构（预设+自定义项）、应用状态联动；全量测试 424 通过 |
| 2.12.1 | **功能减法（方向明确后的收敛）**：移除 4 项被替代/无下游的功能入口——①**语音输入**（System.Speech 质量差，speech_to_text 工具更好）：删除输入框 🎤 按钮、_start_speech/_insert_speech/队列分支（朗读保留）；②**反馈收集**（无下游消费的死功能）：删除右键 👍/👎、反馈记录对话框/菜单/命令面板项（-99 行）；③**示例任务（一键体验）**：从工具菜单移除级联（教学任务完成使命，欢迎页入口保留，_run_playground 保留）；④**配方管理对话框**：被流程体系替代（workflows recipe 步骤已引用 patterns.json 成功模式），删除 show_recipes 与菜单/命令面板入口（底层记录保留）；⑤README 功能清单同步删减；全量测试 420 通过 |
| 2.12.0 | **菜单 = 导航按钮（精确分类重构）**：①**顶级菜单 7 个**——新增「自动化(A)」独立菜单（定时任务/流程管理/知识库/任务检查点/项目任务记录/每日简报），Alt 快捷键覆盖 F/E/V/T/A/S/H；②**工具菜单精确级联**——中心入口（工具中心/插件中心）置顶直达 + 六个分组级联：账户与用量（查余额/用量/预算/上下文/模型对比）、任务与模板（任务模板▸/示例任务▸/批量/简报/纪要/轨迹）、文件与产物（工作目录/文件树/最近产物/进程终端）、能力扩展（自定义工具/Profile/提示词库/角色库/配方/FIM/依赖状态）、🧬 自我进化、系统（失败模式/推送配置/OCR/数据清理/命令面板）；③**设置菜单分组**——AI 行为（系统提示词/角色库/strict 工具模式）/ 应用行为（完成通知/项目上下文/隐私/自启/托盘）+ 保存配置；④**编辑菜单分组**——消息（重发/重生成/变体/续写）/ 剪贴板与复制 / 查找与轨迹 / 数据与分享（收藏/记忆/反馈/分享/朗读）；⑤视图菜单新增 ⛶ 全屏模式（F11）；⑥**工具中心概览导航网格**——快捷操作行（余额/用量/预算/对比/上下文）+ 导航行（定时/流程/知识库/检查点/任务记录/产物/目录/失败模式/插件中心）9 个聚合按钮；⑦新增 `_menu_by_title` 顶级菜单映射（测试/主题定位）；全量测试 420 通过（新增 test_ui_menu 8 项） |
| 2.11.3 | **应用级尺寸自适应（窗口跟随主窗口，非屏幕比例）**：①`_hub_size` 改为**以主窗口为参照**——非全屏 = 主窗口 90-95% 宽×高（插件中心 90%×93%、工具中心 92%×94%，浏览器新标签页式接近充满）、F11 全屏 = 屏幕 85%×85%（四周留边视觉舒适）；②`_screen_scale` 基准从屏幕高度改为**主窗口宽度**（1000≈1.0、1474≈1.47、封顶 1.9）——对话框档位/预览窗随主窗口呼吸，大屏窄屏统一比例（1474 主窗下对话框档位 619/766/943）；③实测：主窗口 1474x921 → 插件中心 90%×93%、工具中心 92%×94%、全屏下 1740x979；④测试——hub 跟随主窗口比例、全屏留边、scale 宽度基准值域；全量测试 412 通过 |
| 2.11.2 | **浏览器式可见性优先（居中/全屏/防截断）**：①**全局居中体系** `_center_geometry`——所有对话框（_dialog_shell）/工具中心/插件中心/文本图片预览窗**打开即居中于主窗口**（视觉重心略高，屏幕内校验，记忆位置仍优先）；②**F11 无边框全屏**——全屏=上下左右铺满无边框（浏览器式），退出恢复原几何；③**防截断**——插件中心两页列表+详情补滚动条（此前无滚动是"元素显示不全"主因）、权限面板补滚动容器（内容多时底部不再被截断）；④主窗口启动居中校验（1474x921+287+105 @1152p）；⑤测试——居中几何屏幕内校验、对话框居中、F11 切换、插件中心滚动条 ≥4；全量测试 410 通过 |
| 2.11.1 | **屏幕自适应（大屏 PC 适配）**：①`_screen_scale` 自适应系数——基准 900 逻辑高（≈720p），1080p→1.2、2K/4K→1.6 封顶；②**对话框档位按系数放大**（_dialog_shell 内吸附到放大后档位，420/520/640 → 537/665/819 @1.28），消除大屏局促；③**主窗口默认几何**——屏幕 72% 宽 × 80% 高（上限 1680x1050，下限布局最小尺寸），1280x820 → 1474x921 @1152p；④**中心窗口** `_hub_size`——工具中心/插件中心按屏幕 ~54-56%×60-62%（上下限 680-1440 × 520-900）；⑤**预览窗**——文本/图片预览随系数放大（图片适配上限 900x650）；⑥**默认字号提升** `_apply_screen_font_default`——仅当用户未自定义字号时按屏幕高提升（1080p→11、≥1300→13）；⑦测试适配——对话框吸附测试改为通用断言（档位递增/比例/吸附成员），新增系数值域与基准测试；全量测试 406 通过 |
| 2.11.0 | **UI 设计语言升级：菜单 → 正式页面**：①**工具中心**（Ctrl+Shift+T）——正式窗口三页签：概览（模型/今日用量/依赖就绪度/工作目录/安全状态卡片 + 查余额/用量/预算/知识库/定时/流程快捷入口）/ 工具设置（全部工具启停，`edit_tools` 重构为可嵌入 panel）/ 权限（行动能力闸门，`edit_permissions` 重构为 panel）；②**插件中心**（Ctrl+Shift+P）——三页签：我的插件（图标列表+详情 + 导入/导出/场景重应用/启停/卸载 + **拖拽 .wtplugin 直接导入**）/ 画廊（示例插件+详情+一键安装）/ 工坊（需求输入+AI 生成）；③**菜单瘦身**——工具菜单顶部为两大中心入口 + 高频直连，工具设置/权限/插件三件套移入中心，删除 350+ 行旧对话框重复代码；旧入口（show_plugins 等）自动转发到中心（向后兼容）；④快捷键 Ctrl+Shift+T/P；⑤工具设置/权限面板重构为返回保存回调的 panel 组件（中心与对话框共用）；全量测试 404 通过 |
| 2.10.0 | **插件体验全面升级**：①**安装后引导卡片**——装完自动弹出引导（使用方式 + 一键试用：技能=直接插入输入框 / 流程=立即运行 / 场景=应用配置），让用户 30 秒感知插件价值；②**共用安装流程** `_install_plugin_file`——解析 → **重复安装检测（同名已装提示覆盖更新：先精确卸载旧条目再装新版）** → 依赖自检确认 → 安装 → 场景询问 → 引导卡片，filedialog 与拖拽导入共用；③**详情渲染** `_plugin_detail_text`——图标 + 名称/版本/作者 + 能力明细（逐个工具/技能/流程步骤数/场景）+ 缺失依赖 + 使用方式；④**画廊升级**——emoji 图标（示例插件 📕📊📋）+ 左列表右详情双栏 + 已安装版本判断 + 安装走引导；⑤**管理升级**——图标列表 + 详情双栏 + **卸载影响清单**（将移除 N 工具/N 技能/N 流程）+ 拖拽 .wtplugin 直接导入（DND）；⑥修复 `_refresh_user_tools_cache` 提升为实例方法（共用安装流程调用）；全量测试 401 通过 |
| 2.9.1 | **插件调用闭环 + 菜单重组**：①**流程手动运行**——「流程管理」新增「运行选中」按钮（确认后立即 run_workflow，补上用户手动触发流程的缺口）；②**场景重应用**——插件管理新增「应用场景配置」按钮（scenario 随时重应用，不再限于安装时一次）；③**技能注入 AI**——已安装插件技能清单注入动态上下文（`_plugin_skills_hint`），AI 在对话中知晓用户可用模板并在相关任务中直接完成或建议；修复 prompts.load_prompts 丢弃 `_source` 标记导致技能来源不可识别；④**工具菜单重组**——新增「🧩 插件」独立分组（插件管理/画廊/工坊/依赖状态），依赖状态与失败模式库归位（依赖→插件组、失败模式→系统组），消除布局混乱；⑤对话框使用方式提示（工具=AI 自动调用 · 技能=⚡指令 · 流程=AI/手动运行）；全量测试 398 通过 |
| 2.9.0 | **插件画廊 + 会话轨迹**：①**内置示例插件**——sample_plugins/ 随程序分发 3 个高质量示例（小红书文案助手/周报生成器/会议纪要助手），演示插件能力并为 AI 造插件提供范例；②**插件画廊 UI**——工具菜单「🛍 插件画廊」：示例插件列表（可安装/已安装状态）+ 一键安装（复用 apply_plugin 链路），双击即装；③**会话轨迹视图**——show_session_timeline 从消息级升级为 blocks 级混合时间线（`_timeline_items` 纯函数可测）：用户/助手回复/思考/工具调用（✅❌+参数+结果摘要）/系统事件（压缩、任务完成、中断等 note/error），双击可跳转原文，不可跳转条目（工具/事件/思考）明确提示；④README 插件体系章节补充画廊与 AI 生成说明；全量测试 396 通过 |
| 2.8.0 | **🧩 插件工坊（AI 造插件，差异化之路核心）**：①`create_plugin` 工具——AI 根据需求生成并安装 .wtplugin 插件：简化工具描述自动转换完整 schema（name/endpoint/method/params），支持工具/技能/流程/场景任意组合，生成后立即生效；②权限整合——create_plugin 加入 ACTION_TOOLS 审批闸门（confirm 模式弹窗确认，auto/full_auto 直接放行）；③插件工坊 UI——工具菜单「插件工坊」：示例需求引导 + 需求输入框 → 发送给 AI（引导 AI 用 create_plugin）；④安装报告含缺失依赖提示，数据文件变更经 mtime 缓存自动失效（无需重启）；⑤默认工具列表加入 create_plugin；全量测试 392 通过（新增 6 项） |
| 2.7.0 | **🧩 插件体系（零代码能力扩展，差异化之路起点）**：①`.wtplugin` 插件格式——工具/技能（提示词模板）/流程/一键场景的组合包，单文件 JSON 可分享安装；②`plugins.py` 模块——校验/解析/列表/应用/卸载/启停：安装合并进 user_tools.json、prompts.json、workflows.json（条目带 `_source: plugin:<slug>` 来源标记），**卸载/停用精确移除本插件条目，用户手动添加的同名能力不受影响**；③插件管理 UI——工具菜单「🧩 插件管理」：列表（✅/⏸ 状态）+ 详情 + 内容预览 + 依赖自检（requires 缺失提示）+ 导入（filedialog+确认）/导出分享/停用启用/卸载；④插件场景配置一键应用（思考档/系统提示词/推荐工具，联动现有场景机制）；⑤README 新增插件体系章节（格式/安装/卸载/分发说明）；全量测试 386 通过（新增 test_plugins 12 项） |
| 2.6.0 | **依赖自检 + 压缩事实提炼**：①**依赖状态**——17 项可选能力清单（Pillow/pystray/playwright/faster-whisper/PyMuPDF/reportlab/python-docx/pptx/feedparser/qrcode/pyzbar/diskcache/imageio-ffmpeg/markdown/pywin32/tkinterdnd2/tiktoken）统一检测：已装 ✅ / 缺失 ⚠ + 安装命令，工具菜单「🔌 依赖状态」查看，打包 exe 排查利器；②**压缩事实提炼**——上下文压缩摘要器同时输出「关键事实」清单（事实/决策/待办，每条 - 前缀），随摘要注入后续对话，摘要消息显式标注可长期引用要点；③**启动性能观测**——初始化按阶段计时（配置/UI 构建）写日志，主程序记录启动与运行总时长，为后续优化提供基线；全量测试 374 通过 |
| 2.5.0 | **主动助手（每日简报）+ 对比参数化**：①**每日简报**——新增 `daily_brief` 工具：复用 WeChat Writer 采集引擎（RSS+搜索+关键词过滤）→ 复用当前对话客户端（模型一致）LLM 提炼要点与点评 +「今日趋势」→ 保存工作区 briefs/brief_YYYYMMDD.md；支持 topic 关键词过滤与 max_items；工具菜单「📰 生成今日简报」一键入口（AI 走工具链，产物入最近产物），可配合 schedule_task 定时生成晨报；②**模型对比参数化**——「模型对比」改为弹窗：两个可输入 Combobox（候选 = 内置模型 + 各 Profile 模型名），支持任意模型名自由输入，相同模型校验；③默认工具列表加入 daily_brief；全量测试 370 通过 |
| 2.4.0 | **产物应用内预览 + 更新下载**：①**应用内预览**——`_open_path` 统一入口：md/txt/代码/JSON/CSV 等在应用内预览（Markdown 走 mdparse 渲染，粗体/代码/链接/标题样式可点击，链接仅放行 http(s)），图片（PIL）等比缩放适配 800x600，右上角「用系统程序打开」回退；可执行文件等保持系统打开；②**更新包应用内下载**——update_url 配置的 latest.json 中 url 指向直链安装包（.exe/.zip/.7z/.msi）时，应用内下载到「下载/WhaleTalk 更新」目录（后台线程 + 2GB 限流），完成弹窗提示打开所在文件夹，无需跳转浏览器；③预览分类/渲染/图片 4 项 UI 测试；全量测试 364 通过 |
| 2.3.0 | **任务可靠性（断点续跑 + 省钱）**：①**自动断点**——工具链每完成一步自动持久化检查点（auto 标记 + 任务名/工具链摘要，隐私模式跳过），无需 AI 主动保存；②**一键续跑**——任务中断/停止/崩溃/重启后，聊天区出现可点击「▶ 从断点继续」（一键发送恢复指令，AI 先 task_checkpoint_load 再续跑），正常完成自动清除自动断点（手动检查点保留，task_checkpoint_clear 仅清 auto）；③**预算感知思考降档**——接近月度预算 80% 时 auto/max 思考档自动降 high 并提示（手动选择的 low/medium/high 不干预），纯函数化可测；④**更新通道落地**——检查更新源改为 config.json 的 update_url（支持 latest.json：version+url），未配置时提示明确，启动自动检查联动；全量测试 360 通过 |
| 2.2.0 | **高峰错峰 + strict 工具模式**：①**高峰错峰执行**——定时任务「错峰」选项：触发时刻处于高峰时段（9-12 / 14-18）时自动顺延到最近空闲时段（12:00 / 18:00，已过则次日 0:00）执行，官方峰谷定价空闲价仅为高峰一半；面板勾选/AI 用 schedule_task(off_peak=True) 皆可，调度器用 defer_until 字段持久化顺延状态（time/cron 当天一次语义保持，every 型自然错峰）；②**strict 工具模式（Beta）**——设置菜单一键开启（自动启用 Beta API）：所有工具 schema 自动规范化为 strict 格式（全部属性 required + additionalProperties=false，递归处理嵌套 object / items / anyOf），模型输出 Function 调用严格遵循 JSON Schema，减少参数格式错误；③错峰顺延时刻纯函数化（_defer_until）可测；全量测试 353 通过 |
| 2.1.0 | **思考模式深度优化（超越官方开箱体验）**：①思考档位对齐官方完整映射表——恢复 medium/xhigh 档（官方映射 medium→high · high→high · xhigh→high · max→max，修正此前 xhigh→max 的错误假设），UI 提供 none/low/medium/high/xhigh/max 六档 + auto 智能路由；②**思考成本优化**——无工具调用的历史轮次发送时自动剥离 reasoning_content（官方规则：该内容在后续轮次会被 API 忽略，不传即省每轮数千 token 输入费），带工具调用的轮次完整回传（官方要求）；③**缓存友好消息布局**——恒定的 json_hint 保持最前、可变的记忆注入追加在末尾（system 消息位置任意），记忆/项目上下文刷新不再破坏稳定前缀，最大化官方硬盘缓存命中（前缀完整匹配规则）；④**动态注入分离**——长期记忆+工作目录+行为指令走稳定 system 注入，项目上下文/检查点/成功模式/任务记录/失败模式/相关文件走 trailing_text 追加到本轮 user 消息尾部（不污染会话历史）；⑤**JSON 输出自校验重试**——输出自动 json.loads 校验，解析失败自动追加修正提示重试一次（官方明示 JSON 输出有概率非法，应用层救回）；⑥auto 智能路由增强——多步骤指令（编号/步骤式 ≥3 条）自动升级思考深度；全量测试 347 通过 |
| 2.0.0 | **V4 正式版适配（鲸语正式版 GA）**：①模型适配——DeepSeek-V4-Flash-0731 / DeepSeek-V4-Pro-0813 正式版（MODELS 记录版本号与 1M 上下文 / 384K 输出上限）；②**新峰谷定价体系**（2026-08-17 生效）——PRICING 更新为官方高峰价（flash 3.0/9.0/缓存命中 0.10，pro 9.0/27.0/0.30 元每百万 tokens），estimate_cost 按「空闲时段=高峰一半」自动打折（统计/预算/省钱报告即时准确），峰谷判断下沉 shared.py 与客户端共享；③思考档位对齐官方——移除 xhigh，正式档位 none/low/high/max + auto 智能路由（low/high/max 直接透传 reasoning_effort）；修复 auto 判简单任务时误传 reasoning_effort="none" 的隐患（改走禁用思考+采样参数路径）；④输出上限放宽——max_tokens 上限 65536→393216（V4 最大输出 384K）；⑤缓存警示文案动态化——按当前模型高峰价显示命中/未命中差价（不再写死 0.02/1）；⑥高峰提示文案更新为「按高峰价计费（空闲为一半）」；全量测试 340 通过（新增 V4 GA 适配 10 项） |
| 1.12.0 | **自动化与常驻体系**：①定时任务补跑——程序未运行时错过的任务启动时自动补执行（time 型补当天一次、cron 型补最近一次匹配，不积压）；②三层自动化打通——流程步骤支持引用配方（`{"recipe": "配方名", "text": "目标"}` 自动注入已验证工具链），定时任务新增 workflow 动作（到点自动运行流程，UI 校验流程存在性）；③系统托盘常驻——pystray 托盘图标（显示/隐藏/退出，回调走 UI 队列线程安全，线程崩溃检测防窗口锁死），设置菜单「关闭时最小化到托盘」开关（不可用/启动失败时提示并回滚）；④开机自启——HKCU Run 键注册（自动探测 venv pythonw / 打包 exe），设置菜单一键开关；⑤失败模式库——AI 工具执行失败自动积累（工具/参数/错误/时间，同错去重上限 50 条），注入「已知失败模式」上下文引导 AI 规避已知坑，工具菜单查看/清空；⑥任务中断检测——**修复"回复到一半误报任务完成"**：`_consume_stream` 捕获 finish_reason，输出达 max_tokens 上限被截断 / 流式连接中途断线 / 工具调用流截断 / 工具轮数耗尽均通过 on_truncated 通知 UI，任务报告区分「任务完成 ✅」与「任务中断 ⚠」（附继续路径指引），纯对话截断显示「回复中断」，桌面通知/任务面板同步区分；⑦代码去重——cron 引擎/PATH_RE/OCR 脚本抽入 shared.py（消除 main 与 deepseek_client 双份实现漂移）；⑧修复 edit_file 正则替换串反向引用被解释（`\1`/`\\` 静默改写，改 lambda 原样替换）、wechat_writer 缺失 markdown 依赖声明、测试死代码断言；全量测试 330 通过 |
| 1.11.0 | **UI 布局定版（Layout Specification v1.0）**：统一 LAYOUT 尺寸常量系统（窗口 1280x820/minsize 880x620、侧栏 260(200-420)、面板 280(240-480)/文件视图 460、菜单 34/状态栏 30、内容列 560-860）；**核心修复输入区与聊天内容列同宽对齐**（1280 窗口错位 96px→0，四档窗口全部对齐）；窗口几何记忆（config window_geometry 恢复+屏幕内校验）；紧凑模式重校准（≤1120 收侧栏、≤1000 收面板，窄窗优先保聊天，内容列物理容器让步防越界）；对话框三档规范化（420/520/640 + 高 300/420/460/540/620 自动吸附，26 个既有对话框全部归一）；状态栏右段收窄（context 条 120px）；全量测试 312 通过（新增 test_ui_layout 13 项） |
| 1.11.0 | **UI 定版大版本**（完整清单）：①品牌一致性——导出 MD 头/窗口标题/splash 启动界面统一鲸语品牌（清除 DeepSeek Assistant/DeepSeek 引擎残留），关于/帮助/余额查询/用量统计/导出成功 5 类弹窗从系统 messagebox 升级为品牌对话框，欢迎页更新；②菜单栏——新增「视图」菜单（主题/字号/Markdown/面板显隐/建议开关），工具菜单 6 组功能分区，试玩→示例任务，新增 Alt+F/E/V/T/S/H 菜单快捷键；③状态栏三段式信息分级（左=模式/目录/统计/预算，右=模型/场景/思考）；④主题 token 定版——新增 hover/note/mention/quote_bg/input_placeholder 五色（菜单悬停、时间戳、引用块、占位符全部接入）；⑤字号规范——fsz=8 与 8pt label 全量统一 9pt（main 73 处 + 面板 4 处）；⑥侧栏「会话」→「对话」+ 按钮文案统一；⑦设置面板外观组补字号标签；全量测试 299 通过 |
| 1.11.0 | **UI 定版大版本**：品牌一致性（导出 MD 头/窗口标题统一鲸语品牌、关于/帮助升级为品牌对话框、欢迎页更新）；菜单栏重构（新增「视图」菜单：主题/字号/Markdown/面板显隐/建议开关；工具菜单按 6 组功能分区：账户与用量/任务与模板/能力管理/数据与文件/自我进化/系统；「试玩任务」更名为「示例任务」）；状态栏三段式信息分级（左=模式/目录/统计/预算，右=模型/场景/思考）；侧栏命名与字号规范（「会话」→「对话」、8pt 小字统一 9pt、新增 toggle_sidebar 左侧栏显隐）；全量测试 299 通过 |
| 1.10.17 | 产物可见性全面升级：①📦 产物条——输入框上方常驻显示最近产物（打开/所在文件夹/复制路径/关闭），工具结果出现即更新；②📂 文件面板——右侧设置面板改双 Tab，文件视图树形浏览（工作区/草稿箱/最近产物/数据目录，懒加载、双击打开、右键菜单含注入输入框），自动加宽 430px；③公众号草稿统一写入工作区 drafts/（与 publish_draft 同目录，产物面板直达）；全量测试 297 通过 |
| 1.10.15 | 公众号自动写作工具（按《wechat_writer_公众号自动写作工具方案.md》PRD）：新增独立包 wechat_writer/（config/sources/topic/writer/quality/history/output/llm/main，9 模块零 GUI 依赖）+ 注册为 run_wechat_writer 工具（文档创作组）；真实核验通过：真实 RSS 采集→LLM 选题（两次运行主题自动去重）→三阶段写作（初稿缺来源标注自动重写）→质检 100 分→草稿箱/存档/HTML 落盘；修复 knowledge_index 增量复用秒级 mtime 误判（改纳秒）；全量测试 278 通过 |
| 1.10.14 | 新增 9 个工具（按 PRD 需求文档）：pdf_extract（PyMuPDF 文本/表格/元数据/页码范围/扫描件提示/加密提示）/ pdf_create（reportlab + Markdown 渲染 + 中文字体自动嵌入 + 与 pdf_extract 闭环）/ docx_read（标题层级/列表/表格→Markdown，保持文档顺序）/ pptx_read（标题/要点/备注/图片占位）/ rss_fetch（订阅管理 + 抓取去重 + 时间过滤）/ qrcode（生成 PNG + 识别多码，pyzbar 缺失降级）/ kv_store（diskcache 持久化 + TTL + 模糊检索）/ media_ffmpeg（info/截图/转码/提音频，参数白名单 + 2GB/300s 限制）/ webdav（httpx 原生 PROPFIND/GET/PUT/DELETE，凭据 DPAPI 加密）；全部遵循可选依赖模式（缺库返回可操作提示）、权限白名单、写入类工具进审批流；config.json 已注册默认启用；全量测试 249 通过 |
| 1.10.13 | 68 个工具逐一精修：send_email 多收件人修复（sendmail 传列表）；get_weather 的 date 真正生效（传 wttr.in，近 3 天预报）；read_email IMAP SINCE 英文月份（中文系统不再 BAD）；environment_info 包名映射（pillow→PIL）；read_file 按行模式单行截断（防数百 MB 单行 OOM）；image_process 逐操作明确报错 + 操作计数；write_csv/excel 混合行兜底；search_web 结果链接 SSRF/危险 URL 过滤；fetch_url 编码自适应（GBK 网页不再乱码）；PostgreSQL 查询 15s 语句超时；run_command/start_process 跟随工作目录执行（📁 传导）；cron 值域校验（分时日月周，main 与调度工具同步）；read_csv/excel/DB 查询单元格截断；chart_data 数据校验（非数值/NaN/饼图全零/kind 白名单）；image_generate size 白名单 + URL 20MB 限流；run_python 返回注明工作目录；write_code_project 字节级大小校验；schema 类型修正（integer/number）；pip 静音版本检查 |
| 1.10.12 | 全库加固与体验完善：run_python 沙箱 ast 深度检查（拦截 from-import 别名/importlib 动态导入/getattr·下标反射/写模式 open/pathlib 写，修复正则可绕过）；SSRF 加 DNS 重绑定防护（域名解析落内网即拦）；image_understand URL 图片 8MB 流式限流；自定义工具 endpoint SSRF 校验；自我进化提案移除 .bat 白名单；Webhook token 恒定时间比较 + 请求体上限；messages 复合读写加锁（worker 压缩/裁剪 vs 主线程快照/回填）；审批/询问/计划弹窗超时自动销毁；快照写盘防并发（_snapshot_writing）；摘要请求响应停止；失败判定前缀统一常量；run_python 沙箱 cwd 锚定工作区；流程编排检查-置位原子化；语音合成异步化防阻塞工具池；桌面通知/OCR 占位符替换修复；定时任务弹窗降级状态栏；外部配置原子写统一；编辑器增强（Tab/Shift+Tab 缩进、括号自动配对、配对退格、Ctrl+Backspace 删词）；聊天增强（右键朗读指定消息、快速动作扩充至 8 项、F5 重新生成）；Whisper 模型实例缓存；search_local 控制流清理；全量测试 181 通过 |
| 1.10.11 | 全代码库审查修复（30+ 项）：语音/OCR 失效修复（subprocess 补漏）、worker 线程安全（Tk 变量捕获）、会话 ID 路径穿越防护、SSRF 防护（内网/回环/元数据地址）、数据库只读校验强化（INTO OUTFILE/pg_read_file 等拦截）、run_python 静态黑名单恢复、Windows 进程树终止、权限模型大小写/符号链接/shlex 修复、工具停止竞态（副作用如实记录）、后台导出/落盘不再冻结 UI、右键菜单子菜单泄漏、_trim_context O(n²) 修复、分帧渲染挂起项丢失修复、面板 destroy 后守卫等；全量测试 120 通过 |
| 1.10.8 | 模式三态单选：标准 / 🤖完全智能 / 💬纯对话（互斥，消除同时开启的语义冲突），切换即时生效并持久化 |
| 1.10.7 | 建议展示改固定停靠：菜单栏右侧独立建议区（不弹窗不遮挡），采纳/关闭按钮，60 秒自动隐藏 |
| 1.10.6 | 纯对话模式人格重写：纯正向设定（博学友善的对话伙伴），零工具/任务/否定式词汇（消除"此地无银三百两"），记忆引导语自然化 |
| 1.10.5 | 对话/任务分离：纯对话模式（不注入工具提示词/行为指令/成功模式/任务记录，不传工具 schema，AI 回归纯粹对话写作能力），状态栏 💬 标识 |
| 1.10.4 | 任务面板懒启动：纯对话不再弹出悬浮面板（首个工具调用时才显示），观感大幅改善 |
| 1.10.3 | 采纳功能建议一期：智能思考档 auto（按复杂度路由 none/high/max）、主动建议引擎（代码块/模板/工作目录启发式 + 右下角建议条一键采纳）、项目任务记录（tasklog 跨会话交接，按工作目录） |
| 1.10.2 | 自我进化第三维度：功能建议——鲸语基于自我认知提出新功能/升级方向（名称/价值/实现思路/复杂度/优先级），产出建议 MD 文档 |
| 1.10.1 | 采纳第二轮审查新增项：Profile API Key DPAPI 加密（明文不再落盘）、产物核验覆盖 write_code_project/edit_file、run_python 入审批名单、stats 公共只读接口、统一原子 JSON 写（快照/会话/状态文件）、摘要显式超时 |
| 1.10.0 | 采纳第二份审查报告（6 核心 + 3 观察）：run_python 静态危险拦截（防任意代码执行，修正提案正则误拦）、calculate 移除幂+深度/位数限制（防 DoS）、publish_draft 路径穿越防护、crypto 加密 fail-closed（明文不落盘）、stats 原子写、首启误报修复、exporters 粗体成对替换、stop_process 等待回收、read_project_file 分页读取 |
| 1.9.5 | 自我进化工作流重构：审查产出改为**报告 MD 文档**（鲸语做诊断、开发 AI 做实施，职责分离），审查指令强制报告结构（问题总览/现状代码/替换代码/验证方式），新增「打开审查报告目录」 |
| 1.9.4 | 采纳自我进化提案（9 项）：循环防护补齐 tool 结果（防悬空 400）、stats 加锁、read_file 纳入权限模型（安全收紧）、tokens 真 LRU、exporters 行内代码修复、crypto 解密失败处理、save_config 原子写、隐私模式彻底移除文件日志、关键异常补日志 |
| 1.9.3 | 自我进化工具无条件可用（不受 enabled_tools/工具开关限制）、审查指令明确专用工具与项目位置（禁止用工作区工具分析自身） |
| 1.9.2 | 自我进化主动化：管理员一键发起自我审查（5 个重点可选）、定期督促提醒（evolution_reminder_days 默认 7 天） |
| 1.9.1 | 产物核验闭环：写工具返回真实核验结果（实际字节/存在性）、write_code_project 逐文件明细、verify_files 核验工具、行为指令强制写后自检、任务报告自动核验产物（缺失标记 ⚠） |
| 1.9.0 | 自我进化：感知自身代码（project_info/read_project_file）+ 分支提案（create_evolution）+ 提案查看/差异预览/采纳（.evobak 备份）/忽略 |
| 1.8.0 | 任务质量闭环：行为指令注入（先计划/自检/验证）、相关文件自动注入、项目关键文件摘要、成功模式记忆、environment_info 环境感知、验证步骤强制化 |
| 1.7.2 | 工作目录机制：AI 明确任务执行位置（active_dir），用户一键指定/新建子目录（自动加入权限），状态栏常显，工作目录提示注入每次请求，场景包自动重置 |
| 1.7.1 | 进程终端：后台服务器/长驻进程实时输出终端（独立窗口），start_process/stop_process/list_processes 工具，Python 自动 -u 无缓冲，退出自动清理进程 |
| 1.7.0 | 任务执行可见性：悬浮任务面板（实时工具状态/统计/产物）、工具卡片结果摘要与失败自动展开、任务完成报告升级、失败即时提示 |
| 1.6.1 | 完全智能模式：允许目录内全自动免审批（系统阻止列表仍生效），设置面板一键开启/关闭，状态栏 🤖 标识 |
| 1.6.0 | 开箱即用与产品化：场景包（办公/开发/创作一键配置）、试玩任务库（10 个）、项目上下文注入、任务报告、一键回滚 .bak、省钱报告、反馈收集、对话分享、工具进度名称显示 |
| 1.5.0 | 效率与智能体闭环：API Key DPAPI 加密、输入草稿持久化、剪贴板即问、数据清理、任务计划确认、工作区文件树、长期记忆、完成通知、定时任务、语音输入、剪贴板 OCR、缓存统计增强 |
| 1.4.0 | 智能体 L2-L4：write_code_project（多文件工程）/ browser_navigate / web_screenshot（Playwright 可选）/ publish_draft（本地草稿箱，只建草稿）+ Agent 任务模板扩展至 6 个 |
| 1.3.0 | 智能体化 L1-L3：权限模型（permissions.py，默认全关）+ 审批流（confirm 弹窗）+ 审计日志 + 6 个行动工具（write_file / edit_file / list_dir / run_command / search_local / create_doc），全部走现有工具循环与队列协议 |
| 1.2.0 | 非破坏性演进：导出 HTML/JSONL（exporters.py）、输入历史 Alt+↑/↓、Ctrl+Enter 发送、Ctrl+Shift+V 链接粘贴、Profile 多账号、长会话惰性折叠（默认关）、自动更新前自动备份 |
| 1.1.0 | 品牌化：更名「鲸语 WhaleTalk」，数据目录迁移，深海蓝鲸启动界面；新增 JSON 输出 / Beta API（前缀续写 + FIM）/ 回复变体 / 引用回复 / 会话置顶 / 收藏跳转 / 峰谷定价感知 / 缓存警示 / 输入 token 估算 / 思考动画 / 流式智能跟随 / 历史库批量删除；大量性能与健壮性优化 |
| 1.0.0 | 初版：流式对话、思考模式、Agent 工具调用、多会话、上下文压缩、统计预算、历史会话库等核心能力 |
