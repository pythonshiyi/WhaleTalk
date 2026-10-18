"""能力注册表（capability.py）回归测试。

锁定的核心承诺（改这里之前先读 capability.py 的模块文档）：

1. **同一张注册表**：能力工具装载后必须出现在 TOOLS / TOOL_CALL_MAP / TOOL_GROUPS，
   否则就是回到「装了但模型找不到」的老毛病；
2. **声明即门槛**：manifest 缺 api / 前缀 / exec / 权限声明一律拒装（不做部分装载）；
3. **最小权限在调用期强制**：声明的 cli 名单与实际命令不符时必须拒绝执行；
4. **卸载是温和失效**：旧句柄返回「已卸载」，但已进入执行的调用不被打断；
5. **熔断**：连续失败后不再真的执行，避免坏能力反复吃掉超时预算。
"""
import os
import time

import pytest

import capability
import deepseek_client as dc
import toolkit


def _manifest(caps, name="测试能力包"):
    return {"api": capability.API_VERSION, "meta": {"name": name}, "capabilities": caps}


def _cap(name="p_test_echo", **kw):
    base = {
        "name": name,
        "description": "测试用能力：复用内置 get_date",
        "parameters": {"type": "object", "properties": {}},
        "exec": {"type": "builtin_ref", "tool": "get_date"},
        "perms": {"tools": ["get_date"]},
        "groups": ["🔧 系统与基础"],
    }
    base.update(kw)
    return base


