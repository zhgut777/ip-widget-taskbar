# -*- coding: utf-8 -*-
"""IP-виджет: ваш IP, страна и флаг — маленькая карточка на рабочем столе.

Запуск:
    ip-widget.vbs            — двойной щелчок, без окна консоли
    pythonw ip_widget.pyw     — то же самое вручную
    python ip_widget.pyw      — с окном консоли (для отладки)
    python ip_widget.pyw --preview out.png [ok|loading|error]   — отрисовать карточку в PNG

Управление:
    перетаскивание мышью  — переместить карточку
    щелчок по карточке    — обновить данные
    правый щелчок         — меню: обновить, копировать, поверх всех окон,
                            автозапуск, выход

Данные: ipwho.is -> ipapi.co -> ip-api.com (первый доступный источник).
Флаги: flagcdn.com, кэшируются в %LOCALAPPDATA%\\IPWidget\\flags.
Настройки: %APPDATA%\\IPWidget\\config.json
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------------
#  Пути и константы оформления
# --------------------------------------------------------------------------

APP_NAME = "IPWidget"
APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME / "flags"
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "widget.log"
RU_NAMES_PATH = None  # определяется ниже: рядом со скриптом или внутри exe


def _resource_path(name: str) -> Path:
    """Путь к ресурсу: рядом со .pyw или внутри собранного exe (PyInstaller)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / name
    return Path(__file__).resolve().parent / name


RU_NAMES_PATH = _resource_path("countries_ru.json")

CARD_W = 340          # логическая ширина карточки
CARD_H = 114          # логическая высота карточки
SHADOW = 20           # запас вокруг карточки под тень
RADIUS = 14           # радиус скругления
BAR_H = 34            # высота плашки на панели задач
BAR_MAX_W = 300       # предельная ширина плашки, чтобы не заезжать на значки
REFRESH_MIN = 10      # период автообновления по умолчанию, минут
WATCH_MS = 1000       # период слежения за панелью задач, мс
RENDER_CACHE_MAX = 8  # сколько готовых картинок держать в кэше

# варианты периода автообновления: (минуты, подпись в меню); 0 — только вручную
INTERVAL_OPTIONS = (
    (1, "Каждую минуту"),
    (5, "Каждые 5 минут"),
    (10, "Каждые 10 минут"),
    (30, "Каждые 30 минут"),
    (60, "Каждый час"),
    (0, "Только вручную"),
)

DEBUG = os.environ.get("IPW_DEBUG") == "1"

DEFAULT_CONFIG = {
    "mode": "bar",
    "x": None,
    "y": None,
    "topmost": True,
    "autostart": False,
    "interval_min": REFRESH_MIN,
}

COL_BG = (28, 28, 33, 255)
COL_BORDER = (66, 66, 76, 255)
COL_TXT = (240, 240, 244, 255)
COL_SUB = (152, 157, 166, 255)
COL_DIM = (118, 123, 132, 255)
COL_ACCENT = (122, 166, 255, 255)
COL_ERR = (255, 118, 118, 255)
COL_OK = (86, 214, 138, 255)


def log(msg: str) -> None:
    """Тихая запись в лог: виджет запускается без консоли."""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg.rstrip() + "\n")
    except Exception:
        pass


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG})
    except FileNotFoundError:
        pass
    except Exception:
        log("config load:\n" + traceback.format_exc())
    return cfg


def save_config(cfg: dict) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        log("config save:\n" + traceback.format_exc())


# --------------------------------------------------------------------------
#  Сеть: IP, страна, провайдер
# --------------------------------------------------------------------------

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IPWidget/1.0"


def _http_json(url: str, timeout: float = 7.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(200_000)
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("ожидался объект JSON")
    return data


def _norm(ip: str = "", cc: str = "", country: str = "", city: str = "",
          region: str = "", utc: str = "", isp: str = "") -> dict:
    return {
        "ip": (ip or "").strip(),
        "cc": (cc or "").strip().upper(),
        "country_en": (country or "").strip(),
        "city": (city or "").strip(),
        "region": (region or "").strip(),
        "utc": (utc or "").strip(),
        "isp": (isp or "").strip(),
    }


def _fetch_ipwho() -> dict:
    d = _http_json("https://ipwho.is/")
    if not d.get("success", True):
        raise ValueError(str(d.get("message") or "ipwho.is: отказ"))
    tz = d.get("timezone") or {}
    conn = d.get("connection") or {}
    return _norm(d.get("ip", ""), d.get("country_code", ""), d.get("country", ""),
                 d.get("city", ""), d.get("region", ""), tz.get("utc", ""),
                 conn.get("isp") or conn.get("org", ""))


def _fetch_ipapi() -> dict:
    d = _http_json("https://ipapi.co/json/")
    if d.get("error"):
        raise ValueError(str(d.get("reason") or "ipapi.co: отказ"))
    utc = d.get("utc_offset", "") or ""
    if len(utc) == 5 and utc[0] in "+-":          # +0100 -> +01:00
        utc = f"{utc[:3]}:{utc[3:]}"
    return _norm(d.get("ip", ""), d.get("country_code", ""), d.get("country_name", ""),
                 d.get("city", ""), d.get("region", ""), utc, d.get("org", ""))


def _fetch_ipapi_co_free() -> dict:
    d = _http_json("http://ip-api.com/json/?fields=status,message,country,countryCode,city,"
                   "regionName,timezone,isp,query")
    if d.get("status") != "success":
        raise ValueError(str(d.get("message") or "ip-api.com: отказ"))
    return _norm(d.get("query", ""), d.get("countryCode", ""), d.get("country", ""),
                 d.get("city", ""), d.get("regionName", ""), "", d.get("isp", ""))


SOURCES = (_fetch_ipwho, _fetch_ipapi, _fetch_ipapi_co_free)


def fetch_ip() -> dict:
    """Пробует источники по очереди, возвращает первый успешный ответ."""
    errors = []
    for src in SOURCES:
        try:
            data = src()
            if data["ip"]:
                return data
            errors.append(f"{src.__name__}: пустой IP")
        except Exception as exc:  # noqa: BLE001 — нужен любой сбой источника
            errors.append(f"{src.__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


# --------------------------------------------------------------------------
#  Флаг: скачать один раз, дальше — из кэша
# --------------------------------------------------------------------------

def flag_cache_path(cc: str) -> Path:
    return CACHE_DIR / f"{cc.lower()}.png"


def ensure_flag(cc: str) -> None:
    """Скачивает флаг страны в кэш, если его там ещё нет."""
    if not cc or len(cc) != 2:
        return
    path = flag_cache_path(cc)
    if path.exists() and path.stat().st_size > 0:
        return
    url = f"https://flagcdn.com/w320/{cc.lower()}.png"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read(500_000)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


def load_flag(cc: str) -> Image.Image | None:
    if not cc or len(cc) != 2:
        return None
    path = flag_cache_path(cc)
    try:
        if path.exists() and path.stat().st_size > 0:
            with Image.open(path) as im:
                return im.convert("RGBA")
    except Exception:
        log(f"flag load {cc}:\n" + traceback.format_exc())
    return None


def ru_country_name(cc: str, country_en: str = "") -> str:
    """Русское название страны по коду; иначе — английское."""
    global _RU_NAMES
    if not _RU_NAMES:
        try:
            with open(RU_NAMES_PATH, "r", encoding="utf-8-sig") as fh:
                _RU_NAMES = json.load(fh)
        except Exception:
            _RU_NAMES = {}
    return _RU_NAMES.get((cc or "").upper()) or country_en or (cc or "Неизвестно")


_RU_NAMES: dict = {}


# --------------------------------------------------------------------------
#  Отрисовка карточки
# --------------------------------------------------------------------------

_FONT_FILES = {
    "semibold": ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"),
    "regular": ("segoeui.ttf", "arial.ttf"),
    "mono": ("consola.ttf", "cour.ttf"),
}
_font_cache: dict = {}


def _font(kind: str, size_px: int) -> ImageFont.FreeTypeFont:
    key = (kind, size_px)
    if key not in _font_cache:
        font = None
        for name in _FONT_FILES[kind]:
            try:
                font = ImageFont.truetype(name, size_px)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default(size_px)
        _font_cache[key] = font
    return _font_cache[key]


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> str:
    """Обрезает текст с многоточием по ширине."""
    if not text:
        return ""
    if draw.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid].rstrip() + ell, font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + ell) if lo else ell


