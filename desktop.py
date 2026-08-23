# -*- coding: utf-8 -*-
"""鲸语 WhaleTalk 桌面端入口（pywebview + 系统托盘）。

启动流程：
1. 内嵌启动本地 API 服务（api_server，127.0.0.1:8745，同源服务前端 dist）
2. 等待端口就绪
3. pywebview 创建原生窗口加载 http://127.0.0.1:8745/
4. 系统托盘（pystray）：显示 / 隐藏 / 退出；关闭窗口 → 最小化到托盘（可配置）

运行：python desktop.py
"""
import logging
import os
import sys
import threading
import time
import socket
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_PORT = 8745
APP_NAME = "鲸语 WhaleTalk"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("whaletalk.desktop")

# 导入项目模块（桌面端运行于项目根）
sys.path.insert(0, BASE_DIR)
import api_server  # noqa: E402


def _port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _start_api():
    """启动本地 API（幂等：已运行则复用）。"""
    # 若 8745 已是鲸语 API（/v1/token 可访问），直接复用（如浏览器版已启动）
    if _port_open(API_PORT):
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/v1/token", timeout=2) as r:
                if r.status == 200:
                    logger.info("复用已有 API: http://127.0.0.1:%s", API_PORT)
                    return True
        except Exception:
            pass
    try:
        import config_utils
        cfg = config_utils.load_config()
        tok = str(cfg.get("inbound_token") or "").strip()
    except Exception:
        tok = ""
    port, token, err = api_server.start_server(port=API_PORT, token=tok)
    if err:
        logger.error("API 启动失败: %s", err)
        return False
    if not _port_open(API_PORT):
        t0 = time.time()
        while time.time() - t0 < 15:
            if _port_open(API_PORT):
                break
            time.sleep(0.3)
    logger.info("API 就绪: http://127.0.0.1:%s", port)
    return True


def _cfg_bool(key, default):
    try:
        import config_utils
        return bool(config_utils.load_config().get(key, default))
    except Exception:
        return default


def _make_tray(window):
    """系统托盘：显示 / 隐藏 / 退出。"""
    import pystray
    from PIL import Image, ImageDraw

    def icon_img():
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([6, 14, 58, 56], fill=(14, 165, 233, 255))
        d.ellipse([12, 20, 52, 50], fill=(56, 189, 248, 255))
        d.arc([24, 30, 40, 48], start=200, end=340, fill=(255, 255, 255, 230), width=3)
        return img

    def on_show(icon, item):
        try:
            window.show()
            window.restore()
        except Exception:
            pass

    def on_hide(icon, item):
        try:
            window.hide()
        except Exception:
            pass

    def on_quit(icon, item):
        icon.stop()
        try:
            window.destroy()
        except Exception:
            pass
        api_server.stop_server()
        os._exit(0)

    try:
        menu = pystray.Menu(
            pystray.MenuItem("🐳 显示鲸语", on_show, default=True),
            pystray.MenuItem("🙈 隐藏窗口", on_hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", on_quit),
        )
        tray = pystray.Icon("whaletalk", icon_img(), APP_NAME, menu)
        return tray
    except Exception as e:
        logger.warning("托盘启动失败: %s", e)
        return None


def main():
    # 1. 启动 API
    if not _start_api():
        logger.error("API 启动失败，退出")
        return 1

    # 2. 导入 webview（延迟 import：确保 API 已就绪）
    import webview

    # 3. 创建窗口
    window = webview.create_window(
        APP_NAME,
        f"http://127.0.0.1:{API_PORT}/",
        width=1200,
        height=800,
        min_size=(900, 600),
        confirm_close=False,
    )

    # 4. 托盘（后台线程）
    tray = _make_tray(window)
    if tray is not None:
        threading.Thread(target=tray.run, daemon=True).start()

    # 5. 主循环
    try:
        webview.start(debug=False)
    except KeyboardInterrupt:
        pass
    finally:
        api_server.stop_server()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())