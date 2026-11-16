"""Панель задач: где она, видна ли, какая на ней тема и какие области заняты."""

from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import system, uia
from . import winapi as w
from .layout import Interval, merge_intervals
from .logs import log
from .paths import OWN_CLASS_PREFIXES

Rect = tuple[int, int, int, int]

# Классы окон оболочки, пока один из них активен, плашку наверх не поднимаем,
# иначе она окажется поверх меню «Пуск», быстрых настроек и т. п.
SHELL_POPUP_CLASSES = frozenset({
    "Windows.UI.Core.CoreWindow",
    "Windows.UI.Composition.DesktopWindowContentBridge",
    "Windows.UI.Input.InputSite.WindowClass",
    "XamlExplorerHostIslandWindow",
    "TopLevelWindowForOverflowXamlIsland",
    "TaskSwitcherWnd",
    "MultitaskingViewFrame",
    "ForegroundStaging",
})


# --------------------------------------------------------------------------
#  Состояние панели
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskbarInfo:
    hwnd: int
    rect: Rect          # физические пиксели
    monitor: Rect
    dpi: int

    @property
    def scale(self) -> float:
        return max(1.0, self.dpi / 96.0)

    @property
    def horizontal(self) -> bool:
        return (self.rect[2] - self.rect[0]) >= (self.rect[3] - self.rect[1])


def tray_hwnd() -> int:
    return w.FindWindowW("Shell_TrayWnd", None) or 0


def window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    return buf.value if hwnd and w.GetClassNameW(hwnd, buf, 256) else ""


def fullscreen_app_running() -> bool:
    """Полноэкранное приложение или режим презентации — плашку прячем."""
    state = ctypes.c_int()
    if w.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return False
    return state.value in (w.QUNS_BUSY, w.QUNS_RUNNING_D3D_FULL_SCREEN, w.QUNS_PRESENTATION_MODE)


def read_taskbar() -> Optional[TaskbarInfo]:
    """Описание видимой панели задач или None (скрыта, автоскрытие, нет оболочки)."""
    hwnd = tray_hwnd()
    if not hwnd or not w.IsWindowVisible(hwnd):
        return None
    rect = w.RECT()
    if not w.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 16 or height < 8:
        return None
    monitor = w.MONITORINFO()
    monitor.cbSize = ctypes.sizeof(w.MONITORINFO)
    hmon = w.MonitorFromWindow(hwnd, w.MONITOR_DEFAULTTONEAREST)
    if not hmon or not w.GetMonitorInfoW(hmon, ctypes.byref(monitor)):
        return None
    mon = monitor.rcMonitor
    # Автоскрытая панель «уезжает» за край экрана: от неё остаётся тонкая полоска.
    visible_w = min(rect.right, mon.right) - max(rect.left, mon.left)
    visible_h = min(rect.bottom, mon.bottom) - max(rect.top, mon.top)
    if visible_w <= 0 or visible_h <= 0 or visible_w * visible_h < 0.6 * width * height:
        return None
    return TaskbarInfo(hwnd, (rect.left, rect.top, rect.right, rect.bottom),
                       (mon.left, mon.top, mon.right, mon.bottom), system.dpi_for(hwnd))


def shell_popup_open() -> bool:
    return window_class(w.GetForegroundWindow()) in SHELL_POPUP_CLASSES


def window_above(target: int, other: int) -> bool:
    """True, если окно other стоит в z-порядке выше окна target."""
    if not target or not other:
        return False
    hwnd = w.GetWindow(target, w.GW_HWNDPREV)
    guard = 0
    while hwnd and guard < 4000:
        if hwnd == other:
            return True
        hwnd = w.GetWindow(hwnd, w.GW_HWNDPREV)
        guard += 1
    return False


def set_owner(hwnd: int, owner: int) -> bool:
    """Делает owner владельцем окна: владелец всегда ниже своего окна, поэтому плашка
    не ныряет под панель задач при кликах по ней."""
    try:
        if w.IS_64:
            w.SetWindowLongPtrW(hwnd, w.GWLP_HWNDPARENT, owner)
        else:
            w.SetWindowLongW(hwnd, w.GWLP_HWNDPARENT, owner)
        return True
    except OSError:
        log.warning("не удалось назначить владельца окна", exc_info=True)
        return False


# --------------------------------------------------------------------------
#  Тема панели
# --------------------------------------------------------------------------

def _sample_taskbar_luma(info: TaskbarInfo) -> Optional[float]:
    """Средняя яркость фона панели по нескольким точкам (нужна, если панель цветная)."""
    left, top, right, bottom = info.rect
    mid_y = (top + bottom) // 2
    hdc = w.GetDC(None)
    if not hdc:
        return None
    samples = []
    try:
        for step in range(1, 25):
            x = left + (right - left) * step // 25
            if w.WindowFromPoint(w.POINT(x, mid_y)) != info.hwnd:
                continue                      # здесь значок или наша плашка — пропускаем
            color = w.GetPixel(hdc, x, mid_y)
            if color == 0xFFFFFFFF:
                continue
            r, g, b = color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF
            samples.append(0.2126 * r + 0.7152 * g + 0.0722 * b)
            if len(samples) >= 6:
                break
    finally:
        w.ReleaseDC(None, hdc)
    return sum(samples) / len(samples) if samples else None


