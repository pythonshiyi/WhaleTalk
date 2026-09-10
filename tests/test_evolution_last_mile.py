# -*- coding: utf-8 -*-
"""进化提案「最后一公里」回归（G17）。

现象：提案正文写在 docs/evolutions/*.md，而入口页 EVOLUTION.md 只有
「（鲸语补充：改动内容、原因、风险与验证方式）」占位符；列表与详情都不读嵌套正文
→ 人点进「自主」栏目看到空壳，方案被静默丢弃。

覆盖：入口页自动生成（非空壳）/ 占位符入口页自动补摘要 / 摘要提取 /
列表带摘要与状态 / 详情递归包含嵌套正文。
"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402
import deepseek_client as dc  # noqa: E402
from agent_tools.tool_system import create_evolution  # noqa: E402

BODY = """# 失败记忆生命周期收敛

把 failures.json 从「只追加」升级为有生命周期的记忆：指纹归并、复现计数、
修复验证自动消解、溢出归档。

## 方案
- stores.record_failures：按指纹合并
"""


@pytest.fixture()
def evo_dir(tmp_path, monkeypatch):
    d = str(tmp_path / "evolutions")
    os.makedirs(d, exist_ok=True)
    monkeypatch.setattr(dc, "EVOLUTIONS_DIR", d)
    monkeypatch.setattr(api_server, "EVOLUTIONS_DIR", d)
    return d


def _only_branch(d):
    names = [n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))]
    assert len(names) == 1
    return names[0]


def test_create_evolution_generates_substantive_entry_page(evo_dir):
    out = create_evolution("demo_proposal", [
        {"path": "docs/evolutions/demo.md", "content": BODY},
    ])
    assert "已创建" in out
    branch = os.path.join(evo_dir, _only_branch(evo_dir))
    index = os.path.join(branch, "EVOLUTION.md")
    assert os.path.exists(index), "缺失入口页时应自动生成"
    text = open(index, encoding="utf-8").read()
    assert "（鲸语补充" not in text, "自动生成不得再落占位符空壳"
    assert "## 方案要点" in text and "## 影响范围" in text and "## 正文" in text
    assert "失败记忆生命周期收敛" in text, "摘要应来自正文标题"
    assert "docs/evolutions/demo.md" in text, "应指出正文位置"


def test_create_evolution_fills_placeholder_entry_page(evo_dir):
    """AI 自己交的入口页若是占位符 → 自动补一节方案要点（保留原文）。"""
    create_evolution("demo2", [
        {"path": "EVOLUTION.md", "content": "# 提案 demo2\n\n## 说明\n（鲸语补充：改动内容、原因、风险与验证方式）\n"},
        {"path": "docs/evolutions/demo2.md", "content": BODY},
    ])
    branch = os.path.join(evo_dir, _only_branch(evo_dir))
    text = open(os.path.join(branch, "EVOLUTION.md"), encoding="utf-8").read()
    assert "（鲸语补充" in text, "保留 AI 原文"
    assert "## 方案要点" in text, "但必须补上实质内容"
    assert "失败记忆生命周期收敛" in text


def test_create_evolution_keeps_real_entry_page_untouched(evo_dir):
    real = "# 提案 x\n\n把 run_python 加上内存上限看门狗，超限杀进程树。\n"
    create_evolution("demo3", [{"path": "EVOLUTION.md", "content": real}])
    branch = os.path.join(evo_dir, _only_branch(evo_dir))
    text = open(os.path.join(branch, "EVOLUTION.md"), encoding="utf-8").read()
    assert text == real, "已写好实质内容的入口页不应被改动"


def test_evolution_meta_reads_nested_body(evo_dir):
    create_evolution("meta_case", [{"path": "docs/evolutions/meta.md", "content": BODY}])
    name = _only_branch(evo_dir)
    meta = api_server._evolution_meta(name)
    assert meta["title"] == "进化提案：meta_case"
    assert meta["has_body"] is True and meta["placeholder"] is False
    assert "有生命周期的记忆" in meta["summary"], "摘要应取整段而非半句"
    assert "溢出归档" in meta["summary"], "折行段落应被合并"
    assert meta["body_files"] == ["docs/evolutions/meta.md"]


def test_summary_skips_code_fence_and_lead_in(evo_dir, tmp_path, monkeypatch):
    """真实提案形态：引导句 + 代码块 + 结论段 → 摘要应是结论段。"""
    body = (
        "# 提案：某改进\n\n"
        "- 编号：G1\n\n"
        "## 1. 问题\n\n"
        "`api_server.py:2113` 当前实现：\n\n"
        "```python\n"
        "def _record_failure(name, result):\n"
        "    return None\n"
        "```\n\n"
        "全代码 resolved 出现 **0 次** —— 失败只被记录与覆盖，永不标记已修复。\n"
    )
    d = str(tmp_path / "evolutions2")
    branch = os.path.join(d, "real_case")
    os.makedirs(os.path.join(branch, "docs"), exist_ok=True)
    with open(os.path.join(branch, "docs", "real.md"), "w", encoding="utf-8") as f:
        f.write(body)
    monkeypatch.setattr(api_server, "EVOLUTIONS_DIR", d)
    meta = api_server._evolution_meta("real_case")
    assert meta["summary"].startswith("全代码"), f"摘要不应取代码块或引导句：{meta['summary']}"
    assert "永不标记已修复" in meta["summary"]


def test_evolutions_list_exposes_summary_and_status(evo_dir):
    create_evolution("list_case", [{"path": "docs/evolutions/a.md", "content": BODY}])
    data = api_server._evolutions()
    item = data["evolutions"][0]
    assert item["status"] == "proposed" and item["applied"] is False
    assert item["summary"] and item["has_body"] is True


def test_evolution_detail_includes_nested_files(evo_dir):
    create_evolution("detail_case", [
        {"path": "docs/evolutions/d.md", "content": BODY},
        {"path": "agent_tools/probe.py", "content": "X = 1\n"},
    ])
    name = _only_branch(evo_dir)
    detail = api_server._evolution_detail(name)
    names = {f["rel"] for f in detail["files"]}
    assert "EVOLUTION.md" in names
    assert "docs/evolutions/d.md" in names, "详情必须包含嵌套正文（此前完全看不到）"
    probe = [f for f in detail["files"] if f["rel"] == "agent_tools/probe.py"][0]
    assert probe["nested"] is True
    assert probe["apply_target"].startswith("（正文/附件")
    assert detail["summary"], "详情应带摘要"


def test_placeholder_detection_for_empty_proposal(tmp_path, monkeypatch):
    """既无正文、入口页又只有占位符 → 标记为 placeholder（前端可提示补正文）。"""
    d = str(tmp_path / "evolutions")
    branch = os.path.join(d, "empty_case")
    os.makedirs(branch)
    with open(os.path.join(branch, "EVOLUTION.md"), "w", encoding="utf-8") as f:
        f.write("# 提案\n\n## 说明\n（鲸语补充：改动内容、原因、风险与验证方式）\n")
    monkeypatch.setattr(api_server, "EVOLUTIONS_DIR", d)
    meta = api_server._evolution_meta("empty_case")
    assert meta["has_body"] is False and meta["placeholder"] is True
    assert meta["summary"] == ""
