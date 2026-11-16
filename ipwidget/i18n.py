"""Строки интерфейса (русский и английский) и названия стран."""

from __future__ import annotations

import json
import locale
from functools import lru_cache

from .logs import log
from .paths import resource_path

STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "loading": "Определяю…",
        "no_data": "Нет данных",
        "unknown_country": "Неизвестно",
        # ошибки сети
        "err_timeout": "сервер не ответил",
        "err_network": "нет доступа к сети",
        "err_cert": "ошибка сертификата",
        "err_server": "сервис отклонил запрос",
        "err_bad_response": "некорректный ответ сервиса",
        "err_unknown": "неизвестная ошибка",
        # меню
        "menu_refresh": "Обновить",
        "menu_interval": "Автообновление",
        "interval_1": "Каждую минуту",
        "interval_5": "Каждые 5 минут",
        "interval_10": "Каждые 10 минут",
        "interval_30": "Каждые 30 минут",
        "interval_60": "Каждый час",
        "interval_0": "Только вручную",
        "menu_copy_ip": "Копировать IP",
        "menu_copy_all": "Копировать страну и IP",
        "menu_alerts": "Оповещения",
        "alert_on_change": "Подсвечивать смену IP и страны",
        "alert_set_expected": "Ожидаемая страна: {name} (запомнить текущую)",
        "alert_clear_expected": "Сбросить ожидаемую страну ({name})",
        "menu_placement": "Положение на панели",
        "placement_auto": "Автоматически",
        "placement_left": "У левого края",
        "placement_right": "Рядом со значками панели",
        "menu_language": "Язык",
        "language_auto": "Как в системе",
        "menu_autostart": "Запускать при входе",
        "menu_both_families": "Показывать IPv4 и IPv6",
        "menu_open_log": "Открыть папку с журналом",
        "menu_about": "IP-виджет {version}",
        "menu_exit": "Выход",
        # подсказка
        "tip_ip": "IP",
        "tip_ipv4": "IPv4",
        "tip_ipv6": "IPv6",
        "tip_isp": "Провайдер",
        "tip_tz": "Часовой пояс",
        "tip_source": "Источник",
        "tip_updated": "Обновлено в {time}",
        "tip_stale": "Не удалось обновить ({reason}). Данные от {time}",
        "tip_failed": "Не удалось получить данные: {reason}",
        "tip_mismatch": "Страна не совпадает с ожидаемой ({name})",
        "tip_changed": "IP или страна только что изменились",
        "tip_hint": "Клик — обновить · правый клик — меню",
    },
    "en": {
        "loading": "Detecting…",
        "no_data": "No data",
        "unknown_country": "Unknown",
        "err_timeout": "server did not respond",
        "err_network": "no network access",
        "err_cert": "certificate error",
        "err_server": "service rejected the request",
        "err_bad_response": "invalid service response",
        "err_unknown": "unknown error",
        "menu_refresh": "Refresh",
        "menu_interval": "Auto-refresh",
        "interval_1": "Every minute",
        "interval_5": "Every 5 minutes",
        "interval_10": "Every 10 minutes",
        "interval_30": "Every 30 minutes",
        "interval_60": "Every hour",
        "interval_0": "Manual only",
        "menu_copy_ip": "Copy IP",
        "menu_copy_all": "Copy country and IP",
        "menu_alerts": "Alerts",
        "alert_on_change": "Highlight IP and country changes",
        "alert_set_expected": "Expected country: {name} (remember current)",
        "alert_clear_expected": "Clear expected country ({name})",
        "menu_placement": "Position on taskbar",
        "placement_auto": "Automatic",
        "placement_left": "At the left edge",
        "placement_right": "Next to the taskbar icons",
        "menu_language": "Language",
        "language_auto": "Same as system",
        "menu_autostart": "Start with Windows",
        "menu_both_families": "Show IPv4 and IPv6",
        "menu_open_log": "Open log folder",
        "menu_about": "IP widget {version}",
        "menu_exit": "Exit",
        "tip_ip": "IP",
        "tip_ipv4": "IPv4",
        "tip_ipv6": "IPv6",
        "tip_isp": "Provider",
        "tip_tz": "Time zone",
        "tip_source": "Source",
        "tip_updated": "Updated at {time}",
        "tip_stale": "Update failed ({reason}). Data from {time}",
        "tip_failed": "Could not get data: {reason}",
        "tip_mismatch": "Country differs from the expected one ({name})",
        "tip_changed": "IP or country has just changed",
        "tip_hint": "Click — refresh · right click — menu",
    },
}

_language = "ru"


def detect_system_language() -> str:
    """'ru' для русскоязычной Windows, иначе 'en'."""
    try:
        import ctypes
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (lang_id & 0x3FF) == 0x19:       # LANG_RUSSIAN
            return "ru"
        return "en"
    except Exception:  # noqa: BLE001 — не Windows или нет доступа: смотрим на локаль Python
        pass
    try:
        name = locale.getlocale()[0] or ""
    except (ValueError, TypeError):
        name = ""
    return "ru" if name.lower().startswith("ru") else "en"


def set_language(setting: str) -> str:
    """Применяет 'auto' | 'ru' | 'en'; возвращает действующий язык."""
    global _language
    _language = detect_system_language() if setting == "auto" else setting
    if _language not in STRINGS:
        _language = "en"
    return _language


def current_language() -> str:
    return _language


def t(key: str, **kwargs: object) -> str:
    """Строка интерфейса; нет перевода — берём русскую, нет и её — сам ключ."""
    text = STRINGS[_language].get(key) or STRINGS["ru"].get(key) or key
    return text.format(**kwargs) if kwargs else text


@lru_cache(maxsize=4)
def _country_table(lang: str) -> dict[str, str]:
    path = resource_path("data", f"countries_{lang}.json")
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return {str(k).upper(): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        log.warning("не удалось прочитать список стран %s", path, exc_info=True)
        return {}


def country_name(cc: str, fallback: str = "") -> str:
    """Название страны на языке интерфейса; иначе название из API, иначе код."""
    cc = (cc or "").upper()
    return _country_table(_language).get(cc) or fallback or cc or t("unknown_country")
