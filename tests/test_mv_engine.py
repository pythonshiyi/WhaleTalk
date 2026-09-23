"""mv_engine（自建 MV 引擎）回归：歌词解析/卡点网格/字幕/校验/序列对齐/帧渲染。

不触发 whisper/音频解码（纯函数），保证 CI 快且确定。
"""
import importlib.util
import os

import mv_engine as me


def test_parse_lyrics_text_drops_sections_and_tags():
    t = "主歌\n[ti:釉下青]\n窑火在冬夜\n\n副歌\n谁在描\n[00:01.00]已带时间戳行"
    lines = me.parse_lyrics_text(t)
    assert "窑火在冬夜" in lines and "谁在描" in lines
    assert "主歌" not in lines and "副歌" not in lines
    assert all("ti:" not in x for x in lines)


def test_parse_lrc():
    rows = me.parse_lrc("[00:26.06]第一句\n[00:32.36]第二句\n")
    assert rows[0]["text"] == "第一句"
    assert abs(rows[0]["start"] - 26.06) < 0.01
    assert abs(rows[0]["end"] - 32.36) < 0.01
    assert rows[1]["text"] == "第二句"


def test_build_shots_covers_full_song_without_gaps():
    beats = [i * 0.5 for i in range(1, 40)]
    shots = me.build_shots(20.0, beats, lines=[])
    assert shots, "应产出分镜"
    assert shots[0]["t_start"] == 0.0
    assert abs(shots[-1]["t_end"] - 20.0) < 0.01
    gaps = [shots[i + 1]["t_start"] - shots[i]["t_end"] for i in range(len(shots) - 1)]
    assert max(gaps) <= 1e-6


def test_build_shots_no_beats_fallback():
    shots = me.build_shots(9.0, [])
    assert shots and abs(shots[-1]["t_end"] - 9.0) < 0.01


def test_build_shots_attaches_lyric_ref():
    lines = [{"text": "第一句", "start": 1.0, "end": 2.0, "index": 0}]
    shots = me.build_shots(10.0, [i * 0.5 for i in range(1, 20)], lines=lines)
    refs = [s["lyric_ref"] for s in shots]
    assert "第一句" in refs


def test_build_srt_skips_untimed(tmp_path):
    n = me.build_srt([{"text": "a", "start": 1.0, "end": 2.0},
                      {"text": "b", "start": None, "end": None}], str(tmp_path / "a.srt"))
    assert n == 1


def test_verify_shots_flags_gap():
    shots = [{"index": 0, "t_start": 0.0, "t_end": 5.0, "duration": 5.0},
             {"index": 1, "t_start": 6.0, "t_end": 10.0, "duration": 4.0}]
    d = {label: ok for label, ok, _ in me.verify_shots(shots, 10.0)}
    assert d["镜头间无空隙"] is False


def test_verify_shots_ok():
    shots = [{"index": 0, "t_start": 0.0, "t_end": 5.0, "duration": 5.0},
             {"index": 1, "t_start": 5.0, "t_end": 10.0, "duration": 5.0}]
    d = {label: ok for label, ok, _ in me.verify_shots(shots, 10.0)}
    assert all(d.values())


def test_words_to_spans():
    w = [("a", 1.0, 1.4), ("b", 1.5, 1.9), ("c", 5.0, 5.4)]
    assert me._words_to_spans(w) == [(1.0, 1.9), (5.0, 5.4)]


def test_merge_spans():
    assert me._merge_spans([(0, 1), (1.2, 2)], [(0.5, 1.5)]) == [(0.0, 2.0)]
    assert me._merge_spans([], []) == []


def test_align_by_sequence_maps_lines():
    words = [("第", 1.0, 1.2), ("一", 1.2, 1.4), ("句", 1.4, 1.6), ("歌", 1.6, 1.8), ("词", 1.8, 2.0),
             ("第", 3.0, 3.2), ("二", 3.2, 3.4), ("句", 3.4, 3.6), ("歌", 3.6, 3.8), ("词", 3.8, 4.0)]
    rows = me._align_by_sequence(["第一句歌词", "第二句歌词"], words)
    assert rows[0]["start"] == 1.0 and rows[1]["start"] == 3.0


def test_fill_gaps_interpolates_and_is_monotonic():
    rows = [{"text": "a", "start": 1.0, "end": 2.0, "index": 0, "confidence": 1.0},
            {"text": "b", "start": None, "end": None, "index": 1, "confidence": 0.0},
            {"text": "c", "start": 5.0, "end": 6.0, "index": 2, "confidence": 1.0}]
    out = me._fill_gaps(rows)
    assert out[1]["start"] is not None
    assert 2.0 <= out[1]["start"] <= 5.0
    assert out[1]["end"] <= 5.0 + 1e-6
    assert [r["start"] for r in out] == sorted(r["start"] for r in out)


