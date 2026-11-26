"""read_file 相对路径必须相对工作区解析（P0 评测踩到的真实不一致）。

write_file 一直用 permissions.resolve(path)，read_file 此前把原始相对路径交给
open()，按进程 cwd（仓库根）解析 → 刚写进工作区的文件读不到。此用例锁死行为。
"""

from __future__ import annotations

import os

import permissions


def test_read_file_relative_to_workspace():
    workspace = permissions.WORKSPACE_DIR
    assert workspace, "conftest 应已初始化 permissions"
    os.makedirs(workspace, exist_ok=True)
    target = os.path.join(workspace, "rel_read.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write("relative-ok")

    # 先 import deepseek_client：agent_tools 的域模块有导入顺序契约，
    # 直接 import agent_tools.tool_files 会触发重复注册（见 toolkit.py）。
    import deepseek_client as dc

    out = dc.read_file("rel_read.txt")
    assert "relative-ok" in out
    assert not out.startswith("错误")


def test_read_file_relative_missing_reports_error():
    import deepseek_client as dc

    out = dc.read_file("definitely_missing_relative.txt")
    assert out.startswith("错误")
