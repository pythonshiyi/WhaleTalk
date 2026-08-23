# -*- coding: utf-8 -*-
"""一键启动：WhaleTalk WebUI（生产模式）。

流程：检查/启动本地 API（api_server.py，8745 端口，同时服务前端静态页面）
→ 自动打开浏览器 http://127.0.0.1:8745/（同源，token 自取，零配置）。

开发模式（改 UI 代码热更新）：单独运行 webui/npm run dev。
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PORT = 8745

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def _port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _wait_port(port, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _port_open(port):
            return True
        time.sleep(0.4)
    return False


def _start_api():
    if _port_open(API_PORT):
        print(f"[API] 已在运行：http://127.0.0.1:{API_PORT}")
        return True
    log = os.path.join(BASE_DIR, "data", "webui_api.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    try:
        p = subprocess.Popen(
            [sys.executable, "-u", "api_server.py"],
            cwd=BASE_DIR,
            stdout=open(log, "a", encoding="utf-8", errors="replace"),
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        print(f"[API] 启动失败: {e}")
        return False
    if not _wait_port(API_PORT):
        print(f"[API] 启动超时（PID {p.pid}）")
        return False
    print(f"[API] 已启动：http://127.0.0.1:{API_PORT}")
    return True


def main():
    if not _start_api():
        print("启动失败，请检查日志。")
        return 1
    url = f"http://127.0.0.1:{API_PORT}/"
    print(f"打开界面：{url}")
    webbrowser.open(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())