def test_render_frames_creates_files(tmp_path):
    if importlib.util.find_spec("PIL") is None:
        import pytest
        pytest.skip("无 PIL")
    shots = [{"index": 0, "lyric_ref": "第一句"}, {"index": 1, "lyric_ref": ""}]
    frames = me.render_frames(shots, str(tmp_path), w=180, h=320, title="测试歌", artist="歌手")
    assert len(frames) == 2
    assert all(os.path.isfile(f["path"]) and os.path.getsize(f["path"]) > 0 for f in frames)


def test_assign_lines_monotonic_no_collapse():
    lines = [f"L{i}" for i in range(6)]
    spans = [(26.0, 30.0), (30.0, 60.0)]  # 2 段 vs 6 行 → 需切分
    out = me._assign_lines(lines, spans)
    assert [o["text"] for o in out] == lines
    starts = [o["start"] for o in out]
    assert starts == sorted(starts), "必须单调"
    assert len(set(starts)) == len(starts), "不得塌缩到同一时间"
    assert out[0]["start"] >= 26.0 - 1e-6
    assert out[-1]["end"] <= 60.0 + 1e-6


# ── 逐帧动画渲染管线（compose_frame / render_movie / 断点续跑）─────────────
def _lines():
    return [{"text": "山河为砚", "start": 1.0, "end": 3.0, "index": 0},
            {"text": "眼里有光", "start": 4.0, "end": 6.0, "index": 1}]


def test_build_shots_assigns_scene():
    shots = me.build_shots(10.0, [i * 0.5 for i in range(1, 19)], lines=_lines())
    assert all("scene" in s for s in shots)
    assert any(s["scene"] for s in shots)


def test_compose_frame_contract():
    import numpy as np
    shots = me.build_shots(8.0, [i * 0.5 for i in range(1, 15)], lines=_lines())
    ctx = me.build_render_ctx(shots, _lines(), w=96, h=64, fps=10, duration=8.0,
                              title="测试歌", artist="演唱：AI", credits="作词|AI")
    img = me.compose_frame(20, ctx)
    assert img.shape == (64, 96, 3) and img.dtype == np.float32
    assert img.min() >= 0 and img.max() <= 1.0 + 1e-6
    assert np.allclose(img, me.compose_frame(20, ctx)), "同帧须确定"


def test_render_chunk_resume(tmp_path):
    shots = me.build_shots(4.0, [i * 0.5 for i in range(1, 9)], lines=_lines())
    ctx = me.build_render_ctx(shots, _lines(), w=64, h=48, fps=5, duration=4.0)
    d = str(tmp_path)
    n = me.render_chunk(0, 10, d, 90, ctx, False)
    assert n == 10 and me._count_frames(d) == 10
    # 已存在帧应被跳过（断点续跑）
    assert me.render_chunk(0, 10, d, 90, ctx, False) == 10
    assert me._count_frames(d) == 10


def test_render_movie_resume(tmp_path):
    shots = me.build_shots(3.0, [i * 0.5 for i in range(1, 7)], lines=_lines())
    d = str(tmp_path / "frames")
    res = me.render_movie(shots, _lines(), d, w=64, h=48, fps=5, duration=3.0,
                          procs=1, chunk=8, gpu="off")
    assert res["n_frames"] == 15 and res["rendered"] == 15
    assert not res["errors"]
    res2 = me.render_movie(shots, _lines(), d, w=64, h=48, fps=5, duration=3.0,
                           procs=1, chunk=8, gpu="off")
    assert res2["new"] == 0 and res2["rendered"] == 15


def test_dynamic_check_reports(tmp_path):
    shots = me.build_shots(10.0, [i * 0.5 for i in range(1, 19)], lines=_lines())
    res = me.dynamic_check(shots, _lines(), w=96, h=64, fps=10, duration=10.0,
                           per_shot=6, step=0.2)
    assert res, "应有镜内采样结果"
    assert all("ok" in r and "scene" in r for r in res)


def test_preview_frames_writes(tmp_path):
    shots = me.build_shots(6.0, [i * 0.5 for i in range(1, 11)], lines=_lines())
    out, ctx = me.preview_frames(shots, _lines(), str(tmp_path / "prev"), n=4,
                                 w=64, h=48, fps=10, duration=6.0)
    assert out and all(os.path.isfile(p) for _t, p in out)
    assert ctx["total"] == 6.0

