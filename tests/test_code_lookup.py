"""code_lookup 代码结构定位工具测试（AST 级，只读）。"""
import deepseek_client as dsc

SAMPLE = '''\
import os
import json as _json

from config_defaults import VERSION

class Engine:
    """示例引擎"""
    def run(self, task):
        return do_work(task)

def do_work(task):
    return _json.dumps({"task": task})

def helper():
    global _cache
    _cache = os.getcwd()

result = do_work("x")
engine = Engine()
'''


def _write_sample(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE, encoding="utf-8")
    return str(tmp_path)


def test_lookup_def(tmp_path):
    base = _write_sample(tmp_path)
    r = dsc.code_lookup(base, "do_work", "def")
    assert "def do_work(task)" in r and "sample.py" in r, r


def test_lookup_class(tmp_path):
    base = _write_sample(tmp_path)
    r = dsc.code_lookup(base, "Engine", "class")
    assert "class Engine" in r and "sample.py:6" in r, r


def test_lookup_call_points(tmp_path):
    base = _write_sample(tmp_path)
    r = dsc.code_lookup(base, "do_work", "call")
    assert "call do_work" in r and "sample.py" in r, r
    assert "do_work(task)" not in r.split("：")[-1], "定义行不应混入调用结果"


def test_lookup_import(tmp_path):
    base = _write_sample(tmp_path)
    r = dsc.code_lookup(base, "VERSION", "import")
    assert "import VERSION" in r, r


def test_lookup_import_alias(tmp_path):
    base = _write_sample(tmp_path)
    r = dsc.code_lookup(base, "_json", "import")
    assert "import json as _json" in r, r


def test_lookup_missing_symbol(tmp_path):
    base = _write_sample(tmp_path)
    r = dsc.code_lookup(base, "no_such_symbol", "def")
    assert "未找到" in r, r


def test_lookup_invalid_kind(tmp_path):
    base = _write_sample(tmp_path)
    r = dsc.code_lookup(base, "x", "banana")
    assert "错误" in r, r


def test_lookup_empty_symbol(tmp_path):
    base = _write_sample(tmp_path)
    r = dsc.code_lookup(base, "", "def")
    assert "错误" in r, r