def _rounded_mask(w: int, h: int, r: int, ss: int = 4) -> Image.Image:
    """Сглаженная маска прямоугольника со скруглёнными углами."""
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=255)
    return mask.resize((w, h), Image.LANCZOS)


def _icon_glyph(size: int, color: tuple, codepoint: str = "\ue72c") -> Image.Image:
    """Системный значок Windows (Segoe MDL2 Assets) с 4x сглаживанием.

    E72C — «обновить». Если шрифта нет, рисуем круговую стрелку вручную.
    """
    ss = 4
    big = max(8, size * ss)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("segmdl2.ttf", int(big * 0.86))
        d.text((big / 2, big / 2), codepoint, font=font, fill=color, anchor="mm")
        return img.resize((size, size), Image.LANCZOS)
    except Exception:
        return _drawn_refresh(size, color)


def _drawn_refresh(size: int, color: tuple) -> Image.Image:
    """Запасной вариант значка «обновить», если нет системного шрифта."""
    ss = 4
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(big * 0.16)
    width = max(2, int(big * 0.12))
    d.arc((pad, pad, big - pad - 1, big - pad - 1), start=-40, end=250, fill=color, width=width)
    import math
    r = (big - 2 * pad) / 2
    ang = math.radians(250)
    ax, ay = big / 2 + r * math.cos(ang), big / 2 + r * math.sin(ang)
    head = max(3, int(big * 0.16))
    d.polygon([(ax + head * 0.2, ay - head), (ax + head, ay + head * 0.1),
               (ax - head * 0.9, ay + head * 0.4)], fill=color)
    return img.resize((size, size), Image.LANCZOS)


_FLAG_CACHE: dict = {}


def _flag_image(cc: str, box_w: int, box_h: int, radius: int) -> tuple | None:
    """Флаг, уже приведённый к нужному размеру и скруглению (кэш)."""
    if not cc or len(cc) != 2:
        return None
    key = (cc, box_w, box_h, radius)
    cached = _FLAG_CACHE.get(key)
    if cached is not None:
        return cached
    flag = load_flag(cc)
    if flag is None or not flag.height:
        return None
    ratio = min(box_w / flag.width, box_h / flag.height)
    nw, nh = max(1, int(flag.width * ratio)), max(1, int(flag.height * ratio))
    processed = flag.resize((nw, nh), Image.LANCZOS)
    if len(_FLAG_CACHE) >= 12:
        _FLAG_CACHE.pop(next(iter(_FLAG_CACHE)))
    _FLAG_CACHE[key] = (processed, _rounded_mask(nw, nh, radius))
    return _FLAG_CACHE[key]


# --- кэш фона -------------------------------------------------------------
# Фон (тень, скругления, прозрачная заливка) не зависит от данных и стоит
# дороже всего: суперсэмплинг + размытие тени. Держим его посчитанным.
_BG_CACHE: dict = {}
_BG_CACHE_MAX = 6


def _cached_background(key, builder):
    background = _BG_CACHE.get(key)
    if background is None:
        if len(_BG_CACHE) >= _BG_CACHE_MAX:
            _BG_CACHE.pop(next(iter(_BG_CACHE)))
        background = builder()
        _BG_CACHE[key] = background
    return background


def _card_background(w: int, h: int, s: float) -> Image.Image:
    """Тень + подложка карточки (одна и та же при любых данных)."""
    key = ("card", w, h, round(s, 3))

    def build():
        pad = int(round(SHADOW * s))
        total_w, total_h = w + 2 * pad, h + 2 * pad
        SS = 4  # суперсэмплинг для сглаживания форм
        base = Image.new("RGBA", (total_w * SS, total_h * SS), (0, 0, 0, 0))
        bx0, by0 = pad * SS, pad * SS
        bx1, by1 = (pad + w) * SS, (pad + h) * SS
        radius = int(round(RADIUS * s * SS))

        shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (bx0, by0 + int(3 * s * SS), bx1, by1 + int(3 * s * SS)),
            radius=radius, fill=(0, 0, 0, 120))
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, int(8 * s * SS))))
        base.alpha_composite(shadow)

        card_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ImageDraw.Draw(card_layer).rounded_rectangle(
            (bx0, by0, bx1, by1), radius=radius, fill=COL_BG,
            outline=COL_BORDER, width=max(1, int(round(s * SS))))
        base.alpha_composite(card_layer)
        return base.resize((total_w, total_h), Image.LANCZOS)

    return _cached_background(key, build)


def _bar_background(w: int, h: int, s: float, hover: bool, dark: bool) -> Image.Image:
    """Фон плашки: невидимая заливка (alpha=1) и подсветка при наведении."""
    key = ("bar", w, h, round(s, 3), hover, dark)

    def build():
        pad = max(2, int(round(3 * s)))
        total_w, total_h = w + 2 * pad, h + 2 * pad
        SS = 4
        base = Image.new("RGBA", (total_w * SS, total_h * SS), (0, 0, 0, 0))
        draw = ImageDraw.Draw(base)
        rect = (pad * SS, pad * SS, (pad + w) * SS, (pad + h) * SS)
        radius = int(round(9 * s * SS))
        # Заливка с alpha = 1: глазом не видна, но Windows не пропускает клики
        # сквозь полностью прозрачные пиксели — иначе плашка нажималась бы
        # только по буквам.
        draw.rounded_rectangle(rect, radius=radius, fill=(0, 0, 0, 1))
        if hover:
            draw.rounded_rectangle(rect, radius=radius,
                                   fill=(255, 255, 255, 28) if dark else (0, 0, 0, 22))
        return base.resize((total_w, total_h), Image.LANCZOS)

    return _cached_background(key, build)


