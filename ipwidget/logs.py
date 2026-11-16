"""Журнал: у виджета нет консоли, поэтому всё пишем в файл с ротацией."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import APP_DIR, LOG_PATH

log = logging.getLogger("ipwidget")
log.addHandler(logging.NullHandler())     # без настройки журнал молчит (тесты, --preview)

MAX_BYTES = 256 * 1024
BACKUPS = 2


def setup(debug: bool = False) -> None:
    """Подключает файловый журнал (до ~0,75 МБ суммарно) и не падает, если папка недоступна."""
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    if any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        return
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_PATH, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                      encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                                               "%Y-%m-%d %H:%M:%S"))
        log.addHandler(handler)
    except OSError:
        pass       # журнал недоступен — работаем без него
