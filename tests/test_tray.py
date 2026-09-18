"""托盘（任务栏图标）纯函数回归：状态摘要 / tooltip / token 格式化。

不依赖 pystray/Pillow —— TrayController 才用到它们，纯函数可独立测试。
"""
import tray


def test_fmt_tokens():
    assert tray.fmt_tokens(0) == "0"
    assert tray.fmt_tokens(999) == "999"
    assert tray.fmt_tokens(3400) == "3.4k"
    assert tray.fmt_tokens(1_200_000) == "1.2M"
    assert tray.fmt_tokens(None) == "0"
    assert tray.fmt_tokens("bad") == "0"


def test_status_lines_empty():
    assert tray.status_lines(None) == []
    assert tray.status_lines({}) == []  # 空快照不产出状态行


def test_status_lines_content():
    st = {
        "full_auto": True, "model": "deepseek-flash",
        "monthly_cost": 12.5, "monthly_budget": 100,
        "usage_total": {"prompt": 1_200_000, "completion": 3400},
        "peak_hour": True,
        "trust": {"state": "unconfirmed", "pending": 1, "alerts": 0},
        "degrade": {"critical": 2},
    }
    d = dict(tray.status_lines(st))
    assert d["模式"] == "任务模式"
    assert d["模型"] == "deepseek-flash"
    assert d["本月"].startswith("¥12.50")
    assert d["累计"] == "↑1.2M ↓3.4k"
    assert d["时段"] == "高峰"
    assert "待确认 1" in d["内核"]
    assert d["降级"] == "2"


def test_status_lines_dialog_no_budget():
    d = dict(tray.status_lines({"full_auto": False, "model": "m", "monthly_cost": 0}))
    assert d["模式"] == "对话模式"
    assert d["本月"] == "¥0.00"
    assert "内核" not in d and "降级" not in d


def test_tooltip_text():
    t = tray.tooltip_text({"model": "deepseek-flash"}, 8745)
    assert "运行中" in t and "127.0.0.1:8745" in t and "deepseek-flash" in t
    assert "运行中" in tray.tooltip_text(None, None)


def test_trust_unconfirmed():
    assert tray.trust_unconfirmed({"trust": {"state": "unconfirmed"}}) is True
    assert tray.trust_unconfirmed({"trust": {"state": "ok"}}) is False
    assert tray.trust_unconfirmed(None) is False


def test_menu_signature_stable_and_sensitive():
    """菜单指纹：同内容稳定，任一可见字段变化即变——用于「仅变化时重建菜单」的门控。"""
    st = {"full_auto": False, "model": "m", "monthly_cost": 1.0}
    toggles = (False, True, True, False)
    base = tray.menu_signature(8745, "3.11.3", st, toggles)
    assert base == tray.menu_signature(8745, "3.11.3", dict(st), tuple(toggles))  # 同内容稳定
    assert base != tray.menu_signature(8745, "3.11.3", {"full_auto": False, "model": "m2"}, toggles)  # 模型变化
    assert base != tray.menu_signature(8745, "3.11.3", st, (True, True, True, False))  # 勾选态变化
    assert base != tray.menu_signature(9999, "3.11.3", st, toggles)  # 端口变化
    assert base != tray.menu_signature(8745, "9.9.9", st, toggles)  # 版本变化
    assert base != tray.menu_signature(8745, "3.11.3", {}, toggles)  # 空快照（状态行变化）


def test_notify_without_icon_is_safe():
    # 无托盘（未 run）时不得抛异常
    assert tray.notify("t", "m") is False
