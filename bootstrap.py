"""鲸语 WhaleTalk · 跨平台引导启动器（替代原 start.bat / build_exe.bat）。

为什么用 Python 而不是 .bat：批处理在「UTF-8 无 BOM + chcp 65001」下会被 cmd.exe
错误切行（中文提示后半段被当成命令执行）；且一把梭 `pip install -r requirements.txt`
遇到死代理或单个包失败就整批失败。本脚本把解释器/网络/代理这些环境差异全部吃干净。

一条命令完成：
    1) 校验 Python 版本（>= 3.9）
    2) 准备虚拟环境（默认仓库根 .venv；有 uv 则用 uv 秒建，--no-venv 直接用当前解释器）
    3) 代理预检：系统代理已配置但不可达时自动绕过（修复 pip 死代理导致的全量失败）
    4) 逐包安装 requirements.txt（单包失败不阻断其余，超时/重试兜底；支持离线 wheel）
    5) 拉起 web_app.py（其余参数原样透传）

用法：
    python bootstrap.py                 # 建 .venv → 装依赖 → 启动（推荐）
    python bootstrap.py --server        # 透传参数给 web_app.py（仅 API 服务）
    python bootstrap.py --no-venv       # 直接用当前解释器（CI / 已激活 venv）
    python bootstrap.py --skip-install  # 跳过安装，直接启动
    python bootstrap.py --installer uv  # 强制用 uv 安装（默认 auto：有 uv 就用）
    python bootstrap.py --offline       # 仅用本地 wheel 安装（默认目录 ./wheels）
    python bootstrap.py check           # 只体检环境，不安装不启动
    python bootstrap.py doctor          # 生成可贴 issue 的诊断报告
    python bootstrap.py build           # PyInstaller 打包 dist/WhaleTalk.exe
    python bootstrap.py --mirror URL    # 自定义 pip 源（默认清华）
    python bootstrap.py --dev           # 额外安装 requirements-dev.txt
"""
import argparse
import contextlib
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DEFAULT_VENV = ".venv"
MIN_PY = (3, 9)
_STDIO_HARDENED = False


def _harden_stdio():
    """stdout/stderr 强制 UTF-8（防 GBK 终端打印中文崩溃），与 web_app 同款。"""
    global _STDIO_HARDENED
    if _STDIO_HARDENED:
        return
    _STDIO_HARDENED = True
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8", errors="replace")


