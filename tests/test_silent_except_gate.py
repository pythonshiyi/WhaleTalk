"""静默吞异常门禁（tools/check_silent_except.py）的自我回归。

门禁自己也要被门禁：如果扫描器认不出「新增的一处 pass」，那它只是在提供虚假安全感。
本组用例用临时目录造样本，验证「能抓新增 / 能放行豁免 / 存量基线可用」三件事，
并断言仓库当前基线确实覆盖全部存量（即门禁此刻是绿的）。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import check_silent_except as gate  # noqa: E402


SAMPLE = '''
import os


def read_cfg(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        pass


def read_cfg_exempt(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:  # noqa: silent-except: 配置文件不存在是正常情况
        pass


def read_cfg_visible(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""
'''


def _write(tmp_path, text=SAMPLE):
    p = tmp_path / "mod.py"
    p.write_text(text, encoding="utf-8")
    return p


def test_scan_finds_silent_handler(tmp_path):
    _write(tmp_path)
    found = gate.scan(tmp_path)
    names = {e["func"] for e in found}
    assert names == {"read_cfg"}, names       # 豁免的不算、有语句的不算
    assert found[0]["file"] == "mod.py"


def test_scan_honours_noqa(tmp_path):
    _write(tmp_path, SAMPLE.replace("# noqa: silent-except: 配置文件不存在是正常情况", ""))
    names = {e["func"] for e in gate.scan(tmp_path)}
    assert names == {"read_cfg", "read_cfg_exempt"}


def test_new_silent_handler_is_reported_as_new(tmp_path):
    _write(tmp_path)
    entries = gate.scan(tmp_path)
    new, fixed = gate.diff(entries, baseline=set())
    assert len(new) == 1 and not fixed


def test_baseline_covers_existing_silent_handler(tmp_path):
    _write(tmp_path)
    entries = gate.scan(tmp_path)
    baseline = {e["fingerprint"] for e in entries}
    new, fixed = gate.diff(entries, baseline)
    assert not new and not fixed


def test_duplicate_handlers_get_distinct_fingerprints(tmp_path):
    """同一函数里两处「长得一样」的静默点必须各自计数，否则多写一处也不会红。"""
    text = '''
def twice():
    try:
        a = 1
    except ValueError:
        pass
    try:
        a = 1
    except ValueError:
        pass
'''
    _write(tmp_path, text)
    entries = gate.scan(tmp_path)
    assert len(entries) == 2
    assert len({e["fingerprint"] for e in entries}) == 2


def test_repo_baseline_matches_current_scan():
    """仓库当前状态必须过门禁（改了存量又忘了更新基线时，这条会红）。"""
    entries = gate.scan(REPO)
    baseline = gate.load_baseline()
    assert baseline, "基线文件缺失"
    new, _fixed = gate.diff(entries, baseline)
    assert not new, f"存在未登记的新静默吞异常：{[(e['file'], e['line']) for e in new][:5]}"


def test_baseline_file_is_sorted_and_unique():
    lines = [ln.strip() for ln in gate.load_baseline.__globals__["BASELINE"]
             .read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    assert lines == sorted(set(lines))
