"""pytest 公共夹具：项目根入 sys.path + 权限模块以临时目录初始化。

说明：deepseek_client 的进化闸函数（_evolve_compile/_evolve_smoke/_evolve_tests）
与 create_evolution/self_evolve 均依赖 permissions 模块与项目根，因此必须在导入
被测模块前完成初始化，并关闭审计写盘避免测试噪声。
"""
import os
import sys
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(scope="session", autouse=True)
def _bootstrap():
    import permissions

    td = tempfile.mkdtemp(prefix="wt_perms_")
    permissions.init(
        os.path.join(td, "config.json"),
        os.path.join(td, "workspace"),
    )
    permissions.set_audit_enabled(False)
    yield


@pytest.fixture
def isolate_patterns(tmp_path, monkeypatch):
    """把成功模式库指到临时目录，避免测试写入生产 data/patterns.json。

    历史事故：patterns.json 曾出现 8 条 tool0–tool7（args 为空、result 恒 "ok"、
    时间戳全相同），占满 20 条中的 8 条窗口——是某个测试/冒烟脚本直接调记账函数
    留下的。现在记账侧有三条准入（空参数 / 无信息量结果 / toolN 名）+ 注入侧按
    args 过滤，即使写入也不会污染注入；但**新测试仍应主动隔离**，不要把赌注压在
    下游过滤上。
    """
    import api_server as A

    target = tmp_path / "patterns.json"
    monkeypatch.setattr(A, "PATTERNS_PATH", str(target))
    return str(target)
