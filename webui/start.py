# -*- coding: utf-8 -*-
"""一键启动：WhaleTalk WebUI（生产模式）。

流程：启动本地 API（api_server.py，8745 端口，同源服务前端静态页面）
→ 自动打开默认浏览器 http://127.0.0.1:8745/（同源，token 自取，零配置）
→ 系统托盘常驻（关浏览器不退出服务；托盘「退出」停止）。

注意：本文件只是 web_app.py 的别名入口（供开机自启等场景），
真正的逻辑与参数都在 web_app.py。

开发模式（改 UI 代码热更新）：单独运行 webui/npm run dev。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def main():
    from web_app import main as web_main
    # 默认：浏览器 + 托盘常驻
    return web_main()


if __name__ == "__main__":
    raise SystemExit(main())
