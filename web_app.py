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
    python web_app.py --no-webui-build → 跳过自动构建前端（默认：未构建/源码更新时自动 npm run build）

退出方式：
- 系统托盘菜单「退出」（停止服务并退出）
- 终端 Ctrl+C（--server 模式）
"""
import argparse
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

APP_NAME = "鲸语 WhaleTalk"
API_PORT = 8745
WEBUI_DIR = os.path.join(BASE_DIR, "webui")
# 依赖安装镜像：默认清华源（可 WHALETALK_PIP_MIRROR 覆盖，如换阿里源/内网源）
PIP_MIRROR = os.environ.get("WHALETALK_PIP_MIRROR", "https://pypi.tuna.tsinghua.edu.cn/simple")


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


# ── WebUI 构建保障 ──────────────────────────────
def _harden_stdio():
    """stdout/stderr 非 UTF 编码（如管道/重定向到 GBK 文件）时改为 replace，
    避免日志里的 ✓/✅ 等字符触发 UnicodeEncodeError 导致启动崩溃。"""
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass


def _webui_dist_index():
    return os.path.join(WEBUI_DIR, "dist", "index.html")


def _webui_built():
    return os.path.isfile(_webui_dist_index())


def _webui_sources_mtime():
    """构建输入（src/public/index.html/vite.config.js/package.json）的最新修改时间。"""
    mtime = 0.0
    for sub in ("src", "public"):
        root = os.path.join(WEBUI_DIR, sub)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                try:
                    mtime = max(mtime, os.path.getmtime(os.path.join(dirpath, fn)))
                except OSError:
                    pass
    for fn in ("index.html", "vite.config.js", "package.json"):
        p = os.path.join(WEBUI_DIR, fn)
        if os.path.isfile(p):
            try:
                mtime = max(mtime, os.path.getmtime(p))
            except OSError:
                pass
    return mtime


def _webui_needs_build():
    """是否需要构建：产物缺失，或源码比产物新（保证 UI 改动重启后生效）。"""
    idx = _webui_dist_index()
    if not _webui_built():
        return True
    src_mtime = _webui_sources_mtime()
    if src_mtime <= 0:
        return False
    try:
        return src_mtime > os.path.getmtime(idx)
    except OSError:
        return True


def _run_npm(args, timeout=900):
    """在 webui 目录执行 npm 命令。Windows 用 npm.cmd 并静默建窗，避免黑窗闪现。
    返回 (ok, 输出尾部)。"""
    npm = "npm.cmd" if os.name == "nt" else "npm"
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(
            [npm] + args,
            cwd=WEBUI_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **kwargs,
        )
        tail = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()[-2000:]
        return r.returncode == 0, tail
    except FileNotFoundError:
        return False, "找不到 npm 命令：请先安装 Node.js（https://nodejs.org）"
    except subprocess.TimeoutExpired:
        return False, f"npm {' '.join(args)} 超时（{timeout}s）"
    except Exception as e:
        return False, str(e)


def _ensure_webui_build():
    """确保 WebUI 构建产物就绪（开箱即用）：
    - 已构建（dist/index.html 存在且源码未更新）→ 跳过；
    - 未构建或源码有更新 → 自动 npm run build（缺依赖先 npm ci/install）。
    打包 exe（前端随程序分发）与 WHALETALK_NO_WEBUI_BUILD=1 时跳过自动构建。
    返回 (ok, 说明)。"""
    if getattr(sys, "frozen", False):
        return _webui_built(), "打包模式：前端产物随程序分发，跳过构建"
    if os.environ.get("WHALETALK_NO_WEBUI_BUILD") == "1":
        return _webui_built(), "WHALETALK_NO_WEBUI_BUILD=1：已跳过自动构建"
    if not _webui_needs_build():
        return True, "WebUI 已构建，跳过构建步骤"
    # 缺依赖先装（有 package-lock.json 用 npm ci 更快更可复现）
    if not os.path.isdir(os.path.join(WEBUI_DIR, "node_modules")):
        install = "ci" if os.path.isfile(os.path.join(WEBUI_DIR, "package-lock.json")) else "install"
        print(f"⏳ WebUI 依赖缺失，正在自动安装（npm {install}）…")
        ok, tail = _run_npm([install])
        if not ok:
            print(f"❌ WebUI 依赖安装失败：\n{tail}")
            return False, "WebUI 依赖安装失败"
    print("⏳ WebUI 未构建，正在自动构建（npm run build）…")
    ok, tail = _run_npm(["run", "build"])
    if ok:
        print("✅ WebUI 自动构建完成")
        return True, "WebUI 自动构建成功"
    print(f"❌ WebUI 自动构建失败：\n{tail}")
    return False, "WebUI 自动构建失败"


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


# ── Python 依赖自检（首启自动安装，显式感知） ──────────────
def _missing_auto_deps():
    """返回缺失的自动安装依赖列表 [(pip包名, 显示名)]。"""
    import importlib.util
    try:
        import deps
    except Exception:
        return []
    miss = []
    for imp, pkg, label in deps.AUTO_INSTALL_DEPS:
        try:
            if importlib.util.find_spec(imp) is None:
                miss.append((pkg, label))
        except Exception:
            miss.append((pkg, label))
    return miss


def _heavy_deps_report():
    """返回缺失的可选（重型）依赖 dict 列表。"""
    import importlib.util
    try:
        import deps
    except Exception:
        return []
    out = []
    for d in deps.HEAVY_DEPS:
        imp = d.get("import", "")
        try:
            if importlib.util.find_spec(imp) is None:
                out.append(d)
        except Exception:
            out.append(d)
    return out


def _pip_install(pkg):
    """用清华源安装单个包，返回是否成功。"""
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", pkg, "-i", PIP_MIRROR,
         "--disable-pip-version-check", "--no-warn-script-location"],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def _install_optional(dep):
    """安装一个可选（重型）依赖：pip 装 + 可选的后续命令（如下载 Chromium）。"""
    ok = True
    if dep.get("pip"):
        ok = _pip_install(dep["pip"]) and ok
    if ok and dep.get("post_cmd"):
        try:
            r = subprocess.run([sys.executable, "-m"] + list(dep["post_cmd"]),
                               capture_output=True, text=True, timeout=1800)
            ok = r.returncode == 0
        except Exception:
            ok = False
    return ok


def _install_deps_console(miss):
    ok_all = True
    for i, (pkg, label) in enumerate(miss, 1):
        print(f"  [{i}/{len(miss)}] 安装 {label}（{pkg}）…", flush=True)
        ok = _pip_install(pkg)
        ok_all = ok_all and ok
        print(f"    {'✅' if ok else '❌ 失败'} {label}")
    return ok_all


def _deps_dialog(miss, heavy, tk):
    """首启依赖弹窗：核心自动装 + 可选能力勾选（用户取舍）。"""
    root = tk.Tk()
    root.title("鲸语 · 首次启动初始化")
    root.geometry("500x340")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    tk.Label(root, text="🐋 鲸语 · 启动前准备", font=("Microsoft YaHei", 13, "bold")).pack(pady=(14, 4))
    tk.Label(root, text="使用清华源镜像，全程可见进度", font=("Microsoft YaHei", 9), fg="#666").pack()

    body = tk.Frame(root)
    body.pack(fill="both", expand=True, padx=18, pady=(6, 0))

    if miss:
        tk.Label(body, text=f"将自动安装 {len(miss)} 个核心依赖：", font=("Microsoft YaHei", 10, "bold")).pack(anchor="w")
        tk.Label(body, text="、".join(m[1] for m in miss), font=("Microsoft YaHei", 9),
                 fg="#555", wraplength=450, justify="left").pack(anchor="w", pady=(2, 6))

    check_vars = {}
    if heavy:
        tk.Label(body, text="可选能力（勾选后一并安装，之后也可在设置里再装）：",
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=(6, 2))
        for d in heavy:
            var = tk.BooleanVar(value=False)
            check_vars[d["import"]] = var
            line = tk.Frame(body)
            line.pack(anchor="w", fill="x", pady=1)
            tk.Checkbutton(line, variable=var).pack(side="left")
            tk.Label(line, text=d["label"], font=("Microsoft YaHei", 10)).pack(side="left")
            note = d.get("note") or d.get("desc") or ""
            tk.Label(line, text=f"　{note}", font=("Microsoft YaHei", 8), fg="#888").pack(side="left")

    status = tk.StringVar(value="准备就绪")
    tk.Label(root, textvariable=status, font=("Microsoft YaHei", 9), fg="#0a84ff").pack(pady=(6, 8))

    btnrow = tk.Frame(root)
    btnrow.pack(pady=(0, 14))
    result = {"ok": True}

    def _run(chosen):
        import threading

        def worker():
            total = len(miss) + len(chosen)
            done = 0
            result["ok"] = True
            for i, (pkg, label) in enumerate(miss, 1):
                done += 1
                status.set(f"[{done}/{total}] 安装核心 {label}…")
                if not _pip_install(pkg):
                    result["ok"] = False
            for d in chosen:
                done += 1
                status.set(f"[{done}/{total}] 安装 {d['label']}…")
                if not _install_optional(d):
                    result["ok"] = False
            status.set("✅ 依赖就绪，即将启动鲸语…" if result["ok"] else "⚠ 部分依赖失败，可稍后重试")
            root.after(1300, root.destroy)

        threading.Thread(target=worker, daemon=True).start()

    def start():
        chosen = [d for d in heavy if check_vars.get(d["import"]) and check_vars[d["import"]].get()]
        btn_start.config(state="disabled")
        btn_skip.config(state="disabled")
        _run(chosen)

    def skip():
        btn_start.config(state="disabled")
        btn_skip.config(state="disabled")
        _run([])  # 只装核心（若有），不装可选

    btn_start = tk.Button(btnrow, text="开始安装", command=start, width=14, font=("Microsoft YaHei", 10))
    btn_start.pack(side="left", padx=5)
    btn_skip = tk.Button(btnrow, text=("跳过可选，仅装核心" if miss else "暂不安装"), command=skip,
                         width=18, font=("Microsoft YaHei", 10))
    btn_skip.pack(side="left", padx=5)

    root.mainloop()
    return result["ok"]


def _ensure_python_deps():
    """启动时检测并安装缺失的 Python 依赖，返回 True 表示可继续。"""
    miss = _missing_auto_deps()
    heavy = _heavy_deps_report()
    if not miss and not heavy:
        print("✅ Python 依赖完整")
        return True
    if os.environ.get("WHALETALK_DEPS_CONSOLE") == "1":
        if miss:
            print(f"⚠️ 检测到 {len(miss)} 个缺失依赖，正在安装…")
            return _install_deps_console(miss)
        print("⚠️ 核心依赖完整，以下可选能力未安装（可稍后在设置中安装）：")
        for d in heavy:
            print(f"   - {d['label']}：{d['desc']}（pip install {d['pip']}）")
        return True
    try:
        import tkinter as tk
        return _deps_dialog(miss, heavy, tk)
    except Exception:
        if miss:
            return _install_deps_console(miss)
        return True


def main():
    _harden_stdio()
    parser = argparse.ArgumentParser(prog="whaletalk", description=APP_NAME + " · 纯 Web + 托盘常驻")
    parser.add_argument("--server", action="store_true", help="仅启动 API 服务（终端常驻，不托盘不开浏览器）")
    parser.add_argument("--no-browser", action="store_true", help="常驻但不自动打开浏览器（手动访问）")
    parser.add_argument("--no-tray", action="store_true", help="常驻但不启用系统托盘")
    parser.add_argument("--no-shortcuts", action="store_true", help="不自动创建桌面/开始菜单快捷方式")
    parser.add_argument("--no-webui-build", action="store_true", help="不自动构建 WebUI（产物缺失时界面不可用）")
    parser.add_argument("--install-shortcuts", action="store_true", help="强制重建桌面/开始菜单快捷方式")
    parser.add_argument("--port", type=int, default=API_PORT, help=f"API 端口（默认 {API_PORT}）")
    parser.add_argument("--no-deps-check", action="store_true", help="跳过 Python 依赖自检/自动安装")
    args = parser.parse_args()

    if not args.no_deps_check:
        _ensure_python_deps()
    if not args.server:
        _hide_console()
    # 快捷方式：强制 / 首次启动自动创建（不指定 --no-shortcuts）
    if args.install_shortcuts or (not args.no_shortcuts and not _shortcuts_exist()):
        if _create_shortcuts():
            print("✅ 已创建 桌面 + 开始菜单 快捷方式")
        else:
            print("[快捷方式] 创建失败（可稍后用托盘菜单「📌 桌面快捷方式」重试）")
    # WebUI 构建保障：已构建则跳过；未构建/源码更新则自动 npm run build
    if not args.no_webui_build:
        ok, note = _ensure_webui_build()
        if not ok:
            print(f"⚠️ {note}：可手动执行 cd webui && npm install && npm run build；API 仍会启动")
    elif not _webui_built():
        print("⚠️ --no-webui-build：WebUI 未构建，界面将无法打开（请手动执行 cd webui && npm run build）")
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
