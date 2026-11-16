"""Настройки: хранятся в %APPDATA%\\IPWidget\\config.json, любое значение проверяется."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .logs import log
from .paths import CONFIG_PATH

# Периоды автообновления из меню, минуты (0 — только вручную).
INTERVAL_CHOICES = (1, 5, 10, 30, 60, 0)
LANGUAGES = ("auto", "ru", "en")
PLACEMENTS = ("auto", "left", "right")


def _choice(value: Any, allowed: tuple, default: Any) -> Any:
    return value if value in allowed else default


def _boolean(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _country(value: Any) -> str:
    if isinstance(value, str) and len(value.strip()) == 2 and value.strip().isascii() \
            and value.strip().isalpha():
        return value.strip().upper()
    return ""


@dataclass
class Config:
    interval_min: int = 10          # период автообновления, мин; 0 — вручную
    language: str = "auto"          # auto | ru | en
    placement: str = "auto"         # auto | left | right — где искать место на панели
    alert_on_change: bool = True    # подсвечивать смену IP/страны
    show_both_families: bool = True  # в подсказке показывать и IPv4, и IPv6
    expected_cc: str = ""           # «ожидаемая» страна; при несовпадении плашка краснеет

    @classmethod
    def from_dict(cls, raw: Any) -> "Config":
        """Собирает настройки из произвольного JSON: неверное значение -> по умолчанию."""
        cfg = cls()
        if not isinstance(raw, dict):
            return cfg
        interval = raw.get("interval_min")
        if isinstance(interval, int) and not isinstance(interval, bool):
            # допускаем только известные периоды, иначе — значение по умолчанию
            cfg.interval_min = _choice(interval, INTERVAL_CHOICES, cfg.interval_min)
        cfg.language = _choice(raw.get("language"), LANGUAGES, cfg.language)
        cfg.placement = _choice(raw.get("placement"), PLACEMENTS, cfg.placement)
        cfg.alert_on_change = _boolean(raw.get("alert_on_change"), cfg.alert_on_change)
        cfg.show_both_families = _boolean(raw.get("show_both_families"), cfg.show_both_families)
        cfg.expected_cc = _country(raw.get("expected_cc"))
        return cfg

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def load_config(path: Path = CONFIG_PATH) -> Config:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return Config.from_dict(json.load(fh))
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError):
        log.warning("config: не удалось прочитать %s, взяты значения по умолчанию", path,
                    exc_info=True)
        return Config()


def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None:
    """Пишет через временный файл: обрыв записи не оставит наполовину записанный config."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(cfg), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        log.warning("config: не удалось сохранить %s", path, exc_info=True)

