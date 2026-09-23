"""mv_produce（AI MV 上游引擎接入）回归：定位/自检门禁/歌词参数/错误路径/compose 接线。

薄封装契约：本工具只通过 subprocess 调上游 mv_api/CLI，不 import 其重依赖。
"""
import json
import os

import api_server  # noqa: F401  # 先触发完整工具注册，避免后 import agent_tools 重复注册


def _tm():
    from agent_tools import tool_mv
    return tool_mv


# ── 项目定位 ──────────────────────────────────────────────────────────────
def test_find_root_explicit(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "mv_api.py").write_text("x", encoding="utf-8")
    (tmp_path / "app" / "cli.py").write_text("x", encoding="utf-8")
    assert _tm()._mv_find_root(str(tmp_path)) == os.path.abspath(str(tmp_path))


def test_find_root_returns_valid_or_empty(tmp_path):
    # 显式给一个不含 mv_api.py 的目录：允许回退到队列里其它候选（本机可能装有 MV 项目），
    # 但返回的必须要么是空串，要么确实是合法项目根。
    r = _tm()._mv_find_root(str(tmp_path))
    assert r == "" or os.path.isfile(os.path.join(r, "mv_api.py"))


# ── 镜头网格自检 ──────────────────────────────────────────────────────────
def test_verify_grid_ok():
    shots = [{"t_start": 0.0, "t_end": 10.0}, {"t_start": 10.0, "t_end": 20.0}]
    checks = _tm()._mv_verify_grid(shots, 20.0)
    assert all(ok for _l, ok, _d in checks), checks


def test_verify_grid_detects_gap_and_short_tail():
    shots = [{"t_start": 0.0, "t_end": 10.0}, {"t_start": 12.0, "t_end": 18.0}]
    checks = dict((label, ok) for label, ok, _d in _tm()._mv_verify_grid(shots, 30.0))
    assert checks["镜头间无空隙"] is False
    assert checks["末镜覆盖到片尾"] is False


def test_verify_grid_empty():
    checks = _tm()._mv_verify_grid([], 10.0)
    assert checks[0][1] is False


# ── 歌词参数（文本 → 临时文件；路径 → 原样） ──────────────────────────────
def test_lyrics_arg_raw_text(tmp_path):
    tm = _tm()
    args, tmp = tm._mv_lyrics_arg("第一句歌词\n第二句歌词")
    assert args[0] == "--lyrics" and tmp and os.path.isfile(tmp)
    with open(tmp, encoding="utf-8") as f:
        assert "第二句歌词" in f.read()
    os.remove(tmp)


def test_lyrics_arg_path(tmp_path):
    p = tmp_path / "a.lrc"
    p.write_text("[00:01.00]hi", encoding="utf-8")
    args, tmp = _tm()._mv_lyrics_arg(str(p))
    assert args[0] == "--lyrics" and tmp == ""
    assert os.path.normcase(args[1]) == os.path.normcase(str(p))


# ── 报告：PASS/FAIL 判定 ──────────────────────────────────────────────────
def test_report_pass_and_fail():
    tm = _tm()
    ok = tm._mv_report("plan", {"BPM": 96}, [("甲", True, ""), ("乙", True, "")])
    assert "PASS" in ok and "判定：" in ok
    bad = tm._mv_report("render", {}, [("甲", True, ""), ("乙", False, "缺口 3s")])
    assert "FAIL" in bad and "不得宣称完成" in bad


# ── 错误路径（不触网/不跑 MV） ────────────────────────────────────────────
def test_tool_root_not_found(monkeypatch):
    monkeypatch.setattr(_tm(), "_mv_find_root", lambda explicit="": "")
    assert "未找到 AI MV 程序" in _tm().mv_produce(action="plan", audio="x.wav", engine="external")


def test_tool_deps_missing(monkeypatch):
    tm = _tm()
    monkeypatch.setattr(tm, "_mv_find_root", lambda explicit="": r"D:\fake\MV")
    monkeypatch.setattr(tm, "_mv_python", lambda root: "")
    out = tm.mv_produce(action="plan", audio="x.wav", engine="external")
    assert "找不到能运行 AI MV 的 Python 环境" in out


def test_tool_bad_action(monkeypatch):
    tm = _tm()
    monkeypatch.setattr(tm, "_mv_find_root", lambda explicit="": r"D:\fake\MV")
    monkeypatch.setattr(tm, "_mv_python", lambda root: "python")
    assert "action 需为" in tm.mv_produce(action="nope", audio="x.wav")


# ── compose 接线：storyboard → mv_compose（出图不可用则回退 render） ───────
_BUNDLE = {
    "storyboard": [{"prompt": "p1", "subtitle": "s1", "duration": 2.0},
                   {"prompt": "p2", "subtitle": "s2", "duration": 3.0}],
    "resolution": "1080x1920", "fps": 30, "duration": 3.0, "effect": "kenburns",
    "transition": 0.0, "generate_images": False, "narrate": False,
    "subtitle": True, "bgm": "", "audio": "a.wav",
    "meta": {"duration": 5.0, "style_id": "citypop_night_v1"},
}


