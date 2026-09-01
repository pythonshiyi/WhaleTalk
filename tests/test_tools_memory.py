# -*- coding: utf-8 -*-
"""记忆工具执行路径测试（TECH_NOTES TODO-3：按领域扩充分子级用例）。

覆盖 write_memory / read_memory / update_memory / delete_memory /
query_memory_graph 的真实执行路径：写读往返、去重、空值拒绝、图谱、
过滤、总开关。存储隔离：每个用例注入独立临时 MEMORY_FILE。
"""
import os

import pytest

import deepseek_client as dc


@pytest.fixture(autouse=True)
def _mem(tmp_path):
    """隔离记忆存储：每个用例独立的 memory.json + 恢复总开关。"""
    dc.MEMORY_FILE = str(tmp_path / "memory.json")
    saved = dc.MEMORY_ENABLED
    dc.MEMORY_ENABLED = True
    yield
    dc.MEMORY_ENABLED = saved
    dc.MEMORY_FILE = None


def _facts():
    import json

    if not dc.MEMORY_FILE or not os.path.exists(dc.MEMORY_FILE):
        return []
    with open(dc.MEMORY_FILE, encoding="utf-8") as f:
        return json.load(f).get("facts") or []


def test_write_then_read_roundtrip():
    r = dc.write_memory("记得每周五备份数据库")
    assert "已写入" in r
    out = dc.read_memory("备份数据库")
    assert "每周五备份数据库" in out


def test_write_dedup():
    assert "已写入" in dc.write_memory("去重测试条目")
    r = dc.write_memory("去重测试条目")
    assert "已存在" in r
    assert len(_facts()) == 1


def test_write_empty_rejected():
    r = dc.write_memory("")
    assert "错误" in r and "空" in r


def test_write_entities_and_graph_query():
    dc.write_memory("张三负责项目A的联调", entities="张三,项目A",
                    relations="张三-负责-项目A")
    out = dc.query_memory_graph(entity="张三")
    assert "项目A" in out
    # 知识图谱应能通过关系方向查到
    rel = dc.query_memory_graph(entity="张三", relation="负责")
    assert "项目A" in rel


def test_update_memory():
    dc.write_memory("旧版本结论")
    r = dc.update_memory("旧版本结论", "新版本结论")
    assert "已修改" in r
    assert "新版本结论" in dc.read_memory("版本结论")
    assert "旧版本结论" not in dc.read_memory("版本结论")


def test_delete_memory_by_keyword():
    dc.write_memory("要删除的记忆条目")
    r = dc.delete_memory("要删除的记忆条目")
    assert "已删除" in r
    assert "（无匹配记忆）" in dc.read_memory("要删除的")


def test_read_filter_by_type():
    dc.write_memory("这是一条偏好", type="偏好")
    dc.write_memory("这是一条事实", type="事实")
    out = dc.read_memory("这是", type="偏好")
    assert "偏好" in out
    assert "事实" not in out


def test_memory_disabled_flag():
    dc.MEMORY_ENABLED = False
    r = dc.write_memory("不应写入")
    assert "已关闭" in r
    assert len(_facts()) == 0


def test_write_persists_to_disk():
    dc.write_memory("磁盘持久化条目")
    assert len(_facts()) == 1
    entry = _facts()[0]
    assert entry["value"] == "磁盘持久化条目"
    assert entry["key"]
