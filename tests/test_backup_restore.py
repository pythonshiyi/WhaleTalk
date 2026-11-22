"""备份恢复：只恢复数据/配置、拒绝路径穿越、恢复前自动备份。"""
import json
import os
import zipfile

import backup


def _write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def test_restore_data_scope_keeps_code_and_backs_up_first(tmp_path, monkeypatch):
    base = tmp_path / "app"
    bdir = tmp_path / "backups"
    base.mkdir()
    _write(str(base / "api_server.py"), "print('old')\n")
    _write(str(base / "data" / "foo.txt"), "old-data")
    _write(str(base / "config.json"), json.dumps({"model": "old"}))

    monkeypatch.setattr(backup, "BASE_DIR", str(base))
    monkeypatch.setattr(backup, "BACKUP_DIR", str(bdir))
    bdir.mkdir()

    zpath = bdir / "WhaleTalk_vtest.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("data/foo.txt", "new-data")
        zf.writestr("config.json", json.dumps({"model": "new"}))
        zf.writestr("api_server.py", "print('new')\n")
        zf.writestr("../outside.txt", "must-not-escape")

    res = backup.restore_backup("WhaleTalk_vtest.zip", scope="data", base_dir=str(base))
    assert res["ok"] is True
    assert res["restored"] >= 2
    assert res["requires_restart"] is True
    assert (base / "data" / "foo.txt").read_text(encoding="utf-8") == "new-data"
    assert json.loads((base / "config.json").read_text(encoding="utf-8"))["model"] == "new"
    assert (base / "api_server.py").read_text(encoding="utf-8") == "print('old')\n", "data 范围不得覆盖程序"
    assert res["pre_backup"].startswith("WhaleTalk_v")
    assert not (tmp_path / "outside.txt").exists()


def test_restore_all_overwrites_code(tmp_path, monkeypatch):
    base = tmp_path / "app"
    bdir = tmp_path / "backups"
    base.mkdir()
    _write(str(base / "api_server.py"), "old\n")
    _write(str(base / "data" / "foo.txt"), "old")
    monkeypatch.setattr(backup, "BASE_DIR", str(base))
    monkeypatch.setattr(backup, "BACKUP_DIR", str(bdir))
    bdir.mkdir()
    with zipfile.ZipFile(bdir / "WhaleTalk_vfull.zip", "w") as zf:
        zf.writestr("api_server.py", "new\n")
        zf.writestr("data/foo.txt", "new")
    res = backup.restore_backup("WhaleTalk_vfull.zip", scope="all", base_dir=str(base))
    assert res["ok"] is True
    assert (base / "api_server.py").read_text(encoding="utf-8") == "new\n"
