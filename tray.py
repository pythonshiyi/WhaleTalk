# -*- coding: utf-8 -*-
"""系统托盘（任务栏图标）控制器 —— 常驻入口的原生体验层。

职责：
- 菜单构建（状态区 / 打开 / 开关 / 维护 / 退出），开关用 **勾选态**（checked）；
- 动态状态：tooltip + 只读状态区，由注入的 status provider 定时刷新（默认 5s）；
- 托盘气泡通知（复用 notify_on_done 开关）；
- 配置读写一律经 `config_utils.mutable_config()`（绝不改共享只读配置对象）。

解耦：状态与动作通过 provider/callback 注入，本模块**不 import api_server**，
避免与 api_server 形成导入环（api_server 在需要通知时惰性 `import tray`）。
纯函数（status_lines / tooltip_text / fmt_tokens）不依赖 pystray，可单测。
"""
import os
import threading

APP_NAME = "鲸语 WhaleTalk"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_REFRESH_SECONDS = 5.0

_STATUS_PROVIDER = None  # () -> dict（通常注入 api_server._status）
_ICON = None             # pystray.Icon 实例（notify 用）
_ICON_LOCK = threading.Lock()

# 菜单是否正在显示：Windows 右键弹出菜单时 pystray 在线程里阻塞于 TrackPopupMenuEx，
# 期间**不能**重建菜单——`update_menu()` 会 DestroyMenu 正在显示的原生 HMENU，
# 导致菜单与鼠标卡死（v3.11.3 每 5s 无条件重建菜单即触发此问题）。
_MENU_OPEN = threading.Event()


# ── 纯函数（可单测）─────────────────────────────────
def fmt_tokens(n):
    """token 数 → 紧凑文本（1.2M / 3.4k / 123）。"""
    try:
        n = int(n or 0)
    except Exception:
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def status_lines(st):
    """把状态快照转成托盘只读状态区的中文文本列表（空快照返回空列表）。"""
    if not st:
        return []
    lines = []
    lines.append(("模式", "任务模式" if st.get("full_auto") else "对话模式"))
    lines.append(("模型", str(st.get("model") or "—")))
    cost = float(st.get("monthly_cost") or 0.0)
    budget = float(st.get("monthly_budget") or 0.0)
    lines.append(("本月", f"¥{cost:.2f}" + (f" / ¥{budget:.0f}" if budget > 0 else "")))
    u = st.get("usage_total") or {}
    lines.append(("累计", f"↑{fmt_tokens(u.get('prompt'))} ↓{fmt_tokens(u.get('completion'))}"))
    if st.get("peak_hour"):
        lines.append(("时段", "高峰"))
    tr = st.get("trust") or {}
    if tr.get("state") == "unconfirmed":
        n = int(tr.get("pending") or 0) + int(tr.get("alerts") or 0)
        lines.append(("内核", f"待确认 {n} 项"))
    dg = st.get("degrade") or {}
    if int(dg.get("critical") or 0) > 0:
        lines.append(("降级", str(dg.get("critical"))))
    return lines


def tooltip_text(st, port=None, version=None):
    """托盘悬停提示：应用名 [v版本] · 运行中 · 端口 · 模型。"""
    name = APP_NAME + (f" v{version}" if version else "")
    parts = [name, "运行中"]
    if port:
        parts.append(f"127.0.0.1:{port}")
    if st and st.get("model"):
        parts.append(str(st["model"]))
    return " · ".join(parts)


def _app_version():
    try:
        import config_defaults
        return str(config_defaults.VERSION)
    except Exception:
        return ""


def trust_unconfirmed(st):
    tr = (st or {}).get("trust") or {}
    return tr.get("state") == "unconfirmed"


def menu_signature(port, version, st, toggles):
    """菜单内容指纹：仅当指纹变化时才重建菜单，避免无谓的 DestroyMenu/重建。

    toggles 为四个开关的当前布尔值元组（autostart / completion_sound /
    notify_on_done / silent_start）——它们也参与菜单渲染（勾选态）。
    """
    parts = [f"p={port}", f"v={version}"]
    for label, val in status_lines(st):
        parts.append(f"{label}={val}")
    parts.append("t=" + "".join("1" if bool(x) else "0" for x in (toggles or ())))
    return "|".join(parts)


