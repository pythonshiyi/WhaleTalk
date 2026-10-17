"""设置页「搜索源凭据」端到端回归：微博 Cookie 的保存/读取/清除（v3.16.22）。

覆盖三条必须成立的行为：
  1. **加密落盘**：Cookie 绝不能以明文写进 config.json（它是账号凭据）；
  2. **未配置即跳过**：没填 Cookie 时 search_web 的 weibo 源直接跳过，不影响其它源；
  3. **清除语义**：`clear_weibo_cookie` 要真正删除磁盘键——因为 config_utils.save_config
     对「明文为空但磁盘有密文」的敏感字段**保留原密文**（保护 API Key 不被无关保存
     抹掉），这让 Cookie 的「清空」在默认路径下静默无效，必须显式删除。
"""
import json
import os

import pytest


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """把 config 指向临时文件，绝不碰用户真实 config.json。

    关键：`api_server` 在**导入时**会把 `config_utils.DEFAULT_CONFIG_PATH` 重置为自己
    的 `CONFIG_PATH`（模块级常量，指向仓库根 config.json）。因此必须在导入 api_server
    之后再打补丁，且**同时**改 `api_server.CONFIG_PATH` 与 config_utils 的默认路径，
    否则 `_services_save` 仍会写到真实 config.json（实测踩过：曾把测试 Cookie 写进了
    仓库根的 config.json）。另外 load_config 有进程级缓存，需一并清空。
    """
    import api_server
    import config_utils

    cfg_path = str(tmp_path / "config.json")
    monkeypatch.setattr(api_server, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_utils, "DEFAULT_CONFIG_PATH", cfg_path)
    monkeypatch.setattr(api_server, "DATA_DIR", str(tmp_path))
    cache = getattr(config_utils, "_CONFIG_CACHE", None)
    if isinstance(cache, dict):
        cache.clear()
    # 防御：确保补丁真的生效，否则后续断言会写到真实文件
    assert config_utils.DEFAULT_CONFIG_PATH == cfg_path
    return tmp_path / "config.json"


COOKIE = "SUB=_2A25TEST; SUBP=0033WrSXqPxfM725"


def test_services_get_exposes_search_group(isolated_config):
    import api_server
    out = api_server._services_get()
    assert "search" in out, "外部服务配置须暴露 search 分组"
    assert "weibo_cookie" in out["search"]
    assert out["search"]["has_weibo_cookie"] is False


def test_save_encrypts_cookie_not_plaintext(isolated_config):
    import api_server
    res, err = api_server._services_save({"search": {"weibo_cookie": COOKIE}})
    assert res and res.get("ok"), err
    stored = json.loads(isolated_config.read_text(encoding="utf-8")).get("weibo_cookie")
    assert stored, "应写入磁盘"
    assert COOKIE not in str(stored), "Cookie 绝不能明文落盘"
    assert str(stored).startswith("dpapi:"), f"应为 DPAPI 密文：{stored[:20]}"


def test_save_then_get_roundtrips(isolated_config):
    import api_server
    api_server._services_save({"search": {"weibo_cookie": COOKIE}})
    d = api_server._services_get()
    assert d["search"]["weibo_cookie"] == COOKIE, "GET 应返回解密后的值"
    assert d["search"]["has_weibo_cookie"] is True


def test_empty_save_does_not_wipe_existing_cookie(isolated_config):
    """空提交（未打清除标记）必须保留原值——防误删。"""
    import api_server
    api_server._services_save({"search": {"weibo_cookie": COOKIE}})
    api_server._services_save({"search": {"weibo_cookie": ""}})
    assert api_server._services_get()["search"]["weibo_cookie"] == COOKIE


def test_explicit_clear_deletes_key(isolated_config):
    """clear_weibo_cookie=True 要真正把键从 config.json 删掉（而非写空串）。"""
    import api_server
    api_server._services_save({"search": {"weibo_cookie": COOKIE}})
    api_server._services_save({"search": {"clear_weibo_cookie": True}})
    data = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert "weibo_cookie" not in data, f"键应被删除，实际：{data.get('weibo_cookie')!r}"
    assert api_server._services_get()["search"]["weibo_cookie"] == ""
    assert api_server._services_get()["search"]["has_weibo_cookie"] is False


def test_weibo_engine_skips_when_no_cookie(isolated_config, monkeypatch):
    """未配置 Cookie → weibo 源抛「未配置」（被记为不可用并跳过），不是 0 条结果。"""
    import deepseek_client as dc
    with pytest.raises(RuntimeError, match="cookie"):
        dc._search_weibo("deepseek")


def test_weibo_engine_uses_saved_cookie(isolated_config, monkeypatch):
    """配置后引擎应带 Cookie 发请求（此处用假客户端验证请求头）。"""
    import api_server
    import deepseek_client as dc
    api_server._services_save({"search": {"weibo_cookie": COOKIE}})

    seen = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": 1, "data": {"cards": []}}

    class _Client:
        def get(self, url, **kw):
            seen.update(kw)
            return _Resp()

    monkeypatch.setattr(dc, "_http_client", lambda *a, **k: _Client())
    dc._search_weibo("x", num=2)
    assert seen.get("headers", {}).get("Cookie") == COOKIE, "应带上已保存的 Cookie"
