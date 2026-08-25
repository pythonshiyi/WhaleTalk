# -*- coding: utf-8 -*-
"""鲸语 WhaleTalk · 唯一启动入口（纯 Web + 托盘常驻）。

产品形态（v3.1 定版）：
- 浏览器是唯一界面：本地 API（127.0.0.1:8745）同源服务前端，自动打开默认浏览器。
- 本地 API 进程常驻后台：关浏览器标签页/浏览器不关服务；系统托盘提供「显示/退出」。
- 托盘退出 = 停止服务 + 退出进程（不再有 pywebview 原生窗口，desktop.py 已废弃）。

原生软件体验：
- 桌面图标 + 开始菜单快捷方式（首次运行自动创建，--install-shortcuts 强制重建）
- 托盘菜单：打开界面 / 开机自启开关 / 完成提示音开关 / 桌面快捷方式 / 服务信息 / 退出
- 单实例：重复启动（exe/脚本）只打开浏览器，不重复起服务
- minimize_to_tray 开启时静默进托盘（不自动弹浏览器），托盘「打开界面」随时进入
- 开机自启：注册 HKCU Run（源码=pythonw 无窗 / 打包=exe 自身）

用法：
    python web_app.py             → 启动本地服务 + 打开浏览器 + 托盘常驻（推荐）
    python web_app.py --server    → 只启动 API 服务（终端常驻，供调试）
    python web_app.py --no-tray   → 常驻但不启用系统托盘（无 pystray 环境）
    python web_app.py --no-browser → 常驻但不自动打开浏览器（手动访问）
    python web_app.py --install-shortcuts → 强制重建桌面/开始菜单快捷方式

退出方式：
- 系统托盘菜单「退出」（停止服务并退出）
- 终端 Ctrl+C（--server 模式）
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


def _probe_existing(port):
    """探测端口是否已是鲸语 API（/v1/token 可访问）。"""
    try:
        import socket
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/token", timeout=2) as r:
                return r.status == 200
    except Exception:
        return False


def _hide_console():
    """Windows：隐藏自身控制台窗口（python.exe / bat / 任意入口启动都不黑窗）。
    --server 调试模式保持可见；WHALETALK_NO_HIDE=1 可显式恢复。"""
    if os.environ.get("WHALETALK_NO_HIDE") == "1":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def _open_browser(port):
    try:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}/")
    except Exception:
        pass


def _target_and_args():
    """快捷方式目标：打包=exe 自身；源码=pythonw 无窗起 webui/start.py。"""
    if getattr(sys, "frozen", False):
        return sys.executable, []
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return exe, [os.path.join(BASE_DIR, "webui", "start.py")]


def _create_shortcuts():
    """创建桌面 + 开始菜单快捷方式（幂等覆盖）；清理旧版 WhaleTalk.exe.lnk。返回 True/False。"""
    try:
        import subprocess
        import tempfile
        target, args = _target_and_args()
        ico = os.path.join(BASE_DIR, "app.ico")
        arg = " ".join(f'"{a}"' for a in args)
        icon_line = f'$s.IconLocation = "{ico}",0\r\n' if os.path.exists(ico) else ""
        make = (
            f"$s.TargetPath = '{target}'\r\n"
            f"$s.Arguments = '{arg}'\r\n"
            f"$s.WorkingDirectory = '{BASE_DIR}'\r\n"
            + icon_line
            + f"$s.Description = '{APP_NAME}（本地 AI 智能体）'\r\n"
            + "$s.Save()\r\n"
        )
        script = (
            "$ws = New-Object -ComObject WScript.Shell\r\n"
            # 清理旧版入口（老 pywebview/带黑窗的 WhaleTalk.exe 快捷方式），避免点错
            "Remove-Item ([Environment]::GetFolderPath('Desktop') + '\\WhaleTalk.exe.lnk') -ErrorAction SilentlyContinue\r\n"
            "Remove-Item ([Environment]::GetFolderPath('Programs') + '\\WhaleTalk.exe.lnk') -ErrorAction SilentlyContinue\r\n"
            f"$s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\\{APP_NAME}.lnk')\r\n"
            + make
            + f"$s = $ws.CreateShortcut([Environment]::GetFolderPath('Programs') + '\\{APP_NAME}.lnk')\r\n"
            + make
        )
        fd, ps_path = tempfile.mkstemp(suffix=".ps1")
        os.close(fd)
        try:
            with open(ps_path, "w", encoding="utf-8-sig") as f:
                f.write(script)
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_path],
                capture_output=True, timeout=30,
            )
            return r.returncode == 0
        finally:
            try:
                os.remove(ps_path)
            except OSError:
                pass
    except Exception:
        return False


def _shortcuts_exist():
    """新入口快捷方式已就绪 且 不残留旧版 WhaleTalk.exe.lnk（有残留即下次启动重建清理）。"""
    try:
        d = os.path.join(os.path.expanduser("~"), "Desktop", f"{APP_NAME}.lnk")
        old = os.path.join(os.path.expanduser("~"), "Desktop", "WhaleTalk.exe.lnk")
        return os.path.exists(d) and not os.path.exists(old)
    except Exception:
        return False


def _make_tray(stop_cb):
    """系统托盘：打开界面 / 自启开关 / 提示音开关 / 桌面快捷方式 / 服务信息 / 退出。"""
    import threading
    try:
        import pystray
        from pystray import Menu, MenuItem
        from PIL import Image, ImageDraw
    except Exception as e:
        print(f"[托盘] 不可用：{e}（--no-tray 可跳过）")
        return None

    def icon_img():
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([6, 14, 58, 56], fill=(14, 165, 233, 255))
        d.ellipse([12, 20, 52, 50], fill=(56, 189, 248, 255))
        d.arc([24, 30, 40, 48], start=200, end=340, fill=(255, 255, 255, 230), width=3)
        return img

    def _cfg():
        try:
            import config_utils
            return config_utils.load_config()
        except Exception:
            return {}

    def on_open(icon, item):
        _open_browser(API_PORT)

    def on_autostart(icon, item):
        try:
            import api_server
            cfg = _cfg()
            cur = bool(cfg.get("autostart", False))
            ok = api_server._apply_autostart(not cur)
            if ok:
                cfg["autostart"] = not cur
                import config_utils
                config_utils.save_config(cfg)
        except Exception:
            pass
        try:
            icon.update_menu()
        except Exception:
            pass

    def on_sound(icon, item):
        try:
            cfg = _cfg()
            cfg["completion_sound"] = not bool(cfg.get("completion_sound", True))
            import config_utils
            config_utils.save_config(cfg)
        except Exception:
            pass
        try:
            icon.update_menu()
        except Exception:
            pass

    def on_mini(icon, item):
        try:
            cfg = _cfg()
            cfg["silent_start"] = not bool(cfg.get("silent_start", False))
            import config_utils
            config_utils.save_config(cfg)
        except Exception:
            pass
        try:
            icon.update_menu()
        except Exception:
            pass

    def on_shortcut(icon, item):
        ok = _create_shortcuts()
        try:
            icon.visible = False
            icon.visible = True
        except Exception:
            pass
        return ok

    def on_quit(icon, item):
        icon.stop()
        try:
            stop_cb()
        except Exception:
            pass
        os._exit(0)

    def autostart_text(item):
        return "🚀 开机自启：开" if _cfg().get("autostart", False) else "🚀 开机自启：关"

    def sound_text(item):
        return "🔔 完成提示音：开" if _cfg().get("completion_sound", True) else "🔔 完成提示音：关"

    def mini_text(item):
        return "🖥 静默启动（不弹浏览器）：开" if _cfg().get("silent_start", False) else "🖥 静默启动（不弹浏览器）：关"

    try:
        menu = Menu(
            MenuItem("🌐 打开界面", on_open, default=True),
            Menu.SEPARATOR,
            MenuItem(autostart_text, on_autostart),
            MenuItem(sound_text, on_sound),
            MenuItem(mini_text, on_mini),
            Menu.SEPARATOR,
            MenuItem("📌 桌面快捷方式", on_shortcut),
            Menu.SEPARATOR,
            MenuItem(f"服务 http://127.0.0.1:{API_PORT}", None),
            MenuItem("✕ 退出", on_quit),
        )
        tray = pystray.Icon("whaletalk", icon_img(), APP_NAME, menu)
        return tray
    except Exception as e:
        print(f"[托盘] 启动失败：{e}")
        return None


def _serve_forever(port, open_browser=False, tray=True):
    """启动 API 并常驻（默认带系统托盘；单实例：已有服务时只打开界面）。"""
    import api_server
    if not api_server.is_running():
        if _probe_existing(port):
            # 已有实例在运行（开机自启/前端已常驻）：本进程只负责打开界面后退出
            if open_browser:
                _open_browser(port)
                print(f"服务已在运行：http://127.0.0.1:{port}（已打开界面，本进程退出）")
            return 0
        port, _, err = _start_api(port)
        if err:
            print(f"API 启动失败: {err}")
            return 1
    if open_browser:
        _open_browser(port)
    print(f"鲸语 Web API 就绪：http://127.0.0.1:{port}（托盘常驻，关闭浏览器不退出）")
    print("提示：左上角/设置页可关闭「自动打开浏览器」改为静默启动")

    stop_cb = api_server.stop_server
    tray_icon = _make_tray(stop_cb) if tray else None
    if tray_icon is not None:
        import threading
        threading.Thread(target=tray_icon.run, daemon=True).start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        api_server.stop_server()
    return 0


def main():
    parser = argparse.ArgumentParser(prog="whaletalk", description=APP_NAME + " · 纯 Web + 托盘常驻")
    parser.add_argument("--server", action="store_true", help="仅启动 API 服务（终端常驻，不托盘不开浏览器）")
    parser.add_argument("--no-browser", action="store_true", help="常驻但不自动打开浏览器（手动访问）")
    parser.add_argument("--no-tray", action="store_true", help="常驻但不启用系统托盘")
    parser.add_argument("--no-shortcuts", action="store_true", help="不自动创建桌面/开始菜单快捷方式")
    parser.add_argument("--install-shortcuts", action="store_true", help="强制重建桌面/开始菜单快捷方式")
    parser.add_argument("--port", type=int, default=API_PORT, help=f"API 端口（默认 {API_PORT}）")
    args = parser.parse_args()

    if not args.server:
        _hide_console()
    # 快捷方式：强制 / 首次启动自动创建（不指定 --no-shortcuts）
    if args.install_shortcuts or (not args.no_shortcuts and not _shortcuts_exist()):
        if _create_shortcuts():
            print("✅ 已创建 桌面 + 开始菜单 快捷方式")
        else:
            print("[快捷方式] 创建失败（可稍后用托盘菜单「📌 桌面快捷方式」重试）")
    if args.server:
        return _serve_forever(args.port, open_browser=False, tray=False)
    # 默认（推荐）：浏览器 + 托盘常驻；静默启动时不自动弹浏览器
    try:
        import config_utils
        silent = bool(config_utils.load_config().get("silent_start", False))
    except Exception:
        silent = False
    open_browser = not args.no_browser and not silent
    if silent:
        print(f"🔕 静默启动模式：服务已常驻 http://127.0.0.1:{args.port}（托盘「🌐 打开界面」随时进入）")
    return _serve_forever(args.port, open_browser=open_browser, tray=not args.no_tray)


if __name__ == "__main__":
    sys.exit(main())
