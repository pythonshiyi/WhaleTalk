"""前端构建的 Node/npm 定位回归。

背景：nvm-windows 的「当前版本」符号链接（C:\\Program Files\\nodejs）缺失/失效时，
`node` 不在 PATH——仅凭 `npm.cmd` 会报「'node' 不是内部或外部命令」，前端无法构建。
`resolve_node_env()` 从 nvm 各版本目录/常见位置探测 node.exe 并注入子进程 PATH。
"""
import os

import web_app


def test_candidate_node_dirs_only_return_dirs_with_node_exe():
    for d in web_app._candidate_node_dirs():
        assert os.path.isfile(os.path.join(d, "node.exe")), d


def test_resolve_node_env_returns_npm_and_env():
    npm, env = web_app.resolve_node_env()
    assert isinstance(npm, str) and npm
    assert isinstance(env, dict)
    assert "PATH" in env
    # 若本机探测到含 node.exe 的目录，应已前置进子进程 PATH
    dirs = web_app._candidate_node_dirs()
    if dirs:
        assert dirs[0] in env["PATH"]
