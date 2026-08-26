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
  - `permissions.py`：权限模型（默认全开，独立双工作线程池，可审计）
  - 其余小模块见 README 文件结构
- 所有用户可见输入（路径 / 命令 / SQL/工具参数）必须经校验：路径走 `permissions.resolve()`；命令走 `permissions.check_shell()`；SSRF / 路径越界 / 注入防护不得绕过
- 写文件类工具必须返回真实结果（字节数 / 行数 / 差异），禁止用"假成功"占位
- 错误处理遵循"显式失败"原则：缺依赖/不可用时必须向用户明确报错与安装指引，禁止静默吞错后假装成功
- 日志用 `logging`；异常不要吞掉——`except Exception` 至少要 `logging.exception`
- 中文注释为主 + 必要英文注释；注释解释「为什么」，而不是「是什么」

## 提交信息 / Commit Messages

- 类型前缀：`fix:` / `feat:` / `docs:` / `chore:` / `refactor:`
- 示例：`fix: run_python 沙箱补 ast 校验`
