"""公众号写作的三层配置：配置层 / 单次参数层 / 硬边界层。

锁定的承诺：
1. 单次参数只在配置区间内生效，超界被**夹住并告知**（不报错、不静默）；
2. 受众只接受 general/geek，非法值被忽略且有提示；
3. 设置页读写走同一个归一化函数（写入后回读，越界值被钳制）；
4. 发布不在本能力范围内（工具只产草稿）——这条由 manifest 与工具实现共同保证。
"""
import json

import pytest

import api_server


# ── 1. 单次参数层 ───────────────────────────────────────────────────

def test_chars_above_range_is_clamped_with_note():
    from wechat_writer import config as wcfg
    cfg, notes = wcfg.load_config_with_notes(None, {"chars": 800})
    assert cfg["style"]["min_chars"] == 1200          # 配置下限
    assert cfg["style"]["max_chars"] == 1320
    assert notes and "1200" in notes[0] and "800" in notes[0]


def test_chars_inside_range_narrows_window():
    from wechat_writer import config as wcfg
    cfg, notes = wcfg.load_config_with_notes(None, {"chars": 2000})
    assert notes == []
    assert cfg["style"]["min_chars"] <= 2000 <= cfg["style"]["max_chars"]
    assert cfg["style"]["min_chars"] > 1200           # 以目标为中心收窄


def test_invalid_audience_is_ignored_with_note():
    from wechat_writer import config as wcfg
    cfg, notes = wcfg.load_config_with_notes(None, {"audience": "expert"})
    assert cfg["style"]["audience"] == "general"
    assert any("general" in n for n in notes)


def test_valid_audience_applies():
    from wechat_writer import config as wcfg
    cfg, notes = wcfg.load_config_with_notes(None, {"audience": "geek"})
    assert cfg["style"]["audience"] == "geek" and notes == []


def test_sections_clamped_to_2_10():
    from wechat_writer import config as wcfg
    cfg, _ = wcfg.load_config_with_notes(None, {"sections": 99})
    assert cfg["style"]["sections"] == 10


def test_no_overrides_keeps_legacy_behaviour():
    from wechat_writer import config as wcfg
    cfg = wcfg.load_config(None)
    assert cfg["style"]["min_chars"] == 1200 and cfg["style"]["max_chars"] == 2500


# ── 2. 配置层：读写同一归一化口径 ────────────────────────────────────

@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "wechat_writer.json"
    monkeypatch.setattr(api_server, "_wechat_config_path", lambda: str(p))
    return p


def test_save_then_load_roundtrip(cfg_path):
    r = api_server._wechat_config_save({"config": {"style": {"min_chars": 1500, "max_chars": 3000,
                                                            "audience": "geek"}}})
    assert r["ok"]
    assert cfg_path.exists()
    got = api_server._wechat_config_get()
    assert got["ok"]
    assert got["config"]["style"]["min_chars"] == 1500
    assert got["config"]["style"]["audience"] == "geek"


def test_save_clamps_out_of_range_values(cfg_path):
    api_server._wechat_config_save({"config": {"style": {"min_chars": 10, "max_chars": 99999}}})
    got = api_server._wechat_config_get()["config"]["style"]
    assert got["min_chars"] >= 500
    assert got["max_chars"] <= 8000


def test_save_ignores_unknown_keys(cfg_path):
    api_server._wechat_config_save({"config": {"evil": {"x": 1}, "style": {"tone": "冷静"}}})
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "evil" not in raw
    assert raw["style"]["tone"] == "冷静"


def test_save_merges_with_existing_keys(cfg_path):
    api_server._wechat_config_save({"config": {"style": {"tone": "严谨"}}})
    api_server._wechat_config_save({"config": {"style": {"sections": 5}}})
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert raw["style"]["tone"] == "严谨"      # 第二次提交没带 tone，不能被抹掉
    assert raw["style"]["sections"] == 5


def test_get_exposes_groups_and_audiences(cfg_path):
    got = api_server._wechat_config_get()
    assert got["ok"]
    assert "ai_media" in got["groups"]
    assert set(got["audiences"]) == {"general", "geek"}
    assert got["path"].endswith("wechat_writer.json")
