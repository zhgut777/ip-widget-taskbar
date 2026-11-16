"""Чистая логика состояния (без Win32): что показывать, когда повторять, что изменилось."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .netinfo import IpInfo

# Паузы перед повтором после сбоя, секунды; дальше — раз в 5 минут, пока не заработает.
RETRY_DELAYS = (15, 30, 60, 120, 300)
# Сколько секунд после смены IP/страны плашка остаётся подсвеченной.
CHANGE_HIGHLIGHT_S = 10


class Status(str, Enum):
    LOADING = "loading"
    OK = "ok"
    ERROR = "error"


class Alert(str, Enum):
    NONE = "none"
    CHANGED = "changed"      # IP или страна только что изменились
    MISMATCH = "mismatch"    # страна не совпадает с «ожидаемой»


@dataclass(frozen=True)
class View:
    """Всё, что нужно для отрисовки плашки. Хешируется — служит ключом кэша картинок."""
    status: Status = Status.LOADING
    cc: str = ""
    country: str = ""        # уже на языке интерфейса
    ip: str = ""             # полный адрес
    stale: bool = False      # последнее обновление не удалось — данные могут устареть
    alert: Alert = Alert.NONE


def retry_delay(fail_count: int) -> int:
    """Пауза перед очередной попыткой после fail_count подряд неудачных (>=1)."""
    index = min(max(fail_count, 1) - 1, len(RETRY_DELAYS) - 1)
    return RETRY_DELAYS[index]


def changed(previous: Optional[IpInfo], current: IpInfo) -> bool:
    """Изменился ли основной адрес или страна по сравнению с прошлым удачным ответом.

    Сравниваем «основной» адрес того же семейства: если сервис ответил то IPv4, то
    IPv6 при одном и том же подключении, это не смена сети.
    """
    if previous is None:
        return False
    if previous.cc != current.cc:
        return True
    same_family = (previous.ipv4, current.ipv4) if previous.ipv4 and current.ipv4 else \
                  (previous.ipv6, current.ipv6)
    return bool(same_family[0] and same_family[1] and same_family[0] != same_family[1])


def alert_for(info: Optional[IpInfo], expected_cc: str, highlight_changes: bool,
              recently_changed: bool) -> Alert:
    if info is None:
        return Alert.NONE
    if expected_cc and info.cc and info.cc != expected_cc:
        return Alert.MISMATCH
    if highlight_changes and recently_changed:
        return Alert.CHANGED
    return Alert.NONE
