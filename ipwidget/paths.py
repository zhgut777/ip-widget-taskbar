"""Пути к настройкам, кэшу, логу и ресурсам (рядом с кодом или внутри exe)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_NAME

# Для отладки можно запустить второй, независимый экземпляр:
#   set IPW_INSTANCE=dev   -> отдельные настройки, лог, мьютекс
INSTANCE = "".join(ch for ch in os.environ.get("IPW_INSTANCE", "") if ch.isalnum())[:16]
_SUFFIX = f"-{INSTANCE}" if INSTANCE else ""

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / f"{APP_NAME}{_SUFFIX}"
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / f"{APP_NAME}{_SUFFIX}" / "flags"
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "widget.log"

MUTEX_NAME = f"Local\\{APP_NAME}2_SingleInstance{_SUFFIX}"
# Классы окон (суффикс нужен только отладочным экземплярам). Префиксы используются, чтобы
# при поиске свободного места на панели не принять за «занятое» собственное окно.
WINDOW_CLASS_PREFIX = f"{APP_NAME}2PlateWindow"
TIP_CLASS_PREFIX = f"{APP_NAME}2TipWindow"
WINDOW_CLASS = f"{WINDOW_CLASS_PREFIX}{_SUFFIX}"
TIP_CLASS = f"{TIP_CLASS_PREFIX}{_SUFFIX}"
OWN_CLASS_PREFIXES = (WINDOW_CLASS_PREFIX, TIP_CLASS_PREFIX)


def resource_path(*parts: str) -> Path:
    """Файл данных: рядом с пакетом или во временной папке PyInstaller."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base.joinpath("ipwidget", *parts)
    return Path(__file__).resolve().parent.joinpath(*parts)
