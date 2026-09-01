# -*- coding: utf-8 -*-
"""存储执行路径测试（TECH_NOTES TODO-3：按领域扩充分子级用例）。

覆盖 task_checkpoint_save/load、secret_store（DPAPI 加密保险箱）、
kv_store（diskcache 嵌入式键值）、verify_files（产物核验）的真实执行。
存储隔离：每个用例注入独立临时路径变量。
"""
import os

import pytest

import deepseek_client as dc


@pytest.fixture(autouse=True)
def _stores(tmp_path):
    """隔离存储：注入临时 SECRETS/CHECKPOINT/KV 路径。"""
    dc.SECRETS_FILE = str(tmp_path / "secrets.json")
    dc.CHECKPOINT_FILE = str(tmp_path / "task_checkpoint.json")
    dc.KV_CACHE_DIR = str(tmp_path / "kv_cache")
    yield
    for attr in ("SECRETS_FILE", "CHECKPOINT_FILE", "KV_CACHE_DIR"):
        setattr(dc, attr, None)


def test_checkpoint_save_load_roundtrip():
    dc.task_checkpoint_save(name="报表任务", status="进行中",
                            pending=["拉数据", "算指标"])
    out = dc.task_checkpoint_load()
    assert "报表任务" in out
    assert "进行中" in out
    assert "拉数据" in out and "算指标" in out


def test_checkpoint_overwrite_same_name():
    dc.task_checkpoint_save(name="同一任务", pending=["步骤1"])
    dc.task_checkpoint_save(name="同一任务", status="已完成", pending=[])
    out = dc.task_checkpoint_load()
    assert "已完成" in out
    assert "步骤1" not in out


def test_checkpoint_load_without_file():
    assert "当前没有任务检查点" in dc.task_checkpoint_load()


def test_secret_set_get_list_delete():
    assert "已加密保存" in dc.secret_store("set", "api_key", "sk-xxx")
    assert dc.secret_store("get", "api_key") == "sk-xxx"
    assert "api_key" in dc.secret_store("list")
    assert "已删除" in dc.secret_store("delete", "api_key")
    assert "未找到" in dc.secret_store("get", "api_key")


def test_secret_requires_name_and_value():
    assert "name 必填" in dc.secret_store("set", "", "v")
    assert "value 必填" in dc.secret_store("set", "k", "")
    assert "action 仅支持" in dc.secret_store("frobnicate", "k", "v")


def test_kv_set_get_delete():
    assert "已写入" in dc.kv_store("set", "theme", "dark")
    assert dc.kv_store("get", "theme") == "key=theme: dark"
    assert "已删除" in dc.kv_store("delete", "theme")
    assert "不存在或已过期" in dc.kv_store("get", "theme")


def test_kv_keys_and_invalid_action():
    dc.kv_store("set", "a", "1")
    dc.kv_store("set", "b", "2")
    keys = dc.kv_store("keys")
    assert "a" in keys and "b" in keys
    assert "action 仅支持" in dc.kv_store("nope", "a", "1")


def test_verify_files_mixed(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("12345", encoding="utf-8")
    missing = tmp_path / "nope.txt"
    out = dc.verify_files([str(ok), str(missing)])
    assert "✅ 存在" in out
    assert "❌ 缺失" in out
    assert "5 字节" in out


def test_verify_files_empty_list():
    out = dc.verify_files([])
    assert out  # 不抛异常，返回可读信息
