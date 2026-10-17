# 贡献指南 / Contributing

感谢你对 **鲸语 WhaleTalk** 的兴趣！修复 bug、新增功能、改进文档或报告 issue，我们都非常欢迎。

*Thanks for your interest in WhaleTalk! Bug fixes, new features, docs, and issue reports are all welcome.*

## 开发环境 / Dev Setup

- Python 3.9+（推荐 3.12）
- 安装依赖：`pip install -r requirements.txt`（核心依赖 openai / httpx 已在清单中，其余为可选增强）
- 引导/运行：`python bootstrap.py`（建 `.venv` + 逐包装依赖 + 启动；`--no-venv` 复用当前解释器，`check` 只体检，其余参数透传 `web_app.py`）
- 运行（跳过引导）：`python web_app.py`（本地 API + 自动打开浏览器 + 托盘常驻；首次启动自动构建 WebUI——`webui/dist` 缺失或源码更新时自动 `npm run build`，已构建则跳过，`--no-webui-build` 可跳过）
- 打包：`python bootstrap.py build`（产出 `dist\WhaleTalk.exe`）

## 前端开发 / Frontend Dev

```bash
cd webui
npm install
npm run dev      # Vite 热更新开发服务（API 直连 127.0.0.1:8745）
npm run build    # 产物输出 webui/dist（由 api_server 同源服务）
```

## 架构规范 / Architecture

- 产品形态：**纯 Web + 本地 API 常驻**。浏览器是唯一界面；`web_app.py` 是唯一入口；旧 Tkinter 桌面已移除
- 模块职责（完整清单见 [MODULES.md](MODULES.md)，接手先读 [docs/AI_PROJECT_GUIDE.md](docs/AI_PROJECT_GUIDE.md)）：
  - `web_app.py`：启动入口（本地 API + 浏览器 + 托盘/快捷方式/开机自启）
  - `api_server.py`：本地 HTTP API（REST + SSE 流式，102 个 `/v1` 路由），同源服务前端构建产物
  - `deepseek_client.py`：统一模型客户端 + 六层工具注册表 + smart_tools（工具实现已迁至 `agent_tools/`）
  - `agent_tools/tool_*.py`：165 个工具的实现；用 `@tool()` 声明（单一事实源）
  - `permissions.py`：权限模型（默认自由：黑名单主导 + `blocklist_enabled` 一键开关；审计只记不拦）
- 所有用户可见输入（路径 / 命令 / SQL/工具参数）必须经校验：路径走 `permissions.resolve()`；命令走 `permissions.check_shell()`；网络请求走 `permissions.check_network_host()`（blacklist 模式只拦用户黑名单；旧 whitelist 模式回退 `security._safe_url` 严格 SSRF 判断）；路径越界 / 注入防护不得绕过
- 写文件类工具必须返回真实结果（字节数 / 行数 / 差异），禁止用"假成功"占位
- 错误处理遵循"显式失败"原则：缺依赖/不可用时必须向用户明确报错与安装指引，禁止静默吞错后假装成功
- 工具返回约定（历史沉淀，全仓 500+ 处内联统一）：错误一律以「错误：」开头（全角冒号）；成功态用结构化 Markdown（标题 + 明细行）；纯数据才返回 JSON 字符串（以 `{` 开头）——返回契约不需要额外包装函数
- 日志用 `logging`；异常不要吞掉——`except Exception` 至少要 `logging.exception`
- 中文注释为主 + 必要英文注释；注释解释「为什么」，而不是「是什么」

## 质量门禁 / Quality Gates

**改动工具系统前，务必本地跑：**

```bash
python tools/audit_tools.py --strict   # 六层一致性审计（schema↔实现↔短语↔分组↔预激活），门禁模式
python tools/validate_tools.py         # smart_tools 全链路回归（能力地图/compact/schema 合法性）
python tools/island_check.py --strict  # 十层孤岛对账（工具可达性 + __all__ re-export）
python tools/check_docs.py             # 文档数字 vs 源码实测
```

- 工具声明是**单一事实源**：新增/修改工具只在函数定义处写一次 `@tool()`（`schema` / `groups` / `phrases` / `preactivate` / `hooks`），六层数据由注册表自动生成。随后把工具名加入 `deepseek_client.py` 的 `_TOOL_ORDER`（涉及分组/预激活时再加 `_GROUP_ORDER` / `_HINT_ORDER`），并在域模块 `__all__` 与 `agent_tools/__init__.py.__all__` re-export
- 新增工具默认**零审批**（blacklist 主导，`approval_actions` 默认空）。仅当设计上确需让用户可选加严时，才把工具名登记入 `permissions` 的 `approval_actions`（blacklist 模式）或 `ACTION_TOOLS`（旧 whitelist 模式），并在变更说明中写明理由；否则不要登记
- 工具描述要完整说清「做什么 + 关键约束」，**没有长度上限**——smart 模式不截断描述（描述是工具能力的一部分，不得为省 token 删减）；参数描述必须 100% 覆盖；数组参数必须带 `items`（缺则 API 400）
- 当前 CI（`.github/workflows/ci.yml`）执行 ruff 关键规则 + 入口编译检查 + WebUI 构建 + `pytest tests/`（86 文件 / 852 用例）+ 工具系统四道门禁；前端另有 `npm test`（21 个 node 套件）与 `npm run typecheck`。本地改完先跑四道门禁，再 `python -m pytest -q` 与 `cd webui && npm test`。

