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
