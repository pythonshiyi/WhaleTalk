"""启动流程健壮性回归（端口绑定 fail-fast + 可操作错误）。

背景（真实启动失败）：start_server 此前**先**启动调度/社区循环/看门狗/入站，
**最后**才 bind。一旦端口被占/非法，bind 失败直接 return，却**不回滚已启动的后台线程**
——启动失败仍跑调度（可能触发定时任务）、社区循环（会发网络请求），留下半初始化状态。

本测试锁定修复后的两条保证：
  1. bind 失败时**绝不泄漏**任何后台线程（_SCHEDULER_THREAD / _BRAIN_COMMUNITY_THREAD 为空）；
  2. bind 失败时返回**可操作**的错误信息（端口占用/保留/非法 → 明确原因 + 解决办法），
     而不是原始 WinError。
"""
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402


def _reset_server_state():
    api_server._SERVER = None
    api_server._SCHEDULER_THREAD = None
    api_server._BRAIN_COMMUNITY_THREAD = None


def test_invalid_port_no_thread_leak():
    """非法端口（超范围）：bind 失败 → 无后台线程、无 _SERVER、错误可操作。"""
    _reset_server_state()
    port, tok, err = api_server.start_server(port=99999, token="")
    assert port is None and err
    assert "端口" in err
    assert api_server._SERVER is None
    assert api_server._SCHEDULER_THREAD is None, "绑定失败不得启动调度线程"
    assert api_server._BRAIN_COMMUNITY_THREAD is None, "绑定失败不得启动社区循环线程"


def test_port_occupied_no_thread_leak():
    """端口被独占占用：bind 失败 → 无后台线程、错误可操作。"""
    _reset_server_state()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    occupied_port = s.getsockname()[1]
    s.listen(1)
    try:
        port, tok, err = api_server.start_server(port=occupied_port, token="")
        assert port is None and err
        assert "端口" in err and str(occupied_port) in err
        assert api_server._SCHEDULER_THREAD is None
        assert api_server._BRAIN_COMMUNITY_THREAD is None
        assert api_server._SERVER is None
    finally:
        s.close()


def test_successful_start_then_stop():
    """正常路径不受影响：绑定成功 → 线程就绪 → 可优雅停止。"""
    _reset_server_state()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()
    try:
        port, tok, err = api_server.start_server(port=free_port, token="test-token")
        assert err is None and port == free_port and tok == "test-token"
        assert api_server.is_running()
        assert api_server._SCHEDULER_THREAD is not None
    finally:
        api_server.stop_server()
        _reset_server_state()


def test_probe_existing_false_for_dead_port():
    import web_app
    assert web_app._probe_existing(1) is False
