# 贡献指南 / Contributing

感谢你对 **鲸语 WhaleTalk** 的兴趣！修复 bug、新增功能、改进文档或报告 issue，我们都非常欢迎。

*Thanks for your interest in WhaleTalk! Bug fixes, new features, docs, and issue reports are all welcome.*

## 开发环境 / Dev Setup

- Python 3.9+（推荐 3.12）
- 安装依赖：`pip install -r requirements.txt`（核心依赖 openai / httpx 已在清单中，其余为可选增强）
- 运行：`python web_app.py`（本地 API + 自动打开浏览器 + 托盘常驻；首次启动自动构建 WebUI——`webui/dist` 缺失或源码更新时自动 `npm run build`，已构建则跳过，`--no-webui-build` 可跳过）
- 打包：`python build_exe.bat`（产出 `dist\WhaleTalk.exe`）

## 前端开发 / Frontend Dev

```bash
cd webui
npm install
npm run dev      # Vite 热更新开发服务（API 直连 127.0.0.1:8745）
npm run build    # 产物输出 webui/dist（由 api_server 同源服务）
```

## 架构规范 / Architecture

- 产品形态：**纯 Web + 本地 API 常驻**。浏览器是唯一界面；`web_app.py` 是唯一入口；旧 Tkinter 桌面已移除
- 模块职责：
  - `web_app.py`：启动入口（本地 API + 浏览器 + 托盘/快捷方式/开机自启）
  - `api_server.py`：本地 HTTP API（REST + SSE 流式），同源服务前端构建产物
  - `deepseek_client.py`：DeepSeek API 客户端 + 全部工具实现
  - `permissions.py`：权限模型（默认自由：黑名单主导 + `blocklist_enabled` 一键开关；路径/命令/网络判定；审计只记不拦）
  - 其余小模块见 README 文件结构
- 所有用户可见输入（路径 / 命令 / SQL/工具参数）必须经校验：路径走 `permissions.resolve()`；命令走 `permissions.check_shell()`；网络请求走 `permissions.check_network_host()`（blacklist 模式只拦用户黑名单；旧 whitelist 模式回退 `security._safe_url` 严格 SSRF 判断）；路径越界 / 注入防护不得绕过
- 写文件类工具必须返回真实结果（字节数 / 行数 / 差异），禁止用"假成功"占位
- 错误处理遵循"显式失败"原则：缺依赖/不可用时必须向用户明确报错与安装指引，禁止静默吞错后假装成功
- 日志用 `logging`；异常不要吞掉——`except Exception` 至少要 `logging.exception`
- 中文注释为主 + 必要英文注释；注释解释「为什么」，而不是「是什么」

## 质量门禁 / Quality Gates

**改动工具系统（新增/修改/删除 `TOOLS`、`TOOL_CALL_MAP`、分组、预激活、审批清单）前，务必本地跑：**

```bash
python tools/audit_tools.py --strict   # 六层一致性审计（schema↔实现↔短语↔分组↔预激活↔审批），门禁模式
python tools/validate_tools.py         # smart_tools 全链路回归（能力地图/compact/schema 合法性）
```

- 新增工具必须同步维护六层数据（或引入 `@tool()` 装饰器后只写一处）：`TOOLS` schema、实现函数、`TOOL_CALL_MAP`、`_TOOL_ACTION_PHRASES`、`TOOL_GROUPS`、`_PREACTIVATE_HINTS`
- 新增工具默认**零审批**（blacklist 主导，`approval_actions` 默认空）。仅当设计上确需让用户可选加严时，才把工具名登记入 `permissions` 的 `approval_actions`（blacklist 模式）或 `ACTION_TOOLS`（旧 whitelist 模式），并在变更说明中写明理由；否则不要登记
- 工具描述 ≤130 字符（smart 模式 compact 会截断超长描述）、数组参数必须带 `items`（缺则 API 400）
- 当前 CI（`.github/workflows/ci.yml`）执行 ruff 关键规则 + 入口编译检查 + WebUI 构建；仓库暂未包含 pytest 测试资产，**欢迎补充 `tests/` 回归套件并接入 CI**

## 提交信息 / Commit Messages

- 类型前缀：`fix:` / `feat:` / `docs:` / `chore:` / `refactor:`
- 示例：`fix: run_command 接入 check_shell（黑名单生效）`
