"""角色卡：存储 / 提示词拼装 / 会话绑定。

覆盖：
- 首次读取写入内置角色，且内置角色不可删除；
- 新建、更新、复制、删除；
- 角色提示词包含人设、示例、边界与安全规则；
- 对话模式用角色提示词替换基础提示，quiet_mode 下不注入；
- 会话元数据保留 persona_id。
"""
import context_providers as cp
import personas


def _init(tmp_path):
    personas.init(str(tmp_path / "personas.json"), reset=True)


def test_seed_builtin_personas(tmp_path):
    _init(tmp_path)
    items = personas.list_personas()
    assert len(items) >= 3
    assert any(p["id"] == "p_xiaojing" and p["builtin"] for p in items)
    assert all(p["name"] for p in items)


def test_create_update_duplicate_delete(tmp_path):
    _init(tmp_path)
    created = personas.save_persona({
        "name": "测试角色",
        "avatar": "🧪",
        "tagline": "用于测试",
        "personality": "冷静",
    })
    assert created["id"].startswith("p_")
    assert personas.get_persona(created["id"])["name"] == "测试角色"

    updated = personas.save_persona({**created, "name": "测试角色改"})
    assert updated["id"] == created["id"]
    assert updated["created_at"] == created["created_at"]
    assert personas.get_persona(created["id"])["name"] == "测试角色改"

    dup = personas.duplicate_persona(created["id"])
    assert dup["id"] != created["id"]
    assert dup["name"].endswith("副本")
    assert dup["builtin"] is False

    assert personas.delete_persona(created["id"]) is True
    assert personas.get_persona(created["id"]) is None

    try:
        personas.delete_persona("p_xiaojing")
    except ValueError as e:
        assert "内置角色" in str(e)
    else:
        raise AssertionError("内置角色不应被删除")


def test_build_prompt_contains_card_and_guardrails(tmp_path):
    _init(tmp_path)
    p = personas.get_persona("p_xiaojing")
    prompt = personas.build_prompt(p)
    assert "小鲸" in prompt
    assert "温柔" in prompt
    assert "对话示例" in prompt
    assert "不冒充真人" in prompt
    assert "退出角色" in prompt
    assert personas.build_prompt(None) == ""


def test_persona_prompt_replaces_dialog_prompt():
    deps = {"dialog_prompt": "DIALOG", "persona_prompt": "PERSONA"}
    ctx = cp.Context(messages=[{"role": "user", "content": "hi"}], cfg={}, pure_chat=True, deps=deps)
    assert cp.assemble(ctx)["prompt"] == "PERSONA"

    quiet = cp.Context(messages=[{"role": "user", "content": "hi"}], cfg={},
                       pure_chat=True, quiet_mode=True, deps=deps)
    assert cp.assemble(quiet)["prompt"] == "DIALOG"

    task = cp.Context(messages=[{"role": "user", "content": "hi"}], cfg={},
                      pure_chat=False, deps={**deps, "default_prompt": "DEFAULT"})
    assert cp.assemble(task)["prompt"] == "DEFAULT"


def test_session_meta_keeps_persona_id():
    import api_server

    meta = api_server._session_meta({"id": "s1", "persona_id": "p_xiaojing"}, "s1.json")
    assert meta["persona_id"] == "p_xiaojing"


def test_api_helpers_save_resolve_and_delete(tmp_path):
    _init(tmp_path)
    import api_server

    listed = api_server._personas()
    assert listed["count"] >= 3

    result, err = api_server._personas_save({
        "action": "save",
        "persona": {"name": "API 角色", "personality": "简洁"},
    })
    assert err is None
    pid = result["persona"]["id"]
    assert api_server._resolve_persona({"persona_id": pid})["name"] == "API 角色"
    assert api_server._resolve_persona({"persona_id": "p_missing"}) is None

    result, err = api_server._personas_save({"action": "duplicate", "id": pid})
    assert err is None and result["persona"]["id"] != pid

    result, err = api_server._personas_save({"action": "delete", "id": pid})
    assert err is None
    assert api_server._resolve_persona({"persona_id": pid}) is None

