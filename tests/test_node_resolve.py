"""前端构建的 Node/npm 定位回归。

背景：nvm-windows 的「当前版本」符号链接（C:\\Program Files\\nodejs）缺失/失效时，
`node` 不在 PATH——仅凭 `npm.cmd` 会报「'node' 不是内部或外部命令」，前端无法构建。
`resolve_node_env()` 从 nvm 各版本目录/常见位置探测 node.exe 并注入子进程 PATH。
"""
import os

import pytest

import web_app


def test_candidate_node_dirs_only_return_dirs_with_node_exe():
    for d in web_app._candidate_node_dirs():
        assert os.path.isfile(os.path.join(d, "node.exe")), d


def test_resolve_node_env_returns_npm_and_env():
    npm, env = web_app.resolve_node_env()
    assert isinstance(npm, str) and npm
    assert isinstance(env, dict)
    assert "PATH" in env


def test_resolve_node_env_prefers_path_when_node_available(monkeypatch):
    """node/npm 已在 PATH 上时走快路径：不改写 PATH，直接用 PATH 上的 npm。

    该分支此前无锁定——旧断言在「本机装有 nvm、node 也在 PATH」时必然失败：
    快路径提前返回、未前置探测目录，断言却拿探测目录去比对未改写的 PATH。
    """
    monkeypatch.setattr(web_app.shutil, "which", lambda name, *a, **kw: "fake/" + name)
    before = os.environ.get("PATH", "")
    npm, env = web_app.resolve_node_env()
    assert npm and "npm" in npm, npm
    assert env["PATH"] == before, "快路径不应改写子进程 PATH"


def test_resolve_node_env_prepends_discovered_dir_when_node_missing(monkeypatch):
    """node 不在 PATH 时，第一个探测到的目录必须前置进子进程 PATH（nvm 兜底）。"""
    dirs = web_app._candidate_node_dirs()
    if not dirs:
        pytest.skip("本机未探测到任何含 node.exe 的目录")
    monkeypatch.setattr(web_app.shutil, "which", lambda name, *a, **kw: None)
    _, env = web_app.resolve_node_env()
    assert dirs[0] in env["PATH"].split(os.pathsep), "探测目录未前置进 PATH"
