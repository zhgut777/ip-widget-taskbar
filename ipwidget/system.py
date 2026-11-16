"""Системные мелочи Windows: тема, автозапуск, буфер обмена, масштаб экрана."""

from __future__ import annotations

import ctypes
import sys
import winreg
from pathlib import Path

from . import winapi as w
from .logs import log

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "IPWidget"
PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


# --------------------------------------------------------------------------
#  Масштаб экрана
# --------------------------------------------------------------------------

def enable_dpi_awareness() -> None:
    """Per-Monitor V2 (Windows 10 1703+), иначе системный уровень."""
    if w.SetProcessDpiAwarenessContext and w.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        return
    if w.SetProcessDPIAware:
        w.SetProcessDPIAware()


def dpi_for(hwnd) -> int:
    """DPI монитора, на котором стоит окно (по умолчанию 96)."""
    dpi = 0
    if hwnd and w.GetDpiForWindow:
        dpi = int(w.GetDpiForWindow(hwnd))
    if not dpi and w.GetDpiForSystem:
        dpi = int(w.GetDpiForSystem())
    return dpi or 96


# --------------------------------------------------------------------------
#  Тема Windows
# --------------------------------------------------------------------------

def _reg_int(name: str, default: int = 0) -> int:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
            return int(winreg.QueryValueEx(key, name)[0])
    except (OSError, ValueError):
        return default


def apps_use_light() -> bool:
    return _reg_int("AppsUseLightTheme", 0) == 1


def system_uses_light() -> bool:
    """Светлая ли тема панели задач (настройка «Режим Windows по умолчанию»)."""
    return _reg_int("SystemUsesLightTheme", 0) == 1


def accent_on_taskbar() -> bool:
    """Включён ли «Показать цвет элементов на панели задач» (тогда она цветная)."""
    return _reg_int("ColorPrevalence", 0) == 1


def theme_signature() -> tuple[int, int, int]:
    return (_reg_int("AppsUseLightTheme", 0), _reg_int("SystemUsesLightTheme", 0),
            _reg_int("ColorPrevalence", 0))


def apply_menu_theme(light: bool) -> None:
    """Системные меню в тон теме (недокументированный uxtheme, сборки 18362+).

    На более старых сборках номера функций означают другое, поэтому там не трогаем.
    """
    if w.windows_build() < 18362:
        return
    try:
        ux = ctypes.WinDLL("uxtheme")
        ux[135](3 if light else 2)     # SetPreferredAppMode: ForceLight / ForceDark
        ux[136]()                      # FlushMenuThemes
    except (OSError, AttributeError):
        log.debug("не удалось применить тему меню", exc_info=True)


# --------------------------------------------------------------------------
#  Автозапуск
# --------------------------------------------------------------------------

def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    entry = Path(__file__).resolve().parent.parent / "ip_widget.pyw"
    return f'"{pythonw if pythonw.exists() else exe}" "{entry}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        log.warning("автозапуск: не удалось изменить реестр", exc_info=True)
        return False


# --------------------------------------------------------------------------
#  Буфер обмена
# --------------------------------------------------------------------------

def copy_text(text: str) -> bool:
    """Кладёт строку в буфер обмена. Пустую строку не копируем — не затираем чужое."""
    if not text or not w.OpenClipboard(None):
        return False
    handle = None
    try:
        w.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = w.GlobalAlloc(w.GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = w.GlobalLock(handle)
        if not ptr:
            w.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, data, len(data))
        w.GlobalUnlock(handle)
        if w.SetClipboardData(w.CF_UNICODETEXT, handle):
            handle = None          # теперь память принадлежит системе
            return True
        return False
    finally:
        if handle:
            w.GlobalFree(handle)   # SetClipboardData не удался — освобождаем сами
        w.CloseClipboard()


def open_folder(path: Path) -> None:
    w.ShellExecuteW(None, "open", str(path), None, None, 1)
