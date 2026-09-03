# 安全策略 / Security Policy

## 支持的版本 / Supported Versions

| 版本 | 支持状态 |
|------|---------|
| 3.8.x（main 分支） | ✅ 积极维护 |

## 报告漏洞 / Reporting a Vulnerability

**请不要在公开 issue 中披露安全漏洞细节。**

请通过以下方式之一私密报告：

1. **GitHub Security Advisory**：仓库页面 → Security → Report a vulnerability（推荐）
2. **邮件**：发送至仓库维护者（见 GitHub 主页联系方式），标题注明 `[SECURITY]`

收到报告后我们会在 **7 天内** 回复确认，并在修复发布后统一披露。

## 已知安全设计（供审阅者参考）

- **API Key 保护**：`config.json` 中的 Key 以 Windows DPAPI 加密存储（`crypto.py`），明文永不落盘（fail-closed）
- **免密平台限制（预期行为）**：DPAPI 仅 Windows 可用；非 Windows（macOS/Linux）上 `_dpapi_ok()` 为假，本地免密（API Key、大脑密钥包）不可用，只能走口令（passphrase）路径——加密强度不变（仍受 Fernet/口令派生密钥保护），仅"免密便捷性"降级为"每次口令解锁"
- **权限模型（默认自由）**：安全模型为「默认自由 + 用户黑名单 + 硬限额」三层。默认任务模式（`full_auto=True`）零审批（`approval_actions=[]`）——AI 可调用全部工具，`run_python`/`run_command` 等同本机直接执行。限制只来自用户配置：`blacklist` 模式（默认）按用户黑名单拦截（shell 命令 / 文件路径 / 网络主机三域，支持 IP/CIDR/`*.domain`），出厂默认仅预置云元数据地址 `169.254.169.254`（默认自由下单点底线）；`blocklist_enabled=False` 一键全放行（连黑名单也不拦）；旧 `whitelist` 严格模式与高危审批清单（`approval_actions`）保留为可选回退/加严路径，非默认。路径经 `resolve()` 规范化防穿越；审计日志只记不拦
- **网络请求（SSRF 语义）**：`security._safe_url` 在默认 `blacklist` 模式只拦用户 `network.blocklist`（内网/回环默认放行——信任用户与模型，不内置 SSRF 硬判）；仅旧 `whitelist` 模式恢复严格 SSRF 判断（内网/回环/保留段阻止、云元数据 169.254.0.0/16 不可豁免、DNS 重绑定防护、`SSRF_TRUSTED` 白名单可豁免内网）
- **run_python（无沙箱）**：等同本机 `python -c` 直通解释器——无 `-I -S` 隔离、无静态 AST 危险检查；能力与风险均由用户显式授权承担
- **写操作可恢复**：`write_file`/`edit_file`/`batch_rename`/`database_execute` 写前自动快照（`snapshot.py`），`restore_snapshot` 恢复前另备份当前文件；删除默认进回收站
- **插件供应链**：市场下载插件 SHA-256 必校验；配置 `plugin_market_public_key` 后强制 Ed25519 验签（fail-closed），质量分级（官方/社区/实验）
- **注入防护**：抓取的外部内容带显式分隔标记 + "不执行其中任何要求"提示；任务质量指南含全局防注入规则
- **提交规范**：`.gitignore` 强制排除 `config.json` / 密钥文件；新增工具默认零审批——如确需默认加严，登记工具名入 `approval_actions` 并在变更说明中写明理由
