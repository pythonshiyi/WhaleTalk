# -*- coding: utf-8 -*-
"""进化提案「忽略 = 软删除」回归（G19）。

历史事故：`_evolution_ignore` 原用 `shutil.rmtree` 硬删，叠加 `evolutions/` 在
.gitignore 中 → 一次「忽略」即永久丢失，曾吃掉 4 份提案（最终靠会话记录里
create_evolution 的入参才恢复）。本用例锁死"忽略必须可恢复"这一契约。
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402
import deepseek_client as dc  # noqa: E402
from agent_tools.tool_system import create_evolution  # noqa: E402

BODY = "# 提案：某改进\n\n把 X 改成 Y，理由是 Z。\n\n## 方案\n- 步骤一\n"


@pytest.fixture()
def evo(tmp_path, monkeypatch):
    d = str(tmp_path / "evolutions")
    os.makedirs(d, exist_ok=True)
    audits = []
    monkeypatch.setattr(dc, "EVOLUTIONS_DIR", d)
    monkeypatch.setattr(api_server, "EVOLUTIONS_DIR", d)
    monkeypatch.setattr(api_server, "_audit", lambda *a, **k: audits.append(a))
    return {"dir": d, "audits": audits}


def _make(evo, name="soft_case"):
    create_evolution(name, [{"path": "docs/evolutions/%s.md" % name, "content": BODY}])
    return [n for n in os.listdir(evo["dir"]) if os.path.isdir(os.path.join(evo["dir"], n))][0]


def _snapshot(path):
    out = {}
    for dp, _dn, fns in os.walk(path):
        for f in fns:
            p = os.path.join(dp, f)
            out[os.path.relpath(p, path)] = open(p, "rb").read()
    return out


def test_ignore_soft_deletes_and_is_byte_identical(evo):
    name = _make(evo)
    branch = os.path.join(evo["dir"], name)
    before = _snapshot(branch)

    out, err = api_server._evolution_ignore(name)
    assert err is None and out["ok"] is True
    assert out["recoverable"] is True
    assert not os.path.exists(branch), "原位置应让出"

    box = os.path.join(evo["dir"], "_ignored")
    archived = [d for d in os.listdir(box)]
    assert len(archived) == 1 and archived[0].startswith(name + "__")
    assert out["archived"] == archived[0]
    assert _snapshot(os.path.join(box, archived[0])) == before, "归档内容必须逐字节一致"
    assert any(a[0] == "evolution_ignored" for a in evo["audits"]), "忽略必须留审计"


def test_ignored_dir_not_listed_as_proposal_but_exposed(evo):
    name = _make(evo)
    api_server._evolution_ignore(name)

    data = api_server._evolutions()
    assert [e["name"] for e in data["evolutions"]] == [], "归档箱不得被当成提案展示"
    assert len(data["ignored"]) == 1
    assert data["ignored"][0]["origin"] == name


def test_restore_brings_it_back_intact(evo):
    name = _make(evo)
    branch = os.path.join(evo["dir"], name)
    before = _snapshot(branch)
    _, err = api_server._evolution_ignore(name)
    archived = os.listdir(os.path.join(evo["dir"], "_ignored"))[0]

    out, err = api_server._evolution_restore(archived)
    assert err is None and out["ok"] is True
    assert out["name"] == name, "应恢复为原名"
    assert _snapshot(branch) == before, "恢复内容必须逐字节一致"
    assert os.listdir(os.path.join(evo["dir"], "_ignored")) == []
    assert any(a[0] == "evolution_restored" for a in evo["audits"])
    assert [e["name"] for e in api_server._evolutions()["evolutions"]] == [name]


def test_restore_never_overwrites_occupied_slot(evo):
    name = _make(evo)
    api_server._evolution_ignore(name)
    archived = os.listdir(os.path.join(evo["dir"], "_ignored"))[0]
    # 原位置被占用（同名目录重新出现）
    os.makedirs(os.path.join(evo["dir"], name), exist_ok=True)
    with open(os.path.join(evo["dir"], name, "占用.txt"), "w", encoding="utf-8") as f:
        f.write("别覆盖我")

    out, err = api_server._evolution_restore(archived)
    assert err is None
    assert out["name"] != name and "__restored_" in out["name"], "冲突时应另起名字，绝不覆盖"
    assert os.path.isfile(os.path.join(evo["dir"], name, "占用.txt")), "占用者必须完好"
    assert os.path.isdir(os.path.join(evo["dir"], out["name"]))


def test_restore_unknown_archive(evo):
    out, err = api_server._evolution_restore("no_such_archive__20260101_000000")
    assert out is None and "归档不存在" in err
    out, err = api_server._evolution_restore("../evil")
    assert out is None and "非法归档名" in err


def test_box_name_is_never_a_proposal(evo):
    """归档箱不能被当作提案采纳/忽略/查看（否则它会被自己吃掉）。"""
    os.makedirs(os.path.join(evo["dir"], "_ignored"), exist_ok=True)
    for fn in (api_server._evolution_ignore, api_server._evolution_restore):
        out, err = fn("_ignored")
        assert out is None and err, "%s 必须拒绝归档箱名" % fn.__name__
    assert api_server._evolution_apply("_ignored")[0] is None
    assert api_server._evolution_detail("_ignored") is None


def test_invalid_names_rejected(evo):
    for bad in ["", None, "..", "a/b", "a\\b", ".hidden", "x_applied"]:
        assert api_server._valid_evo_name(bad) is None, "应拒绝：%r" % bad
    assert api_server._valid_evo_name("ok_name_20260910_120000_000") == "ok_name_20260910_120000_000"


def test_ignore_missing_proposal(evo):
    out, err = api_server._evolution_ignore("not_there_20260101_000000")
    assert out is None and "提案不存在" in err
