# -*- coding: utf-8 -*-
"""统一模型（DeepSeek V4.1 Flash）集成回归。

背景：2026-09-10 DeepSeek 发布 V4.1 Flash 并把全部模型升级为**单一原生多模态
模型**（快速/专家/识图模式合并为统一的智能模式）。旧模型 V4 Flash 与 V4 Flash
Vision Exp 已下线，V4 Pro 自 2026-09-14 12:00 起同样路由到 V4.1 Flash。

本测试锁定集成契约：
  1. 模型注册表收敛为一项，默认模型 / 视觉模型 / 配置默认值三处一致；
  2. 旧模型名经 resolve_model 归一到统一模型，但**不改写自定义网关模型名**；
  3. is_vision_model 对统一模型与旧名恒为真（原生多模态，无需切换模式）；
  4. 峰谷新价（2026-09-10 12:00 生效）与历史价回算正确，历史记录不被追溯改价；
  5. config_utils 仅在官方端点做旧名迁移；
  6. 前端预设与设置不残留旧模型名。
"""
import pathlib
import re

import deepseek_client as dc
import stats

REPO = pathlib.Path(__file__).resolve().parent.parent
UNIFIED = "deepseek-flash"
LEGACY = [
    "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
    "deepseek-v4.1-flash-expires-on-0910",
    "deepseek-v4-pro",
]


# ── 1. 单一模型注册表 ─────────────────────────────────────────────
def test_registry_has_exactly_one_model():
    assert list(dc.MODELS.keys()) == [UNIFIED], "模型注册表应只保留统一模型一项"
    assert dc.MODEL_ID == UNIFIED


def test_default_and_vision_model_point_to_unified():
    """默认模型与视觉模型必须是同一个——原生多模态意味着不再有独立视觉模型。"""
    assert dc.DEFAULT_MODEL == UNIFIED
    assert dc.VISION_MODEL == UNIFIED


def test_default_config_uses_unified_model():
    import config_defaults

    assert config_defaults.DEFAULT_CONFIG["model"] == UNIFIED


def test_model_metadata_declares_native_multimodal():
    meta = dc.MODELS[UNIFIED]
    assert meta.get("vision") is True, "统一模型必须声明原生视觉能力"
    assert meta.get("v41") is True
    assert int(meta["max_context_tokens"]) >= 1_000_000
    assert int(meta["max_output_tokens"]) >= 384 * 1024
    assert "V4.1" in str(meta.get("label")), f"标签应体现 V4.1：{meta.get('label')}"


def test_no_legacy_name_is_a_registry_key():
    for name in LEGACY:
        assert name not in dc.MODELS, f"旧模型名不应再作为注册表键：{name}"


# ── 2. 别名归一 ──────────────────────────────────────────────────
def test_legacy_aliases_resolve_to_unified():
    for name in LEGACY:
        assert dc.resolve_model(name) == UNIFIED, f"{name} 应归一到统一模型"
        assert dc.is_legacy_model(name) is True


def test_custom_models_are_never_rewritten():
    """自定义 OpenAI 兼容模型（Ollama/Kimi/智谱等）绝不能被改写。"""
    for name in ("gpt-4o", "qwen2.5", "moonshot-v1-8k", "glm-4-flash",
                 "llama3.1:8b", "my-local-model", UNIFIED, ""):
        assert dc.resolve_model(name) == name, f"{name!r} 不应被别名表改写"
        assert dc.is_legacy_model(name) is False


# ── 3. 视觉能力归一 ──────────────────────────────────────────────
def test_is_vision_model_true_for_unified_and_legacy():
    assert dc.is_vision_model(UNIFIED) is True
    for name in LEGACY:
        assert dc.is_vision_model(name) is True, f"{name} 经归一后应支持视觉"


def test_is_vision_model_false_for_unknown_custom_without_vision():
    assert dc.is_vision_model("gpt-4o") is False
    assert dc.is_vision_model("qwen2.5") is False
    assert dc.is_vision_model("") is False
    assert dc.is_vision_model(None) is False
    # 名称启发式仍然保留（自定义端点的视觉模型）
    assert dc.is_vision_model("some-vision-model") is True


# ── 4. 定价 ─────────────────────────────────────────────────────
def test_current_pricing_matches_official_announcement():
    """官方 2026-09-10 12:00 生效价（高峰时段，元/百万 tokens）。"""
    assert stats.PRICING[UNIFIED] == {"prompt": 2.0, "completion": 8.0, "cache_hit": 0.04}
    assert stats.DEFAULT_PRICE == {"prompt": 2.0, "completion": 8.0, "cache_hit": 0.04}