def _safe_icon_class():
    """返回可标记「菜单打开中」的 Icon 子类（仅 Windows 需要）。

    pystray 的 win32 后端 `_on_notify` 在右键时会阻塞于 TrackPopupMenuEx 直到
    菜单关闭——在这段区间内 `_refresh_loop` 必须放弃重建菜单，否则会销毁正在
    显示的原生 HMENU 并卡死菜单。此子类用 `_MENU_OPEN` 事件把该区间暴露给刷新线程。
    其它后端没有 `_on_notify`，原样返回基类。
    """
    import pystray
    base = pystray.Icon
    if not hasattr(base, "_on_notify"):
        return base

    class _SafeIcon(base):  # type: ignore[misc, valid-type]
        def _on_notify(self, wparam, lparam):
            _MENU_OPEN.set()
            try:
                return super()._on_notify(wparam, lparam)
            finally:
                _MENU_OPEN.clear()

    return _SafeIcon


# ── 通知 ────────────────────────────────────────────
def set_status_provider(fn):
    global _STATUS_PROVIDER
    _STATUS_PROVIDER = fn


def notify(title, message):
    """托盘气泡通知。无图标（--no-tray / pystray 不可用）时返回 False。"""
    with _ICON_LOCK:
        icon = _ICON
    if icon is None:
        return False
    try:
        icon.notify(str(message), str(title))
        return True
    except Exception:
        return False


# ── 配置读写（mutable_config：绝不改共享只读对象）──────
def _cfg_get(key, default=None):
    try:
        import config_utils
        return config_utils.load_config().get(key, default)
    except Exception:
        return default


def _cfg_set(key, value):
    try:
        import config_utils
        cfg = config_utils.mutable_config()  # 深拷贝副本，避免污染共享缓存
        cfg[key] = value
        config_utils.save_config(cfg)
        return True
    except Exception:
        return False


def _icon_image():
    """托盘图标：优先品牌 app.ico；缺失时退回手绘兜底。"""
    try:
        from PIL import Image
        ico = os.path.join(BASE_DIR, "app.ico")
        if os.path.isfile(ico):
            img = Image.open(ico)
            return img.convert("RGBA")
    except Exception:
        pass
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([6, 14, 58, 56], fill=(14, 165, 233, 255))
        d.ellipse([12, 20, 52, 50], fill=(56, 189, 248, 255))
        d.arc([24, 30, 40, 48], start=200, end=340, fill=(255, 255, 255, 230), width=3)
        return img
    except Exception:
        return None


