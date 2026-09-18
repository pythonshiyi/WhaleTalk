"""音频崩溃加固回归：提示音子进程隔离 + tts_stop 不再跨线程调用 COM。

背景（真实崩溃 2026-09-18 22:06，故障模块 MMDevApi.dll_unloaded，0xc0000005）：
本机音频栈不稳时，进程内 winsound/SAPI 调用会原生崩溃并杀死整个 python 进程。
"""
import subprocess
import threading

import api_server


def _tts_mod():
    """延迟导入 agent_tools（先由 api_server 触发完整工具注册，避免重复注册报错）。"""
    import agent_tools.tool_desktop as td
    return td


def test_completion_sound_runs_in_subprocess(monkeypatch):
    calls = {}

    class _FakePopen:
        def __init__(self, args, **kw):
            calls["args"] = args
            calls["kw"] = kw

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    api_server._play_completion_sound()
    assert calls, "应在子进程播放提示音"
    args = calls["args"]
    assert args[1] == "-c"
    assert "winsound" in args[2] and "MessageBeep" in args[2]


def test_tts_stop_only_sets_event():
    td = _tts_mod()
    ev = threading.Event()

    class _BoomVoice:
        def __getattr__(self, name):
            raise AssertionError(f"tts_stop 不应触碰 COM 对象：{name}")

    with td._ACTIVE_SPEAK_LOCK:
        td._ACTIVE_SPEAK["spk_test"] = {"event": ev, "voice": _BoomVoice(), "thread": None}
    try:
        out = td.tts_stop("spk_test")
        assert ev.is_set()
        assert "已停止" in out
    finally:
        with td._ACTIVE_SPEAK_LOCK:
            td._ACTIVE_SPEAK.pop("spk_test", None)


def test_tts_stop_empty_returns_message():
    assert "没有进行中" in _tts_mod().tts_stop("")
