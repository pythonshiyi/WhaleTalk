"""通道注册表：统一的通道发现、启停与状态汇总。

新增一个平台只需两步：

    # 1. 写适配器（继承 ChannelAdapter，只实现传输差异）
    class DouyinAdapter(ChannelAdapter):
        name = "douyin"
        display_name = "抖音直播"
        def capabilities(self): return Capability(group=True, gifts=True, streaming=True)
        ...

    # 2. 注册
    from channel_registry import registry
    registry.register(DouyinAdapter, build_runtime)

之后 `/v1/channels` 自动可见、启停自动生效、**共享层一行不改**。

注册表本身不持有业务状态，状态保存在适配器与运行时里，因此可安全地在
测试中新建独立实例。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Type

from channel_base import ChannelAdapter, ChannelRuntime, PersonaContext

logger = logging.getLogger("whaletalk.channel.registry")


class ChannelRegistry:
    """通道注册表。线程安全。"""

    def __init__(self) -> None:
        self._adapters: Dict[str, ChannelAdapter] = {}
        self._factories: Dict[str, Callable[[], ChannelAdapter]] = {}
        self._runtimes: Dict[str, ChannelRuntime] = {}
        self._lock = threading.RLock()

    # ── 注册 ──

    def register(
        self,
        adapter_cls: Type[ChannelAdapter],
        runtime_builder: Optional[Callable[[ChannelAdapter], ChannelRuntime]] = None,
        *,
        replace: bool = False,
    ) -> ChannelAdapter:
        """注册一个通道。重复注册默认报错，避免静默覆盖。"""
        name = str(getattr(adapter_cls, "name", "") or "").strip()
        if not name:
            raise ValueError("适配器必须有非空的 name")

        def _factory() -> ChannelAdapter:
            inst = adapter_cls()
            if runtime_builder is not None:
                self._runtimes[name] = runtime_builder(inst)
            return inst

        with self._lock:
            if name in self._factories and not replace:
                raise ValueError(f"通道「{name}」已注册（replace=True 可覆盖）")
            self._factories[name] = _factory
            inst = _factory()
            self._adapters[name] = inst
        logger.info("通道已注册：%s（%s）", name, inst.display_name)
        return inst

    def has(self, name: str) -> bool:
        with self._lock:
            return str(name) in self._adapters

    def get(self, name: str) -> Optional[ChannelAdapter]:
        with self._lock:
            return self._adapters.get(str(name))

    def runtime(self, name: str) -> Optional[ChannelRuntime]:
        with self._lock:
            return self._runtimes.get(str(name))

    def names(self) -> List[str]:
        with self._lock:
            return list(self._adapters.keys())

    def all(self) -> List[ChannelAdapter]:
        with self._lock:
            return list(self._adapters.values())

    # ── 统一启停 ──

    def start_all(self, load_cfg: Callable[[str], Dict[str, Any]]) -> Dict[str, str]:
        """启动所有已启用通道。单个通道失败不影响其他通道。"""
        results: Dict[str, str] = {}
        for adapter in self.all():
            try:
                cfg = load_cfg(adapter.name) or {}
                if not cfg.get("enabled"):
                    results[adapter.name] = "disabled"
                    continue
                adapter.start(cfg)
                results[adapter.name] = "started"
            except Exception as e:
                adapter.set_error(str(e))
                results[adapter.name] = f"error: {e}"
                logger.exception("通道 %s 启动失败", adapter.name)
        return results

    def stop_all(self) -> None:
        for adapter in self.all():
            try:
                adapter.stop()
            except Exception:
                logger.exception("通道 %s 停止失败", adapter.name)

    # ── 状态汇总 ──

    def describe(self) -> List[Dict[str, Any]]:
        """供 `/v1/channels` 使用：列出全部通道及能力与运行状态。"""
        out = []
        for adapter in self.all():
            try:
                cap = adapter.capabilities().as_dict()
            except Exception:
                cap = {}
            item = {
                "name": adapter.name,
                "display_name": adapter.display_name,
                "capabilities": cap,
            }
            try:
                item.update(adapter.connection_state())
            except Exception:
                item.update({"running": False, "connected": False, "last_error": ""})
            out.append(item)
        return out


# 进程级默认注册表。测试可自行 new ChannelRegistry() 隔离。
registry = ChannelRegistry()


def build_runtime(
    adapter_cls: Type[ChannelAdapter],
    *,
    load_cfg: Callable[[], Dict[str, Any]],
    load_state: Callable[[], Dict[str, Any]],
    save_state: Callable[[Dict[str, Any]], Any],
    session_prefix: str = "",
    reply_fn: Optional[Callable[..., str]] = None,
) -> Callable[[ChannelAdapter], ChannelRuntime]:
    """构造一个 runtime_builder，省去每个适配器重复写样板。"""

    def _build(adapter: ChannelAdapter) -> ChannelRuntime:
        ctx = PersonaContext(session_prefix or adapter.name)
        return ChannelRuntime(
            adapter,
            load_cfg=load_cfg,
            load_state=load_state,
            save_state=save_state,
            context=ctx,
            reply_fn=reply_fn,
        )

    return _build


__all__ = ["ChannelRegistry", "build_runtime", "registry"]