def _render_card(state: dict, scale: float, hover: bool = False) -> Image.Image:
    """Большая карточка на рабочем столе (размеры в физических пикселях)."""
    s = scale
    w = int(round(CARD_W * s))
    h = int(round(CARD_H * s))
    pad = int(round(SHADOW * s))
    out = _card_background(w, h, s).copy()
    d = ImageDraw.Draw(out)

    status = state.get("status", "ok")
    cc = state.get("cc", "")

    # --- флаг -------------------------------------------------------------
    fx, fy = pad + int(round(18 * s)), pad + int(round(20 * s))
    fw, fh = int(round(60 * s)), int(round(40 * s))
    flag = _flag_image(cc, fw, fh, max(2, int(round(5 * s)))) if status != "loading" else None
    if flag is not None:
        image, mask = flag
        out.paste(image, (fx + (fw - image.width) // 2, fy + (fh - image.height) // 2), mask)
    else:
        # заглушка: прямоугольник с кодом страны
        ph = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
        ImageDraw.Draw(ph).rounded_rectangle((0, 0, fw - 1, fh - 1), radius=max(2, int(round(6 * s))),
                                             fill=(62, 66, 78, 255))
        out.paste(ph, (fx, fy), _rounded_mask(fw, fh, max(2, int(round(6 * s)))))
        label = cc or "–"
        f = _font("semibold", max(9, int(round(14 * s))))
        d.text((fx + fw / 2, fy + fh / 2), label, font=f, fill=COL_SUB, anchor="mm")

    # --- тексты -----------------------------------------------------------
    tx = pad + int(round(94 * s))
    right = pad + w - int(round(18 * s))
    icon_size = max(10, int(round(17 * s)))
    title_max = right - int(round(icon_size + 8 * s)) - tx

    if status == "loading":
        title, subtitle, ip_text, footer = "Определяю…", "запрашиваю данные об IP", "", "подключение…"
    elif status == "error":
        title = "Нет данных"
        subtitle = state.get("error", "проверьте соединение")
        ip_text = ""
        footer = "правый клик → «Обновить»"
    else:
        title = ru_country_name(cc, state.get("country_en", ""))
        bits = [state.get("city") or state.get("region") or ""]
        if state.get("utc"):
            bits.append(f"UTC{state['utc']}")
        subtitle = " · ".join([b for b in bits if b]) or state.get("country_en", "")
        ip_text = state.get("ip", "—")
        footer = state.get("isp", "") or "интернет-провайдер неизвестен"

    f_title = _font("semibold", max(11, int(round(15.5 * s))))
    f_sub = _font("regular", max(9, int(round(11 * s))))
    f_ip = _font("mono", max(10, int(round(14 * s))))
    f_foot = _font("regular", max(8, int(round(10 * s))))

    title = _fit(d, title, f_title, title_max)
    d.text((tx, pad + int(round(16 * s))), title, font=f_title,
           fill=COL_ERR if status == "error" else COL_TXT, anchor="lt")

    d.text((tx, pad + int(round(40 * s))), _fit(d, subtitle, f_sub, right - tx),
           font=f_sub, fill=COL_ERR if status == "error" else COL_SUB, anchor="lt")

    if ip_text:
        d.text((tx, pad + int(round(64 * s))), ip_text, font=f_ip, fill=COL_TXT, anchor="lt")

    stamp = state.get("updated") or ""
    stamp_w = int(d.textlength(stamp, font=f_foot)) + int(round(10 * s)) if stamp else 0
    d.text((tx, pad + int(round(89 * s))), _fit(d, footer, f_foot, right - tx - stamp_w),
           font=f_foot, fill=COL_DIM, anchor="lt")
    if stamp:
        col = COL_OK if status == "ok" else COL_DIM
        d.text((right, pad + int(round(89 * s))), stamp, font=f_foot, fill=col, anchor="rt")

    # --- значок обновления ------------------------------------------------
    icon = _icon_glyph(icon_size, COL_ACCENT if hover else COL_SUB)
    out.alpha_composite(icon, (right - icon_size, pad + int(round(19 * s))))

    return out


def _render_bar(state: dict, scale: float, hover: bool = False, dark: bool = True) -> Image.Image:
    """Компактная плашка для панели задач: флаг · страна · IP.

    Рамки и «коробки» нет: фон прозрачный, при наведении — мягкая подсветка.
    Цвета текста подстраиваются под светлую или тёмную панель задач.
    """
    s = scale
    pad = max(2, int(round(3 * s)))          # запас под сглаживание подсветки
    h = int(round(BAR_H * s))
    meas = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    status = state.get("status", "ok")
    cc = state.get("cc", "")
    if status == "loading":
        title, ip_text = "Определяю…", ""
    elif status == "error":
        title, ip_text = "Нет данных", ""
    else:
        title = ru_country_name(cc, state.get("country_en", ""))
        ip_text = state.get("ip", "")

    f_title = _font("semibold", max(9, int(round(12.5 * s))))
    f_ip = _font("mono", max(8, int(round(12.5 * s))))   # по высоте цифр = высоте прописных
    f_dot = _font("regular", max(8, int(round(12 * s))))

    gap = int(round(8 * s))
    padx = int(round(12 * s))
    flag_w, flag_h = int(round(26 * s)), int(round(17 * s))
    dot_w = int(round(12 * s))
    max_w = int(round(BAR_MAX_W * s))

    tw = meas.textlength(title, font=f_title)
    iw = meas.textlength(ip_text, font=f_ip) if ip_text else 0
    fixed = padx * 2 + flag_w + gap + (gap + dot_w + iw if ip_text else 0)
    if fixed + tw > max_w:
        title = _fit(meas, title, f_title, max(0, max_w - fixed))
        tw = meas.textlength(title, font=f_title)
    w = int(round(fixed + tw))

    if dark:
        col_title = COL_TXT
        col_ip = (206, 211, 219, 255)
        col_dot = (150, 155, 163, 255)
        col_error = COL_ERR
        col_placeholder = (66, 70, 80, 255)
    else:
        col_title = (26, 27, 31, 255)
        col_ip = (56, 60, 68, 255)
        col_dot = (110, 115, 124, 255)
        col_error = (190, 40, 40, 255)
        col_placeholder = (198, 202, 210, 255)

    out = _bar_background(w, h, s, hover, dark).copy()
    d = ImageDraw.Draw(out)

    # флаг
    fx = pad + padx
    fy = pad + (h - flag_h) // 2
    flag = _flag_image(cc, flag_w, flag_h, max(2, int(round(3 * s)))) if status != "loading" else None
    if flag is not None:
        image, mask = flag
        out.paste(image, (fx + (flag_w - image.width) // 2, fy + (flag_h - image.height) // 2), mask)
    else:
        ph = Image.new("RGBA", (flag_w, flag_h), (0, 0, 0, 0))
        ImageDraw.Draw(ph).rounded_rectangle((0, 0, flag_w - 1, flag_h - 1),
                                             radius=max(2, int(round(4 * s))),
                                             fill=col_placeholder)
        out.paste(ph, (fx, fy), _rounded_mask(flag_w, flag_h, max(2, int(round(4 * s)))))

    # Страна, разделитель и IP рисуются по общей базовой линии: у Segoe UI и
    # Consolas разные метрики, поэтому привязка к центру строки «проваливала» цифры.
    mid = pad + h / 2
    try:
        title_box = d.textbbox((0, 0), title, font=f_title, anchor="ls")
        baseline = mid - (title_box[1] + title_box[3]) / 2
    except Exception:
        baseline = mid

    x = fx + flag_w + gap
    d.text((x, baseline), title, font=f_title,
           fill=col_error if status == "error" else col_title, anchor="ls")
    x = int(x + tw)
    if ip_text:
        d.text((x + int(round(2 * s)), baseline), "·", font=f_dot, fill=col_dot, anchor="ls")
        x += dot_w
        d.text((x, baseline), ip_text, font=f_ip, fill=col_ip, anchor="ls")
    return out


def render(state: dict, scale: float, hover: bool = False, mode: str = "card",
           bar_dark: bool = True) -> Image.Image:
    """Изображение виджета: большая карточка или плашка на панели задач."""
    if mode == "bar":
        return _render_bar(state, scale, hover, bar_dark)
    return _render_card(state, scale, hover)


# --------------------------------------------------------------------------
#  Win32: окно со скруглением и попиксельной прозрачностью
# --------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

IS_64 = ctypes.sizeof(ctypes.c_void_p) == 8
WPARAM = ctypes.c_ulonglong if IS_64 else ctypes.c_ulong
LPARAM = ctypes.c_longlong if IS_64 else ctypes.c_long
LRESULT = ctypes.c_longlong if IS_64 else ctypes.c_long
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)
CHILDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG,
                                  wt.DWORD, wt.DWORD)

WM_DESTROY, WM_CLOSE, WM_TIMER, WM_COMMAND = 0x0002, 0x0010, 0x0113, 0x0111
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONUP = 0x0200, 0x0201, 0x0202, 0x0205
WM_MOUSELEAVE, WM_SETCURSOR, WM_APP = 0x02A3, 0x0020, 0x8000
WM_NCHITTEST = 0x0084
WM_SETTINGCHANGE = 0x001A
WM_THEMECHANGED = 0x031A
MA_NOACTIVATE = 3
HTCLIENT = 1
GW_HWNDPREV = 3
GWLP_HWNDPARENT = -8        # индекс «владельца» окна (SetWindowLongPtr)
WM_TOPFIX = 0x8001          # WM_APP + 1: «проверь z-порядок и поднимись»
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOPMOST, WS_EX_TOOLWINDOW = 0x00080000, 0x00000008, 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x00000002, 0x00, 0x01
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SPI_GETWORKAREA = 0x0030
IDC_ARROW, IDC_HAND = 32512, 32649
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0100, 0x0002
TPM_BOTTOMALIGN = 0x0020
GWL_EXSTYLE = -20
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x000, 0x800, 0x008, 0x001
MF_POPUP = 0x010
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
ERROR_ALREADY_EXISTS = 183

HWND_TOPMOST = ctypes.c_void_p((1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1)
HWND_NOTOPMOST = ctypes.c_void_p((1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 2)


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wt.LONG), ("cy", wt.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", WPARAM),
                ("lParam", LPARAM), ("time", wt.DWORD), ("pt", POINT), ("lPrivate", wt.DWORD)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("dwFlags", wt.DWORD), ("hwndTrack", wt.HWND),
                ("dwHoverTime", wt.DWORD)]


user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = (wt.HWND, wt.UINT, WPARAM, LPARAM)
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = (wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND,
                                   wt.HMENU, wt.HINSTANCE, ctypes.c_void_p)
user32.RegisterClassW.restype = wt.ATOM
user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
user32.UpdateLayeredWindow.restype = wt.BOOL
user32.UpdateLayeredWindow.argtypes = (wt.HWND, wt.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(SIZE), wt.HDC, ctypes.POINTER(POINT),
                                       wt.DWORD, ctypes.POINTER(BLENDFUNCTION), wt.DWORD)
user32.SetWindowPos.restype = wt.BOOL
user32.SetWindowPos.argtypes = (wt.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT)
user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SetCapture.restype = wt.HWND
user32.SetCapture.argtypes = (wt.HWND,)
user32.ReleaseCapture.restype = wt.BOOL
user32.LoadCursorW.restype = wt.HANDLE
user32.LoadCursorW.argtypes = (wt.HINSTANCE, ctypes.c_void_p)
user32.SetCursor.restype = wt.HANDLE
user32.SetCursor.argtypes = (wt.HANDLE,)
user32.SetTimer.restype = ctypes.c_size_t
user32.SetTimer.argtypes = (wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p)
user32.KillTimer.restype = wt.BOOL
user32.KillTimer.argtypes = (wt.HWND, ctypes.c_size_t)
user32.PostMessageW.argtypes = (wt.HWND, wt.UINT, WPARAM, LPARAM)
user32.DestroyWindow.argtypes = (wt.HWND,)
user32.ShowWindow.argtypes = (wt.HWND, ctypes.c_int)
user32.GetMessageW.argtypes = (ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT)
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = (ctypes.POINTER(MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(MSG),)
user32.DispatchMessageW.restype = LRESULT
user32.PostQuitMessage.argtypes = (ctypes.c_int,)
user32.SystemParametersInfoW.argtypes = (wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT)
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.restype = wt.BOOL
user32.AppendMenuW.argtypes = (wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR)
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = (wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wt.HWND, ctypes.c_void_p)
user32.DestroyMenu.argtypes = (wt.HMENU,)
user32.SetForegroundWindow.argtypes = (wt.HWND,)
user32.OpenClipboard.argtypes = (wt.HWND,)
user32.EmptyClipboard.restype = wt.BOOL
user32.SetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = (wt.UINT, wt.HANDLE)
user32.CloseClipboard.restype = wt.BOOL
user32.TrackMouseEvent.argtypes = (ctypes.POINTER(TRACKMOUSEEVENT),)
user32.MessageBoxW.argtypes = (wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT)
user32.SetProcessDPIAware.restype = wt.BOOL
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = (wt.HWND,)
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = (wt.HWND, wt.HDC)
user32.GetDpiForSystem.restype = wt.UINT
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = (wt.LPCWSTR, wt.LPCWSTR)
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(RECT))
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowVisible.argtypes = (wt.HWND,)
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = (wt.HWND, ctypes.c_int)
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = (wt.HWND, ctypes.c_int, ctypes.c_long)
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
user32.AttachThreadInput.restype = wt.BOOL
user32.AttachThreadInput.argtypes = (wt.DWORD, wt.DWORD, wt.BOOL)
kernel32.GetCurrentThreadId.restype = wt.DWORD
user32.EnumChildWindows.restype = wt.BOOL
user32.EnumChildWindows.argtypes = (wt.HWND, CHILDENUMPROC, wt.LPARAM)
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = (wt.HWND, ctypes.c_wchar_p, ctypes.c_int)
user32.EnumWindows.restype = wt.BOOL
user32.EnumWindows.argtypes = (CHILDENUMPROC, wt.LPARAM)
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = (wt.HWND, wt.UINT)
user32.SetWinEventHook.restype = wt.HANDLE
user32.SetWinEventHook.argtypes = (wt.DWORD, wt.DWORD, wt.HMODULE, WINEVENTPROC,
                                   wt.DWORD, wt.DWORD, wt.DWORD)
user32.UnhookWinEvent.restype = wt.BOOL
user32.UnhookWinEvent.argtypes = (wt.HANDLE,)
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = (wt.HWND, ctypes.c_int, ctypes.c_ssize_t)
user32.IsWindow.restype = wt.BOOL
user32.IsWindow.argtypes = (wt.HWND,)
user32.WindowFromPoint.restype = wt.HWND
user32.WindowFromPoint.argtypes = (POINT,)
gdi32.GetPixel.restype = wt.DWORD
gdi32.GetPixel.argtypes = (wt.HDC, ctypes.c_int, ctypes.c_int)

gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = (wt.HDC,)
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = (wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD)
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = (wt.HDC, wt.HGDIOBJ)
gdi32.DeleteObject.argtypes = (wt.HGDIOBJ,)
gdi32.DeleteDC.argtypes = (wt.HDC,)
gdi32.GdiFlush.restype = wt.BOOL

kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = (wt.LPCWSTR,)
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wt.BOOL, wt.LPCWSTR)


def _dpi_scale() -> float:
    """Масштаб системы: 1.0 при 100 %, 2.0 при 200 % и т. д."""
    aware = False
    try:  # Windows 10 1703+
        aware = bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
    except Exception:
        pass
    if not aware:
        try:  # старые версии Windows
            aware = bool(user32.SetProcessDPIAware())
        except Exception:
            pass
    try:
        dpi = user32.GetDpiForSystem()
    except Exception:
        dpi = 96
    if not dpi:
        dpi = 96
    return max(1.0, dpi / 96.0)


def _work_area() -> tuple[int, int, int, int]:
    rect = RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, 1560, 992


def _shell_tray_hwnd():
    """Окно панели задач (Shell_TrayWnd) или None."""
    return user32.FindWindowW("Shell_TrayWnd", None)


def _taskbar_rect() -> tuple[int, int, int, int] | None:
    """Прямоугольник видимой панели задач; None, если она скрыта."""
    hwnd = _shell_tray_hwnd()
    if not hwnd or not user32.IsWindowVisible(hwnd):
        return None
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.right - rect.left < 16 or rect.bottom - rect.top < 8:
        return None  # панель автоскрыта и «сложилась»
    return rect.left, rect.top, rect.right, rect.bottom


# Служебные окна панели задач: это не значки, их не надо объезжать.
_SKIP_TASKBAR_CLASSES = {
    "Windows.UI.Composition.DesktopWindowContentBridge",
    "Windows.UI.Input.InputSite.WindowClass",
    "Windows.UI.Core.CoreWindow",
    "TrayDummySearchControl",
}


def _taskbar_children_rects() -> list[tuple[int, int, int, int]]:
    """Видимые элементы панели задач (Пуск, кнопки, трей) — занятые области."""
    tray = _shell_tray_hwnd()
    if not tray:
        return []
    found: list[tuple[int, int, int, int]] = []
    buffer = ctypes.create_unicode_buffer(256)

    def collect(hwnd, _lparam):
        length = user32.GetClassNameW(hwnd, buffer, 256)
        name = buffer.value if length else ""
        if name in _SKIP_TASKBAR_CLASSES or not user32.IsWindowVisible(hwnd):
            return True
        rect = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            if rect.right > rect.left and rect.bottom > rect.top:
                found.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumChildWindows(tray, CHILDENUMPROC(collect), 0)
    return found


CLR_INVALID = 0xFFFFFFFF


def _sample_taskbar_color() -> tuple[int, int, int] | None:
    """Средний цвет фона панели задач (для подстройки под светлую/тёмную тему)."""
    tray = _shell_tray_hwnd()
    rect = _taskbar_rect()
    if not tray or not rect:
        return None
    left, top, right, bottom = rect
    mid_y = (top + bottom) // 2
    hdc = user32.GetDC(None)
    if not hdc:
        return None
    samples = []
    try:
        for step in range(1, 25):
            x = left + (right - left) * step // 25
            if user32.WindowFromPoint(POINT(x, mid_y)) != tray:
                continue          # здесь значок или наша плашка — пропускаем
            color = gdi32.GetPixel(hdc, x, mid_y)
            if color == CLR_INVALID:
                continue
            samples.append(((color & 0xFF), ((color >> 8) & 0xFF), ((color >> 16) & 0xFF)))
            if len(samples) >= 6:
                break
    finally:
        user32.ReleaseDC(None, hdc)
    if not samples:
        return None
    return tuple(sum(channel) // len(samples) for channel in zip(*samples))


def _taskbar_is_dark() -> bool:
    color = _sample_taskbar_color()
    if color is None:
        return True
    red, green, blue = color
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) < 128


# Всплывающие панели оболочки: пока они открыты, плашку наверх не поднимаем,
# иначе она окажется поверх меню «Пуск», быстрых настроек и т. п.
_SHELL_POPUP_CLASSES = {
    "Windows.UI.Core.CoreWindow",
    "Windows.UI.Composition.DesktopWindowContentBridge",
    "Windows.UI.Input.InputSite.WindowClass",
    "XamlExplorerHostIslandWindow",
    "TopLevelWindowForOverflowXamlIsland",
    "TaskSwitcherWnd",
    "MultitaskingViewFrame",
    "ForegroundStaging",
}


def _shell_popup_open() -> bool:
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return False
    buffer = ctypes.create_unicode_buffer(256)
    length = user32.GetClassNameW(foreground, buffer, 256)
    return bool(length) and buffer.value in _SHELL_POPUP_CLASSES


def _zorder_indices(targets: tuple) -> dict:
    """Позиции окон в z-порядке сверху вниз: 0 — самое верхнее окно."""
    found: dict = {}
    counter = [0]

    def collect(hwnd, _lparam):
        if hwnd in targets and hwnd not in found:
            found[hwnd] = counter[0]
            if len(found) == len(targets):
                return False
        counter[0] += 1
        return True

    user32.EnumWindows(CHILDENUMPROC(collect), 0)
    return found


def _window_above(target: int, other: int) -> bool:
    """True, если окно other стоит в z-порядке выше окна target."""
    if not target or not other:
        return False
    hwnd = user32.GetWindow(target, GW_HWNDPREV)
    guard = 0
    while hwnd and guard < 4000:
        if hwnd == other:
            return True
        hwnd = user32.GetWindow(hwnd, GW_HWNDPREV)
        guard += 1
    return False


def _set_owner(hwnd: int, owner: int) -> bool:
    """Делает окно «принадлежащим» другому окну.

    Владелец всегда ниже своего окна в z-порядке — поэтому плашка, владелец
    которой — панель задач, не ныряет под неё при кликах.
    """
    try:
        if IS_64:
            user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, owner)
        else:
            user32.SetWindowLongW(hwnd, GWLP_HWNDPARENT, owner)
        return True
    except Exception:
        log("set owner:\n" + traceback.format_exc())
        return False


def _premultiplied_bgra(img: Image.Image) -> bytes:
    """Байты BGRA с премультиплицированной альфой — так их ждёт UpdateLayeredWindow."""
    red, green, blue, alpha = img.split()
    premultiplied = ImageChops.multiply(Image.merge("RGB", (red, green, blue)), alpha.convert("RGB"))
    pr, pg, pb = premultiplied.split()
    return Image.merge("RGBA", (pr, pg, pb, alpha)).tobytes("raw", "BGRA")


def _copy_clipboard(text: str) -> None:
    if not user32.OpenClipboard(None):
        return
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        kernel32.GlobalAlloc.restype = wt.HGLOBAL
        kernel32.GlobalAlloc.argtypes = (wt.UINT, ctypes.c_size_t)
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = (wt.HGLOBAL,)
        kernel32.GlobalUnlock.argtypes = (wt.HGLOBAL,)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = kernel32.GlobalLock(handle)
        if ptr:
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)
    finally:
        user32.CloseClipboard()


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "IPWidget"


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):         # собранный exe запускается сам
        return f'"{Path(sys.executable).resolve()}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    exe = pyw if pyw.exists() else exe
    return f'"{exe}" "{Path(__file__).resolve()}"'