def _read_cap(name="p_test_read", **kw):
    base = {
        "name": name,
        "description": "读文件（复用内置 read_file）",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        "exec": {"type": "builtin_ref", "tool": "read_file", "args_map": {"path": "path"}},
        "perms": {"tools": ["read_file"], "files": []},
        "groups": ["📁 文件与目录"],
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _clean_capabilities():
    capability.reset_for_tests()
    yield
    capability.reset_for_tests()


# ── 1. 校验：拒绝部分合法的装载 ──────────────────────────────────────────

def test_validate_rejects_wrong_api_version():
    ok, errs = capability.validate_manifest({"api": "whaletalk.tool/2",
                                             "capabilities": [_cap()]})
    assert not ok
    assert any("api" in e for e in errs)


def test_validate_rejects_empty_capabilities():
    ok, errs = capability.validate_manifest(
        {"api": capability.API_VERSION, "capabilities": []})
    assert not ok
    assert any("capabilities" in e for e in errs)


def test_validate_rejects_name_without_prefix():
    ok, errs = capability.validate_manifest(_manifest([_cap(name="echo")]))
    assert not ok
    assert any("p_" in e for e in errs)


def test_validate_http_requires_net_permission():
    cap = _cap(name="p_http_x", exec={"type": "http", "url": "https://example.com"},
               perms={})
    ok, errs = capability.validate_manifest(_manifest([cap]))
    assert not ok
    assert any("perms.net" in e for e in errs)


def test_validate_python_requires_explicit_permission():
    cap = _cap(name="p_py_x", exec={"type": "python", "entry": "mod:run"}, perms={})
    ok, errs = capability.validate_manifest(_manifest([cap]))
    assert not ok
    assert any("perms.python" in e for e in errs)


def test_validate_cli_requires_command_list():
    cap = _cap(name="p_cli_x", exec={"type": "cli", "command": "git"}, perms={})
    ok, errs = capability.validate_manifest(_manifest([cap]))
    assert not ok
    assert any("perms.cli" in e for e in errs)


def test_validate_builtin_ref_requires_tool_permission():
    cap = _cap(name="p_ref_x", perms={})
    ok, errs = capability.validate_manifest(_manifest([cap]))
    assert not ok
    assert any("perms.tools" in e for e in errs)


def test_validate_requires_groups():
    """不声明分组的能力进不了能力地图（模型看不到）= 装了等于没装，直接拒装。"""
    cap = _cap()
    cap.pop("groups", None)
    ok, errs = capability.validate_manifest(_manifest([cap]))
    assert not ok
    assert any("groups" in e for e in errs)


def test_validate_accepts_wellformed_manifest():
    ok, errs = capability.validate_manifest(_manifest([_cap()]))
    assert ok, errs


# ── 2. 装载：进入同一张注册表与六层视图 ─────────────────────────────────

def test_register_lands_in_registry_and_layers():
    res = capability.register_manifest(_manifest([_cap()]), plugin_id="pkg")
    assert res["ok"], res
    assert "p_test_echo" in toolkit.registry_names()
    assert any(t["function"]["name"] == "p_test_echo" for t in dc.TOOLS)
    assert "p_test_echo" in dc.TOOL_CALL_MAP
    grouped = [g for g, names in dc.TOOL_GROUPS if "p_test_echo" in names]
    assert grouped, "能力未进入能力地图分组（模型将看不到它）"


def test_register_appends_new_group():
    cap = _cap(name="p_test_group", groups=["🧪 测试分组"])
    assert capability.register_manifest(_manifest([cap]), plugin_id="pkg")["ok"]
    assert "🧪 测试分组" in [g for g, _ in dc.TOOL_GROUPS]


def test_execution_uses_builtin_tool(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("鲸语能力测试内容", encoding="utf-8")
    assert capability.register_manifest(_manifest([_read_cap()]), plugin_id="pkg")["ok"]
    out = dc.TOOL_CALL_MAP["p_test_read"](path=str(f))
    assert "鲸语能力测试内容" in out


def test_name_collision_between_packages_is_rejected():
    assert capability.register_manifest(_manifest([_cap()]), plugin_id="pkg-a")["ok"]
    res = capability.register_manifest(_manifest([_cap()]), plugin_id="pkg-b")
    assert not res["ok"]
    assert any("占用" in e for e in res["errors"])


def test_conflict_makes_registration_atomic():
    """第二个能力名冲突时，第一个也不得留下（不做部分装载）。"""
    caps = [_cap(name="p_atomic_a"), _cap(name="p_atomic_b")]
    assert capability.register_manifest(_manifest(caps), plugin_id="pkg-a")["ok"]
    capability.reset_for_tests()
    # 先注册 p_atomic_a（另一个包），再整包注册冲突项
    assert capability.register_manifest(_manifest([_cap(name="p_atomic_b")]),
                                        plugin_id="pkg-b")["ok"]
    res = capability.register_manifest(_manifest(caps), plugin_id="pkg-c")
    assert not res["ok"]
    assert "p_atomic_a" not in toolkit.registry_names()


# ── 3. 卸载：温和失效 ──────────────────────────────────────────────────

def test_unload_removes_layers_and_invalidates_old_handle():
    assert capability.register_manifest(_manifest([_cap()]), plugin_id="pkg")["ok"]
    old = dc.TOOL_CALL_MAP["p_test_echo"]

    res = capability.unregister_plugin("pkg")
    assert res["ok"] and "p_test_echo" in res["removed"]
    assert "p_test_echo" not in dc.TOOL_CALL_MAP
    assert not any(t["function"]["name"] == "p_test_echo" for t in dc.TOOLS)

    # 旧句柄仍可调用，但明确报「已卸载」而不是静默成功或抛异常
    out = old()
    assert out.startswith("错误")
    assert "已卸载" in out


def test_hot_replace_swaps_behaviour_without_restart():
    assert capability.register_manifest(_manifest([_cap()]), plugin_id="pkg")["ok"]
    cap2 = _cap(description="第二版能力")
    assert capability.register_manifest(_manifest([cap2]), plugin_id="pkg")["ok"]
    found = [t for t in dc.TOOLS if t["function"]["name"] == "p_test_echo"]
    assert len(found) == 1
    assert found[0]["function"]["description"] == "第二版能力"
    assert capability.epoch_of("pkg") >= 2


def test_unregister_unknown_package_is_noop():
    res = capability.unregister_plugin("nope")
    assert not res["ok"]
    assert "未装载" in res["error"]


# ── 4. 权限强制（调用期）──────────────────────────────────────────────

def test_cli_command_outside_declared_list_is_refused():
    cap = _cap(name="p_cli_test", exec={"type": "cli", "command": "git", "args": ["--version"]},
               perms={"cli": ["ffmpeg"]})
    assert capability.register_manifest(_manifest([cap]), plugin_id="pkg")["ok"]
    out = dc.TOOL_CALL_MAP["p_cli_test"]()
    assert out.startswith("错误")
    assert "cli 权限" in out


def test_disallowed_path_is_refused_by_declared_root(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("secret", encoding="utf-8")
    cap = _read_cap(perms={"tools": ["read_file"], "files": [str(tmp_path / "allowed-only")]})
    assert capability.register_manifest(_manifest([cap]), plugin_id="pkg")["ok"]
    out = dc.TOOL_CALL_MAP["p_test_read"](path=str(f))
    assert out.startswith("错误")
    assert "files 权限" in out


# ── 5. 熔断 / 超时 / 截断 / 健康度 ──────────────────────────────────────

def test_breaker_opens_after_consecutive_failures():
    # read_file 传空路径必定返回「错误：路径为空或过长」→ 计入失败
    cap = _read_cap(name="p_test_bad")
    assert capability.register_manifest(_manifest([cap]), plugin_id="pkg")["ok"]
    fn = dc.TOOL_CALL_MAP["p_test_bad"]
    for _ in range(capability.BREAKER_FAILS):
        assert fn(path="").startswith("错误")
    out = fn(path="")
    assert "熔断" in out
    assert capability.health("p_test_bad")["breaker_open"] is True


def test_timeout_returns_error(tmp_path):
    (tmp_path / "slowmod.py").write_text(
        "import time\n\ndef run(**kwargs):\n    time.sleep(1.4)\n    return 'done'\n",
        encoding="utf-8")
    cap = _cap(name="p_test_slow", exec={"type": "python", "entry": "slowmod:run"},
               perms={"python": True}, budget={"timeout": 1})
    assert capability.register_manifest(_manifest([cap]), base_dir=str(tmp_path),
                                        plugin_id="pkg")["ok"]
    out = dc.TOOL_CALL_MAP["p_test_slow"]()
    assert out.startswith("错误")
    assert "超时" in out


def test_output_is_truncated_by_io_limit(tmp_path):
    f = tmp_path / "long.txt"
    f.write_text("A" * 500, encoding="utf-8")
    cap = _read_cap(name="p_test_long", io={"max_chars": 100})
    assert capability.register_manifest(_manifest([cap]), plugin_id="pkg")["ok"]
    out = dc.TOOL_CALL_MAP["p_test_long"](path=str(f))
    assert "输出已截断" in out
    assert len(out) < 300


def test_health_counts_calls(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("ok", encoding="utf-8")
    assert capability.register_manifest(_manifest([_read_cap()]), plugin_id="pkg")["ok"]
    dc.TOOL_CALL_MAP["p_test_read"](path=str(f))
    h = capability.health("p_test_read")
    assert h["calls"] == 1 and h["ok"] == 1 and h["fail"] == 0
    assert capability.health_all()["count"] == 1


# ── 6. 旧格式桥 ────────────────────────────────────────────────────────

def test_adapt_legacy_converts_http_tools():
    legacy = {"meta": {"name": "旧插件"},
              "contents": {"tools": [{"function": {
                  "name": "get_weather_legacy", "description": "旧天气",
                  "endpoint": "https://example.com/w"}}]}}
    m = capability.adapt_legacy(legacy)
    ok, errs = capability.validate_manifest(m)
    assert ok, errs
    assert m["capabilities"][0]["name"].startswith("p_")
    assert m["capabilities"][0]["perms"]["net"] is True


def test_capability_names_never_shadow_core():
    """内置工具不可被能力遮蔽：manifest 里出现同名的 core 工具即拒装。"""
    cap = _cap(name="p_read_file")
    cap["exec"] = {"type": "builtin_ref", "tool": "get_date"}
    cap["perms"] = {"tools": ["get_date"]}
    assert capability.register_manifest(_manifest([cap]), plugin_id="p1")["ok"]
    res = capability.register_manifest(_manifest([cap]), plugin_id="p2")
    assert not res["ok"]
    assert "p_read_file" in toolkit.registry_names()   # 原注册仍在


# ── 7. 内置包（bundled）：可停用，不可卸载 ────────────────────────────

def test_bundled_pack_cannot_be_unloaded():
    res = capability.register_manifest(_manifest([_cap(name="p_builtin_x")]),
                                      plugin_id="builtin-x", bundled=True)
    assert res["ok"]
    out = capability.unregister_plugin("builtin-x")
    assert not out["ok"]
    assert "只允许停用" in out["error"]
    assert "p_builtin_x" in toolkit.registry_names()      # 仍在注册表里

    # force=True 是内部热替换用的通道，仍可撤销（否则组件无法重载）
    assert capability.unregister_plugin("builtin-x", force=True)["ok"]


def test_bundled_flag_survives_listing():
    assert capability.register_manifest(_manifest([_cap(name="p_builtin_y")]),
                                        plugin_id="builtin-y", bundled=True)["ok"]
    packs = capability.list_plugins()
    assert any(p["plugin_id"] == "builtin-y" and p["bundled"] for p in packs)


def test_default_enabled_false_is_skipped(tmp_path):
    import json as _json
    good = _manifest([_cap(name="p_pack_on")])
    off = _manifest([_cap(name="p_pack_off")], name="默认关闭")
    off["meta"]["default_enabled"] = False
    (tmp_path / "on.json").write_text(_json.dumps(good, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "off.json").write_text(_json.dumps(off, ensure_ascii=False), encoding="utf-8")
    out = capability.load_dir(str(tmp_path))
    assert out["loaded"] == 1
    assert "off.json" in out["skipped"]
    assert "p_pack_on" in toolkit.registry_names()
    assert "p_pack_off" not in toolkit.registry_names()


# ── 7. 内置包（bundled）：可停用，不可卸载 ────────────────────────────

def test_bundled_pack_cannot_be_unloaded():
    res = capability.register_manifest(_manifest([_cap(name="p_builtin_x")]),
                                      plugin_id="builtin-x", bundled=True)
    assert res["ok"]
    out = capability.unregister_plugin("builtin-x")
    assert not out["ok"]
    assert "只允许停用" in out["error"]
    assert "p_builtin_x" in toolkit.registry_names()      # 仍在注册表里

    # force=True 是内部热替换用的通道，仍可撤销（否则组件无法重载）
    assert capability.unregister_plugin("builtin-x", force=True)["ok"]


def test_bundled_flag_survives_listing():
    assert capability.register_manifest(_manifest([_cap(name="p_builtin_y")]),
                                        plugin_id="builtin-y", bundled=True)["ok"]
    packs = capability.list_plugins()
    assert any(p["plugin_id"] == "builtin-y" and p["bundled"] for p in packs)


def test_default_enabled_false_is_skipped(tmp_path):
    import json as _json
    good = _manifest([_cap(name="p_pack_on")])
    off = _manifest([_cap(name="p_pack_off")], name="默认关闭")
    off["meta"]["default_enabled"] = False
    (tmp_path / "on.json").write_text(_json.dumps(good, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "off.json").write_text(_json.dumps(off, ensure_ascii=False), encoding="utf-8")
    out = capability.load_dir(str(tmp_path))
    assert out["loaded"] == 1
    assert "off.json" in out["skipped"]
    assert "p_pack_on" in toolkit.registry_names()
    assert "p_pack_off" not in toolkit.registry_names()


# ── 8. 中文能力包名不得互相顶替 ──────────────────────────────────────────

def test_chinese_pack_names_get_distinct_ids(tmp_path):
    """中文名 slug 化后不能塌成同一个 id。

    真实踩过：三个内置包名分别是「公众号写作 / 音乐 MV 成片 / 每日简报」，
    旧实现把中文全剔除后统一回落成 "cap"，后装的被当成"热替换"顶掉先装的，
    结果 3 个包只剩 2 项能力，且没有任何报错。
    """
    import json as _json
    for i, nm in enumerate(("公众号写作", "每日简报", "音乐 MV 成片")):
        m = _manifest([_cap(name=f"p_cn_{i}")], name=nm)
        (tmp_path / f"pack{i}.json").write_text(_json.dumps(m, ensure_ascii=False), encoding="utf-8")
    out = capability.load_dir(str(tmp_path))
    assert out["loaded"] == 3, out
    ids = {p["plugin_id"] for p in capability.list_plugins()}
    assert len(ids) == 3, f"中文名能力包 id 冲突：{ids}"
    assert len(capability.list_capabilities()) == 3


def test_slugify_is_stable_and_ascii_safe():
    assert capability.slugify("公众号写作") == capability.slugify("公众号写作")
    assert capability.slugify("公众号写作") != capability.slugify("每日简报")
    assert capability.slugify("hello world") == "hello-world"


# ── 8. 中文能力包名不得互相顶替 ──────────────────────────────────────────

def test_chinese_pack_names_get_distinct_ids(tmp_path):
    """中文名 slug 化后不能塌成同一个 id。

    真实踩过：三个内置包名分别是「公众号写作 / 音乐 MV 成片 / 每日简报」，
    旧实现把中文全剔除后统一回落成 "cap"，后装的被当成"热替换"顶掉先装的，
    结果 3 个包只剩 2 项能力，且没有任何报错。
    """
    import json as _json
    for i, nm in enumerate(("公众号写作", "每日简报", "音乐 MV 成片")):
        m = _manifest([_cap(name=f"p_cn_{i}")], name=nm)
        (tmp_path / f"pack{i}.json").write_text(_json.dumps(m, ensure_ascii=False), encoding="utf-8")
    out = capability.load_dir(str(tmp_path))
    assert out["loaded"] == 3, out
    ids = {p["plugin_id"] for p in capability.list_plugins()}
    assert len(ids) == 3, f"中文名能力包 id 冲突：{ids}"
    assert len(capability.list_capabilities()) == 3


def test_slugify_is_stable_and_ascii_safe():
    assert capability.slugify("公众号写作") == capability.slugify("公众号写作")
    assert capability.slugify("公众号写作") != capability.slugify("每日简报")
    assert capability.slugify("hello world") == "hello-world"
