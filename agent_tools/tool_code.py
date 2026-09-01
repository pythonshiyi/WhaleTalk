# -*- coding: utf-8 -*-
"""tool_code —— P0-1 批量拆分（工具域模块）：💻 编程与执行.

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
引用 deepseek_client 的常量与辅助依赖加载顺序契约——主文件在共享基建全部定义后
才执行 `from agent_tools import *`，此处 from-import 可安全解析。
"""

import os
import re
import subprocess
import sys
import time

import permissions

from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import deepseek_client as _dc  # 可变注入配置动态访问（dc.X 注入后立即生效）
from deepseek_client import (

    PIP_ALLOWLIST_NOTICE,
    RUN_PY_MAX_CHARS,
    RUN_PY_MAX_OUTPUT,
    RUN_PY_TIMEOUT,
    TOOL_RESULT_FAIL_PREFIXES,
    _SEARCH_SKIP_DIRS,
    _atomic_write,
    _code_lookup_args,
    _kill_tree,
    _legacy_system_status,
    _mem_tokens,
    _plan_text,
    _run_python_blocked,
    _subagent_write_code,
    _verify_build,
    get_active_client,
)



@tool(
        {
            "type": "function",
            "function": {
                "name": "run_python",
                "description": "在隔离的 Python 子进程中执行代码（默认 -S 不加载第三方库、无网络库）；with_site=true 时加载已安装的第三方库并可访问外网（httpx/requests 等），需要新库时先调用 pip_install 安装",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python 代码"},
                        "with_site": {"type": "boolean", "description": "可选：true 时加载第三方库并允许网络请求（需已安装，如 pip_install 安装的库）"},
                    },
                    "required": ["code"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='执行 Python 代码',
    preactivate=(('代码', '编程', 'python', 'bug', '脚本', '函数'),),
)
def run_python(code, with_site=False):
    if not code or len(code) > RUN_PY_MAX_CHARS:
        return f"错误：代码为空或超过 {RUN_PY_MAX_CHARS} 字符"
    block = _run_python_blocked(code)
    if block:
        permissions.audit("run_python_blocked", "static_check", block[:200], result="denied")
        return f"权限拒绝：{block}"
    try:
        argv = [sys.executable, "-I"]
        if not with_site:
            argv.append("-S")
        argv += ["-c", code]
        # SpooledTemporaryFile 重定向输出：进程刷屏打印时内存峰值限 1MB，
        # 超时 kill 后读取截断，不再全量 buffered 进内存（GB 级打印防 OOM）
        import tempfile

        with tempfile.SpooledTemporaryFile(
            max_size=1 << 20, mode="w+t", encoding="utf-8", errors="replace"
        ) as out:
            proc = subprocess.Popen(
                argv,
                stdout=out,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=permissions.WORKSPACE_DIR or None,
            )
            try:
                proc.wait(timeout=RUN_PY_TIMEOUT)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
                return f"错误：执行超时（>{RUN_PY_TIMEOUT}秒）"
            out.seek(0)
            out_data = out.read(RUN_PY_MAX_OUTPUT)
            out.seek(0, os.SEEK_END)
            if out.tell() > RUN_PY_MAX_OUTPUT:
                out_data += "\n[输出已截断]"
        if not out_data.strip():
            return f"执行成功（无输出），工作目录：{permissions.WORKSPACE_DIR or '（当前目录）'}"
        permissions.audit("run_python", "python -I -S -c <code>", f"{len(code)} 字符, rc={proc.returncode}")
        return (
            out_data
            + f"\n[工作目录：{permissions.WORKSPACE_DIR or '（当前目录）'}，"
            + ("加载第三方库" if with_site else "未加载第三方库（-S 隔离）")
            + "]"
        )
    except Exception as e:
        return f"错误：{e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "执行白名单命令（python/pip/pytest/git 等，禁止 shell 拼接），需开启 shell 权限并可能需确认",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string", "description": "完整命令行，如 python hello.py"}},
                    "required": ["command"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='执行系统命令',
    preactivate=(('执行命令', '终端', '命令行', '运行命令', 'cmd'),),
)
def run_command(command):
    """执行白名单命令（argv 直传，禁止 shell 拼接）。"""
    ok, reason, argv = permissions.check_shell(command)
    if not ok:
        return reason
    timeout = permissions.shell_timeout()
    try:
        # 输出 spool 到临时文件：命令刷屏（type 大日志）时内存峰值限 1MB
        import tempfile

        with tempfile.SpooledTemporaryFile(
            max_size=1 << 20, mode="w+t", encoding="utf-8", errors="replace"
        ) as out:
            proc = subprocess.Popen(
                argv,
                stdout=out,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                # 命令在工作目录执行（跟随 📁 目录设置），相对路径引用不再漂移
                cwd=_dc.WORKING_DIR or permissions.WORKSPACE_DIR or None,
            )
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
                return f"错误：命令超时（>{timeout} 秒）"
            out.seek(0)
            out_data = out.read(20000)
            out.seek(0, os.SEEK_END)
            if out.tell() > 20000:
                out_data += "\n[输出已截断]"
        permissions.audit("run_command", " ".join(argv), f"rc={proc.returncode}")
        if not out_data.strip():
            return f"执行成功（无输出），退出码 {proc.returncode}"
        return f"退出码 {proc.returncode}\n{out_data}"
    except Exception as e:
        return f"错误：{e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "run_lint",
                "description": "静态检查（ruff）：发现语法/风格/未定义变量/未用 import 等常见问题。写完 Python 代码后立即调用，错误尽早暴露成本更低",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "可选：目标目录（不填用工作目录）"},
                        "fix": {"type": "boolean", "description": "可选：是否自动修复（--fix）"},
                    },
                    "required": [],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='静态检查',
    preactivate=(('lint', '静态检查', '语法检查', '代码规范', 'ruff'),),
)
def run_lint(path=None, fix=False):
    """静态检查（ruff）：对目录跑 ruff，返回错误/警告摘要。

    写完 Python 代码后立即调用，快速发现语法/风格/未定义变量/未用 import 等低级错误，
    避免错误累积到测试/运行阶段才暴露。
    """
    import shutil

    base = path or _dc.WORKING_DIR or permissions.WORKSPACE_DIR or os.getcwd()
    ok, reason = permissions.check_filesystem(base, write=bool(fix))
    if not ok:
        return reason
    base = permissions.resolve(base) or base
    if not os.path.isdir(base):
        return f"错误：目录不存在：{base}"

    ruff = shutil.which("ruff")
    if not ruff:
        return "错误：本机未安装 ruff（可 pip install ruff 后重试）"

    cmd = [ruff, "check"]
    if fix:
        cmd.append("--fix")
    cmd.append(base)
    try:
        import tempfile
        with tempfile.SpooledTemporaryFile(
            max_size=1 << 20, mode="w+t", encoding="utf-8", errors="replace"
        ) as out:
            proc = subprocess.Popen(
                cmd, stdout=out, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", cwd=base,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                return "错误：ruff 检查超时（120 秒）"
            out.seek(0)
            out_data = out.read(12000)
            out.seek(0, os.SEEK_END)
            if out.tell() > 12000:
                out_data += "\n[输出已截断]"
        if proc.returncode == 0:
            return "无问题（ruff 检查通过）"
        return f"ruff 检查发现问题：\n{out_data}"
    except Exception as e:
        return f"错误：ruff 执行失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "run_tests",
                "description": "运行测试（pytest/unittest）并返回结果摘要：自我验证闭环第一步（写完代码后自测）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "可选：测试文件或目录（留空自动扫描允许目录内的 test_*.py）"},
                        "framework": {"type": "string", "description": "可选：auto/pytest/unittest（默认 auto）"},
                    },
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='运行项目测试',
    preactivate=(('一键验证', '验证项目', '自测', '检查一下', '跑测试'), ('代码', '编程', 'python', 'bug', '脚本', '函数')),
)
def run_tests(path=None, framework="auto"):
    """在允许目录内运行测试（pytest/unittest），返回结果摘要。

    path：测试文件或目录（留空则扫描允许目录内 *_test.py / test_*.py）。
    """
    target = None
    if str(path or "").strip():
        target = permissions.resolve(path)
        if not target or not os.path.exists(target):
            return f"错误：路径不存在：{path}"
        ok, reason = permissions.check_filesystem(target, write=False)
        if not ok:
            return reason
    import glob as _glob

    if target is None:
        base = permissions.WORKSPACE_DIR
        if not base:
            return "错误：未配置工作目录"
        found = _glob.glob(os.path.join(base, "**", "test_*.py"), recursive=True)[:20] + \
                _glob.glob(os.path.join(base, "**", "*_test.py"), recursive=True)[:20]
        if not found:
            return "错误：允许目录内未找到测试文件（test_*.py / *_test.py）"
        target = found[0]
    fw = str(framework or "auto").lower()
    if fw == "unittest":
        cmd = [sys.executable, "-m", "unittest", "discover", "-v"]
        if target and os.path.isfile(target):
            cmd = [sys.executable, "-m", "unittest", "-v", str(target)]
    elif fw == "pytest":
        cmd = [sys.executable, "-m", "pytest", "-q"]
        if target:
            cmd.append(str(target))
    else:  # auto：优先 pytest（函数测试与 unittest 类都能跑）；pytest 缺失时回退 unittest discover
        try:
            import pytest  # noqa: F401
            _has_pytest = True
        except ImportError:
            _has_pytest = False
        if _has_pytest:
            cmd = [sys.executable, "-m", "pytest", "-q"]
            if target:
                cmd.append(str(target))
        else:
            cmd = [sys.executable, "-m", "unittest", "discover", "-v"]
            if target:
                cmd.append(str(target))
    try:
        # SpooledTemporaryFile 限流：pytest -v / unittest 输出可达 MB 级，
        # capture_output 全量进内存会 OOM；内存峰值限 1MB 后自动转磁盘
        import tempfile

        with tempfile.SpooledTemporaryFile(
            max_size=1 << 20, mode="w+t", encoding="utf-8", errors="replace"
        ) as out:
            proc = subprocess.Popen(
                cmd, stdout=out, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace",
                cwd=os.path.dirname(target) if os.path.isfile(target) else target,
            )
            try:
                proc.wait(timeout=180)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
                return "错误：测试执行超时（180 秒）"
            out.seek(0)
            out_data = out.read(12000)
            out.seek(0, os.SEEK_END)
            if out.tell() > 12000:
                out_data += "\n[输出已截断]"
        return f"退出码 {proc.returncode}\n{out_data}"
    except subprocess.TimeoutExpired:
        return "错误：测试超时（>180 秒）"
    except Exception as e:
        return f"错误：运行测试失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "verify_project",
                "description": "一键验证：静态检查(ruff)→测试(pytest)→前端构建(npm run build)。项目开发完成后调用，按检测到的产物类型跑完并汇总",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "可选：项目目录（不填用工作目录）"},
                    },
                    "required": [],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='一键验证',
    preactivate=(('一键验证', '验证项目', '自测', '检查一下', '跑测试'),),
)
def verify_project(path=None):
    """一键验证：静态检查（ruff）→ 测试（pytest）→ 前端构建（npm run build）。

    项目开发完成后调用，按检测到的产物类型依次跑完验证步骤并汇总结果，
    作为「写完代码自测」的收尾闭环。
    """
    import glob as _glob

    base = path or _dc.WORKING_DIR or permissions.WORKSPACE_DIR or os.getcwd()
    ok, reason = permissions.check_filesystem(base, write=False)
    if not ok:
        return reason
    base = permissions.resolve(base) or base
    if not os.path.isdir(base):
        return f"错误：目录不存在：{base}"

    lines = [f"一键验证：{base}", ""]
    steps = 0

    if _glob.glob(os.path.join(base, "**", "*.py"), recursive=True):
        steps += 1
        lines.append(f"[{steps}] 静态检查（ruff）：")
        lines.append("  " + run_lint(base).replace("\n", "\n  "))
        lines.append("")

    tests = _glob.glob(os.path.join(base, "**", "test_*.py"), recursive=True) + \
            _glob.glob(os.path.join(base, "**", "*_test.py"), recursive=True)
    if tests:
        steps += 1
        lines.append(f"[{steps}] 测试（pytest）：")
        lines.append("  " + run_tests(base).replace("\n", "\n  "))
        lines.append("")

    if os.path.isfile(os.path.join(base, "package.json")):
        steps += 1
        lines.append(f"[{steps}] 前端构建（npm run build）：")
        lines.append("  " + _verify_build(base).replace("\n", "\n  "))
        lines.append("")

    if steps == 0:
        return "未发现可验证产物（无 .py 文件、无测试文件、无 package.json）"
    lines.append(f"验证完成：共 {steps} 步")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "project_scaffold",
                "description": "生成标准项目脚手架：python(后端)/react(前端)/fullstack(全栈)，创建目录结构+基础文件。从零开发项目时先调它，无需手动搭结构",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_type": {"type": "string", "description": "类型：python / react / fullstack"},
                        "name": {"type": "string", "description": "可选：项目名（默认 my_project）"},
                        "path": {"type": "string", "description": "可选：父目录（不填用工作目录）"},
                    },
                    "required": ["project_type"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='项目脚手架',
    preactivate=(('脚手架', '建项目', '初始化项目', '项目模板', '搭项目', '新项目'),),
)
def project_scaffold(project_type, name=None, path=None):
    """生成标准项目脚手架（目录结构 + 基础文件）。

    project_type: python（后端）/ react（前端）/ fullstack（全栈）。
    生成后 AI 直接在此基础上开发，无需从零搭结构。
    """
    ptype = str(project_type or "").strip().lower()
    if ptype not in ("python", "react", "fullstack"):
        return "错误：project_type 仅支持 python / react / fullstack"

    base = path or _dc.WORKING_DIR or permissions.WORKSPACE_DIR or os.getcwd()
    ok, reason = permissions.check_filesystem(base, write=True)
    if not ok:
        return reason
    base = permissions.resolve(base) or base

    name = (name or "my_project").strip()
    proj_dir = os.path.join(base, name)
    created = []

    def write(rel, content):
        p = os.path.join(proj_dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        created.append(rel)

    if ptype in ("python", "fullstack"):
        root = "" if ptype == "python" else "backend"
        write(os.path.join(root, "main.py"),
              "def main():\n    print(\"Hello from __NAME__\")\n\n\nif __name__ == \"__main__\":\n    main()\n")
        write(os.path.join(root, "requirements.txt"), "# 依赖列表，每行一个包名\n")
        write(os.path.join(root, "README.md"),
              "# __NAME__\n\n## 运行\n```\npython main.py\n```\n\n## 测试\n```\npytest tests/\n```\n")
        write(os.path.join(root, "tests", "test_main.py"),
              "def test_main():\n    from main import main\n    assert callable(main)\n")

    if ptype in ("react", "fullstack"):
        root = "" if ptype == "react" else "frontend"
        write(os.path.join(root, "package.json"),
              '{\n  "name": "__NAME__",\n  "version": "0.1.0",\n  "type": "module",\n'
              '  "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},\n'
              '  "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},\n'
              '  "devDependencies": {"@vitejs/plugin-react": "^4.3.4", "vite": "^6.0.0"}\n}\n')
        write(os.path.join(root, "index.html"),
              '<!doctype html>\n<html>\n  <head>\n    <meta charset="UTF-8" />\n    <title>__NAME__</title>\n'
              '  </head>\n  <body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.jsx"></script>\n'
              '  </body>\n</html>\n')
        write(os.path.join(root, "vite.config.js"),
              "import { defineConfig } from 'vite'\nimport react from '@vitejs/plugin-react'\n\n"
              "export default defineConfig({ plugins: [react()] })\n")
        write(os.path.join(root, "src", "main.jsx"),
              "import React from 'react'\nimport ReactDOM from 'react-dom/client'\nimport App from './App.jsx'\n"
              "import './styles.css'\n\nReactDOM.createRoot(document.getElementById('root')).render(\n"
              "  <React.StrictMode><App /></React.StrictMode>\n)\n")
        write(os.path.join(root, "src", "App.jsx"),
              "import React from 'react'\n\nexport default function App() {\n"
              "  return <div className=\"app\"><h1>__NAME__</h1></div>\n}\n")
        write(os.path.join(root, "src", "styles.css"),
              "body { margin: 0; font-family: system-ui, sans-serif; }\n.app { padding: 2rem; }\n")

    for rel in created:
        p = os.path.join(proj_dir, rel)
        with open(p, "r", encoding="utf-8") as f:
            s = f.read()
        with open(p, "w", encoding="utf-8") as f:
            f.write(s.replace("__NAME__", name))

    return f"已生成 {ptype} 脚手架：{proj_dir}\n" + "\n".join(f"  {r}" for r in created)


@tool(
        {
            "type": "function",
            "function": {
                "name": "dev_plan",
                "description": "开发计划持久化：长项目分步执行、断点恢复。init 初始化计划 / show 查看进度 / step_done 标记完成 / clear 清除",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "init/show/step_done/clear"},
                        "title": {"type": "string", "description": "init 时：计划标题"},
                        "goal": {"type": "string", "description": "init 时：目标说明"},
                        "steps": {"type": "array", "items": {"type": "string"}, "description": "init 时：步骤列表"},
                        "step_index": {"type": "integer", "description": "step_done 时：步骤编号（从 0 开始）"},
                        "path": {"type": "string", "description": "可选：项目目录（不填用工作目录）"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='开发计划',
    preactivate=(('脚手架', '建项目', '初始化项目', '项目模板', '搭项目', '新项目'), ('开发计划', '分步', '任务进度', '断点', '做到哪一步')),
)
def dev_plan(action, title=None, goal=None, steps=None, step_index=None, path=None):
    """开发计划持久化：长项目分步执行、断点恢复，避免迷路/重复劳动。

    action:
      init       初始化计划（title + goal + steps，steps 为字符串数组）
      show       查看当前计划与进度
      step_done  标记某步完成（step_index，从 0 开始）
      clear      清除计划

    计划存于项目目录 .whaletalk_plan.json，跨轮次持久。
    """
    import json
    from datetime import datetime

    base = path or _dc.WORKING_DIR or permissions.WORKSPACE_DIR or os.getcwd()
    ok, reason = permissions.check_filesystem(base, write=(action in ("init", "step_done", "clear")))
    if not ok:
        return reason
    base = permissions.resolve(base) or base
    plan_path = os.path.join(base, ".whaletalk_plan.json")
    act = (action or "").strip().lower()

    def load():
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    if act == "init":
        if not title or not steps:
            return "错误：init 需要 title 和 steps（步骤列表）"
        step_list = steps if isinstance(steps, list) else [s.strip() for s in str(steps).split("\n") if s.strip()]
        plan = {
            "title": str(title).strip(),
            "goal": str(goal or "").strip(),
            "steps": [{"desc": str(s).strip(), "done": False} for s in step_list],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        return f"已初始化开发计划「{plan['title']}」（{len(plan['steps'])} 步）\n" + _plan_text(plan)

    if act == "show":
        plan = load()
        if not plan:
            return "无进行中的开发计划（用 dev_plan init 初始化）"
        return _plan_text(plan)

    if act == "step_done":
        plan = load()
        if not plan:
            return "无进行中的开发计划"
        try:
            idx = int(step_index)
        except (TypeError, ValueError):
            return "错误：step_index 需为步骤编号（从 0 开始）"
        if idx < 0 or idx >= len(plan["steps"]):
            return f"错误：step_index 越界（0~{len(plan['steps']) - 1}）"
        plan["steps"][idx]["done"] = True
        plan["updated_at"] = datetime.now().isoformat(timespec="seconds")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        return _plan_text(plan)

    if act == "clear":
        if os.path.exists(plan_path):
            os.remove(plan_path)
        return "已清除开发计划"

    return "错误：未知 action，可用 init/show/step_done/clear"


@tool(
        {
            "type": "function",
            "function": {
                "name": "get_status",
                "description": "全局态势总览：一次掌握系统、用量、运行任务、健康、待办、当前项目。默认返回核心摘要；需细节用 section 钻取（recent/processes/checkpoint 等）。涉全局/进度/状态/多任务时优先调用",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string", "description": "可选：钻取的详情区块（recent/processes/schedules/checkpoint/health），不填返回核心摘要"},
                    },
                    "required": [],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='全局态势总览',
    preactivate=(('全局', '概况', '整体情况', '运行情况', '什么情况', '进展', '状态', '工作台'),),
)
def get_status(section=None):
    """全局态势总览（单一事实源 build_situation）：默认核心摘要，section 取详情。

    由 api_server 注入 BUILD_SITUATION 函数；未接线（如测试/降级）时回退到资源自检。
    """
    if _dc.BUILD_SITUATION is not None:
        try:
            return _dc.BUILD_SITUATION(section)
        except Exception as e:
            return f"态势快照获取失败：{e}"
    return _legacy_system_status()


@tool(
        {
            "type": "function",
            "function": {
                "name": "project_map",
                "description": "生成项目结构地图：文件树 + Python 符号表（函数/类+行号）+ import 依赖图。开发多文件项目前先调它掌握全貌，避免逐文件读全文、漏改跨文件依赖",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "可选：项目目录（不填用工作目录）"},
                        "max_files": {"type": "integer", "description": "可选：文件树/符号表扫描上限（默认 150）"},
                    },
                    "required": [],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='项目结构地图',
    preactivate=(('依赖图', '符号表', '项目结构', '代码地图', '函数定义', '调用关系', '引用'),),
)
def project_map(path=None, max_files=150):
    """生成项目结构地图：文件树 + Python 符号表（函数/类+行号）+ import 依赖图。

    让 AI 无需逐文件读全文即可掌握多文件项目的结构，避免跨文件漏改。
    """
    import ast

    base = path or _dc.WORKING_DIR or permissions.WORKSPACE_DIR or os.getcwd()
    ok, reason = permissions.check_filesystem(base, write=False)
    if not ok:
        return reason
    base = permissions.resolve(base) or base
    if not os.path.isdir(base):
        return f"错误：目录不存在：{base}"

    py_files = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build")]
        for fn in files:
            if fn.endswith(".py"):
                py_files.append(os.path.join(root, fn))
        if len(py_files) >= max_files:
            break
    py_files = py_files[:max_files]

    lines = [f"项目地图：{base}", f"Python 文件数：{len(py_files)}", "", "[文件树]"]
    for p in py_files:
        lines.append(f"  {os.path.relpath(p, base)}")

    symbols = {}
    imports = {}
    for p in py_files:
        rel = os.path.relpath(p, base)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                tree = ast.parse(f.read())
        except Exception:
            continue
        syms, deps = [], []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                syms.append(f"def {node.name} (L{node.lineno})")
            elif isinstance(node, ast.ClassDef):
                syms.append(f"class {node.name} (L{node.lineno})")
            elif isinstance(node, ast.Import):
                deps.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                deps.append("." * node.level + (node.module or ""))
        if syms:
            symbols[rel] = syms
        if deps:
            imports[rel] = deps

    if symbols:
        lines += ["", "[符号表]"]
        for rel, syms in symbols.items():
            lines.append(f"  {rel}:")
            for s in syms:
                lines.append(f"    - {s}")
    if imports:
        lines += ["", "[依赖图]"]
        for rel, deps in imports.items():
            lines.append(f"  {rel} -> {', '.join(deps[:10])}")
    if not symbols and not imports:
        lines += ["", "（未发现 Python 符号或 import）"]
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "find_symbol",
                "description": "定位 Python 符号（函数/类）的定义与引用位置（文件:行号）。改某个函数前先定位它的所有引用，避免漏改",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "符号名（函数名或类名）"},
                        "path": {"type": "string", "description": "可选：项目目录（不填用工作目录）"},
                        "max_files": {"type": "integer", "description": "可选：扫描文件数上限（默认 150）"},
                    },
                    "required": ["name"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='符号定位',
    preactivate=(('依赖图', '符号表', '项目结构', '代码地图', '函数定义', '调用关系', '引用'),),
)
def find_symbol(name, path=None, max_files=150):
    """定位符号（函数/类）的定义与引用位置（Python ast）。

    返回「定义（文件:行号 def/class ...）」与「引用（文件:行号）」，供精确修改前定位。
    """
    import ast

    name = (name or "").strip()
    if not name:
        return "错误：缺少符号名（函数/类名）"
    base = path or _dc.WORKING_DIR or permissions.WORKSPACE_DIR or os.getcwd()
    ok, reason = permissions.check_filesystem(base, write=False)
    if not ok:
        return reason
    base = permissions.resolve(base) or base
    if not os.path.isdir(base):
        return f"错误：目录不存在：{base}"

    py_files = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build")]
        for fn in files:
            if fn.endswith(".py"):
                py_files.append(os.path.join(root, fn))
        if len(py_files) >= max_files:
            break

    defs, refs = [], []
    for p in py_files[:max_files]:
        rel = os.path.relpath(p, base)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                tree = ast.parse(f.read())
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                defs.append(f"{rel}:{node.lineno}  def {node.name}(...)")
            elif isinstance(node, ast.ClassDef) and node.name == name:
                defs.append(f"{rel}:{node.lineno}  class {node.name}")
            elif isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
                refs.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr == name:
                refs.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == name:
                        refs.append(f"{rel}:{node.lineno}")

    lines = [f"符号「{name}」定位结果：", f"  定义（{len(defs)}）："]
    lines += [f"    - {d}" for d in defs[:20]] or ["    - （未找到定义）"]
    lines.append(f"  引用（{len(refs)}）：")
    lines += [f"    - {r}" for r in refs[:30]] or ["    - （未找到引用）"]
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "code_lookup",
                "description": "代码结构定位（AST 只读）：定位 Python 符号的函数/类定义、调用点与导入来源，返回文件行号与摘要。改代码前先查定义与调用点，避免改 A 炸 B",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要扫描的目录或 .py 文件绝对路径"},
                        "symbol": {"type": "string", "description": "要定位的符号名（函数/类名，import 时可为模块名或别名）"},
                        "kind": {"type": "string", "description": "def=函数定义（默认）/ class=类定义 / call=调用点 / import=导入来源"},
                        "max_results": {"type": "integer", "description": "最多返回条数，默认 20"},
                    },
                    "required": ["path", "symbol"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='代码结构定位（函数/类定义、调用点、导入来源）',
    preactivate=(('在哪定义', '定义在哪', '谁在调用', '调用点', '引用关系', '代码结构', '符号定位', '看下源码'),),
)
def code_lookup(path, symbol, kind="def", max_results=20):
    """代码结构定位（AST 级，只读）：在允许目录内解析 Python 文件，
    返回符号的定义/类/调用点/导入位置，一行一条「文件:行号 摘要」。

    kind：
      def     → 函数/方法定义（含参数摘要）
      class   → 类定义（含基类）
      call    → 函数调用点（含实参个数）
      import  → 导入来源（模块/别名）
    """
    ok, reason = permissions.check_filesystem(path, write=False)
    if not ok:
        return reason
    p = permissions.resolve(path)
    if not os.path.exists(p):
        return f"错误：路径不存在：{p}"
    sym = str(symbol or "").strip()
    if not sym:
        return "错误：symbol 不能为空"
    k = str(kind or "def").strip().lower()
    if k not in ("def", "class", "call", "import"):
        return "错误：kind 仅支持 def/class/call/import"
    try:
        limit = max(1, min(200, int(max_results or 20)))
    except (TypeError, ValueError):
        limit = 20
    import ast as _ast

    files = []
    if os.path.isfile(p) and p.lower().endswith(".py"):
        files = [p]
    else:
        scanned = 0
        for root, dirs, fns in os.walk(p):
            dirs[:] = [d for d in dirs if d not in _SEARCH_SKIP_DIRS]
            for fn in fns:
                if scanned >= 2000:
                    break
                if fn.endswith(".py"):
                    files.append(os.path.join(root, fn))
                scanned += 1
            if scanned >= 2000:
                break
    hits = []
    for full in files:
        if len(hits) >= limit:
            break
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except Exception:
            continue
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            continue
        rel = os.path.relpath(full, p) if os.path.isdir(p) else os.path.basename(full)
        for node in _ast.walk(tree):
            if len(hits) >= limit:
                break
            line = getattr(node, "lineno", 0)
            if k == "def" and isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == sym:
                hits.append(f"{rel}:{line}  def {sym}({_code_lookup_args(node)})")
            elif k == "class" and isinstance(node, _ast.ClassDef) and node.name == sym:
                bases = ", ".join(_ast.unparse(b) for b in node.bases[:3]) if node.bases else ""
                hits.append(f"{rel}:{line}  class {sym}({bases})" if bases else f"{rel}:{line}  class {sym}")
            elif k == "call" and isinstance(node, _ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, _ast.Name) else (fn.attr if isinstance(fn, _ast.Attribute) else None)
                if name == sym:
                    n_args = len(node.args) + len(node.keywords)
                    hits.append(f"{rel}:{line}  call {sym}({n_args} 个实参)")
            elif k == "import" and isinstance(node, (_ast.Import, _ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == sym or alias.asname == sym:
                        hits.append(f"{rel}:{line}  import {alias.name}" + (f" as {alias.asname}" if alias.asname else ""))
    if not hits:
        return f"未找到符号「{sym}」（kind={k}，已扫描 {len(files)} 个 .py 文件）"
    note = f"\n[已限制显示前 {limit} 条]" if len(hits) >= limit else ""
    return f"符号「{sym}」（{k}）共 {len(hits)} 处：\n" + "\n".join(hits) + note


@tool(
        {
            "type": "function",
            "function": {
                "name": "write_code_project",
                "description": "创建多文件代码工程（批量写文件，自动建目录，需 write 权限；单文件 ≤50MB、单次 ≤50 文件，无需担心内容字符上限）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_dir": {"type": "string", "description": "工程根目录绝对路径（须在允许目录内）"},
                        "files": {
                            "type": "array",
                            "description": "文件列表",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string", "description": "相对工程根目录的路径，如 src/main.py"},
                                    "content": {"type": "string", "description": "文件内容"},
                                },
                                "required": ["path", "content"],
                            },
                        },
                    },
                    "required": ["project_dir", "files"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='创建完整代码项目',
    preactivate=(('写', '保存', '创建', '生成'),),
)
def write_code_project(project_dir, files):
    """创建多文件代码工程：批量写文件（逐文件原子写 + 越界防护）。"""
    ok, reason = permissions.check_filesystem(project_dir, write=True)
    if not ok:
        return reason
    if not isinstance(files, list) or not files:
        return '错误：files 必须是非空数组 [{"path": "...", "content": "..."}]'
    if len(files) > 50:
        return "错误：文件数超过 50 上限"
    base = permissions.resolve(project_dir)
    created = []
    failed = []
    total = 0
    for f in files[:50]:
        if not isinstance(f, dict):
            failed.append(("?", "元素必须是对象"))
            continue
        rel = str(f.get("path") or "").strip().replace("\\", "/")
        content = f.get("content") or ""
        if not rel or rel in (".", "..") or ".." in rel.split("/"):
            failed.append((rel or "?", "非法相对路径"))
            continue
        # 与 write_file 同规则：按 UTF-8 字节校验（中文 3 字节/字）
        if len(str(content).encode("utf-8", "ignore")) > permissions.max_write_size():
            failed.append((rel, "内容超过大小限制"))
            continue
        full = os.path.normpath(os.path.join(base, rel))
        if full != base and not full.startswith(base.rstrip("\\/") + os.sep):
            failed.append((rel, "路径越界"))
            continue
        try:
            os.makedirs(os.path.dirname(full) or base, exist_ok=True)
            _atomic_write(full, content)
            if not os.path.exists(full):
                failed.append((rel, "写入后核验失败"))
                continue
            created.append(rel)
            total += len(content)
        except Exception as e:
            failed.append((rel, str(e)))
    if not created:
        return "错误：全部文件写入失败：" + "；".join(f"{r}({why})" for r, why in failed)
    permissions.audit("write_code_project", base, f"{len(created)} 个文件")
    lines = [f"已创建代码工程 {base}（{len(created)} 个文件，均核验存在）", "文件清单："]
    lines += ["· " + c for c in created]
    if failed:
        lines.append(f"⚠ 失败 {len(failed)} 个：")
        lines += ["· " + r + "：" + why for r, why in failed]
    lines.append(f"共 {len(created)} 个文件，{total} 字符")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "pip_install",
                "description": "安装 Python 库，安装后配合 run_python(with_site=true) 使用；已装常用库：openpyxl/matplotlib/pymysql/psycopg2/Pillow 等",
                "parameters": {
                    "type": "object",
                    "properties": {"package": {"type": "string", "description": "要安装的包名（如 pandas / requests，是否需用户确认由权限配置决定）"}},
                    "required": ["package"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='安装 Python 包',
    preactivate=(('安装库', 'pip安装', '装个包', '缺库', '装依赖'),),
)
def pip_install(package):
    """安装 Python 库到当前环境（配合 run_python(with_site=true) 使用）。

    完全体模式下不限制包名；若 PIP_ALLOWLIST 为列表则仅允许白名单内安装。
    """
    pkg = str(package or "").strip()
    if not pkg:
        return "错误：请提供要安装的包名"
    base = re.split(r"[<>=!~]", pkg)[0].strip().lower()
    if _dc.PIP_ALLOWLIST is not None and base not in _dc.PIP_ALLOWLIST:
        return f"权限拒绝：仅允许安装白名单库：{_dc.PIP_ALLOWLIST}"
    try:
        # SpooledTemporaryFile 限流：--quiet 下 pip 错误输出仍可能 MB 级，防 OOM
        import tempfile

        with tempfile.SpooledTemporaryFile(
            max_size=1 << 20, mode="w+t", encoding="utf-8", errors="replace"
        ) as out:
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", pkg],
                stdout=out, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace",
            )
            try:
                proc.wait(timeout=300)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
                return "错误：安装超时（300 秒）"
            out.seek(0)
            out_data = out.read()
            if len(out_data) > 20000:
                out_data = out_data[-20000:] + "\n[较早输出已省略]"
        if proc.returncode == 0:
            return f"已安装 {pkg}。\n{PIP_ALLOWLIST_NOTICE}"
        return f"安装失败：{out_data[-800:]}"
    except subprocess.TimeoutExpired:
        return "错误：安装超时（300 秒）"
    except Exception as e:
        return f"错误：{e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "subagent_run",
                "description": "并行子代理：把大任务拆给多个并发子代理。mode=text 汇总结论（并行调研/方案对比）；mode=code 各子代理编写代码模块并落盘（并行开发多模块）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tasks": {"type": "array", "items": {"type": "string"}, "description": "子任务列表（字符串数组，最多 8 个）"},
                        "parallel": {"type": "integer", "description": "可选：并行数 1-4（默认 2）"},
                        "context": {"type": "string", "description": "可选：共享背景上下文（注入每个子代理）"},
                        "mode": {"type": "string", "description": "可选：text（结论）/ code（代码落盘），默认 text"},
                        "output_dir": {"type": "string", "description": "code 模式：代码落盘目录（默认工作目录）"},
                    },
                    "required": ["tasks"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='派发子智能体并行处理',
    preactivate=(('子代理', '并行处理', '子智能体', '分头做'),),
)
def subagent_run(tasks, parallel=2, context="", mode="text", output_dir=None):
    """并行子代理：把大任务拆给多个并发 LLM 子代理。

    mode:
      text（默认）：子代理输出结论，汇总返回（适合并行调研/多方案对比/多文件并行处理）
      code：子代理各自编写代码模块，输出「@@FILE: 路径 + 代码块」，主代理解析后落盘
            （适合并行开发多个模块；output_dir 指定落盘目录，默认工作目录）
    """
    if not isinstance(tasks, list) or not tasks:
        return "错误：tasks 必须是非空数组（每个元素是一个子任务目标）"
    tasks = [str(t) for t in tasks][:8]
    try:
        parallel = max(1, min(4, int(parallel or 2)))
    except (TypeError, ValueError):
        parallel = 2
    client = get_active_client()
    if client is None:
        return "错误：没有可用客户端（请先在设置中配置 API Key）"

    is_code = (mode or "text").strip().lower() == "code"
    out_dir = permissions.resolve(output_dir) if output_dir else (_dc.WORKING_DIR or permissions.WORKSPACE_DIR or os.getcwd())

    base = "你是并行子代理，专注完成分配的子任务，输出简洁、可执行的结论（不要提及子代理身份）。"
    if is_code:
        base = (
            "你是并行子代理，负责编写一个代码模块。\n"
            "输出格式：每个文件用「@@FILE: 相对路径」单独一行开头，紧接着一个代码块（用 ``` 包裹）。\n"
            "例如：\n"
            "@@FILE: src/utils.py\n"
            "```python\n"
            "def helper():\n    return 1\n"
            "```\n"
            "只输出代码文件，不要输出多余的解释或自我介绍。"
        )
    if str(context or "").strip():
        base += f"\n\n【共享背景上下文】\n{context}"
    results = [None] * len(tasks)

    def run(i, task):
        last_err = None
        for attempt in range(2):
            try:
                resp = client.client.chat.completions.create(
                    model=client.model,
                    messages=[
                        {"role": "system", "content": base},
                        {"role": "user", "content": str(task)},
                    ],
                    max_tokens=4096 if is_code else 2048,
                    stream=False,
                    timeout=180.0 if is_code else 120.0,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                results[i] = (resp.choices[0].message.content or "").strip() or "（子代理无输出）"
                return
            except Exception as e:
                last_err = e
                if attempt == 0:
                    time.sleep(1)
        results[i] = f"（子任务失败：{last_err}）"

    import concurrent.futures as cf

    with cf.ThreadPoolExecutor(max_workers=parallel) as ex:
        futures = [ex.submit(run, i, t) for i, t in enumerate(tasks)]
        for _f in cf.as_completed(futures):
            pass

    if is_code:
        return _subagent_write_code(results, tasks, out_dir)

    lines = []
    for i, t in enumerate(tasks):
        lines.append(f"## 子任务 {i + 1}：{t[:80]}\n{results[i]}")
    return "\n\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "verify_output",
                "description": "对照标准答案自评：计算语义相似度（F1/覆盖率），指出缺失要点。自我验证闭环第二步：完成任务后与预期结果对照检查",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expected": {"type": "string", "description": "预期答案/标准要点"},
                        "actual": {"type": "string", "description": "实际输出/实现结果"},
                    },
                    "required": ["expected", "actual"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='核验产物/输出',
    preactivate=(('核验输出', '对照检查', '检查结果', '自评', '核对答案'),),
)
def verify_output(expected, actual):
    """对照标准答案自评：计算语义相似度并指出差异要点（自我验证闭环）。"""
    e = str(expected or "")
    a = str(actual or "")
    if not e.strip():
        return "错误：expected 必填"
    if not a.strip():
        return "评估：实际输出为空（0% 匹配）"
    et, at = set(_mem_tokens(e)), set(_mem_tokens(a))
    if not et or not at:
        return "评估：无法分词比较（内容过短）"
    inter = et & at
    recall = len(inter) / len(et)          # 预期要点覆盖率
    precision = len(inter) / len(at)       # 输出聚焦度
    f1 = 2 * recall * precision / (recall + precision) if (recall + precision) else 0.0
    missing = [t for t in et if t not in at][:10]
    verdict = "通过" if f1 >= 0.7 else ("基本通过" if f1 >= 0.5 else "未通过")
    lines = [
        f"评估：{verdict}（F1={f1:.2f}，覆盖率 {recall:.0%}，聚焦度 {precision:.0%}）",
    ]
    if missing:
        lines.append("缺失要点：" + "、".join(missing))
    if len(e) > 0 and len(a) > 0 and a.strip().startswith(TOOL_RESULT_FAIL_PREFIXES):
        lines.append("提示：实际输出以错误开头，请检查执行是否成功")
    return "\n".join(lines)


__all__ = ['run_python', 'run_command', 'run_lint', 'run_tests', 'verify_project', 'project_scaffold', 'dev_plan', 'get_status', 'project_map', 'find_symbol', 'code_lookup', 'write_code_project', 'pip_install', 'subagent_run', 'verify_output']
