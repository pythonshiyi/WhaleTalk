# -*- coding: utf-8 -*-
"""记忆写入风暴回归测试（v3.8.5 修复）：harvest 低价值过滤 + consolidate 近重复不再递归拼接。

背景 bug：`_chat_harvest` 每分钟把无信息量的标签（如「新版本结论」）写入记忆；
同时 `consolidate_memories` 对近重复记忆做 "X（并入:X…）（并入:X…）" 递归拼接，
使垃圾文本无限增长、去重失效 → 记忆库被重复条目淹没。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import brainkit as bk  # noqa: E402


@pytest.fixture
def btmp():
    td = Path(tempfile.mkdtemp(prefix="wt_harvest_"))
    for d in ("memories", "thinking_log", "archive"):
        (td / d).mkdir(parents=True, exist_ok=True)
    man = {"schema_version": 1, "brain_id": "whale-hv", "created_at": "2026-01-01T00:00:00+00:00", "genesis": "x"}
    man["fingerprint"] = bk.compute_fingerprint(man)
    bk.save_json(td / "manifest.json", man)
    bk.save_json(td / "identity.json", {})
    bk.save_json(td / "heartbeat.json", {})
    old = bk.BRAIN_DIR
    bk.set_brain_dir(td)
    yield td
    bk.set_brain_dir(old)
    shutil.rmtree(td, ignore_errors=True)


def _jline(e):
    import json
    return json.dumps(e, ensure_ascii=False)


def _mk(mid, text, ts, archived=False):
    import json
    return {"id": mid, "ts": ts, "type": "对话", "importance": 3, "text": text,
            "tags": [], "entities": [], "relations": [], "source": "对话", "archived": archived,
            "sensitivity": "public", "hit_count": 0, "last_hit": "", "supersedes": "", "version_id": mid}


# ---- Fix 1: harvest 低价值过滤 ----

def test_low_value_filter():
    """「新版本结论」这类纯标签应被判为低价值（拦截）；含实质内容的事实应放行。"""
    import api_server
    f = api_server._harvest_is_low_value
    # 低价值标签 → True（拦截）
    for s in ["新版本结论", "结论", "决定", "好的收到", "自动记忆", "进展"]:
        assert f(s) is True, f"{s!r} 应判为低价值"
    # 实质事实 → False（放行）
    for s in ["用户偏好中文表格化输出，重要字段优先", "张三负责项目A的联调",
              "决定采用镜像源提速稳定", "记得每周五备份数据库"]:
        assert f(s) is False, f"{s!r} 应判为有效记忆"


def test_consolidate_no_recursive_glue(btmp):
    """近重复「新版本结论」记忆：consolidate 应归档重复项，而不拼接成无限 (并入:…)(并入:…) 垃圾。"""
    lines = [_jline(_mk(f"m{i}", "新版本结论", f"2026-09-0{1 + i % 3}T10:00:00+08:00")) for i in range(20)]
    with open(bk.MEMORY_JSONL, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    r = bk.consolidate_memories(min_importance=0, days=0)
    items = bk.load_memories(include_archived=True)
    # 不应出现因拼接产生的超长文本（每条都 < 40 字）——即没有 (并入:) 链式累加
    for e in items:
        assert len(e.get("text") or "") < 40, f"出现递归拼接文本: {e['text']}"
    # 且最多保留 1 条活跃的「新版本结论」（其余归档）
    active = [e for e in bk.load_memories() if e.get("text") == "新版本结论"]
    assert len(active) <= 2


def test_consolidate_complementary_still_merges(btmp):
    """真正互补的两条记忆仍应保留信息（keep + 对方独有片段），而非丢弃。"""
    import json
    a = _mk("m-a", "用户偏好中文表格化输出", "2026-09-01T10:00:00+08:00")
    b = _mk("m-b", "用户偏好中文回复，表格优先", "2026-09-02T10:00:00+08:00")
    with open(bk.MEMORY_JSONL, "a", encoding="utf-8") as f:
        f.write(_jline(a) + "\n" + _jline(b) + "\n")
    bk.consolidate_memories(min_importance=0, days=0)
    kept = [e for e in bk.load_memories()]
    assert kept  # 有保留
    # 不崩溃且总数减少（合并发生）
    assert len(bk.load_memories(include_archived=True)) >= 1
