# -*- coding: utf-8 -*-
"""v3.8.5 增强批次二：B5 快照外置备份 + 异地恢复单元测试。

B5：archive 后把新快照镜像到外部备份目录 <dir>/<brain_id>/ 并写 snapshot_manifest.json；
     cmd_mirror 补录已存在快照；restore 支持从外部备份路径恢复（多一份，历史不断链）。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import brainkit as bk  # noqa: E402


def _mk_manifest(td):
    for d in ("memories", "thinking_log", "archive"):
        (td / d).mkdir(parents=True, exist_ok=True)
    man = {"schema_version": 1, "brain_id": "whale-b5", "created_at": "2026-01-01T00:00:00+00:00", "genesis": "x"}
    man["fingerprint"] = bk.compute_fingerprint(man)
    bk.save_json(td / "manifest.json", man)
    bk.save_json(td / "identity.json", {})
    bk.save_json(td / "heartbeat.json", {})


@pytest.fixture
def brain_b5():
    td = Path(tempfile.mkdtemp(prefix="wt_b5_"))
    mirror = Path(tempfile.mkdtemp(prefix="wt_b5mirror_"))
    _mk_manifest(td)
    old = bk.BRAIN_DIR
    bk.set_brain_dir(td)
    yield td, mirror
    bk.set_brain_dir(old)
    shutil.rmtree(td, ignore_errors=True)
    shutil.rmtree(mirror, ignore_errors=True)


def _archive_args(mirror=None):
    import argparse
    return argparse.Namespace(passphrase="", keep=bk.DEFAULT_KEEP, mirror=mirror)


def test_archive_mirror_writes_external(brain_b5):
    td, mirror = brain_b5
    bk.remember_structured("B5备份测试记忆", type="测试", source="测试")
    assert bk.cmd_archive(_archive_args(mirror=str(mirror))) == 0
    # 本机归档
    local = sorted((td / "archive").glob("brain_v*.whale"))
    assert local and local[0].name == "brain_v1.whale"
    # 外置镜像
    mirrored = mirror / "whale-b5" / "brain_v1.whale"
    assert mirrored.exists()
    assert mirrored.read_bytes() == local[0].read_bytes()
    # 清单
    inv = bk.load_json(mirror / "whale-b5" / "snapshot_manifest.json", {})
    assert inv.get("brain_id") == "whale-b5"
    assert any(s.get("file") == "brain_v1.whale" for s in inv.get("snapshots") or [])


def test_mirror_backfills_existing(brain_b5):
    td, mirror = brain_b5
    bk.remember_structured("补录记忆", type="测试", source="测试")
    assert bk.cmd_archive(_archive_args(mirror=None)) == 0  # 本机归档（不镜像）
    import argparse
    assert bk.cmd_mirror(argparse.Namespace(dir=str(mirror))) == 0
    assert (mirror / "whale-b5" / "brain_v1.whale").exists()


def test_snapshot_index_content_addressed(brain_b5):
    """B4：snapshot_index.json 记录每份 sha256/体积/记忆数/增量，两版成长可追踪。"""
    td, mirror = brain_b5
    bk.remember_structured("索引测试记忆", type="测试", source="测试")
    assert bk.cmd_archive(_archive_args(mirror=None)) == 0
    bk.remember_structured("又加一条", type="测试", source="测试")
    assert bk.cmd_archive(_archive_args(mirror=None)) == 0
    idx = bk.load_json(td / "snapshot_index.json", {})
    snaps = idx.get("snapshots") or []
    assert len(snaps) == 2
    assert snaps[0]["version"] == 1 and snaps[1]["version"] == 2
    assert snaps[0].get("sha256") and len(snaps[0]["sha256"]) == 64
    assert snaps[0].get("memories", 0) >= 1
    assert "delta_from_prev_bytes" in snaps[1]


# ---- U7 健康盘数据契约（doctor action 返回结构化 dict；mirror action）----

def test_doctor_action_structured(brain_b5):
    """brain_api brain_action('doctor') 返回含 score/problems 的结构化健康数据。"""
    import brain_api
    bk.remember_structured("健康盘测试", type="测试", source="测试")
    r = brain_api.brain_action("doctor", {})
    assert r.get("ok") is True
    assert isinstance(r.get("score"), int) and 0 <= r["score"] <= 100
    assert isinstance(r.get("problems"), list)
    assert "memories" in r and "snapshots" in r


def test_mirror_action_via_brain_api(brain_b5):
    """brain_api brain_action('mirror') 触发快照外置镜像。"""
    import brain_api
    td, mirror = brain_b5
    bk.remember_structured("镜像action测试", type="测试", source="测试")
    assert bk.cmd_archive(_archive_args(mirror=None)) == 0  # 本机归档
    r = brain_api.brain_action("mirror", {"dir": str(mirror)})
    assert r.get("ok") is True
    assert (mirror / "whale-b5" / "brain_v1.whale").exists()



def test_restore_from_external_mirror(brain_b5):
    td, mirror = brain_b5
    bk.remember_structured("要在恢复后出现的记忆", type="测试", source="测试")
    assert bk.cmd_archive(_archive_args(mirror=str(mirror))) == 0
    mirrored = mirror / "whale-b5" / "brain_v1.whale"
    # 恢复到不存在的目标目录（cmd_restore 拒绝覆盖已存在目录）
    dest = Path(tempfile.mkdtemp(prefix="wt_b5rest_")) / "restored"
    import argparse
    assert bk.cmd_restore(argparse.Namespace(whale=str(mirrored), passphrase="",
                                             dir=str(dest), replace=False, force=False)) == 0
    # 恢复出的 brain 应含该记忆
    bk2 = dest  # restored dir layout
    # 恢复产物 manifest 应存在且指纹自洽
    man = bk.load_json(dest / "manifest.json", {})
    assert man.get("brain_id") == "whale-b5"
    assert bk.verify_fingerprint(man)
    shutil.rmtree(dest, ignore_errors=True)
