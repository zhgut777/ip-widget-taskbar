"""Уведомления Windows о смене сети: включили или выключили VPN, переключили Wi-Fi."""

from __future__ import annotations

import ctypes
from typing import Callable

from . import winapi as w
from .logs import log


class NetworkWatcher:
    """Подписка на изменения сетевых интерфейсов и маршрутов (iphlpapi).

    Обратный вызов приходит из системного потока, поэтому он только дёргает
    переданную функцию (она должна быть потокобезопасной — например, PostMessage).
    """

    def __init__(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        self._callback = w.NETCHANGEPROC(self._notified)   # держим ссылку, иначе соберёт GC
        self._handles: list[ctypes.c_void_p] = []

    def _notified(self, _context, _row, _kind) -> None:
        try:
            self._on_change()
        except Exception:  # noqa: BLE001 — из системного потока исключения выпускать нельзя
            pass

    def start(self) -> None:
        for name, subscribe in (("interface", w.NotifyIpInterfaceChange),
                                ("route", w.NotifyRouteChange2)):
            handle = w.wt.HANDLE()
            result = subscribe(w.AF_UNSPEC, self._callback, None, False, ctypes.byref(handle))
            if result == 0 and handle.value:
                self._handles.append(handle)
            else:
                log.warning("не удалось подписаться на события сети (%s): код %s", name, result)

    def stop(self) -> None:
        for handle in self._handles:
            w.CancelMibChangeNotify2(handle)
        self._handles.clear()