def _stub_upstream(monkeypatch, tmp_path):
    tm = _tm()
    monkeypatch.setattr(tm, "_mv_find_root", lambda explicit="": r"D:\fake\MV")
    monkeypatch.setattr(tm, "_mv_python", lambda root: "python")
    monkeypatch.setattr(tm, "_mv_resolve_audio", lambda a: a)
    monkeypatch.setattr(tm, "_mv_lyrics_arg", lambda lyr: ([], ""))

    def fake_run(root, py, cmd, timeout, offline):
        if "--json" in cmd:
            jf = cmd[cmd.index("--json") + 1]
            with open(jf, "w", encoding="utf-8") as f:
                json.dump(_BUNDLE, f, ensure_ascii=False)
        return "", ""

    monkeypatch.setattr(tm, "_mv_run", fake_run)
    return tm


def test_compose_wires_mv_compose(monkeypatch, tmp_path):
    tm = _stub_upstream(monkeypatch, tmp_path)
    captured = {}

    def fake_compose(**kw):
        captured.update(kw)
        with open(kw["output"], "wb") as f:
            f.write(b"x" * 128)

    monkeypatch.setattr(tm, "mv_compose", fake_compose)
    monkeypatch.setattr(tm, "_ff_media_duration", lambda p: 5.0)
    out_path = tmp_path / "out.mp4"
    # engine=external：本用例验证外部 MV 程序的 compose 接线（native 走自建引擎，另有覆盖）
    out = tm.mv_produce(action="compose", audio="a.wav", output=str(out_path), engine="external")
    assert "PASS" in out and "终片存在且非空" in out
    assert captured["storyboard"] == _BUNDLE["storyboard"]
    assert captured["generate_images"] is False


def test_compose_missing_output_is_failure(monkeypatch, tmp_path):
    """终片未真实落盘时不得判 PASS（治假完成）。"""
    tm = _stub_upstream(monkeypatch, tmp_path)
    monkeypatch.setattr(tm, "mv_compose", lambda **kw: "成片已生成：X.mp4")  # 但没写文件
    out = tm.mv_produce(action="compose", audio="a.wav", output=str(tmp_path / "none.mp4"), engine="external")
    assert "错误：合成未产出成片" in out


def test_compose_falls_back_to_render_on_image_failure(monkeypatch, tmp_path):
    tm = _stub_upstream(monkeypatch, tmp_path)
    monkeypatch.setattr(tm, "mv_compose", lambda **kw: "错误：第 1 镜出图失败：404")
    monkeypatch.setattr(tm, "_mv_do_render", lambda *a, **k: "RENDER_FALLBACK_OK")
    out = tm.mv_produce(action="compose", audio="a.wav", out=str(tmp_path), generate_images=True, engine="external")
    assert "回退" in out and "RENDER_FALLBACK_OK" in out


# ── 原生引擎工具链：preview / qc（不依赖 ffmpeg/whisper） ──────────────────
def _stub_native(monkeypatch, tmp_path):
    tm = _tm()
    import permissions
    monkeypatch.setattr(permissions, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(tm, "_mv_native_available", lambda: True)
    monkeypatch.setattr(tm, "_mv_resolve_audio", lambda a: a)
    import mv_engine as me
    monkeypatch.setattr(me, "analyze", lambda audio, **k: {
        "bpm": 120.0, "beats": [i * 0.5 for i in range(1, 12)], "duration": 6.0})
    monkeypatch.setattr(me, "align_lyrics", lambda audio, text, model="base", **k: [
        {"text": "山河为砚", "start": 1.0, "end": 2.5, "index": 0},
        {"text": "眼里有光", "start": 3.0, "end": 4.5, "index": 1}])
    return tm


def test_native_preview_report(monkeypatch, tmp_path):
    tm = _stub_native(monkeypatch, tmp_path)
    out = tm.mv_produce(action="preview", audio="x.wav", lyrics="山河为砚\n眼里有光",
                        resolution="160x96", fps=8, sample=4)
    assert "预览帧落盘" in out and "自检门禁" in out
    assert "引擎：native" in out


def test_native_qc_report(monkeypatch, tmp_path):
    tm = _stub_native(monkeypatch, tmp_path)
    import numpy as np

    import mv_tex
    fd = tmp_path / "frames"
    fd.mkdir()
    for i in range(4):
        img = np.full((48, 64, 3), 0.15, np.float32)
        img[10:20, 10 + i:20 + i, 0] = 1.0
        mv_tex.imwrite_safe(str(fd / f"{i:06d}.jpg"), img)
    out = tm.mv_produce(action="qc", frames_dir=str(fd))
    assert "帧可读" in out and "死白占比" in out


def test_native_status(monkeypatch, tmp_path):
    tm = _stub_native(monkeypatch, tmp_path)
    import numpy as np

    import mv_tex
    fd = tmp_path / "frames"
    fd.mkdir()
    mv_tex.imwrite_safe(str(fd / "000000.jpg"), np.zeros((16, 16, 3), np.float32))
    out = tm.mv_produce(action="status", frames_dir=str(fd))
    assert "进度" in out


# ── 真机集成冒烟：有上游项目+依赖才跑（CI 无则跳过） ──────────────────────
def test_live_styles_if_available():
    tm = _tm()
    root = tm._mv_find_root()
    import pytest
    if not root:
        pytest.skip("未安装 AI MV 上游项目")
    if not tm._mv_python(root):
        pytest.skip("无装好 librosa/soundfile/scipy 的解释器")
    out = tm.mv_produce(action="styles", mv_home=root)
    assert "citypop_night_v1" in out or "风格包" in out
