"""成功模式库清洗工具（tools/clean_patterns.py）。

锁定的判断：**清洗的判据是"这条是不是可复用的调用模式"，不是"有没有 args_sig"**。
按字段格式删会误伤——write_file / list_dir / get_weather 同样没有 args_sig（写入时
还没这套逻辑），却是有复用价值的参数化调用。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import clean_patterns as cp  # noqa: E402


def _e(tool, args="a", result="r", **kw):
    d = {"tool": tool, "args": args, "result": result, "ts": "2026-10-19 13:00:00"}
    d.update(kw)
    return d


# ── 删除规则 ────────────────────────────────────────────────────────

def test_generic_escape_hatch_is_dropped():
    """run_python 的 args 是一次性脚本代码，不是可复用的调用模式。"""
    for tool in ("run_python", "run_command", "run_shell", "run_powershell"):
        assert cp.classify(_e(tool))[0] == "drop", tool


def test_noise_results_are_dropped():
    for r in ("ok", "OK", "success", "true", "", "none"):
        assert cp.classify(_e("read_file", result=r))[0] == "drop", r


def test_test_residue_names_are_dropped():
    assert cp.classify(_e("tool0"))[0] == "drop"
    assert cp.classify(_e("tool7"))[0] == "drop"


def test_empty_args_dropped():
    assert cp.classify(_e("read_file", args=""))[0] == "drop"


def test_non_dict_dropped():
    assert cp.classify("x")[0] == "drop"
    assert cp.classify(None)[0] == "drop"


# ── 保留 + 迁移（这是与"按格式删"的关键差别）────────────────────────

def test_valuable_legacy_entry_is_migrated_not_dropped():
    """没有 args_sig 但有价值的参数化调用 → 补签名，不能删。"""
    action, _why = cp.classify(_e("write_file", "{'path':'/a'}", "已写入 19542 字节"))
    assert action == "migrate"


def test_migrate_adds_args_sig():
    kept, detail = cp.plan([_e("list_dir", "{'path':'/w'}", "FILE a.md 1B")])
    assert len(kept) == 1
    assert kept[0].get("args_sig"), "迁移必须补上 args_sig"
    assert detail[0][0] == "migrate"


def test_new_format_entry_kept_as_is():
    action, _why = cp.classify(_e("read_file", "{'path':'/a'}", "内容", args_sig="abc"))
    assert action == "keep"


def test_plan_preserves_valuable_and_drops_noise():
    entries = [
        _e("run_python", "{'code':'import os'}", "退出码 0"),
        _e("get_weather", "{'location':'上海'}", "上海天气 Cloudy 25°C"),
        _e("image_process", "{'ops':'resize'}", "已处理图像", args_sig="x"),
    ]
    kept, detail = cp.plan(entries)
    assert [x["tool"] for x in kept] == ["get_weather", "image_process"]
    assert sum(1 for d in detail if d[0] == "drop") == 1


def test_plan_on_empty_and_garbage():
    assert cp.plan([]) == ([], [])
    kept, _ = cp.plan([None, "x", 42])
    assert kept == []


def test_idempotent():
    """跑两遍结果不变——可重复执行是这个工具的前提。"""
    entries = [_e("read_file", "{'path':'/a'}", "内容"), _e("run_python", "c", "r")]
    once, _ = cp.plan(entries)
    twice, _ = cp.plan(once)
    assert [x.get("args_sig") for x in once] == [x.get("args_sig") for x in twice]
    assert len(once) == len(twice)


# ── CLI 行为 ────────────────────────────────────────────────────────

def test_cli_dry_run_does_not_write(tmp_path):
    p = tmp_path / "p.json"
    import json
    p.write_text(json.dumps([_e("run_python", "c", "r")]), encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    rc = cp.main(["--path", str(p)])
    assert rc == 0
    assert p.read_text(encoding="utf-8") == before, "默认必须只报告不动手"


def test_cli_yes_writes_and_backs_up(tmp_path):
    import json
    p = tmp_path / "p.json"
    p.write_text(json.dumps([_e("run_python", "c", "r"), _e("read_file", "{'path':'/a'}", "内容")]),
                 encoding="utf-8")
    assert cp.main(["--path", str(p), "--yes"]) == 0
    assert json.loads(p.read_text(encoding="utf-8")) == [
        {**_e("read_file", "{'path':'/a'}", "内容"), "args_sig": cp._sig("{'path':'/a'}")}
    ]
    baks = list(tmp_path.glob("p.json.bak_*"))
    assert baks, "执行前必须备份"


def test_cli_missing_file_is_ok(tmp_path):
    assert cp.main(["--path", str(tmp_path / "nope.json")]) == 0


def test_cli_rejects_non_list(tmp_path):
    p = tmp_path / "p.json"
    p.write_text('{"not":"a list"}', encoding="utf-8")
    assert cp.main(["--path", str(p), "--yes"]) == 1
