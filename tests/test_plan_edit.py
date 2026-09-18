"""③b 计划编辑回归：deepseek_client._apply_plan_edits 定位与写回。"""
import json

import deepseek_client as dc


def test_apply_edits_by_index():
    tcs = [{"id": "a", "name": "read_file", "args": "{}"},
           {"id": "b", "name": "write_file", "args": "{}"}]
    dc._apply_plan_edits(tcs, [{"index": 1, "args": '{"path": "x"}'}])
    assert tcs[1]["args"] == '{"path": "x"}'
    assert tcs[0]["args"] == "{}"


def test_apply_edits_by_id_and_object_args():
    tcs = [{"id": "a", "name": "x", "args": "{}"}]
    dc._apply_plan_edits(tcs, [{"id": "a", "args": {"k": 1}}])
    assert json.loads(tcs[0]["args"])["k"] == 1


def test_apply_edits_positional_default_and_ignores_invalid():
    tcs = [{"id": "a", "args": "old"}]
    dc._apply_plan_edits(tcs, ["bad", {"args": "new"}])   # 第二项无 index → 按位置 1（越界，忽略）
    assert tcs[0]["args"] == "old"
    dc._apply_plan_edits(tcs, [{"args": "new"}])           # 位置 0
    assert tcs[0]["args"] == "new"


def test_apply_edits_empty():
    tcs = [{"id": "a", "args": "x"}]
    assert dc._apply_plan_edits(tcs, None) is tcs
    assert tcs[0]["args"] == "x"