class TrayController:
    """托盘控制器：由 web_app 构造并 run_in_thread()。

    回调（由 web_app 注入，避免本模块依赖具体实现）：
      open_ui / open_workspace / open_data / open_log /
      cleanup / check_update / about / quit_cb / apply_autostart
    """

    def __init__(self, port, *, open_ui, open_workspace, open_data, open_log,
                 cleanup=None, check_update=None, about=None, quit_cb=None,
                 apply_autostart=None):
        self.port = int(port)
        self.open_ui = open_ui
        self.open_workspace = open_workspace
        self.open_data = open_data
        self.open_log = open_log
        self.cleanup = cleanup
        self.check_update = check_update
        self.about = about
        self.quit_cb = quit_cb
        self.apply_autostart = apply_autostart
        self._st = None
        self._icon = None
        self._stop = threading.Event()
        self._last_trust = None
        self._last_sig = None          # 上次菜单指纹：仅变化时重建
        self._wake = threading.Event()  # 开关变更后请求立即刷新
        self.version = _app_version()

    # —— 开关回调 ——
    def _toggle(self, key, value):
        if not _cfg_set(key, value):
            notify(APP_NAME, f"设置失败：{key}")
            return
        if key == "autostart" and callable(self.apply_autostart):
            ok = False
            try:
                ok = bool(self.apply_autostart(bool(value)))
            except Exception:
                ok = False
            if not ok:
                _cfg_set(key, not bool(value))  # 回滚
                notify(APP_NAME, "开机自启设置失败（可稍后重试）")
        self._wake.set()  # 立即刷新勾选态（不必等下一个 5s 周期）

    def _checked(self, key, default=False):
        return lambda item: bool(_cfg_get(key, default))

    def _on_toggle_autostart(self, icon, item):
        self._toggle("autostart", not bool(_cfg_get("autostart", False)))

    def _on_toggle_sound(self, icon, item):
        self._toggle("completion_sound", not bool(_cfg_get("completion_sound", True)))

    def _on_toggle_notify(self, icon, item):
        self._toggle("notify_on_done", not bool(_cfg_get("notify_on_done", True)))

    def _on_toggle_silent(self, icon, item):
        self._toggle("silent_start", not bool(_cfg_get("silent_start", False)))

    def _on_quit(self, icon, item):
        try:
            self._stop.set()
        except Exception:
            pass
        try:
            icon.stop()
        except Exception:
            pass
        try:
            if callable(self.quit_cb):
                self.quit_cb()
        except Exception:
            pass
        os._exit(0)

    # —— 状态刷新 ——
    def _refresh_once(self):
        st = None
        if callable(_STATUS_PROVIDER):
            try:
                st = _STATUS_PROVIDER()
            except Exception:
                st = None
        self._st = st
        if self._icon is None:
            return
        # tooltip 只改通知区提示，不触碰菜单，任何时候都安全
        try:
            self._icon.title = tooltip_text(st, self.port, self.version)
        except Exception:
            pass
        # 菜单：① 正在显示时绝不动（DestroyMenu 卡死）；② 内容未变不重建。
        if _MENU_OPEN.is_set():
            return
        toggles = (
            bool(_cfg_get("autostart", False)),
            bool(_cfg_get("completion_sound", True)),
            bool(_cfg_get("notify_on_done", True)),
            bool(_cfg_get("silent_start", False)),
        )
        sig = menu_signature(self.port, self.version, st, toggles)
        if sig != self._last_sig:
            try:
                # 赋值 menu 内部即调用 update_menu()，无需再显式重建（旧代码重复重建两次）
                self._icon.menu = self._build_menu()
                self._last_sig = sig
            except Exception:
                pass
        # 内核待确认：状态由 ok→unconfirmed 时提示一次
        cur = trust_unconfirmed(st)
        if cur and self._last_trust is False:
            notify(APP_NAME, "自我完整性：有代码改动待确认（点开界面查看）")
        if st is not None:
            self._last_trust = cur

    def _refresh_loop(self):
        while not self._stop.is_set():
            # 菜单打开期间跳过刷新（含状态采集），等关闭后再更新，避免动到正在显示的菜单
            if _MENU_OPEN.is_set():
                self._stop.wait(0.3)
                continue
            self._refresh_once()
            # 正常 5s 一刷；开关变更时 _wake 立即放行
            self._wake.wait(_REFRESH_SECONDS)
            self._wake.clear()

    # —— 菜单 ——
    def _build_menu(self):
        from pystray import Menu, MenuItem
        st = self._st
        items = []
        # 状态区（只读）
        ver = f" · v{self.version}" if self.version else ""
        items.append(MenuItem(f"● 运行中 · 127.0.0.1:{self.port}{ver}", None))
        for label, val in status_lines(st):
            items.append(MenuItem(f"{label}：{val}", None))
        items.append(Menu.SEPARATOR)
        # 打开
        items.append(MenuItem("打开界面", self._wrap(self.open_ui), default=True))
        items.append(MenuItem("打开工作目录", self._wrap(self.open_workspace)))
        items.append(MenuItem("打开数据目录", self._wrap(self.open_data)))
        items.append(MenuItem("查看日志", self._wrap(self.open_log)))
        items.append(Menu.SEPARATOR)
        # 开关（勾选态）
        items.append(MenuItem("开机自启", self._on_toggle_autostart, checked=self._checked("autostart")))
        items.append(MenuItem("完成提示音", self._on_toggle_sound, checked=self._checked("completion_sound", True)))
        items.append(MenuItem("完成时通知", self._on_toggle_notify, checked=self._checked("notify_on_done", True)))
        items.append(MenuItem("静默启动（不弹浏览器）", self._on_toggle_silent, checked=self._checked("silent_start")))
        items.append(Menu.SEPARATOR)
        # 维护
        if callable(self.cleanup):
            items.append(MenuItem("清理空闲进程", self._wrap(self.cleanup)))
        if callable(self.check_update):
            items.append(MenuItem("检查更新", self._wrap(self.check_update)))
        if callable(self.about):
            items.append(MenuItem("关于", self._wrap(self.about)))
        items.append(Menu.SEPARATOR)
        items.append(MenuItem("退出", self._on_quit))
        return Menu(*items)

    @staticmethod
    def _wrap(fn):
        def _cb(icon, item):
            try:
                if callable(fn):
                    fn()
            except Exception:
                pass
        return _cb

    def run(self):
        """构建并运行托盘（阻塞，应在后台线程调用）。失败返回 False。"""
        try:
            import pystray
        except Exception as e:
            print(f"[托盘] 不可用：{e}（--no-tray 可跳过）")
            return False
        img = _icon_image()
        if img is None:
            print("[托盘] 图标生成失败（缺 Pillow？）")
            return False
        try:
            icon = _safe_icon_class()("whaletalk", img, APP_NAME, self._build_menu())
            self._icon = icon
            global _ICON
            with _ICON_LOCK:
                _ICON = icon
            # 首次刷新立即执行（拿到状态后 tooltip/菜单才完整）
            self._refresh_once()
            threading.Thread(target=self._refresh_loop, daemon=True).start()
            icon.run()  # 阻塞
            return True
        except Exception as e:
            print(f"[托盘] 启动失败：{e}")
            return False
        finally:
            with _ICON_LOCK:
                _ICON = None
            self._icon = None
