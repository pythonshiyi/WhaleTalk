# -*- coding: utf-8 -*-
"""技能结晶回归（G14）。

现象：patterns.json 记录成功调用、tasklog 记录任务链，但都只是流水账——
没有工厂把**重复出现**的链固化为可复用技能，AI 每次从零推导同样的步骤。

覆盖：链签名折叠 / 汇总统计 / 结晶阈值 / 去重（签名+名字）/ 参数脱敏 /
模板渲染（参数化）/ api_server 接线（normalize 保留来源标记 + 写入指令库）。
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402
import skill_factory  # noqa: E402

CHAIN = ["search_web", "read_file", "chart_data", "pdf_create"]


def _task(title, chain, ts="2026-09-10 10:00:00"):
    return {"title": title, "chain": chain, "ts": ts}


def test_chain_signature_folds_consecutive_duplicates():
    assert skill_factory.chain_signature(["a", "a", "b", "a"]) == "a>b>a"
    assert skill_factory.chain_signature(["a", "", None, "a"]) == "a"
    assert skill_factory.chain_signature([]) == ""


def test_collect_chains_aggregates_repeats():
    tasks = [_task("做图表报告", CHAIN), _task("再来一份图表", CHAIN), _task("别的活", ["read_file"])]
    agg = skill_factory.collect_chains(tasks)
    sig = skill_factory.chain_signature(CHAIN)
    assert agg[sig]["count"] == 2
    assert agg[sig]["chain"] == CHAIN
    assert "做图表报告" in agg[sig]["titles"]


def test_is_crystallizable_thresholds():
    short = {"chain": ["a", "b"], "count": 5}
    once = {"chain": CHAIN, "count": 1}
    ok = {"chain": CHAIN, "count": 2}
    assert skill_factory.is_crystallizable(short) is False, "链太短不值得成为技能"
    assert skill_factory.is_crystallizable(once) is False, "只出现一次不算模式"
    assert skill_factory.is_crystallizable(ok) is True


def test_crystallize_dedupes_by_signature_and_name():
    tasks = [_task("做图表报告", CHAIN), _task("做图表报告", CHAIN)]
    drafts = skill_factory.crystallize(tasks)
    assert len(drafts) == 1
    assert drafts[0]["auto_skill"] is True
    assert drafts[0]["enabled"] is False, "草稿态：不默认启用"
    assert drafts[0]["source_chain"] == CHAIN
    assert drafts[0]["hits"] == 2

    again = skill_factory.crystallize(tasks, existing=drafts)
    assert again == [], "已结晶过的链不应重复产出"
    # 用户改过名字也应认出来（按 source_sig 去重）
    renamed = [dict(drafts[0], name="我改过的名字")]
    assert skill_factory.crystallize(tasks, existing=renamed) == []


def test_crystallize_orders_by_repeat_count():
    hot = CHAIN
    cold = ["read_file", "write_file", "pdf_create"]
    tasks = [_task("冷", cold)] * 2 + [_task("热", hot)] * 4
    drafts = skill_factory.crystallize(tasks)
    assert drafts[0]["source_chain"] == hot, "重复次数多的链优先结晶"


def test_mask_paths_and_skill_template():
    assert "jingyu" not in skill_factory.mask_paths(r"{'path': 'D:\jingyu\a\b.py'}")
    patterns = [{"tool": "read_file", "args": "{'path': 'D:\\jingyu\\secret\\x.csv'}"}]
    info = {"chain": CHAIN, "count": 3, "titles": ["做图表报告"]}
    skill = skill_factory.build_skill(info, patterns)
    assert "{{TEXT}}" in skill["text"], "必须是参数化模板"
    for step in CHAIN:
        assert f"`{step}`" in skill["text"]
    assert "jingyu" not in skill["text"], "模板不得烧进本机绝对路径"
    assert skill["category"] == skill_factory.SKILL_CATEGORY
    assert "3 次" in skill["desc"]


def test_crystallize_respects_max_drafts():
    tasks = []
    for i in range(5):
        tasks += [_task(f"任务{i}", ["t1", f"t{i}", "t9"])] * 2
    drafts = skill_factory.crystallize(tasks, max_drafts=2)
    assert len(drafts) == 2


def test_crystallize_survives_broken_input():
    assert skill_factory.crystallize(None) == []
    assert skill_factory.crystallize([None, "x", {"chain": None}]) == []
    assert skill_factory.build_skill({})["source_chain"] == []


def test_prompt_normalize_keeps_skill_provenance():
    """normalize 是白名单式重建——来源标记若不透传会被静默丢弃。"""
    raw = {
        "name": "自动技能 · x", "text": "t", "auto_skill": True,
        "source_chain": ["a"], "source_sig": "a", "hits": 3,
    }
    out = api_server._prompt_normalize(raw)
    assert out["auto_skill"] is True and out["source_sig"] == "a"
    assert out["source_chain"] == ["a"] and out["hits"] == 3


def test_api_crystallize_writes_prompt_library(tmp_path, monkeypatch):
    prompts = str(tmp_path / "prompts.json")
    patterns = str(tmp_path / "patterns.json")
    active = str(tmp_path / "workspace")
    import os
    os.makedirs(os.path.join(active, ".whaletalk"), exist_ok=True)
    monkeypatch.setattr(api_server, "PROMPTS_PATH", prompts)
    monkeypatch.setattr(api_server, "PATTERNS_PATH", patterns)
    monkeypatch.setattr(api_server, "_status", lambda: {"active_dir": active})
    monkeypatch.setattr(api_server, "_audit", lambda *a, **k: None)

    with open(os.path.join(active, ".whaletalk", "tasklog.json"), "w", encoding="utf-8") as f:
        json.dump({"tasks": [_task("做图表报告", CHAIN), _task("做图表报告", CHAIN)]}, f, ensure_ascii=False)
    with open(patterns, "w", encoding="utf-8") as f:
        json.dump([{"tool": "read_file", "args": "{'path': 'D:\\\\x\\\\y.csv'}"}], f, ensure_ascii=False)

    assert api_server._crystallize_skills() == 1
    saved = json.load(open(prompts, encoding="utf-8"))
    assert len(saved) == 1 and saved[0]["auto_skill"] is True
    # 幂等：再跑一次不重复写入
    assert api_server._crystallize_skills() == 0
    assert len(json.load(open(prompts, encoding="utf-8"))) == 1


def test_api_crystallize_noop_without_patterns(tmp_path, monkeypatch):
    active = str(tmp_path / "ws")
    import os
    os.makedirs(os.path.join(active, ".whaletalk"), exist_ok=True)
    monkeypatch.setattr(api_server, "PROMPTS_PATH", str(tmp_path / "prompts.json"))
    monkeypatch.setattr(api_server, "PATTERNS_PATH", str(tmp_path / "patterns.json"))
    monkeypatch.setattr(api_server, "_status", lambda: {"active_dir": active})
    assert api_server._crystallize_skills() == 0


def test_record_tasklog_triggers_crystallization(tmp_path, monkeypatch):
    """任务链写入 → 自动触发结晶（无需人工调用）。"""
    prompts = str(tmp_path / "prompts.json")
    active = str(tmp_path / "ws2")
    import os
    monkeypatch.setattr(api_server, "PROMPTS_PATH", prompts)
    monkeypatch.setattr(api_server, "PATTERNS_PATH", str(tmp_path / "patterns.json"))
    monkeypatch.setattr(api_server, "_status", lambda: {"active_dir": active})
    monkeypatch.setattr(api_server, "_audit", lambda *a, **k: None)

    api_server._record_tasklog("做图表报告", CHAIN)
    api_server._record_tasklog("做图表报告", CHAIN)

    saved = json.load(open(prompts, encoding="utf-8"))
    assert len(saved) == 1 and saved[0]["auto_skill"] is True
