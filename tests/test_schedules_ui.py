"""定时任务：稳定 id、启停字段、上次结果回写。"""
import time

import api_server


def test_schedule_save_get_and_mark_result(tmp_path, monkeypatch):
    path = str(tmp_path / "schedules.json")
    monkeypatch.setattr(api_server, "SCHEDULES_PATH", path)
    res, err = api_server._schedules_save({"schedules": [{
        "enabled": True, "action": "message", "text": "hello", "name": "测试",
        "time": "09:00",
    }]})
    assert err is None and res["ok"] is True
    items = api_server._schedules_get()["schedules"]
    assert len(items) == 1
    sid = items[0]["id"]
    assert sid.startswith("sch_")

    api_server._schedule_mark_result(sid, status="error", result="boom", started=time.time() - 1)
    items = api_server._schedules_get()["schedules"]
    assert items[0]["last_status"] == "error"
    assert items[0]["last_result"] == "boom"
    assert items[0]["last_duration"] >= 0

    # 保存列表时保留 id 与上次结果（前端停用/启用不会丢状态）
    api_server._schedules_save({"schedules": [{**items[0], "enabled": False}]})
    items = api_server._schedules_get()["schedules"]
    assert items[0]["id"] == sid
    assert items[0]["enabled"] is False
    assert items[0]["last_status"] == "error"
