"""mv_scene（题材无关场景基元库）回归：契约（形状/范围/确定性/动画）+ 选景。"""
import numpy as np

import mv_scene as S


def test_all_scenes_contract():
    for name, fn in S.SCENES.items():
        img = fn(64, 96, 1.7, {"palette": "citypop_night_v1"})
        assert img.shape == (64, 96, 3), name
        assert img.dtype == np.float32, name
        assert img.min() >= 0.0 and img.max() <= 1.0 + 1e-6, name


def test_scenes_deterministic_and_animated():
    for name, fn in S.SCENES.items():
        a = fn(48, 72, 2.0, {})
        b = fn(48, 72, 2.0, {})
        assert np.allclose(a, b), f"{name} 不确定"
        c = fn(48, 72, 3.0, {})
        assert not np.allclose(a, c), f"{name} 无动画"


def test_choose_scene_keywords():
    assert S.choose_scene("山河为砚") == "inkstone"
    assert S.choose_scene("眼里有光") == "eye"
    assert S.choose_scene("星辰为灯") == "sky"
    assert S.choose_scene("眼泪变河口") == "rain"


def test_choose_scene_fallback_cycles():
    a = S.choose_scene("无关键词的一句话", 0)
    b = S.choose_scene("无关键词的一句话", 1)
    assert a in S.SCENES and b in S.SCENES
    assert a != b


def test_scene_names():
    names = S.scene_names()
    assert "inkstone" in names and "mountains" in names and "empty" in names
