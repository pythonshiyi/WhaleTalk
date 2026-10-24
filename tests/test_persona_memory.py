"""角色级记忆：存储隔离、检索、注入与 API。"""
import context_providers as cp
import persona_memory
import personas


def _init(tmp_path):
    personas.init(str(tmp_path / "personas.json"), reset=True)
    persona_memory.init(str(tmp_path / "persona_memory"), reset=True)


def test_add_dedupe_rank_clear(tmp_path):
    _init(tmp_path)
    assert persona_memory.add("p_xiaojing", "用户喜欢在海边散步", origin="user") is True
    assert persona_memory.add("p_xiaojing", "用户喜欢在海边散步", origin="user") is True
    assert persona_memory.add("p_xiaojing", "用户最近在写小说", origin="agent") is True
    assert persona_memory.count("p_xiaojing") == 2

    picked = persona_memory.rank("p_xiaojing", "海边", limit=5)
    assert picked and "海边" in picked[0]["text"]

    assert persona_memory.clear("p_xiaojing") is True
    assert persona_memory.count("p_xiaojing") == 0


def test_invalid_persona_id_is_rejected(tmp_path):
    _init(tmp_path)
    assert persona_memory.add("../../evil", "x") is False
    assert persona_memory.add("p_missing", "x") is True
    assert persona_memory.count("p_missing") == 1


def test_persona_memory_provider_injects_and_respects_quiet(tmp_path):
    _init(tmp_path)
    persona_memory.add("p_xiaojing", "用户喜欢在海边散步", origin="user")
    facts = persona_memory.facts("p_xiaojing")
    deps = {
        "dialog_prompt": "DIALOG",
        "persona_id": "p_xiaojing",
        "persona_name": "小鲸",
        "persona_memory_load": lambda pid: facts if pid == "p_xiaojing" else [],
    }
    ctx = cp.Context(messages=[{"role": "user", "content": "周末去海边吗"}],
                     cfg={}, pure_chat=True, deps=deps)
    out = cp.assemble(ctx)
    assert "[角色记忆]" in out["text"]
    assert "小鲸" in out["text"]
    assert "海边散步" in out["text"]
    assert "context.persona_memory" in [i["name"] for i in out["receipt"]["injected"]]

    quiet = cp.Context(messages=[{"role": "user", "content": "周末去海边吗"}],
                       cfg={}, pure_chat=True, quiet_mode=True, deps=deps)
    assert "[角色记忆]" not in (cp.assemble(quiet)["text"] or "")


def test_api_memory_helpers(tmp_path):
    _init(tmp_path)
    import api_server

    persona_memory.add("p_xiaojing", "用户喜欢在海边散步", origin="user")
    listed = api_server._personas()
    xiaojing = next(p for p in listed["personas"] if p["id"] == "p_xiaojing")
    assert xiaojing["memory_count"] == 1

    result, err = api_server._persona_memory_get("p_xiaojing")
    assert err is None and result["count"] == 1
    assert result["facts"][0]["text"] == "用户喜欢在海边散步"

    result, err = api_server._persona_memory_action({"action": "clear", "id": "p_xiaojing"})
    assert err is None and result["count"] == 0
