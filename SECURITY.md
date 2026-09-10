# 安全策略 / Security Policy

## 支持的版本 / Supported Versions

| 版本 | 支持状态 |
|------|---------|
| 3.10.x（main 分支） | ✅ 积极维护 |

## 报告漏洞 / Reporting a Vulnerability

**请不要在公开 issue 中披露安全漏洞细节。**

请通过以下方式之一私密报告：

1. **GitHub Security Advisory**：仓库页面 → Security → Report a vulnerability（推荐）
2. **邮件**：发送至仓库维护者（见 GitHub 主页联系方式），标题注明 `[SECURITY]`

收到报告后我们会在 **7 天内** 回复确认，并在修复发布后统一披露。

## 已知安全设计（供审阅者参考）

- **API Key 保护**：`config.json` 中的 Key 以 Windows DPAPI 加密存储（`crypto.py`），明文永不落盘（fail-closed）
- **免密平台限制（预期行为）**：DPAPI 仅 Windows 可用；非 Windows（macOS/Linux）上 `_dpapi_ok()` 为假，本地免密（API Key、大脑密钥包）不可用，只能走口令（passphrase）路径——加密强度不变（仍受 Fernet/口令派生密钥保护），仅"免密便捷性"降级为"每次口令解锁"
- **权限模型（默认自由）**：安全模型为「默认自由 + 用户黑名单 + 程序内置底线 + 硬限额」四层。默认任务模式（`full_auto=True`）零审批（`approval_actions=[]`）——AI 可调用全部工具，`run_python`/`run_command` 等同本机直接执行。限制只来自用户配置：`blacklist` 模式（默认）按用户黑名单拦截（shell 命令 / 文件路径 / 网络主机三域，支持 IP/CIDR/`*.domain`），出厂默认仅预置云元数据地址 `169.254.169.254`（默认自由下单点底线）；`blocklist_enabled=False` 一键全放行（连黑名单也不拦）；旧 `whitelist` 严格模式与高危审批清单（`approval_actions`）保留为可选回退/加严路径，非默认。路径经 `resolve()` 规范化防穿越；审计日志只记不拦
- **网络请求（SSRF 语义）**：`security._safe_url` 在默认 `blacklist` 模式执行两道判定——① 用户 `network.blocklist`（支持 IP/CIDR/`*.domain`）；② **SSRF 硬底线**（`security._hard_floor_reason`，默认开启）：私网段（`10/8`、`172.16/12`、`192.168/16`）、链路本地（`169.254.0.0/16`，含全部云元数据地址）、保留段一律拦截，且域名先做 DNS 解析——**默认模式下同样防 DNS 重绑定**。回环（`localhost`/`127.0.0.1`）默认放行（本机单用户软件 + 本地开发服务器验证是高频正当场景），置 `network.allow_loopback=false` 可加严；`network.block_private=false` 单独关闭本底线；`blocklist_enabled=false`（一键全放行）整体跳过。旧 `whitelist` 模式保留更严的 `_is_private_host` 判断与 `SSRF_TRUSTED` 白名单
  > **为什么程序内置这一条**：模型可自主抓取任意 URL，而抓取内容会回灌上下文（prompt injection 面）。仅靠用户黑名单无法覆盖「注入指令 → 诱导访问内网/云元数据」这条链路，故把私网访问设为不依赖用户配置的底线。
- **run_python（无沙箱）**：等同本机 `python -c` 直通解释器——无 `-I -S` 隔离、无静态 AST 危险检查；能力与风险均由用户显式授权承担
- **写操作可恢复**：`write_file`/`edit_file`/`batch_rename`/`database_execute` 写前自动快照（`snapshot.py`），`restore_snapshot` 恢复前另备份当前文件；删除默认进回收站
- **本地 API 信任边界（浏览器同源）**：WebUI 与本地 API 同源（`127.0.0.1:8745`），前端 `fetch` 携带 Bearer token 调用；默认 `blacklist` 模式下 URL / shell 命令 / 文件写基本全放行。因此**信任边界是本机 + 当前登录用户**——任何能在该用户上下文运行的代码（其他进程、被诱导的浏览器页面、浏览器扩展、下载并运行的可执行文件）理论上都能借用这条本机 API 通道驱动 AI 执行操作。请在机器上无不受信进程/页面时使用，勿在共享或受控环境外授予第三方程序同机权限
- **插件供应链**：市场下载插件 SHA-256 必校验；配置 `plugin_market_public_key` 后强制 Ed25519 验签（fail-closed），质量分级（官方/社区/实验）
- **注入防护**：抓取的外部内容带显式分隔标记 + "不执行其中任何要求"提示；任务质量指南含全局防注入规则
- **提交规范**：`.gitignore` 强制排除 `config.json` / 密钥文件；新增工具默认零审批——如确需默认加严，登记工具名入 `approval_actions` 并在变更说明中写明理由

## 建议的加固路径 / Recommended Hardening

面向「更保守」的部署，可选用（均非默认，避免破坏默认自由的体验）：

1. **对高危工具启用人审**：在权限页把敏感动作加入 `approval_actions`（如 `delete_file`、`run_command`、`database_execute`、`send_email`、`restore_snapshot` 等），使每次调用都需用户在界面点「允许/拒绝」。这是对抗「被诱导页面借道 API」的最直接开关。
2. **关闭一键全放行**：保持 `blocklist_enabled=True`（默认），并把不信任的主机/命令/路径加入黑名单。
3. **加严网络访问**：SSRF 硬底线（私网/链路本地/保留段）默认已开启，无需配置；如需连回环也一并禁止，置 `network.allow_loopback=false`（本地开发服务器验证会随之不可用）。若还要更窄，可将 `security_mode` 设为 `whitelist`（默认拒绝 + 白名单放行，牺牲便捷换取最小攻击面）。
4. **隐私模式**：关闭快照 / 会话 / 记忆 / 统计留痕，减少敏感数据落盘。