def check_python():
    """版本不满足返回错误文本，否则 None。"""
    if sys.version_info < MIN_PY:
        return (f"需要 Python {MIN_PY[0]}.{MIN_PY[1]}+，当前 "
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    return None


def venv_python(venv_dir):
    """venv 内解释器路径（跨平台）。"""
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _uv_cmd():
    """返回 uv 命令前缀（['<path>/uv'] 或 [python, '-m', 'uv']），没有则 None。"""
    exe = shutil.which("uv")
    if exe:
        return [exe]
    with contextlib.suppress(Exception):
        r = subprocess.run([sys.executable, "-m", "uv", "--version"],
                           capture_output=True, text=True, timeout=15, errors="replace")
        if r.returncode == 0:
            return [sys.executable, "-m", "uv"]
    return None


def ensure_venv(venv_dir, use_uv=False, uv_cmd=None):
    """确保 venv 存在；返回 (解释器路径, 是否新建, 错误文本)。

    use_uv 时优先 `uv venv --seed`（秒建且带 pip，保证 web_app 自检回退 pip 也能用）。
    """
    py = venv_python(venv_dir)
    if os.path.exists(py):
        return py, False, None
    parent = os.path.dirname(os.path.abspath(venv_dir))
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            return py, False, f"无法创建目录 {parent}：{e}"
    print(f"[引导] 创建虚拟环境 {venv_dir} …")
    if use_uv and uv_cmd:
        rc = 1
        with contextlib.suppress(Exception):
            rc = subprocess.call(list(uv_cmd) + ["venv", "--seed", "--python", sys.executable, venv_dir])
        if rc == 0 and os.path.exists(py):
            return py, True, None
        print("[引导] ⚠ uv 创建失败，回退 python -m venv")
    try:
        rc = subprocess.call([sys.executable, "-m", "venv", venv_dir])
    except Exception as e:  # noqa: BLE001
        return py, False, f"无法调用 venv 模块：{e}"
    if rc != 0 or not os.path.exists(py):
        hint = "（Debian/Ubuntu 可 `apt install python3-venv`）" if os.name != "nt" else ""
        return py, False, f"虚拟环境创建失败{hint}"
    return py, True, None


def _resolve_installer(requested, uv_available):
    """安装器决策：pip / uv / auto（有 uv 就 uv，否则 pip）。"""
    if requested == "pip":
        return "pip"
    return "uv" if uv_available else "pip"


def _offline_flags(offline, wheel_dir):
    """离线安装参数：返回 (pip 环境变量, uv 命令行参数)。"""
    if not offline:
        return {}, []
    env = {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": wheel_dir,
           "UV_NO_INDEX": "1", "UV_FIND_LINKS": wheel_dir}
    return env, ["--no-index", "--find-links", wheel_dir]


def parse_requirements(path):
    """解析 requirements 文件为规格列表（去注释/空行/选项，支持反斜杠续行）。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        raw_lines = f.read().splitlines()
    logical, buf = [], ""
    for line in raw_lines:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        logical.append(buf + stripped)
        buf = ""
    if buf:
        logical.append(buf)
    specs = []
    for line in logical:
        line = re.sub(r"\s+#.*$", "", line).strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue  # 注释、空行、--index-url/-r/-e 等选项：核心清单不含
        specs.append(line)
    return specs


def _install_uv(uv, python, specs, on_line, offline, wheel_dir):
    """用 uv 逐包安装（保留单包隔离/超时/日志）。"""
    import deps
    base = list(uv) + ["pip", "install", "--python", python]
    if offline:
        _env, flags = _offline_flags(True, wheel_dir)
        base += flags
    else:
        base += ["-i", deps.PIP_MIRROR]
    failed = []
    for i, spec in enumerate(specs, 1):
        if on_line:
            on_line(f"[{i}/{len(specs)}] {spec}")
        rc = deps.run_verbose(base + [spec], on_line, timeout=deps.PIP_INSTALL_TIMEOUT)
        if rc != 0:
            failed.append(spec)
    if failed:
        print(f"[引导] ⚠ 以下 {len(failed)} 项安装失败：{', '.join(failed)}")
    return not failed


def _install(python, req_files, on_line, installer="pip", offline=False, wheel_dir=None):
    """逐包安装多个 requirements 文件，返回是否全部成功。"""
    import deps
    deps.guard_pip_proxy(on_line)
    specs = []
    for req in req_files:
        specs.extend(parse_requirements(req))
    if not specs:
        print("[引导] 未解析到依赖，跳过安装")
        return True
    wheel_dir = wheel_dir or os.path.join(BASE_DIR, "wheels")
    if offline and not os.path.isdir(wheel_dir):
        print(f"[引导] ❌ 离线模式需要本地 wheel 目录：{wheel_dir}")
        print("       可先联网执行：pip download -r requirements.txt -d wheels")
        return False
    if installer == "uv" and not _uv_cmd():
        print("[引导] ⚠ 未找到 uv，回退 pip")
        installer = "pip"
    env, _flags = _offline_flags(offline, wheel_dir)
    for k, v in env.items():
        os.environ[k] = v
    if installer == "uv":
        print(f"[引导] 使用 uv 安装（{len(specs)} 项）…")
        return _install_uv(_uv_cmd(), python, specs, on_line, offline, wheel_dir)
    print(f"[引导] 安装依赖（{len(specs)} 项 · 源 {deps.PIP_MIRROR}）…")
    miss = [(s, s) for s in specs]
    ok, failed = deps.install_many(miss, on_line=on_line, python=python)
    if failed:
        print(f"[引导] ⚠ 以下 {len(failed)} 项安装失败：{', '.join(failed)}")
        print("       可检查网络/代理后重跑，或在程序内『设置 → 可选能力』重试。")
    return ok


def _missing_in(python):
    """用目标解释器探测核心依赖缺失项；探测失败返回 None。"""
    code = (
        "import importlib.util, sys\n"
        f"sys.path.insert(0, {BASE_DIR!r})\n"
        "try:\n"
        "    import deps\n"
        "except Exception:\n"
        "    print('__ERR__'); raise SystemExit(0)\n"
        "for imp, pkg, _ in deps.AUTO_INSTALL_DEPS:\n"
        "    try:\n"
        "        if importlib.util.find_spec(imp) is None:\n"
        "            print(pkg)\n"
        "    except Exception:\n"
        "        print(pkg)\n"
    )
    try:
        r = subprocess.run([python, "-c", code], capture_output=True, text=True,
                           timeout=60, errors="replace")
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0 or "__ERR__" in (r.stdout or ""):
        return None
    return [x for x in (r.stdout or "").strip().splitlines() if x.strip()]


# ── 环境探测（doctor / 预检共用）────────────────────────────────────────
def _version():
    try:
        with open(os.path.join(BASE_DIR, "config_defaults.py"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"""\s*VERSION\s*=\s*["']([^"']+)""", line)
                if m:
                    return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return "?"


def _git_commit():
    with contextlib.suppress(Exception):
        r = subprocess.run(["git", "-C", BASE_DIR, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5, errors="replace")
        if r.returncode == 0:
            return r.stdout.strip()
    return None


def _port_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _exe_version(exe, flag="--version"):
    with contextlib.suppress(Exception):
        r = subprocess.run([exe, flag], capture_output=True, text=True, timeout=15, errors="replace")
        if r.returncode == 0:
            return (r.stdout or r.stderr).strip().splitlines()[0]
    return "?"


def _edge_path():
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.isfile(p):
            return p
    return shutil.which("msedge")


def _tail(path, n=20):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]).rstrip()
    except Exception:  # noqa: BLE001
        return ""


def _mirror_hostport():
    import deps
    return deps._proxy_hostport(deps.PIP_MIRROR)


def _collect_report(target):
    """收集环境诊断，返回可读报告文本。"""
    import deps
    out = []

    def add(s=""):
        out.append(s)

    add("=" * 64)
    add("鲸语 WhaleTalk 诊断报告")
    add("=" * 64)
    add(f"时间        ：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    add(f"版本        ：{_version()}")
    add(f"Git 提交    ：{_git_commit() or '（非 git 仓库）'}")
    add("")
    add("[解释器]")
    add(f"引导 Python ：{sys.executable} ({platform.python_version()})")
    add(f"目标 Python ：{target}")
    add(f"虚拟环境    ：{'存在' if os.path.exists(target) else '不存在'}")
    pyerr = check_python()
    add(f"版本要求    ：{pyerr or '满足 (>= 3.9)'}")
    add("")
    add("[依赖]")
    missing = _missing_in(target) if os.path.exists(target) else None
    total = len(deps.AUTO_INSTALL_DEPS)
    if missing is None:
        add("核心依赖    ：无法探测")
    elif missing:
        add(f"核心依赖    ：缺失 {len(missing)}/{total} → {', '.join(missing)}")
    else:
        add(f"核心依赖    ：{total}/{total} 齐全")
    uv = _uv_cmd()
    add(f"安装器      ：{'uv 可用' if uv else 'pip（未检测到 uv）'}")
    add("")
    add("[网络]")
    hp = deps._proxy_hostport(deps._effective_proxy_env())
    if hp:
        state = "可用" if deps._socket_reachable(*hp) else "不可达（安装时将自动绕过直连）"
        add(f"系统代理    ：{hp[0]}:{hp[1]} {state}")
    else:
        add("系统代理    ：未配置（直连）")
    mh = _mirror_hostport()
    if mh:
        add(f"pip 源      ：{deps.PIP_MIRROR} {'可达' if deps._socket_reachable(*mh) else '不可达'}")
    add("")
    add("[前端 / 系统件]")
    node = shutil.which("node")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    add(f"Node        ：{_exe_version(node) if node else '未安装（前端构建需要）'}")
    add(f"npm         ：{_exe_version(npm) if npm else '未安装'}")
    dist = os.path.join(BASE_DIR, "webui", "dist", "index.html")
    add(f"前端产物    ：{'已构建' if os.path.isfile(dist) else '未构建（首次启动自动构建，需 Node）'}")
    add(f"node_modules：{'存在' if os.path.isdir(os.path.join(BASE_DIR, 'webui', 'node_modules')) else '不存在'}")
    add(f"ffmpeg      ：{shutil.which('ffmpeg') or '未安装（媒体工具需要；winget install Gyan.FFmpeg）'}")
    add(f"Edge        ：{_edge_path() or '未找到（HTML 渲染/转图需要）'}")
    add(f"unrar       ：{shutil.which('unrar') or shutil.which('UnRAR') or '未安装（RAR 解压需要）'}")
    add("")
    add("[服务]")
    add(f"端口 8745   ：{'空闲' if _port_free(8745) else '被占用（可能已在运行或端口冲突）'}")
    with contextlib.suppress(Exception):
        free = shutil.disk_usage(BASE_DIR).free / (1024 ** 3)
        add(f"磁盘可用    ：{free:.1f} GB（{BASE_DIR}）")
    add("")
    add("[环境变量]")
    shown = False
    for k in ("WHALETALK_PIP_MIRROR", "WHALETALK_SKIP_PROXY_CHECK", "NO_PROXY", "no_proxy",
              "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        if os.environ.get(k):
            add(f"{k} = {os.environ[k]}")
            shown = True
    if not shown:
        add("（无相关设置）")
    log = os.path.join(BASE_DIR, "webui", "install.log")
    if os.path.isfile(log):
        add("")
        add("[install.log 末尾]")
        add(_tail(log))
    add("")
    add("提示：把本报告整段贴到 GitHub Issue，可加速定位问题。")
    return "\n".join(out)


def _doctor(target, args):
    """输出并保存诊断报告；返回退出码（报告本身不算失败）。"""
    report = _collect_report(target)
    print(report)
    path = getattr(args, "report", None)
    if path == "-":
        return 0
    if not path:
        path = os.path.join(BASE_DIR, f"whaletalk-diagnose-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\n[体检] 报告已写入：{path}")
    except Exception as e:  # noqa: BLE001
        print(f"\n[体检] ⚠ 报告写入失败：{e}")
    return 0


def _preflight_warnings():
    """启动前的系统件提示（不阻断）。"""
    warns = []
    dist = os.path.join(BASE_DIR, "webui", "dist", "index.html")
    if not os.path.isfile(dist) and not shutil.which("node"):
        warns.append("前端未构建且未检测到 Node.js → 界面不可用（装 Node，或使用打包版）")
    if not shutil.which("ffmpeg"):
        warns.append("未检测到 ffmpeg → 音视频处理不可用（可 winget install Gyan.FFmpeg）")
    return warns


def _check(target):
    """环境体检：解释器 / venv / 代理 / 核心依赖，返回进程退出码。"""
    import deps
    print(f"[体检] 引导解释器：{sys.executable} (Python {sys.version.split()[0]})")
    print(f"[体检] 目标解释器：{target}")
    if not os.path.exists(target):
        print("[体检] ❌ 目标解释器不存在（虚拟环境可能未成功创建）")
        return 1
    hp = deps._proxy_hostport(deps._effective_proxy_env())
    if hp:
        state = "可用" if deps._socket_reachable(*hp) else "不可达（安装时将自动绕过直连）"
        print(f"[体检] 系统代理  ：{hp[0]}:{hp[1]} {state}")
    else:
        print("[体检] 系统代理  ：未配置（直连）")
    print(f"[体检] 安装器    ：{_resolve_installer('auto', _uv_cmd() is not None)}")
    missing = _missing_in(target)
    total = len(deps.AUTO_INSTALL_DEPS)
    if missing is None:
        print("[体检] 依赖状态  ：无法探测（目标解释器执行失败）")
        return 1
    if missing:
        print(f"[体检] 依赖状态  ：缺失 {len(missing)}/{total} 项 → {', '.join(missing)}")
        return 1
    print(f"[体检] 依赖状态  ：核心 {total} 项齐全")
    return 0


def _build(python):
    """安装 PyInstaller 并按 WhaleTalk.spec 打包。"""
    import deps
    on_line = lambda s: print("    " + s, flush=True)  # noqa: E731
    deps.guard_pip_proxy(on_line)
    print("[构建] 安装 PyInstaller …")
    if not deps.pip_install("pyinstaller", on_line=on_line, python=python):
        print("[构建] ❌ PyInstaller 安装失败")
        return 1
    spec = os.path.join(BASE_DIR, "WhaleTalk.spec")
    if not os.path.exists(spec):
        print("[构建] ❌ 未找到 WhaleTalk.spec")
        return 1
    print("[构建] 运行 PyInstaller（--noconfirm --clean WhaleTalk.spec）…")
    rc = subprocess.call([python, "-m", "PyInstaller", "--noconfirm", "--clean", "WhaleTalk.spec"],
                         cwd=BASE_DIR)
    if rc == 0:
        print("[构建] ✅ 打包完成：dist" + os.sep + "WhaleTalk.exe")
        print("       注意：打包前请清空 config.json 中的 API Key。")
    else:
        print(f"[构建] ❌ PyInstaller 退出码 {rc}")
    return rc


def _launch(python, passthrough):
    entry = os.path.join(BASE_DIR, "web_app.py")
    print("[引导] 启动：" + " ".join([python, entry] + list(passthrough)))
    try:
        return subprocess.call([python, entry] + list(passthrough), cwd=BASE_DIR)
    except KeyboardInterrupt:
        return 130


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="bootstrap.py", description="鲸语 WhaleTalk 跨平台引导器")
    p.add_argument("--venv", default=None, help="虚拟环境路径（默认仓库根 .venv）")
    p.add_argument("--no-venv", action="store_true", help="不创建/使用 venv，直接用当前解释器")
    p.add_argument("--skip-install", action="store_true", help="跳过依赖安装，直接启动")
    p.add_argument("--dev", action="store_true", help="额外安装 requirements-dev.txt")
    p.add_argument("--mirror", default=None, help="pip 源（默认清华，等价 WHALETALK_PIP_MIRROR）")
    p.add_argument("--installer", choices=("auto", "uv", "pip"), default="auto",
                   help="依赖安装器（默认 auto：有 uv 用 uv）")
    p.add_argument("--offline", action="store_true", help="仅用本地 wheel 安装（配合 --wheel-dir）")
    p.add_argument("--wheel-dir", default=None, help="离线 wheel 目录（默认仓库根 wheels）")
    p.add_argument("--report", default=None, help="doctor 报告输出路径（'-' 仅打印不写盘）")
    args, unknown = p.parse_known_args(argv)
    return args, unknown


def main(argv=None):
    _harden_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    action = "run"
    if argv and argv[0] in ("run", "check", "doctor", "build"):
        action = argv.pop(0)
    args, passthrough = _parse_args(argv)

    if args.mirror:
        os.environ["WHALETALK_PIP_MIRROR"] = args.mirror
    if args.offline and not args.wheel_dir:
        args.wheel_dir = os.path.join(BASE_DIR, "wheels")

    err = check_python()
    if err:
        print(f"[引导] ❌ {err}")
        return 2

    uv_cmd = _uv_cmd()
    installer = _resolve_installer(args.installer, uv_cmd is not None)

    if args.no_venv:
        target = sys.executable
    else:
        venv_dir = os.path.abspath(args.venv or os.path.join(BASE_DIR, DEFAULT_VENV))
        if action in ("run", "build"):
            target, created, verr = ensure_venv(venv_dir, use_uv=(installer == "uv"), uv_cmd=uv_cmd)
            if verr:
                print(f"[引导] ❌ {verr}")
                return 2
            if created:
                print("[引导] 虚拟环境就绪")
        else:
            target = venv_python(venv_dir)

    if action in ("run", "build") and not args.skip_install:
        reqs = [os.path.join(BASE_DIR, "requirements.txt")]
        if args.dev:
            reqs.append(os.path.join(BASE_DIR, "requirements-dev.txt"))
        _install(target, reqs, lambda s: print("    " + s, flush=True),
                 installer=installer, offline=args.offline, wheel_dir=args.wheel_dir)

    if action == "doctor":
        return _doctor(target, args)
    if action == "check":
        return _check(target)
    if action == "build":
        return _build(target)
    for w in _preflight_warnings():
        print(f"[引导] ⚠ {w}")
    return _launch(target, passthrough)


if __name__ == "__main__":
    sys.exit(main())
