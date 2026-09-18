"""引导器回归：requirements 解析 + 死代理自动绕过 + pip 解释器透传。

背景（真实故障）：某机系统代理 127.0.0.1:7890 残留为「已启用」但进程未运行，
pip 继承后所有请求 WinError 10061 → 原 start.bat 一把梭安装整批失败。现由
bootstrap.py + deps.guard_pip_proxy 吞掉该差异，本套用例锁死这些契约。
"""
import os
import sys

import bootstrap
import deps


def test_parse_requirements_strips_comments_and_options(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(
        "# 顶部注释\n"
        "\n"
        "openai>=1.75.0  # 行内注释\n"
        "qrcode[pil]>=7.4\n"
        "-r other.txt\n"
        "--index-url https://example.com/simple\n"
        "numpy>=1.24.0 \\\n"
        "    ; python_version > '3.6'\n",
        encoding="utf-8",
    )
    specs = bootstrap.parse_requirements(str(req))
    assert "openai>=1.75.0" in specs
    assert "qrcode[pil]>=7.4" in specs
    assert any(s.startswith("numpy>=1.24.0") and "python_version" in s for s in specs)
    assert all(not s.startswith("-") for s in specs)


def test_parse_requirements_missing_file_returns_empty(tmp_path):
    assert bootstrap.parse_requirements(str(tmp_path / "nope.txt")) == []


def test_venv_python_path_shape(tmp_path):
    py = bootstrap.venv_python(str(tmp_path / ".venv"))
    if os.name == "nt":
        assert py.endswith(os.path.join("Scripts", "python.exe"))
    else:
        assert py.endswith(os.path.join("bin", "python"))


def test_check_python_ok():
    assert bootstrap.check_python() is None  # 运行环境必然 >= 3.9


def test_pick_proxy_variants():
    assert deps._pick_proxy("127.0.0.1:7890") == "127.0.0.1:7890"
    assert deps._pick_proxy("http=1.2.3.4:80;https=5.6.7.8:443") == "5.6.7.8:443"
    assert deps._pick_proxy("http=1.2.3.4:80") == "1.2.3.4:80"
    assert deps._pick_proxy("") is None


def test_proxy_hostport_variants():
    assert deps._proxy_hostport("127.0.0.1:7890") == ("127.0.0.1", 7890)
    assert deps._proxy_hostport("http://127.0.0.1:7890") == ("127.0.0.1", 7890)
    assert deps._proxy_hostport("http://proxy.local") == ("proxy.local", 80)
    assert deps._proxy_hostport("") is None


def test_guard_bypasses_dead_proxy(monkeypatch):
    """死代理 → 设 NO_PROXY=* 并返回提示。"""
    monkeypatch.setattr(deps, "_PROXY_GUARDED", False)
    monkeypatch.setattr(deps, "_effective_proxy_env", lambda: "127.0.0.1:7890")
    monkeypatch.setattr(deps, "_socket_reachable", lambda *a, **k: False)
    monkeypatch.delenv("WHALETALK_SKIP_PROXY_CHECK", raising=False)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    msg = deps.guard_pip_proxy()
    assert msg and "7890" in msg
    assert os.environ["NO_PROXY"] == "*"
    assert os.environ["no_proxy"] == "*"


def test_guard_noop_when_proxy_alive(monkeypatch):
    """代理存活 → 不干预（不设 NO_PROXY）。"""
    monkeypatch.setattr(deps, "_PROXY_GUARDED", False)
    monkeypatch.setattr(deps, "_effective_proxy_env", lambda: "127.0.0.1:7890")
    monkeypatch.setattr(deps, "_socket_reachable", lambda *a, **k: True)
    monkeypatch.delenv("WHALETALK_SKIP_PROXY_CHECK", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert deps.guard_pip_proxy() is None
    assert "NO_PROXY" not in os.environ


def test_guard_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(deps, "_PROXY_GUARDED", False)
    monkeypatch.setenv("WHALETALK_SKIP_PROXY_CHECK", "1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert deps.guard_pip_proxy() is None
    assert "NO_PROXY" not in os.environ


def test_pip_install_uses_given_interpreter(monkeypatch):
    """bootstrap 建好 .venv 后，须用 venv 解释器安装（而非当前 sys.executable）。"""
    captured = {}

    def fake_run_verbose(cmd, on_line=None, timeout=None):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(deps, "guard_pip_proxy", lambda *a, **k: None)
    monkeypatch.setattr(deps, "run_verbose", fake_run_verbose)
    ok = deps.pip_install("six", on_line=lambda s: None, python="C:/wt/.venv/Scripts/python.exe")
    assert ok is True
    assert captured["cmd"][:3] == ["C:/wt/.venv/Scripts/python.exe", "-m", "pip"]
    assert "six" in captured["cmd"]


def test_pip_install_empty_package_skips(monkeypatch):
    assert deps.pip_install("   ", on_line=lambda s: None) is False


def test_missing_in_reports_probe_failure(monkeypatch):
    monkeypatch.setattr(bootstrap.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 1, "stdout": ""})())
    assert bootstrap._missing_in(sys.executable) is None


def test_main_forwards_unknown_args_to_web_app(monkeypatch):
    """web_app.py 的参数（--server/--port …）须原样透传，而非被 bootstrap 吞掉。"""
    captured = {}
    monkeypatch.setattr(bootstrap, "ensure_venv", lambda d: ("/py", False, None))
    monkeypatch.setattr(bootstrap, "_install", lambda *a, **k: True)
    monkeypatch.setattr(bootstrap, "_launch",
                        lambda py, pt: captured.update(py=py, pt=pt) or 0)
    rc = bootstrap.main(["--no-venv", "--server", "--port", "9000"])
    assert rc == 0
    assert captured["pt"] == ["--server", "--port", "9000"]


def test_main_check_action_does_not_launch(monkeypatch):
    launched = []
    monkeypatch.setattr(bootstrap, "_check", lambda target: launched.append(target) or 0)
    monkeypatch.setattr(bootstrap, "_install", lambda *a, **k: launched.append("install") or True)
    rc = bootstrap.main(["check", "--no-venv"])
    assert rc == 0
    assert launched == [sys.executable]


def test_resolve_installer():
    assert bootstrap._resolve_installer("pip", True) == "pip"
    assert bootstrap._resolve_installer("uv", False) == "pip"   # 无 uv 时回退
    assert bootstrap._resolve_installer("uv", True) == "uv"
    assert bootstrap._resolve_installer("auto", True) == "uv"
    assert bootstrap._resolve_installer("auto", False) == "pip"


def test_offline_flags():
    env, flags = bootstrap._offline_flags(False, "x")
    assert env == {} and flags == []
    env, flags = bootstrap._offline_flags(True, "C:/wheels")
    assert env["PIP_NO_INDEX"] == "1" and env["PIP_FIND_LINKS"] == "C:/wheels"
    assert "--no-index" in flags and "--find-links" in flags and "C:/wheels" in flags


def test_port_free_true_for_unused_port():
    import socket as _socket
    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert bootstrap._port_free(port) is True


def test_collect_report_has_sections():
    report = bootstrap._collect_report(sys.executable)
    for frag in ("诊断报告", "[解释器]", "[依赖]", "[网络]", "系统代理", "[前端 / 系统件]", "[服务]"):
        assert frag in report, frag


def test_main_doctor_action_writes_via_flag(monkeypatch):
    called = {}
    monkeypatch.setattr(bootstrap, "_doctor",
                        lambda target, args: called.update(target=target, report=args.report) or 0)
    rc = bootstrap.main(["doctor", "--no-venv", "--report", "-"])
    assert rc == 0
    assert called["report"] == "-"