def test_legacy_names_billed_at_unified_price_today():
    """官方把旧名路由到 V4.1 Flash 并按 V4.1 Flash 计费——回落到当前价。"""
    for name in LEGACY:
        assert stats.price_for(name) == stats.DEFAULT_PRICE, f"{name} 应按当前价计费"


def test_historical_records_use_v4_era_prices():
    """分界日之前的用量按当时的 V4 价表回算，不被追溯改价。"""
    assert stats.price_for("deepseek-v4-flash", day="2026-09-05")["completion"] == 9.0
    assert stats.price_for("deepseek-v4-pro", day="2026-09-05")["completion"] == 27.0
    # 未知模型名在历史日期回落到 V4 默认价（而非新价）
    assert stats.price_for("unknown-x", day="2026-09-05")["completion"] == 9.0
    # 分界日当天起按新价
    assert stats.price_for("deepseek-v4-pro", day=stats.PRICE_ERA_CURRENT) == stats.DEFAULT_PRICE


def test_estimate_cost_offpeak_is_half_of_peak(monkeypatch):
    usage = {"prompt": 1_000_000, "completion": 1_000_000, "cache_hit": 0, "cache_miss": 1_000_000}
    monkeypatch.setattr(stats, "is_peak_hour", lambda *a, **k: True)
    peak = stats.estimate_cost(usage, UNIFIED)
    monkeypatch.setattr(stats, "is_peak_hour", lambda *a, **k: False)
    off = stats.estimate_cost(usage, UNIFIED)
    assert abs(peak - off * 2) < 1e-9, f"空闲时段应为高峰的一半：peak={peak} off={off}"
    # 100 万未命中输入 + 100 万输出 = 2.0 + 8.0 = ¥10.00（高峰）
    assert abs(peak - 10.0) < 1e-9


# ── 5. 配置迁移 ──────────────────────────────────────────────────
def test_config_migrates_legacy_model_on_official_endpoint():
    import config_utils

    for name in LEGACY:
        cfg = config_utils.normalize_config({"model": name, "base_url": dc.DEFAULT_BASE_URL})
        assert cfg["model"] == UNIFIED, f"官方端点下 {name} 应被迁移到统一模型"


def test_config_keeps_custom_model_on_custom_endpoint():
    """自定义网关可能恰好重名，绝不擅自改写。"""
    import config_utils

    cfg = config_utils.normalize_config(
        {"model": "deepseek-v4-flash", "base_url": "http://localhost:11434/v1"})
    assert cfg["model"] == "deepseek-v4-flash", "自定义端点下的模型名不应被改写"
    cfg2 = config_utils.normalize_config({"model": "gpt-4o", "base_url": dc.DEFAULT_BASE_URL})
    assert cfg2["model"] == "gpt-4o"


# ── 6. 前端不残留旧模型名 ────────────────────────────────────────
def test_frontend_presets_use_unified_model():
    jsx = (REPO / "webui" / "src" / "components" / "SettingsPage.jsx").read_text(encoding="utf-8")
    assert "deepseek-v4" not in jsx, "前端预设不应再引用旧模型名"
    assert jsx.count('model: "deepseek-flash"') >= 2, "DeepSeek 预设与供应商项应使用统一模型"


# ── 7. 源码中旧模型名只允许出现在别名表与历史价表 ──────────────────
def test_legacy_names_confined_to_alias_and_history_tables():
    """防止有人在别处重新硬编码旧模型名（此前的 DEFAULT_MODEL 就是这么漂的）。"""
    allowed = {"deepseek_client.py", "stats.py"}
    offenders = []
    for p in REPO.rglob("*.py"):
        rel = p.relative_to(REPO)
        parts = set(rel.parts)
        if parts & {".venv", "__pycache__", "tests", "build", "dist"}:
            continue
        if p.name in allowed:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            for name in LEGACY:
                if name in code:
                    offenders.append(f"{rel}: {line.strip()[:80]}")
    assert not offenders, "旧模型名只应出现在 deepseek_client 别名表与 stats 历史价表：\n" + "\n".join(offenders)


def test_docs_mention_unified_model():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "deepseek-flash" in readme, "README 应说明统一模型名"
    assert "V4.1 Flash" in readme
    technique = (REPO / "TECH_NOTES.md").read_text(encoding="utf-8")
    assert "MODEL_ID" in technique and "LEGACY_MODEL_ALIASES" in technique
    # 不应再指导用户"选择视觉模型"
    assert not re.search(r"选择\s*`?deepseek-v4-flash-vision-exp", readme)