### 按改动范围分级验证（不要每次都跑全量）

**全量跑一次约 2 分钟（1060 用例 73s + 四道门禁 + 前端 22 套件），而最小相关集通常只要 1–3 秒。**
日常改动按下表选最小的一档；**全量留给里程碑节点**（大功能收尾、发版前、跨模块重构、用户明确要求），
判断标准是「改动的影响面」，不是「心理上的安全感」。总的原则：**先跑最小相关集，绿了就走**。

| 改了什么 | 最小相关集 | 耗时 |
|---|---|---|
| 文档 / 注释 / 文案 | 不动测试；改了规模数字才跑 `check_docs.py` | ~1s |
| 单个 `.py` 的逻辑小改 | `pytest tests/test_<对应领域>.py -q` | 2–3s |
| 新增 / 修改工具 | `audit_tools.py --strict` + 对应域模块测试 | ~3s |
| API 端点改动 | `pytest tests/test_api_routes.py -q` | ~3s |
| 前端改动 | `cd webui && node runTests.mjs`（或单跑某个 `*.test.mjs`） | 单套 ~1s |
| 跨模块 / 大重构 / 发版前 | 全量：四道门禁 + `pytest -q` + `npm test` | ~2min |

> 阅读代码、排查问题、回答问题这类**不改代码**的任务不需要跑任何测试。
> 需要跑全量时先说明理由再跑，而不是默认就跑——「跑得多」不等于「改得对」。

## 提交信息 / Commit Messages

- 类型前缀：`fix:` / `feat:` / `docs:` / `chore:` / `refactor:`
- 示例：`fix: run_command 接入 check_shell（黑名单生效）`

## 推送 / 远程协作 / Pushing

远程仓库：`github.com/pythonshiyi/WhaleTalk`（分支 `main`）。origin 的 fetch/push 都是 https，
无需改 ssh：

```bash
git remote -v          # 应为 https://github.com/pythonshiyi/WhaleTalk.git
git push origin main
```

凭据由 `credential.helper=wincred`（Windows 凭据管理器）自动提供。2026-09 实测 **https 直推可用**，
不必盲配 ssh key。若 `git push` 长时间无输出 / SSL 握手失败：先 `env | grep -i proxy` 确认是否存在
`HTTP_PROXY` / `HTTPS_PROXY`，停掉卡住的进程后重试。

### 核验是否推送成功（用 ls-remote，别信本地陈旧引用）

本地跟踪引用 `origin/main` 可能不会自动更新，`git log origin/main..HEAD` 会误报"本地领先 N 个提交"。
以**远程真实 HEAD** 为准：

```bash
git ls-remote origin refs/heads/main    # 远程真实 HEAD
git rev-parse HEAD                       # 两者一致 = 已同步
```

### 行尾（提交前必读）

`.gitattributes` 声明 `*.py`/`*.md` 等一律 `eol=lf`，**仓库里所有 blob 存的就是 LF**，从未有 CRLF 入库。
只是在 Windows 上 `core.autocrlf=true` 会让**工作区副本**是 CRLF（`git ls-files --eol` 的 `w/crlf`），
提交时由 `eol=lf` 属性自动归一成 LF——这是**正确且期望的**行为，**不会**造成整文件差异（实测：给
`api_server.py` 做一次无实质改动后 `git add`，`git diff --cached --numstat` 为 0 行）。

- **正常 `git add` 即可**，不需要任何额外步骤。
- **不要**用 `* -text` 覆盖 `.git/info/attributes`：那会关掉归一化，把工作区的 CRLF 原始字节塞进
  一个存 LF 的仓库，反而制造整文件差异（实测 AI 指南：正常 add 是 25 行改动，`-text` 后变成 705/683 行）。
- 想确认某文件的实际状态：`git ls-files --eol <文件>`——`i/` 是仓库内存储（恒为 LF）、`w/` 是工作区。
- 判断"是否真有改动"用 `git diff --ignore-cr-at-eol`；见到莫名整文件差异先查行尾再查逻辑。

### 本地不入库产物

`能力差距分析_*.md`、`*能力报告_*.md`、`*阅读报告_*.md` 等分析文档历来**不入库**（项目只提交代码/前端/测试/依赖），推送前不必 `git add` 它们。`brain/`/`trust/`/`evolutions/`/`data/` 已在 `.gitignore`。
