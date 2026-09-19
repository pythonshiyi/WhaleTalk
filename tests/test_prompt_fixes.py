"""提示词相关修复回归（压缩保留系统提示 / 微信配置 / 技能脱敏 / 选题编号）。"""
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import api_server  # noqa: E402  （先于 agent_tools 导入，遵守导入顺序契约）


def test_compress_keeps_system_prompt(monkeypatch, tmp_path):
    """压缩按 user 切轮时，基础 system 提示词必须固定保留（长会话不丢人格/指令）。"""
    import config_utils
    monkeypatch.setattr(api_server, "ARCHIVES_DIR", str(tmp_path))
    monkeypatch.setattr(api_server, "_context_size", lambda msgs: (10**9, 10**9))

    class _BadClient:
        def chat(self, *a, **k):
            raise RuntimeError("no llm")

    cfg = {"max_context_tokens": 10, "max_context_chars": 10,
           "min_kept_turns": 2, "privacy_mode": True}
    msgs = [{"role": "system", "content": "BASE PROMPT"}]
    for i in range(6):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    out, info = api_server._compress_messages(msgs, cfg, _BadClient())
    assert info is not None
    assert out[0] == {"role": "system", "content": "BASE PROMPT"}, "系统提示词被压缩丢弃"
    assert any(m.get("role") == "system" for m in out)


def test_wechat_run_once_passes_config_path(monkeypatch):
    """run_once 未显式传 config_path 时须回退到模块内 CONFIG_PATH（否则用户配置永不生效）。"""
    from wechat_writer import main as wm
    from wechat_writer import config as cfgmod
    from wechat_writer import sources as srcmod

    seen = {}

    def _spy(path=None):
        seen["path"] = path
        return cfgmod.DEFAULT_CONFIG
    monkeypatch.setattr(cfgmod, "load_config", _spy)
    monkeypatch.setattr(srcmod, "collect_all", lambda cfg, use_blocked=False: [])
    wm.run_once(dry_run=True)
    assert seen.get("path") == wm.CONFIG_PATH


def test_skill_factory_masks_secrets():
    import skill_factory as sf
    src = 'call_api(url="https://x", headers={"Authorization": "Bearer sk-abc1234567890XYZ"})'
    out = sf.mask_paths(src)
    assert "sk-abc1234567890XYZ" not in out
    out2 = sf.mask_paths("api_key=abcdef123456")
    assert "abcdef123456" not in out2


def test_topic_related_is_one_based():
    import wechat_writer.topic as topic

    class _Item:
        def __init__(self, n):
            self.n = n

        def display(self, _n=0):
            return f"item{self.n}"

    items = [_Item(0), _Item(1), _Item(2)]
    fake_json = '[{"name": "选题A", "angle": "a", "related": [1, 3], "why": "w"}]'

    def _llm(messages, **kw):
        return fake_json
    out = topic._llm_pick_candidates(items, [], _llm)
    assert out and out[0].related == [0, 2], f"1 基编号应转 0 基下标，实际 {out[0].related if out else None}"
