"""②d 跨设备同步回归：sync-status / sync-pull 的版本对比与来源识别。"""
import json

import brain_api

LOCAL = [{"name": "brain_v4.whale", "version": 4, "size_kb": 10.0, "mtime": "2026-01-01 00:00"}]


def _prepare(tmp_path, monkeypatch, machine="MACHINE-A"):
    monkeypatch.setattr(brain_api.bk, "load_manifest", lambda: {"brain_id": "testbrain"})
    monkeypatch.setattr(brain_api, "_snapshot_list", lambda: LOCAL)
    monkeypatch.setattr(brain_api, "_sync_machine", lambda: machine)
    base = tmp_path / "testbrain"
    base.mkdir(parents=True)
    (base / "brain_v5.whale").write_bytes(b"remotedata")
    (base / "sync_index.json").write_text(json.dumps({
        "MACHINE-A": {"version": 4, "ts": "2026-01-01T00:00:00", "file": "brain_v4.whale"},
        "MACHINE-B": {"version": 5, "ts": "2026-01-02T00:00:00", "file": "brain_v5.whale"},
    }), encoding="utf-8")
    return str(tmp_path)


def test_sync_status_detects_newer_remote(tmp_path, monkeypatch):
    d = _prepare(tmp_path, monkeypatch)
    st = brain_api.sync_status(d)
    assert st["ok"]
    data = st["data"]
    assert data["local_version"] == 4
    assert data["machine"] == "MACHINE-A"
    assert data["brain_id"] == "testbrain"
    selfs = [r for r in data["remote"] if r["is_self"]]
    assert selfs and selfs[0]["version"] == 4
    assert len(data["newer"]) == 1
    assert data["newer"][0]["machine"] == "MACHINE-B"


def test_sync_pull_returns_remote_path(tmp_path, monkeypatch):
    d = _prepare(tmp_path, monkeypatch)
    r = brain_api.sync_pull(d)
    assert r["ok"]
    assert r["data"]["machine"] == "MACHINE-B"
    assert r["data"]["snapshot"].endswith("brain_v5.whale")


def test_sync_status_requires_dir():
    assert brain_api.sync_status("")["ok"] is False
    assert brain_api.sync_pull("")["ok"] is False


def test_sync_pull_nothing_new(tmp_path, monkeypatch):
    d = _prepare(tmp_path, monkeypatch)
    assert brain_api.sync_pull(d, machine="MACHINE-A")["ok"] is False  # 自己的快照不可拉取