def set_autostart(enabled: bool) -> bool:
    import winreg

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
    except Exception:
        log("autostart:\n" + traceback.format_exc())
        return False


def autostart_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except Exception:
        return False


PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def _reg_int(name: str, default: int = 0) -> int:
    """Числовое значение из ветки персонализации (0 — тёмная, 1 — светлая)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return int(value)
    except Exception:
        return default


def _app_is_light() -> bool:
    """Светлая тема приложений — по ней оформляются системные меню."""
    return _reg_int("AppsUseLightTheme", 0) == 1


def _system_is_light() -> bool:
    """Светлая системная тема — по ней перекрашивается панель задач."""
    return _reg_int("SystemUsesLightTheme", 0) == 1


def _theme_signature() -> tuple[int, int]:
    """Обе темы одной парой: при любом изменении перерисовываемся."""
    return (_reg_int("AppsUseLightTheme", 0), _reg_int("SystemUsesLightTheme", 0))


def _apply_menu_theme(light: bool) -> None:
    """Системные меню в тон теме: ForceLight/ForceDark (недокументированный uxtheme)."""
    try:
        uxtheme = ctypes.WinDLL("uxtheme")
        uxtheme[135](3 if light else 2)   # SetPreferredAppMode(ForceLight / ForceDark)
        uxtheme[136]()                    # FlushMenuThemes
    except Exception:
        pass  # на старых сборках Windows меню останутся по умолчанию


class IpWidget:
    """Окно-виджет с попиксельной прозрачностью: карточка или плашка на панели задач."""

    ID_REFRESH, ID_COPY_IP, ID_COPY_ALL = 1, 2, 3
    ID_TOPMOST, ID_AUTOSTART, ID_EXIT = 10, 11, 99
    ID_MODE = 12
    ID_INTERVAL = 20

    def __init__(self) -> None:
        self.cfg = load_config()
        self.scale = _dpi_scale()
        self.mode = self.cfg.get("mode", "bar")
        self.state = {"status": "loading", "updated": ""}
        self.hover = False
        self._tracking_leave = False
        self._drag_origin = None
        self._drag_window = None
        self._moved = False
        self._busy = False
        self._visible = False
        self._owner_tray = None
        self._bar_dark = True
        self._theme_ticks = 0
        self._theme_sig = _theme_signature()
        self._theme_refresh_ticks = 0
        self._render_cache: dict = {}
        self._bar_layout = None
        self._pending_focus = None
        self._last_app_fg = None
        self._fetch_manual = False
        self._lock = threading.Lock()
        self._pending = None
        self._image = None
        self._pos = None
        self._win_w = self._win_h = 0
        self._hit_refresh = (0, 0, 0, 0)
        self._wndproc = WNDPROC(self._on_message)
        self._winevent_cb = WINEVENTPROC(self._on_winevent)
        self._event_hook = None
        self.hwnd = None
        self._mutex = kernel32.CreateMutexW(None, False, "Local\\IPWidget_SingleInstance")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            log("второй экземпляр — выход")
            raise SystemExit(0)

    # -- запуск -----------------------------------------------------------
    def run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        wc.lpszClassName = "IPWidgetWindowClass"
        user32.RegisterClassW(ctypes.byref(wc))  # класс мог быть зарегистрирован ранее

        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "IPWidgetWindowClass", "IP-виджет", WS_POPUP,
            -32000, -32000, 10, 10, None, None, hinst, None)
        if not self.hwnd:
            log(f"CreateWindowEx failed: {ctypes.get_last_error()}")
            return

        # Мгновенная реакция на смену активного окна: клик по панели задач
        # поднимает её над плашкой, и без хука она успевает «мигнуть».
        self._event_hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None,
            self._winevent_cb, 0, 0, WINEVENT_OUTOFCONTEXT)

        self._render_and_show()
        self._set_visible(True)
        self._update_owner()
        interval = int(self.cfg.get("interval_min", REFRESH_MIN) or 0)
        if interval > 0:
            user32.SetTimer(self.hwnd, 1, interval * 60_000, None)
        user32.SetTimer(self.hwnd, 2, WATCH_MS, None)   # слежение за панелью задач
        self.refresh()

        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # -- отрисовка и позиционирование -------------------------------------
    def _render_key(self, dark: bool) -> tuple:
        """Ключ кэша готовой картинки: всё, что влияет на внешний вид."""
        state = self.state
        return (
            self.mode, self.hover, dark,
            state.get("status"), state.get("cc"), state.get("country_en"),
            state.get("city"), state.get("region"), state.get("utc"),
            state.get("isp"), state.get("ip"), state.get("updated"), state.get("error"),
        )

    def _render_and_show(self, keep_pos: bool = True) -> None:
        dark = self._bar_dark
        if self.mode == "bar":
            dark = _taskbar_is_dark()     # подстраиваемся под светлую/тёмную панель
            self._bar_dark = dark
        key = self._render_key(dark)
        cached = self._render_cache.get(key)
        if cached is None:
            img = render(self.state, self.scale, self.hover, self.mode, dark)
            data = _premultiplied_bgra(img)
            if len(self._render_cache) >= RENDER_CACHE_MAX:
                self._render_cache.pop(next(iter(self._render_cache)))
            self._render_cache[key] = (img, data)
        else:
            img, data = cached
        self._image = img
        self._win_w, self._win_h = img.size
        self._hit_refresh = self._refresh_hit_rect()
        if self.mode == "bar":
            pos = self._bar_pos()
            if pos:
                self._pos = pos
            elif not self._pos:
                self._pos = self._initial_pos()
        elif not (keep_pos and self._pos):
            self._pos = self._initial_pos()
        self._apply_image(img, data)

    def _refresh_hit_rect(self) -> tuple[int, int, int, int]:
        pad = int(round(SHADOW * self.scale))
        return (
            pad + int(round((CARD_W - 18 - 17) * self.scale)),
            pad + int(round(19 * self.scale)),
            pad + int(round((CARD_W - 18) * self.scale)),
            pad + int(round((19 + 17) * self.scale)),
        )

    def _bar_pos(self) -> tuple[int, int] | None:
        """Место плашки в свободной левой части панели задач (с кэшем)."""
        rect = _taskbar_rect()
        if not rect:
            return None                      # панель скрыта — плашку тоже прячем
        cached = self._bar_layout
        if cached and cached[0] == rect and cached[1] == self._win_w:
            return cached[2]
        pos = self._compute_bar_pos(rect)
        self._bar_layout = (rect, self._win_w, pos)
        return pos

    def _compute_bar_pos(self, rect: tuple[int, int, int, int]) -> tuple[int, int]:
        left, top, right, bottom = rect
        margin = int(round(10 * self.scale))
        if ((right - left) < self._win_w + 2 * margin
                or (bottom - top) < int(round(20 * self.scale))):
            # панель нестандартная (например, вертикальная) — левый нижний угол
            work_left, _wt, _wr, work_bottom = _work_area()
            return (int(work_left + int(round(12 * self.scale))),
                    int(work_bottom - self._win_h - int(round(12 * self.scale))))

        y = int(top + max(0, (bottom - top - self._win_h) // 2))
        # ищем первый промежуток между элементами панели, куда влезает плашка
        x = left + margin
        for occupied_left, _ot, occupied_right, _ob in sorted(_taskbar_children_rects()):
            if x + self._win_w <= occupied_left - margin:
                break
            x = max(x, occupied_right + margin)
        if x + self._win_w > right - margin:
            x = left + margin
        return (int(x), y)

    def _initial_pos(self) -> tuple[int, int]:
        wl, wtp, wr, wb = _work_area()
        pad = int(round(SHADOW * self.scale))
        x = self.cfg.get("x")
        y = self.cfg.get("y")
        if x is None or y is None:
            x = wr - self._win_w - int(round(16 * self.scale))
            y = wb - self._win_h - int(round(16 * self.scale))
        x = max(wl, min(int(x), max(wl, wr - self._win_w)))
        y = max(wtp, min(int(y), max(wtp, wb - self._win_h)))
        _ = pad
        return int(x), int(y)

    def _apply_image(self, img: Image.Image, data: bytes | None = None) -> None:
        w, h = img.size
        hdc_screen = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(hdc_screen)
        try:
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0
            bits = ctypes.c_void_p()
            hbm = gdi32.CreateDIBSection(mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            old = gdi32.SelectObject(mem_dc, hbm)
            if data is None:
                data = _premultiplied_bgra(img)
            ctypes.memmove(bits, data, len(data))
            gdi32.GdiFlush()

            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            pt_dst = POINT(self._pos[0], self._pos[1])
            size = SIZE(w, h)
            src = POINT(0, 0)
            user32.UpdateLayeredWindow(self.hwnd, hdc_screen, ctypes.byref(pt_dst),
                                       ctypes.byref(size), mem_dc, ctypes.byref(src),
                                       0, ctypes.byref(blend), ULW_ALPHA)
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(hbm)
        finally:
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, hdc_screen)

    def _move_to(self, x: int, y: int) -> None:
        self._pos = (int(x), int(y))
        user32.SetWindowPos(self.hwnd, None, int(x), int(y), 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

    def _apply_topmost(self) -> None:
        flag = HWND_TOPMOST if self.cfg.get("topmost", True) else HWND_NOTOPMOST
        user32.SetWindowPos(self.hwnd, flag, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def _set_visible(self, visible: bool) -> None:
        if visible == self._visible:
            return
        self._visible = visible
        log("watch: " + ("show" if visible else "hide"))
        if visible:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            if self.cfg.get("topmost", True):
                user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        else:
            user32.ShowWindow(self.hwnd, SW_HIDE)

    def _taskbar_above(self) -> bool:
        """Панель задач оказалась выше плашки (клик по панели поднимает её)."""
        tray = _shell_tray_hwnd()
        if not tray or not self.hwnd:
            return False
        return _window_above(self.hwnd, tray)

    def _enforce_top(self) -> None:
        """Быстро возвращает плашку над панелью задач, чтобы она не мигала."""
        if self.mode != "bar" or not self._visible or not self.hwnd:
            return
        if _shell_popup_open() or not self._taskbar_above():
            return
        log("watch: raise (панель задач поднялась выше)")
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        # Панель может подняться ещё раз, уже после нашего подъёма (активация
        # завершается позже) — перепроверяем чуть позже одним выстрелом.
        user32.SetTimer(self.hwnd, 4, 25, None)

    def _update_owner(self) -> None:
        """Владелец окна — панель задач: владелец всегда ниже своего окна."""
        if not self.hwnd:
            return
        tray = _shell_tray_hwnd() if self.mode == "bar" else None
        if tray == self._owner_tray:
            return
        if _set_owner(self.hwnd, tray or 0):
            self._owner_tray = tray or None

    def _watch(self) -> None:
        """Держит плашку приклеенной к левому углу панели задач."""
        if self.mode != "bar" or not self._win_w:
            return
        self._update_owner()
        pos = self._bar_pos()
        if pos is None:
            self._set_visible(False)   # панель скрыта (автоскрытие, полный экран)
            return
        if self._pos != pos:
            log(f"watch: move {pos} (было {self._pos})")
            self._move_to(*pos)
        self._set_visible(True)
        # Реагируем на смену тёмной/светлой темы сразу, а перерисовку повторяем
        # несколько тиков — панель задач перекрашивается с небольшой задержкой.
        signature = _theme_signature()
        if signature != self._theme_sig:
            self._theme_sig = signature
            self._theme_refresh_ticks = 3
        if self._theme_refresh_ticks > 0:
            self._theme_refresh_ticks -= 1
            self._render_cache.clear()
            self._render_and_show()
        # раз в минуту — страховочная проверка темы панели
        # (основная реакция — на WM_SETTINGCHANGE "ImmersiveColorSet")
        self._theme_ticks += 1
        if self._theme_ticks >= 60:
            self._theme_ticks = 0
            if _taskbar_is_dark() != self._bar_dark:
                self._theme_refresh_ticks = 2
                self._render_cache.clear()
                self._render_and_show()
        self._enforce_top()

    def _on_theme_changed(self) -> None:
        """Сменилась тема Windows: обновляем цвета текста, меню и кэш картинок."""
        self._theme_sig = _theme_signature()
        self._theme_refresh_ticks = 5
        self._render_cache.clear()
        _apply_menu_theme(_app_is_light())
        self._render_and_show()

    def _set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self.cfg["mode"] = mode
        save_config(self.cfg)
        self.hover = False
        self._pos = None
        self._render_and_show(keep_pos=False)
        self._update_owner()
        if mode == "card":
            self._set_visible(True)
            self._apply_topmost()
        else:
            self._watch()
            self._enforce_top()

    def _set_interval(self, minutes: int) -> None:
        """Меняет период автообновления из меню и перезапускает таймер."""
        minutes = max(0, int(minutes))
        self.cfg["interval_min"] = minutes
        save_config(self.cfg)
        user32.KillTimer(self.hwnd, 1)
        if minutes > 0:
            user32.SetTimer(self.hwnd, 1, minutes * 60_000, None)
        if DEBUG:
            log(f"interval: {minutes} мин")

    def _save_position(self) -> None:
        if self.mode == "card" and self._pos:
            self.cfg["x"], self.cfg["y"] = int(self._pos[0]), int(self._pos[1])
            save_config(self.cfg)

    # -- данные ------------------------------------------------------------
    def refresh(self, manual: bool = False) -> None:
        """Обновляет данные.

        manual=True (клик, пункт меню) — с надписью «Определяю…».
        Автообновление по таймеру идёт молча: пока не пришли новые данные,
        виджет не меняется.
        """
        if self._busy:
            return
        self._fetch_manual = manual
        if manual:
            self.state["status"] = "loading"
            self._render_and_show()
        if DEBUG:
            log("refresh: " + ("manual" if manual else "auto"))
        self._busy = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            data = fetch_ip()
            try:
                ensure_flag(data["cc"])
            except Exception:
                log("flag download:\n" + traceback.format_exc())
            data["status"] = "ok"
            data["updated"] = time.strftime("%H:%M")
        except Exception as exc:
            log("fetch:\n" + traceback.format_exc())
            # тихое автообновление: не сбрасываем уже показанные данные
            if not self._fetch_manual and self.state.get("status") == "ok":
                self._busy = False
                return
            data = {"status": "error", "error": _short_error(exc), "updated": time.strftime("%H:%M")}
        with self._lock:
            self._pending = data
        user32.PostMessageW(self.hwnd, WM_APP, 0, 0)

    def _apply_pending(self) -> None:
        with self._lock:
            data, self._pending = self._pending, None
        self._busy = False
        if not data:
            return
        self.state = {k: v for k, v in data.items() if k != "status"}
        self.state["status"] = data.get("status", "ok")
        self._render_and_show()

    # -- меню --------------------------------------------------------------
    def _force_foreground(self, previous) -> None:
        """Активирует окно, чтобы контекстное меню не закрывалось сразу."""
        if user32.SetForegroundWindow(self.hwnd):
            return
        try:
            other_thread = user32.GetWindowThreadProcessId(previous, None) if previous else 0
            my_thread = kernel32.GetCurrentThreadId()
            if other_thread and other_thread != my_thread:
                user32.AttachThreadInput(my_thread, other_thread, True)
                user32.SetForegroundWindow(self.hwnd)
                user32.AttachThreadInput(my_thread, other_thread, False)
        except Exception:
            pass

    def _restore_foreground(self, previous) -> None:
        """Возвращает фокус окну, которое было активным до меню."""
        if not previous or previous == self.hwnd:
            return
        for attempt in range(3):
            if user32.GetForegroundWindow() == previous:
                return
            try:
                current = user32.GetForegroundWindow()
                my_thread = kernel32.GetCurrentThreadId()
                previous_thread = user32.GetWindowThreadProcessId(previous, None)
                current_thread = user32.GetWindowThreadProcessId(current, None) if current else 0
                attached = []
                for thread in {current_thread, previous_thread}:
                    if thread and thread != my_thread:
                        user32.AttachThreadInput(my_thread, thread, True)
                        attached.append(thread)
                user32.SetForegroundWindow(previous)
                for thread in attached:
                    user32.AttachThreadInput(my_thread, thread, False)
            except Exception:
                log("restore foreground:\n" + traceback.format_exc())
            time.sleep(0.02)
        log(f"restore foreground: не удалось (ожидали {previous}, "
            f"получили {user32.GetForegroundWindow()})")

    def _show_menu(self) -> None:
        cursor = POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        current = user32.GetForegroundWindow()
        # если фокус был на панели задач — вернём его последнему настоящему окну
        previous = current if self._focusable_target(current) else self._last_app_fg
        # Меню ставим над виджетом (или под ним, если сверху нет места),
        # чтобы оно не накрывало саму плашку.
        anchor_x, anchor_y, align = self._menu_anchor(cursor)

        # Меню Windows требует активное окно-владелец, а виджет живёт как NOACTIVATE.
        # На время меню разрешаем активацию и возвращаем фокус прежнему окну.
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style & ~WS_EX_NOACTIVATE)
        cmd = 0
        try:
            self._force_foreground(previous)
            # меню оформляем в тон текущей теме Windows
            _apply_menu_theme(_app_is_light())
            menu = user32.CreatePopupMenu()
            if not menu:
                return
            try:
                topmost = MF_CHECKED if self.cfg.get("topmost", True) else 0
                auto = MF_CHECKED if autostart_enabled() else 0
                mode_label = ("Большая карточка на рабочем столе" if self.mode == "bar"
                              else "Прикрепить к панели задач")
                user32.AppendMenuW(menu, MF_STRING, self.ID_REFRESH, "Обновить")
                # подменю с периодом автообновления
                intervals = user32.CreatePopupMenu()
                if intervals:
                    current_interval = int(self.cfg.get("interval_min", REFRESH_MIN) or 0)
                    for index, (minutes, label) in enumerate(INTERVAL_OPTIONS):
                        checked = MF_CHECKED if minutes == current_interval else 0
                        user32.AppendMenuW(intervals, MF_STRING | checked,
                                           self.ID_INTERVAL + index, label)
                    # DestroyMenu(menu) уничтожает и вложенные подменю
                    user32.AppendMenuW(menu, MF_POPUP | MF_STRING, intervals, "Автообновление")
                user32.AppendMenuW(menu, MF_STRING, self.ID_COPY_IP, "Копировать IP")
                user32.AppendMenuW(menu, MF_STRING, self.ID_COPY_ALL, "Копировать страну и IP")
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(menu, MF_STRING, self.ID_MODE, mode_label)
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(menu, MF_STRING | topmost, self.ID_TOPMOST, "Поверх всех окон")
                user32.AppendMenuW(menu, MF_STRING | auto, self.ID_AUTOSTART, "Запускать при входе")
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(menu, MF_STRING, self.ID_EXIT, "Выход")
                cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | align,
                                            anchor_x, anchor_y, 0, self.hwnd, None)
                user32.PostMessageW(self.hwnd, 0, 0, 0)
            finally:
                user32.DestroyMenu(menu)
        finally:
            # фокус возвращаем, пока окно ещё активно, и повторяем после
            # восстановления NOACTIVATE — иначе окно остаётся в фокусе
            self._restore_foreground(previous)
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style)
            self._restore_foreground(previous)
            # окно, «принадлежащее» панели задач, может отдать ей фокус уже
            # после закрытия меню — проверим ещё раз чуть позже
            self._pending_focus = previous
            user32.SetTimer(self.hwnd, 5, 150, None)
        if cmd:
            self._menu_action(cmd)

    def _menu_anchor(self, cursor: POINT) -> tuple[int, int, int]:
        """Точка привязки меню: над виджетом, иначе под ним. Возвращает x, y и флаги."""
        if not self._pos:
            return cursor.x, cursor.y, 0
        left, top = self._pos
        pad = int(round((SHADOW if self.mode == "card" else 3) * self.scale))
        margin = int(round(6 * self.scale))
        x = int(left + pad)
        above = int(top - margin)
        if above > int(round(24 * self.scale)):
            return x, above, TPM_BOTTOMALIGN
        return x, int(top + self._win_h + margin), 0

    def _menu_action(self, cmd: int) -> None:
        try:
            if cmd == self.ID_REFRESH:
                self.refresh(manual=True)
            elif self.ID_INTERVAL <= cmd < self.ID_INTERVAL + len(INTERVAL_OPTIONS):
                self._set_interval(INTERVAL_OPTIONS[cmd - self.ID_INTERVAL][0])
            elif cmd == self.ID_COPY_IP:
                _copy_clipboard(self.state.get("ip", ""))
            elif cmd == self.ID_COPY_ALL:
                name = ru_country_name(self.state.get("cc", ""), self.state.get("country_en", ""))
                _copy_clipboard(f"{name} — {self.state.get('ip', '')}")
            elif cmd == self.ID_TOPMOST:
                self.cfg["topmost"] = not self.cfg.get("topmost", True)
                self._apply_topmost()
                save_config(self.cfg)
            elif cmd == self.ID_AUTOSTART:
                new_value = not autostart_enabled()
                if set_autostart(new_value):
                    self.cfg["autostart"] = new_value
                    save_config(self.cfg)
            elif cmd == self.ID_MODE:
                self._set_mode("card" if self.mode == "bar" else "bar")
            elif cmd == self.ID_EXIT:
                self._save_position()
                user32.DestroyWindow(self.hwnd)
        except Exception:
            log("menu:\n" + traceback.format_exc())

    # -- оконная процедура --------------------------------------------------
    def _on_winevent(self, _hook, event, hwnd, _id_object, _id_child, _thread, _when) -> None:
        """Хук EVENT_SYSTEM_FOREGROUND: сразу просим окно проверить z-порядок."""
        try:
            if event == EVENT_SYSTEM_FOREGROUND and hwnd:
                if self._focusable_target(hwnd):
                    self._last_app_fg = hwnd   # куда вернуть фокус после меню
                if self.hwnd:
                    user32.PostMessageW(self.hwnd, WM_TOPFIX, 0, 0)
        except Exception:
            pass

    def _focusable_target(self, hwnd) -> bool:
        """Можно ли отдать фокус этому окну (панель задач и всплывашки — нет)."""
        if not hwnd or hwnd == self.hwnd or not user32.IsWindow(hwnd):
            return False
        if hwnd == _shell_tray_hwnd():
            return False
        buffer = ctypes.create_unicode_buffer(256)
        length = user32.GetClassNameW(hwnd, buffer, 256)
        if not length or buffer.value in _SHELL_POPUP_CLASSES:
            return False
        return True

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_APP:
                self._apply_pending()
                return 0
            if msg == WM_TOPFIX:
                self._enforce_top()
                return 0
            if msg == WM_SETTINGCHANGE:
                # смена системной темы/цветов панели задач — перерисоваться
                if lparam:
                    try:
                        text = ctypes.cast(ctypes.c_void_p(lparam), ctypes.c_wchar_p).value
                    except Exception:
                        text = None
                    if text in ("ImmersiveColorSet", "ImmersiveColorPolicySet"):
                        self._on_theme_changed()
                return 0
            if msg == WM_THEMECHANGED:
                self._on_theme_changed()
                return 0
            if msg == WM_TIMER:
                if int(wparam) == 2:
                    self._watch()
                elif int(wparam) == 4:
                    user32.KillTimer(hwnd, 4)
                    self._enforce_top()
                elif int(wparam) == 5:
                    user32.KillTimer(hwnd, 5)
                    previous, self._pending_focus = self._pending_focus, None
                    if previous:
                        foreground = user32.GetForegroundWindow()
                        if foreground in (0, self.hwnd, _shell_tray_hwnd()):
                            self._restore_foreground(previous)
                else:
                    self.refresh()
                return 0
            if msg == WM_LBUTTONDOWN:
                if self.mode == "bar":
                    return 0      # плашку не перетаскиваем: она привязана к панели
                self._drag_origin = self._cursor()
                self._drag_window = self._pos
                self._moved = False
                user32.SetCapture(hwnd)
                return 0
            if msg == WM_MOUSEMOVE:
                if self._drag_origin and self._drag_window:
                    cx, cy = self._cursor()
                    dx, dy = cx - self._drag_origin[0], cy - self._drag_origin[1]
                    if abs(dx) > 2 or abs(dy) > 2:
                        self._moved = True
                    self._move_to(self._drag_window[0] + dx, self._drag_window[1] + dy)
                else:
                    self._update_hover(self._cursor())
                if not self._tracking_leave:
                    tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), 0x00000002, hwnd, 0)
                    user32.TrackMouseEvent(ctypes.byref(tme))
                    self._tracking_leave = True
                return 0
            if msg == WM_MOUSELEAVE:
                self._tracking_leave = False
                if self.hover:
                    self.hover = False
                    self._render_and_show()
                return 0
            if msg == WM_LBUTTONUP:
                if self.mode == "bar":
                    self._start_refresh()
                    return 0
                if self._drag_origin:
                    user32.ReleaseCapture()
                    self._drag_origin = None
                    if self._moved:
                        self._save_position()
                    else:
                        self._start_refresh()
                return 0
            if msg == WM_RBUTTONUP:
                self._show_menu()
                return 0
            if msg == WM_SETCURSOR:
                if self._hover_ready():
                    user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND)))
                    return 1
            if msg == WM_NCHITTEST:
                return HTCLIENT
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._save_position()
                if self._event_hook:
                    user32.UnhookWinEvent(self._event_hook)
                    self._event_hook = None
                user32.KillTimer(hwnd, 1)
                user32.KillTimer(hwnd, 2)
                user32.KillTimer(hwnd, 4)
                user32.KillTimer(hwnd, 5)
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log("wndproc:\n" + traceback.format_exc())
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _cursor(self) -> tuple[int, int]:
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _start_refresh(self) -> None:
        self.refresh(manual=True)

    def _hover_ready(self) -> bool:
        cx, cy = self._cursor()
        if self._pos is None:
            return False
        lx, ly = cx - self._pos[0], cy - self._pos[1]
        if self.mode == "bar":
            return 0 <= lx < self._win_w and 0 <= ly < self._win_h
        x0, y0, x1, y1 = self._hit_refresh
        return x0 <= lx <= x1 and y0 <= ly <= y1

    def _update_hover(self, _cursor: tuple[int, int]) -> None:
        hover = self._hover_ready()
        if hover != self.hover:
            self.hover = hover
            self._render_and_show()


def _short_error(exc: Exception) -> str:
    text = str(exc).split(";")[0].strip()
    lower = text.lower()
    if "timed out" in lower or "timeout" in lower:
        return "сервер не ответил"
    if "name or service" in lower or "getaddrinfo" in lower or "resolve" in lower:
        return "нет доступа к сети"
    if "certificate" in lower:
        return "ошибка сертификата"
    return text[:80] or "неизвестная ошибка"


# --------------------------------------------------------------------------
#  Точка входа
# --------------------------------------------------------------------------

def _preview(out_path: str, what: str, mode: str = "card", light: bool = False) -> None:
    demo = {"status": "ok", "ip": "203.0.113.42", "cc": "NL", "country_en": "Netherlands",
            "city": "Rotterdam", "region": "South Holland", "utc": "+01:00",
            "isp": "Snel.com B.V.", "updated": time.strftime("%H:%M")}
    if what == "loading":
        demo = {"status": "loading", "updated": ""}
    elif what == "error":
        demo = {"status": "error", "error": "нет доступа к сети", "updated": time.strftime("%H:%M")}
    ensure_flag(demo.get("cc", ""))
    img = render(demo, 2.0, hover=what == "ok", mode=mode, bar_dark=not light)
    if mode == "bar":
        background = (243, 243, 243, 255) if light else (30, 30, 34, 255)
    else:
        background = (46, 46, 52, 255)
    canvas = Image.new("RGBA", img.size, background)
    canvas.alpha_composite(img)
    canvas.convert("RGB").save(out_path)


def main() -> None:
    args = sys.argv[1:]
    _apply_menu_theme(_app_is_light())
    try:
        if "--preview" in args:
            idx = args.index("--preview")
            out = args[idx + 1] if idx + 1 < len(args) else "preview.png"
            rest = args[idx + 2:]
            mode = "bar" if "bar" in rest else "card"
            light = "light" in rest
            what = next((a for a in rest if a in ("ok", "loading", "error")), "ok")
            _preview(out, what, mode, light)
            print("saved:", out)
            return
        IpWidget().run()
    except SystemExit:
        pass
    except Exception:
        log("main:\n" + traceback.format_exc())
        try:
            user32.MessageBoxW(None, "Не удалось запустить виджет.\n\n"
                                     f"Подробности в файле:\n{LOG_PATH}", "IP-виджет", 0x10)
        except Exception:
            pass


if __name__ == "__main__":
    main()
