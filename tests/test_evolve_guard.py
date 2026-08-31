"""进化闸函数与回滚语义测试：四层验证链各司其职，失败必须回滚、通过必须保留分支。

覆盖：
- _evolve_compile：好文件通过 / 坏语法失败 / 非 py 跳过
- _evolve_smoke：可导入模块通过 / 导入即炸失败 / test_ 文件跳过
- _evolve_tests：改动带测试只跑改动 / 改动无测试回退 tests/ 全量 / 都无则跳过
- create_evolution：分支隔离（绝不改原文件）/ 路径穿越拒绝
- self_evolve：坏补丁回滚且生产文件复原 / 好补丁保留分支且生产文件不变（合入权在用户）
"""
import os
import subprocess

import pytest

import deepseek_client as dsc


# ---------------- _evolve_compile ----------------

def test_compile_good_file(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    r = dsc._evolve_compile(str(tmp_path), ["ok.py"])
    assert r.startswith("编译通过"), r


def test_compile_bad_file(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    r = dsc._evolve_compile(str(tmp_path), ["bad.py"])
    assert r.startswith("语法编译失败") or r.startswith("错误"), r


def test_compile_skips_non_py(tmp_path):
    (tmp_path / "a.md").write_text("# hi\n", encoding="utf-8")
    r = dsc._evolve_compile(str(tmp_path), ["a.md"])
    assert r.startswith("（"), r


# ---------------- _evolve_smoke ----------------

def test_smoke_importable_module(tmp_path):
    (tmp_path / "mymod.py").write_text("VALUE = 42\n", encoding="utf-8")
    r = dsc._evolve_smoke(str(tmp_path), ["mymod.py"])
    assert r.startswith("导入通过"), r


def test_smoke_bad_module(tmp_path):
    (tmp_path / "brokenmod.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    r = dsc._evolve_smoke(str(tmp_path), ["brokenmod.py"])
    assert r.startswith("导入冒烟失败") or r.startswith("错误"), r


def test_smoke_skips_test_files(tmp_path):
    (tmp_path / "test_x.py").write_text("x = 1\n", encoding="utf-8")
    r = dsc._evolve_smoke(str(tmp_path), ["test_x.py"])
    assert r.startswith("（"), r


# ---------------- _evolve_tests ----------------

def test_tests_skip_when_nothing_available(tmp_path):
    (tmp_path / "core.py").write_text("x = 1\n", encoding="utf-8")
    r = dsc._evolve_tests(str(tmp_path), ["core.py"])
    assert r.startswith("（"), r


def test_tests_runs_changed_test_files(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("def test_bad():\n    assert 1 == 2\n", encoding="utf-8")
    r = dsc._evolve_tests(str(tmp_path), ["test_a.py"])
    assert "全部通过" in r, r  # 只跑改动中的 test_a，坏的 test_b 不应被收集


def test_tests_falls_back_to_tests_dir(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_r.py").write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
    r = dsc._evolve_tests(str(tmp_path), ["core.py"])
    assert "全部通过" in r, r


# ---------------- create_evolution ----------------

def test_create_evolution_branch_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(dsc, "EVOLUTIONS_DIR", str(tmp_path / "evolutions"))
    r = dsc.create_evolution("iso_test", [{"path": "a.py", "content": "x = 1\n"}])
    assert "提案已创建" in r, r
    branches = os.listdir(dsc.EVOLUTIONS_DIR)
    assert len(branches) == 1, branches
    branch = os.path.join(dsc.EVOLUTIONS_DIR, branches[0])
    assert os.path.exists(os.path.join(branch, "a.py"))
    assert os.path.exists(os.path.join(branch, "EVOLUTION.md"))
    assert not os.path.exists(str(tmp_path / "a.py")), "分支提案不得污染仓库根"


def test_create_evolution_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(dsc, "EVOLUTIONS_DIR", str(tmp_path / "evolutions"))
    r = dsc.create_evolution("evil", [{"path": "../evil.py", "content": "x = 1\n"}])
    assert "错误" in r or "非法" in r, r
    assert not os.path.exists(str(tmp_path / "evolutions")), "非法提案不得创建任何目录"


# ---------------- self_evolve 回滚语义 ----------------

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "tester",
    "GIT_AUTHOR_EMAIL": "tester@local",
    "GIT_COMMITTER_NAME": "tester",
    "GIT_COMMITTER_EMAIL": "tester@local",
}


def _init_git_repo(tmp_path):
    (tmp_path / "main.py").write_text("VALUE = 'old'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, env=_GIT_ENV
    )


def _evolve_branches(tmp_path):
    out = subprocess.run(
        ["git", "branch", "--list", "evolve/*"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    return out


@pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
                    reason="本机无 git，无法测试分支隔离")
def test_self_evolve_rollback_on_bad_patch(tmp_path):
    _init_git_repo(tmp_path)
    r = dsc.self_evolve(
        "bad_patch",
        [{"path": "main.py", "content": "def broken(:\n"}],
        project_dir=str(tmp_path),
    )
    assert "已回滚" in r, r
    content = (tmp_path / "main.py").read_text(encoding="utf-8")
    assert content == "VALUE = 'old'\n", "回滚后生产文件必须恢复原状"
    assert "evolve/" not in _evolve_branches(tmp_path), "失败分支必须删除"


@pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
                    reason="本机无 git，无法测试分支隔离")
def test_self_evolve_pass_keeps_branch_for_review(tmp_path):
    _init_git_repo(tmp_path)
    r = dsc.self_evolve(
        "good_patch",
        [{"path": "main.py", "content": "VALUE = 'new'\n"}],
        project_dir=str(tmp_path),
    )
    assert "进化完成" in r, r
    content = (tmp_path / "main.py").read_text(encoding="utf-8")
    assert content == "VALUE = 'old'\n", "合入权在用户：通过后生产文件应保持原值"
    assert "evolve/" in _evolve_branches(tmp_path), "通过后分支应保留供审查合入"
