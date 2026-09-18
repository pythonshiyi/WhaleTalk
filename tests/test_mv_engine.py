"""mv_engine（自建 MV 引擎）回归：歌词解析/卡点网格/字幕/校验。

不触发 whisper/音频解码（纯函数），保证 CI 快且确定。
"""
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

