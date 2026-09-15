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


@pytest.fixture(autouse=True)
def _isolate_brain(tmp_path, monkeypatch):
    """把「大脑」数据目录重定向到临时目录。

    回归测试里 write_memory/update_memory 等会经 `_brain_sync_memory` 写入
    `brainkit.BRAIN_DIR`（默认 = 仓库级 `brain/`）。此前只隔离了 `dc.MEMORY_FILE`，
    导致测试夹具（"这是一条偏好"/"去重测试条目"…）被写进**真实大脑记忆库**——
    实测污染过 9 条。此夹具统一隔离，任何测试都不得触碰真实 `brain/`。
    """
    try:
        import brainkit as bk
    except Exception:
        return
    base = tmp_path / "brain"
    for name, rel in (
        ("BRAIN_DIR", ""),
        ("MEMORIES_DIR", "memories"),
        ("THINKING_DIR", "thinking_log"),
        ("ARCHIVE_DIR", "archive"),
        ("KEYS_DIR", ".keys"),
        ("LINEAGE_FILE", ".lineage.json"),
        ("MERGE_LOG_FILE", "merge_log.json"),
        ("MERGE_CONFLICT_FILE", "merge_conflicts.json"),
        ("MEMORY_JSONL", "memories/memory.jsonl"),
    ):
        monkeypatch.setattr(bk, name, (base / rel) if rel else base, raising=False)
    yield
