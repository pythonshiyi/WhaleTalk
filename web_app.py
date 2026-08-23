# -*- coding: utf-8 -*-
"""鲸语 WhaleTalk · Web 版启动入口。

形态：
- Web 前端（webui/dist，React 构建产物，由 api_server 同源服务，127.0.0.1:8745）
- API 后端（api_server.py，同源服务前端静态文件）
- 桌面外壳（desktop.py，pywebview + pystray 托盘；本文件桌面模式即走它）
- tkinter 桌面保底（main.py 原版，`--legacy`）

用法：
    python web_app.py            → 桌面窗口（pywebview + 托盘）
    python web_app.py --browser  → 默认浏览器打开页面（不开原生窗口）
    python web_app.py --server   → 只启动 API 服务（终端常驻）
    python web_app.py --legacy   → 旧版 tkinter 桌面（保底回退）
"""
import argparse
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

APP_NAME = "鲸语 WhaleTalk"
API_PORT = 8745


def _start_api(port):
    """确保本地 API 服务运行（幂等：已运行则复用）。"""
    import api_server
    try:
        import config_utils
        tok = str(config_utils.load_config().get("inbound_token") or "").strip()
    except Exception:
        tok = ""
    return api_server.start_server(port=port, token=tok)


def _serve_forever(port, open_browser=False):
    """启动 API 并驻留（浏览器模式自动打开页面）。"""
    import api_server
    if not api_server.is_running():
        port, _, err = _start_api(port)
        if err:
            print(f"API 启动失败: {err}")
            return 1
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}/")
    print(f"鲸语 Web API 就绪：http://127.0.0.1:{port}（Ctrl+C 退出）")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        api_server.stop_server()
    return 0


def _legacy_main():
    """旧版 tkinter 桌面主循环（保底回退）。"""
    import main as legacy_app  # noqa: F401 -- 仅保底场景引用
    from main import main as legacy_run
    return legacy_run()


def _desktop_main():
    """pywebview 原生窗口 + 托盘（首选桌面形态）。"""
    try:
        from desktop import main as desktop_run
    except ImportError:
        print("缺少 pywebview：pip install pywebview")
        return 2
    return desktop_run()


def main():
    parser = argparse.ArgumentParser(prog="whaletalk-web", description=APP_NAME + " · Web 版")
    parser.add_argument("--browser", action="store_true", help="默认浏览器打开页面")
    parser.add_argument("--server", action="store_true", help="仅启动 API 服务")
    parser.add_argument("--legacy", action="store_true", help="旧版 tkinter 桌面")
    parser.add_argument("--port", type=int, default=API_PORT, help=f"API 端口（默认 {API_PORT}）")
    args = parser.parse_args()

    if args.server:
        return _serve_forever(args.port, open_browser=False)
    if args.browser:
        return _serve_forever(args.port, open_browser=True)
    if args.legacy:
        return _legacy_main()
    return _desktop_main()


if __name__ == "__main__":
    sys.exit(main())
