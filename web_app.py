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
        # IconLocation 逗号必须放在引号内（"path,0"），放外面会变成 PowerShell 数组赋值被忽略 → 图标不生效
        icon_line = f'$s.IconLocation = "{ico},0"\r\n' if os.path.exists(ico) else ""
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
    """新入口快捷方式已就绪 且 不残留旧版 WhaleTalk.exe.lnk 且 目标指向当前项目。

    目标校验：旧项目/旧路径的快捷方式（同名文件已存在但指向别处）会被识别为过期，
    下次启动自动重建为指向当前 BASE_DIR 的入口（含鲸鱼图标）。
    """
    try:
        d = os.path.join(os.path.expanduser("~"), "Desktop", f"{APP_NAME}.lnk")
        old = os.path.join(os.path.expanduser("~"), "Desktop", "WhaleTalk.exe.lnk")
        if not os.path.exists(d) or os.path.exists(old):
            return False
        # 目标校验：lnk 二进制必须包含当前项目路径（UTF-16LE 或 ANSI 编码）
        base = BASE_DIR  # 如 D:\jingyu\WhaleTalk-main（lnk 内为单反斜杠原始形式）
        with open(d, "rb") as f:
            data = f.read()
        return (base.encode("utf-16-le") in data) or (base.encode("utf-8") in data)
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
    for fn in ("index.html", "vite.config.js", "package.json", "package-lock.json"):
        p = os.path.join(WEBUI_DIR, fn)
        if os.path.isfile(p):
            try:
                mtime = max(mtime, os.path.getmtime(p))
            except OSError:
                pass
    return mtime


def _webui_deps_stale():
    """依赖是否过期（决定要不要重跑 npm install/ci）。
    判定：node_modules 缺失，或 package.json 顶层声明的新增依赖尚未安装。
    不依赖 mtime 对比（git clone 会重置文件时间导致误判"每次都要重装"）。
    """
    nm = os.path.join(WEBUI_DIR, "node_modules")
    if not os.path.isdir(nm):
        return True
    pkg_path = os.path.join(WEBUI_DIR, "package.json")
    if not os.path.isfile(pkg_path):
        return False
    try:
        import json as _json
        with open(pkg_path, "r", encoding="utf-8") as f:
            pkg = _json.load(f)
        deps = dict(pkg.get("dependencies") or {})
        # 顶层依赖存在性：npm 会把装好的包放在 node_modules/<name>（scoped 在 node_modules/@scope/name）
        for name in deps:
            seg = name.split("/")
            if len(seg) == 2 and name.startswith("@"):
                check = os.path.join(nm, seg[0], seg[1])
            else:
                check = os.path.join(nm, name)
            if not os.path.isdir(check):
                return True
    except Exception:
        return False
    return False


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