class ThemeTracker:
    """Тёмная или светлая панель — для цвета текста плашки.

    Основной источник — реестр (SystemUsesLightTheme): он точен и ничего не стоит.
    Если на панель выведен акцентный цвет, реестру верить нельзя, и тогда смотрим
    на яркость реальных пикселей, но с гистерезисом — чтобы текст не «моргал»,
    когда яркость колеблется около порога.
    """

    LOW, HIGH = 110.0, 146.0

    def __init__(self) -> None:
        self.dark = True

    def update(self, info: Optional[TaskbarInfo]) -> bool:
        if not system.accent_on_taskbar() or info is None:
            self.dark = not system.system_uses_light()
            return self.dark
        luma = _sample_taskbar_luma(info)
        if luma is None:
            return self.dark
        if luma < self.LOW:
            self.dark = True
        elif luma > self.HIGH:
            self.dark = False
        return self.dark


# --------------------------------------------------------------------------
#  Занятые области панели (фоновый поток)
# --------------------------------------------------------------------------

def occupied_from_items(items: list, rect: Rect) -> list[Interval]:
    """Горизонтальные отрезки, занятые элементами панели.

    Контейнеры (вся панель, «входное окно» XAML) отбрасываются по ширине: они
    накрывают половину экрана и «закрыли» бы свободные места. Собственные окна
    виджета тоже отбрасываются: они принадлежат панели и попадают в её дерево UI
    Automation, а плашка не должна считать саму себя занятым местом.
    """
    left, top, right, bottom = rect
    width = max(1, right - left)
    found: list[Interval] = []
    for it in items:
        if it.cls.startswith(OWN_CLASS_PREFIXES):
            continue
        if it.offscreen or it.right <= it.left or it.bottom <= it.top:
            continue
        if it.bottom <= top or it.top >= bottom:
            continue                                   # не на этой панели
        if (it.right - it.left) >= 0.45 * width:
            continue                                   # контейнер
        found.append((it.left, it.right))
    return merge_intervals(found)


def occupied_from_hwnds(rect: Rect) -> list[Interval]:
    """Запасной способ без UIA: дочерние окна панели (в Windows 11 их почти нет)."""
    tray = tray_hwnd()
    if not tray:
        return []
    skip = SHELL_POPUP_CLASSES | {"TrayDummySearchControl"}
    found: list[Interval] = []

    def collect(hwnd, _lparam):
        if window_class(hwnd) in skip or not w.IsWindowVisible(hwnd):
            return True
        r = w.RECT()
        if w.GetWindowRect(hwnd, ctypes.byref(r)) and r.right > r.left and r.bottom > r.top:
            if (r.right - r.left) < 0.45 * max(1, rect[2] - rect[0]):
                found.append((r.left, r.right))
        return True

    callback = w.CHILDENUMPROC(collect)       # живёт до конца вызова
    w.EnumChildWindows(tray, callback, 0)
    return merge_intervals(found)


class LayoutScanner(threading.Thread):
    """Следит за занятыми местами на панели и сообщает окну, когда они изменились.

    UI Automation обходит XAML-дерево панели ~30 мс, поэтому делаем это не в потоке
    интерфейса и адаптивно: часто сразу после события (смена окна, значки двигаются)
    и редко, когда всё спокойно.
    """

    FAST = 1.5          # секунд между проверками в «оживлённый» период
    SLOW = 10.0         # …и в спокойный
    BURST = (0.35, 1.2)  # дополнительные проверки после события (анимация значков)

    def __init__(self, on_change: Callable[[], None]) -> None:
        super().__init__(name="taskbar-layout", daemon=True)
        self._on_change = on_change
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._lock = threading.Lock()
        self._occupied: list[Interval] = []
        self._rect: Optional[Rect] = None
        self._first_done = threading.Event()
        self._burst: list[float] = []
        self.uia_ok = True

    # -- API для потока интерфейса -------------------------------------------
    def snapshot(self) -> tuple[list[Interval], Optional[Rect]]:
        with self._lock:
            return list(self._occupied), self._rect

    def wait_first(self, timeout: float) -> bool:
        return self._first_done.wait(timeout)

    def poke(self) -> None:
        """Что-то на панели могло измениться — проверить сейчас и ещё пару раз позже."""
        with self._lock:
            self._burst = list(self.BURST)
        self._wake.set()

    def stop(self) -> None:
        self._quit.set()
        self._wake.set()

    # -- поток --------------------------------------------------------------------
    def run(self) -> None:
        automation = None
        quiet_since = time.monotonic()
        try:
            try:
                automation = uia.Automation()
            except OSError:
                log.warning("UI Automation недоступен — ищу место по окнам панели", exc_info=True)
                self.uia_ok = False
            while not self._quit.is_set():
                changed = self._scan_once(automation)
                now = time.monotonic()
                if changed:
                    quiet_since = now
                with self._lock:
                    delay = self._burst.pop(0) if self._burst else None
                if delay is None:
                    delay = self.FAST if now - quiet_since < 15 else self.SLOW
                self._wake.wait(delay)
                self._wake.clear()
        except Exception:  # noqa: BLE001 — поток не должен падать молча
            log.error("поток раскладки панели остановлен", exc_info=True)
        finally:
            if automation is not None:
                automation.close()

    def _scan_once(self, automation) -> bool:
        info = read_taskbar()
        if info is None:
            return False
        occupied: Optional[list[Interval]] = None
        if automation is not None:
            try:
                occupied = occupied_from_items(automation.scan(info.hwnd), info.rect)
            except OSError as exc:
                log.info("UIA: обход панели не удался: %s", exc)
        if occupied is None:
            occupied = occupied_from_hwnds(info.rect)
        with self._lock:
            changed = occupied != self._occupied or info.rect != self._rect
            self._occupied, self._rect = occupied, info.rect
        self._first_done.set()
        if changed:
            self._on_change()
        return changed
