"""配置方案（Profile）与网关凭据记忆回归。

覆盖用户反馈：「保存的网关重启后消失 / 每次切换网关都要重新填 Key」。

- 多方案保存互不覆盖；
- 文件损坏不再被当成空表而遭下次保存清空（先 .bak 恢复，仍失败则抛错）；
- 覆盖前保留 .bak；
- 网关凭据按地址记忆，切换网关自动回填、地址归一（尾斜杠 / 完整端点）。
"""
import json
import os

import pytest

# 必须先导入 api_server：它在导入时会把 profiles.DEFAULT_* 指向真实数据目录，
# 若在夹具 monkeypatch 之后再导入会覆盖掉临时路径（曾导致测试读写真实 data/）。
import api_server  # noqa: F401  (导入顺序即契约)
import profiles


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    pp = str(tmp_path / "profiles.json")
    gp = str(tmp_path / "gateway_keys.json")
    monkeypatch.setattr(profiles, "DEFAULT_PROFILES_PATH", pp)
    monkeypatch.setattr(profiles, "DEFAULT_GATEWAYS_PATH", gp)
    return {"profiles": pp, "gateways": gp}


def test_multi_profiles_saved_and_loaded(paths):
    profiles.save_profiles({"profiles": {
        "官方": {"api_key": "sk-a", "base_url": "https://api.deepseek.com", "model": "deepseek-flash"},
        "中转站A": {"api_key": "sk-b", "base_url": "https://relay.example/v1", "model": "m-relay"},
    }, "current": "官方"})
    d = profiles.load_profiles()
    assert set(d["profiles"]) == {"官方", "中转站A"}
    assert d["profiles"]["中转站A"]["api_key"] == "sk-b"
    assert d["current"] == "官方"


def test_save_keeps_previous_as_bak_and_disk_is_encrypted(paths):
    profiles.save_profiles({"profiles": {"A": {"api_key": "sk-1", "base_url": "u", "model": "m"}}})
    profiles.save_profiles({"profiles": {
        "A": {"api_key": "sk-1", "base_url": "u", "model": "m"},
        "B": {"api_key": "sk-2", "base_url": "u2", "model": "m2"},
    }})
    assert os.path.exists(paths["profiles"] + ".bak")
    with open(paths["profiles"], encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["profiles"]["B"]["api_key"].startswith("dpapi:"), "api_key 必须加密落盘"


def test_corrupt_file_recovers_from_bak(paths):
    profiles.save_profiles({"profiles": {"A": {"api_key": "sk-a", "base_url": "u", "model": "m"}}})
    profiles.save_profiles({"profiles": {
        "A": {"api_key": "sk-a", "base_url": "u", "model": "m"},
        "B": {"api_key": "sk-b", "base_url": "u2", "model": "m2"},
    }})
    with open(paths["profiles"], "w", encoding="utf-8") as f:
        f.write("{ bad json ")
    d = profiles.load_profiles()  # 从 .bak 恢复（B 是最后一版，.bak 为 A）
    assert "A" in d["profiles"]
    assert "A" in profiles.load_profiles()["profiles"], "主文件应被修回"


def test_corrupt_without_bak_raises_not_empty(paths):
    with open(paths["profiles"], "w", encoding="utf-8") as f:
        f.write("{ broken ")
    with pytest.raises(profiles.ProfileReadError):
        profiles.load_profiles()


def test_missing_file_is_empty_not_error(paths):
    d = profiles.load_profiles()
    assert d == {"profiles": {}, "current": ""}


def test_gateway_key_memory_and_url_normalization(paths):
    assert profiles.remember_gateway_key("https://api.deepseek.com", "sk-a", "m-a")
    # 尾斜杠应归一为同一网关
    assert profiles.gateway_key_for("https://api.deepseek.com/")["api_key"] == "sk-a"
    # 完整端点（/chat/completions）也应归一到同一网关
    assert profiles.gateway_key_for("https://api.deepseek.com/chat/completions")["api_key"] == "sk-a"
    assert profiles.gateway_key_for("https://other.example") is None


def test_sync_gateway_key_switches_without_refilling(paths):
    srv = api_server

    def fake_post(cfg, body):
        old_bu = str(cfg.get("base_url") or "").strip()
        old_key = str(cfg.get("api_key") or "").strip()
        old_model = str(cfg.get("model") or "").strip()
        key_provided = bool(str(body.get("api_key") or "").strip())
        url_provided = "base_url" in body and body.get("base_url") is not None
        if "base_url" in body:
            cfg["base_url"] = str(body["base_url"])
        if "model" in body:
            cfg["model"] = str(body["model"])
        if key_provided:
            cfg["api_key"] = str(body["api_key"]).strip()
        return srv._sync_gateway_key(cfg, old_bu, old_key, old_model, key_provided, url_provided)

    cfg = {"base_url": "https://api.deepseek.com", "api_key": "sk-official", "model": "deepseek-flash"}
    # 切到 OpenCode（未给 key）：保留旧 key，不报错
    assert fake_post(cfg, {"base_url": "https://opencode.ai/zen/go/v1"}) is False
    # 给 OpenCode 填自己的 key
    fake_post(cfg, {"api_key": "sk-opencode"})
    # 切回官方：应回填官方 key
    assert fake_post(cfg, {"base_url": "https://api.deepseek.com"}) is True
    assert cfg["api_key"] == "sk-official"
    # 再切回 OpenCode：应回填 OpenCode key（免重填）
    assert fake_post(cfg, {"base_url": "https://opencode.ai/zen/go/v1"}) is True
    assert cfg["api_key"] == "sk-opencode"