def _run_npm_stream(args, on_line, timeout=1200):
    """流式执行 npm（供进度窗显示实时输出）。on_line(line) 接收每行文本。
    返回 (ok, 尾部 2000 字符)。"""
    npm = "npm.cmd" if os.name == "nt" else "npm"
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    tail_buf = []
    try:
        p = subprocess.Popen(
            [npm] + args, cwd=WEBUI_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", **kwargs)
    except FileNotFoundError:
        return False, "找不到 npm 命令：请先安装 Node.js（https://nodejs.org）"
    except Exception as e:
        return False, str(e)
    import threading as _th
    timer = [time.time()]
    deadline = timer[0] + timeout

    def _reader():
        for line in p.stdout:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            tail_buf.append(line)
            if len(tail_buf) > 200:
                tail_buf.pop(0)
            try:
                on_line(line)
            except Exception:
                pass
            timer[0] = time.time()
    _th.Thread(target=_reader, daemon=True).start()
    while p.poll() is None:
        if time.time() > deadline:
            p.kill()
            return False, f"npm {' '.join(args)} 超时（{timeout}s）"
        time.sleep(0.1)
    try:
        p.wait(timeout=5)
    except Exception:
        pass
    return p.returncode == 0, "\n".join(tail_buf[-60:])


# ── 首次初始化友好进度窗（tkinter）─────────────────────────────
# 服务未起、浏览器尚不可用时，用系统窗口展示安装阶段与实时日志，
# 让用户明确感知"正在自动准备"而非干等黑屏。装完自动进入程序。
def _setup_progress_window(title, subtitle, run_steps):
    """展示一个带阶段标题 + 实时日志 + 活动条的安装窗。

    run_steps(on_stage, on_log) 在工作线程执行：
      - on_stage(label) 切换阶段标题（如"正在下载前端依赖…"）
      - on_log(line)    追加一行日志
    返回 True=成功 / False=失败（run_steps 应返回 bool）。tkinter 不可用返回 None。"""
    import tkinter as tk
    from tkinter import ttk
    import queue as _queue
    import threading as _th
    root = tk.Tk()
    root.title(title)
    root.geometry("560x360")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    tk.Label(root, text=subtitle, font=("Microsoft YaHei", 12, "bold")).pack(pady=(14, 2))
    stage = tk.StringVar(value="正在准备…")
    tk.Label(root, textvariable=stage, font=("Microsoft YaHei", 10), fg="#1a4e8a").pack(pady=(6, 2))
    bar = ttk.Progressbar(root, length=500, mode="indeterminate")
    bar.pack(pady=(4, 6))
    bar.start(12)
    # 日志框
    log = tk.Text(root, height=12, width=62, font=("Consolas", 9), state="disabled",
                  relief="solid", borderwidth=1, wrap="word")
    log.pack(padx=18, pady=(2, 6), fill="both", expand=True)
    sc = ttk.Scrollbar(log, command=log.yview)
    log.configure(yscrollcommand=sc.set)
    sc.pack(side="right", fill="y")
    tip = tk.StringVar(value="首次准备可能需要几分钟，请耐心等待；完成后将自动进入")
    tk.Label(root, textvariable=tip, font=("Microsoft YaHei", 8), fg="#999").pack(pady=(0, 8))

    q = _queue.Queue()
    result = {"ok": None}

    def worker():
        try:
            result["ok"] = bool(run_steps(
                lambda s: q.put(("stage", s)),
                lambda l: q.put(("log", l)),
            ))
        except Exception as e:
            result["ok"] = False
            q.put(("log", f"[错误] {e}"))
        finally:
            q.put(("done", None))

    _th.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "stage":
                    stage.set(payload)
                    log.configure(state="normal")
                    log.insert("end", f"\n── {payload}\n")
                    log.configure(state="disabled")
                    log.see("end")
                elif kind == "log":
                    log.configure(state="normal")
                    log.insert("end", payload + "\n")
                    log.configure(state="disabled")
                    log.see("end")
                elif kind == "done":
                    bar.stop()
                    if result["ok"]:
                        stage.set("✅ 准备完成！正在启动鲸语…")
                        tip.set("即将自动打开主界面")
                        root.after(1200, root.destroy)
                    else:
                        stage.set("⚠ 准备未完成（详见上方日志）")
                        tip.set("可关闭本窗口后查看详情，或检查网络后重试")
                        root.after(6000, root.destroy)
                    return
        except _queue.Empty:
            pass
        root.after(100, poll)

    root.after(100, poll)
    root.mainloop()
    return result["ok"]


def _webui_install_steps(on_stage, on_log):
    """首次 WebUI 准备的工作线程主体：装依赖 + 构建。返回 (ok, tail)。"""
    def npm_stream(args, stage):
        on_stage(stage)
        return _run_npm_stream(args, on_log)
    if _webui_deps_stale():
        if os.path.isfile(os.path.join(WEBUI_DIR, "package-lock.json")):
            ok, tail = npm_stream(["ci"], "正在下载前端依赖（npm ci）…")
            if not ok:
                # ci 对 lock/package.json 不一致会失败 → 回退 npm install
                on_log("[npm ci 失败，自动改用 npm install 重试]")
                ok, tail = npm_stream(["install"], "正在下载前端依赖（npm install）…")
        else:
            ok, tail = npm_stream(["install"], "正在下载前端依赖（npm install）…")
        if not ok:
            on_stage("❌ 依赖安装失败")
            return False
    on_stage("正在编译前端界面（npm run build）…")
    ok, tail = _run_npm_stream(["run", "build"], on_log)
    return ok


def _can_show_tk():
    """探测能否创建 tkinter 窗口（主线程执行，避免 tkinter 线程安全与退出残留问题）。
    无头/服务会话（无 DISPLAY/窗口站）下 tk.Tk() 抛 TclError → 返回 False，
    调用方将退回控制台输出，不会卡进 mainloop。"""
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        r.update_idletasks()
        r.destroy()
        return True
    except Exception:
        return False


def _ensure_webui_build():
    """确保 WebUI 构建产物就绪（开箱即用）：
    - 依赖过期（package.json 顶层依赖缺失，如新增了渲染库）→ 无论 dist 新旧都
      强制 npm ci/install + 重建（mtime 会被 git/部署重置，不能作为唯一依据）；
    - 已构建且源码未更新 → 跳过；
    - 未构建或源码有更新 → 自动 npm run build。
    桌面环境（tkinter 可真正驱动）弹友好进度窗实时展示安装/构建；无 GUI 环境退回
    控制台打印。打包 exe 与 WHALETALK_NO_WEBUI_BUILD=1 时跳过。
    返回 (ok, 说明)。"""
    if getattr(sys, "frozen", False):
        return _webui_built(), "打包模式：前端产物随程序分发，跳过构建"
    if os.environ.get("WHALETALK_NO_WEBUI_BUILD") == "1":
        return _webui_built(), "WHALETALK_NO_WEBUI_BUILD=1：已跳过自动构建"
    # 依赖过期是独立于 mtime 的强信号：缺顶层包 ⇒ 现 dist 必非最新 ⇒ 强制重装重建，
    # 否则用户增量拉代码后若 dist 时间戳看似够新会误跳、界面缺新功能。
    if not _webui_deps_stale() and not _webui_needs_build():
        return True, "WebUI 已构建，跳过构建步骤"
    # 桌面环境优先用友好进度窗（服务未起、浏览器不可用，需要给用户实时反馈）
    window_ok = None
    try:
        if _can_show_tk():
            window_ok = _setup_progress_window(
                "鲸语 · 正在准备界面", "🐋 首次运行正在自动下载并构建界面（只需一次）",
                _webui_install_steps)
    except Exception as e:  # 无 tkinter/非桌面 → 走控制台
        window_ok = None
        print(f"[提示] 桌面进度窗不可用（{e}），改用控制台输出")
    if window_ok is not None:
        if window_ok:
            return True, "WebUI 自动构建成功（进度窗）"
        return False, "WebUI 自动构建失败（详见进度窗日志）"
    # 控制台/无 GUI 回退：print 逐步输出
    if _webui_deps_stale():
        if os.path.isfile(os.path.join(WEBUI_DIR, "package-lock.json")):
            print("⏳ WebUI 依赖有更新，正在自动安装（npm ci）…")
            ok, tail = _run_npm(["ci"])
            if not ok:
                print("⏳ npm ci 失败，回退 npm install…")
                ok, tail = _run_npm(["install"])
            if not ok:
                print(f"❌ WebUI 依赖安装失败：\n{tail}")
                return False, "WebUI 依赖安装失败"
        else:
            print("⏳ WebUI 依赖缺失，正在自动安装（npm install）…")
            ok, tail = _run_npm(["install"])
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


def _importable(name):
    """检查模块是否可导入（依赖检测用）。"""
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


# 硬依赖：缺失时程序完全不可用（API 服务起不来），启动时同步静默安装
HARD_DEPS = [
    ("openai", "openai", "核心 API 网关"),
    ("httpx", "httpx", "网络请求"),
]


def _hard_deps_window(hard_miss):
    """硬依赖（openai/httpx，API 必需）缺失时的初始化进度窗。

    API 服务未起、前端尚不可用，用系统窗口让用户明确感知「正在安装」，
    避免双击后无任何反馈的干等。装完自动关闭进入程序。
    """
    import deps
    import queue
    import threading as _th
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("鲸语 · 正在初始化")
    root.geometry("460x170")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    tk.Label(root, text="🐋 鲸语 · 首次启动初始化", font=("Microsoft YaHei", 13, "bold")).pack(pady=(16, 4))
    tk.Label(root, text="正在安装核心组件（清华源镜像），完成后自动进入程序", font=("Microsoft YaHei", 9), fg="#666").pack()
    status = tk.StringVar(value="正在准备…")
    tk.Label(root, textvariable=status, font=("Microsoft YaHei", 10)).pack(pady=(10, 2))
    bar = ttk.Progressbar(root, length=380, mode="determinate")
    bar.pack(pady=(2, 4))
    tip = tk.StringVar(value="首次安装可能需要几分钟，请耐心等待")
    tk.Label(root, textvariable=tip, font=("Microsoft YaHei", 8), fg="#999").pack()

    result = {"ok": True}
    q = queue.Queue()

    def worker():
        ok, failed = deps.install_many(hard_miss)
        result["ok"] = ok and not failed
        q.put("done")

    _th.Thread(target=worker, daemon=True).start()

    def poll():
        st = deps.install_state()
        if st["running"]:
            status.set(f"({st['done']}/{st['total']}) 正在安装 {st['current']}…")
            bar["maximum"] = max(st["total"], 1)
            bar["value"] = st["done"]
        else:
            try:
                q.get_nowait()
                if result["ok"]:
                    status.set("✅ 核心组件就绪，正在启动鲸语…")
                else:
                    status.set("⚠ 安装失败，请检查网络后重试")
                root.after(1400, root.destroy)
                return
            except queue.Empty:
                pass
        root.after(120, poll)

    root.after(120, poll)
    root.mainloop()
    return result["ok"]


def _ensure_python_deps():
    """启动依赖保障：永不弹窗、永不阻塞用户。

    - 硬依赖（openai/httpx）：缺失时同步安装——GUI 下弹系统进度窗明确感知，
      console 模式逐行打印；失败则明确报错；
    - 软核心（功能依赖）：后台自动安装，不阻塞启动，状态写入 deps 供前端
      轮询展示进度（前端打开即可见「正在初始化组件 x/y」）；
    - 可选能力（重型）：完全静默，去设置页「可选能力」面板按需安装。
    """
    import deps
    hard_miss = [(imp, pkg, label) for imp, pkg, label in HARD_DEPS
                 if not _importable(imp)]
    if hard_miss:
        print("⏳ 必需组件缺失，正在自动安装（清华源）…")
        if os.environ.get("WHALETALK_DEPS_CONSOLE") == "1":
            ok = deps.install_many(hard_miss, on_line=lambda s: print("    " + s, flush=True))[0]
        else:
            try:
                ok = _hard_deps_window(hard_miss)
            except Exception:  # noqa: BLE001 - 无 GUI 环境退回控制台
                ok = deps.install_many(hard_miss)[0]
        if not ok:
            print("❌ 核心 API 组件安装失败，请检查网络后重试。")
            return False
    miss = _missing_auto_deps()
    if miss:
        # 首次启动：不自动装，由前端「首次启动向导」统一引导安装（装完才进入程序）。
        # 二次启动：缺失时后台静默补装（不阻塞），用户也可在 设置 → 可选能力 手动管理。
        try:
            import api_server
            first_run = api_server._is_first_run()
        except Exception:  # noqa: BLE001
            first_run = False
        if first_run:
            print(f"ℹ️ 首次启动：{len(miss)} 个功能组件待安装，将由初始化向导引导完成。")
        else:
            import threading
            threading.Thread(target=_install_silent, args=(miss,), daemon=True).start()
            print(f"⏳ {len(miss)} 个功能组件缺失，正在后台自动安装（不影响启动，装好后即可用）。")
    else:
        print("✅ Python 依赖完整")
    return True


def _install_silent(miss):
    """后台静默安装缺失的核心依赖（清华源）。

    状态实时写入 deps.install_state()，前端轮询 /v1/deps 即可显示进度。
    """
    import deps
    ok, failed = deps.install_many(miss, on_line=lambda s: print("    " + s, flush=True))
    if failed:
        print(f"⚠️ 后台安装部分失败：{'、'.join(failed)}（可在 设置 → 可选能力 重试）")
    else:
        print("✅ 后台依赖安装完成")


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
        # 硬依赖安装失败 = API 无法启动，明确报错退出，避免带病运行
        if not _ensure_python_deps():
            print("❌ 必需依赖安装失败，程序未启动。请检查网络后重新运行（或加 --no-deps-check 强制启动调试）。")
            input("按回车退出…") if not args.server else None
            return 1
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
