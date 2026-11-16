"""Сеть: внешний IP, страна, провайдер и флаги.

Правила:
  * только HTTPS с проверкой сертификата — ответ нельзя подменить по дороге;
  * всё, что пришло от сервиса, проверяется (IP — через ipaddress, код страны —
    ровно две латинские буквы, текст — без управляющих символов и с ограничением длины);
  * источники опрашиваются по очереди, но последний рабочий идёт первым, а упавший
    на время откладывается — не тратим по 7 с на заведомо мёртвый сервис.
"""

from __future__ import annotations

import io
import ipaddress
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable, Optional

from PIL import Image

from . import REPO_URL, __version__
from .logs import log
from .paths import CACHE_DIR

USER_AGENT = f"IPWidget/{__version__} (+{REPO_URL})"
TIMEOUT = 6.0              # секунд на один запрос
OTHER_FAMILY_TIMEOUT = 3.0
MAX_BODY = 100_000         # байт JSON-ответа
MAX_FLAG_BYTES = 200_000
SOURCE_COOLDOWN = 300.0    # секунд, на которые отодвигается упавший источник
OTHER_FAMILY_TTL = 3600.0  # как долго помним «второй» IP (v4 <-> v6)
FLAG_URL = "https://flagcdn.com/w160/{cc}.png"


# --------------------------------------------------------------------------
#  Ошибки и проверка данных
# --------------------------------------------------------------------------

class NetError(Exception):
    """Сбой получения данных. kind — ключ для i18n: timeout, network, cert, server,
    bad_response, unknown."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail or kind


def classify_error(exc: BaseException) -> NetError:
    """Приводит любое исключение urllib/socket/json к NetError с понятным видом."""
    if isinstance(exc, NetError):
        return exc
    reason = getattr(exc, "reason", None)
    if isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError)):
        return NetError("timeout", str(exc))
    if isinstance(exc, ssl.SSLCertVerificationError) or isinstance(reason, ssl.SSLCertVerificationError):
        return NetError("cert", str(exc))
    if isinstance(exc, urllib.error.HTTPError):
        return NetError("server", f"HTTP {exc.code}")
    if isinstance(exc, (urllib.error.URLError, OSError)):
        return NetError("network", str(exc))
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return NetError("bad_response", f"{type(exc).__name__}: {exc}")
    return NetError("unknown", f"{type(exc).__name__}: {exc}")


def clean_ip(value: object) -> str:
    """Канонический IP-адрес или пустая строка."""
    if not isinstance(value, str):
        return ""
    try:
        return str(ipaddress.ip_address(value.strip().split("%", 1)[0]))
    except ValueError:
        return ""


def clean_cc(value: object) -> str:
    """Код страны: ровно две латинские буквы в верхнем регистре, иначе пусто."""
    if isinstance(value, str):
        value = value.strip()
        if len(value) == 2 and value.isascii() and value.isalpha():
            return value.upper()
    return ""


def clean_text(value: object, limit: int = 80) -> str:
    """Безопасная строка для показа: без управляющих символов, обрезанная."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value if ch.isprintable()).strip()
    return text[:limit]


def ip_family(ip: str) -> int:
    """4, 6 или 0 (не IP)."""
    try:
        return ipaddress.ip_address(ip).version
    except ValueError:
        return 0


def shorten_ip(ip: str, limit: int = 18) -> str:
    """Для плашки: IPv4 как есть, длинный IPv6 — начало…конец (полный — в подсказке)."""
    if len(ip) <= limit or ":" not in ip:
        return ip
    parts = ip.split(":")
    head = [p for p in parts[:2] if p]
    tail = next((p for p in reversed(parts) if p), "")
    short = f"{':'.join(head)}:…:{tail}" if head else f"…:{tail}"
    return short if len(short) < len(ip) else ip


def offset_text(raw: object) -> str:
    """'+0300' | '+03:00' | 10800 -> '+03:00'; непонятное -> ''."""
    if isinstance(raw, bool):
        return ""
    if isinstance(raw, (int, float)):
        seconds = int(raw)
        sign = "+" if seconds >= 0 else "-"
        seconds = abs(seconds)
        return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"
    if isinstance(raw, str):
        text = raw.strip()
        if len(text) == 5 and text[0] in "+-" and text[1:].isdigit():
            return f"{text[:3]}:{text[3:]}"
        if len(text) == 6 and text[0] in "+-" and text[3] == ":" and (text[1:3] + text[4:]).isdigit():
            return text
    return ""


# --------------------------------------------------------------------------
#  Модель
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class IpInfo:
    ip: str
    cc: str
    country_en: str = ""
    city: str = ""
    region: str = ""
    tz: str = ""            # '+03:00' или название пояса
    isp: str = ""
    source: str = ""        # хост сервиса, давшего ответ
    ip_other: str = ""      # адрес второго семейства (v4 <-> v6), если удалось узнать
    fetched_at: float = 0.0  # time.time()

    @property
    def ipv4(self) -> str:
        return self.ip if ip_family(self.ip) == 4 else (self.ip_other if ip_family(self.ip_other) == 4 else "")

    @property
    def ipv6(self) -> str:
        return self.ip if ip_family(self.ip) == 6 else (self.ip_other if ip_family(self.ip_other) == 6 else "")


def _build(source: str, ip: object, cc: object, country: object = "", city: object = "",
           region: object = "", tz: object = "", isp: object = "") -> IpInfo:
    clean = clean_ip(ip)
    if not clean:
        raise NetError("bad_response", f"{source}: нет корректного IP")
    return IpInfo(ip=clean, cc=clean_cc(cc), country_en=clean_text(country), city=clean_text(city),
                  region=clean_text(region), tz=clean_text(tz, 40), isp=clean_text(isp),
                  source=source)


# --------------------------------------------------------------------------
#  HTTP
# --------------------------------------------------------------------------

def _open(url: str, timeout: float, limit: int = MAX_BODY) -> bytes:
    if not url.lower().startswith("https://"):
        raise NetError("unknown", f"небезопасный адрес: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(limit + 1)
    except Exception as exc:  # noqa: BLE001 — приводим всё к NetError
        raise classify_error(exc) from exc
    if len(body) > limit:
        raise NetError("bad_response", "слишком длинный ответ")
    return body


def http_json(url: str, timeout: float = TIMEOUT) -> dict:
    try:
        data = json.loads(_open(url, timeout).decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise NetError("bad_response", f"не JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise NetError("bad_response", "ожидался объект JSON")
    return data


# --------------------------------------------------------------------------
#  Источники (все HTTPS)
# --------------------------------------------------------------------------

def _from_ipwho(d: dict) -> IpInfo:
    if d.get("success") is False:
        raise NetError("server", str(d.get("message") or "ipwho.is: отказ"))
    tz = d.get("timezone") if isinstance(d.get("timezone"), dict) else {}
    conn = d.get("connection") if isinstance(d.get("connection"), dict) else {}
    return _build("ipwho.is", d.get("ip"), d.get("country_code"), d.get("country"), d.get("city"),
                  d.get("region"), offset_text(tz.get("utc")) or tz.get("id"),
                  conn.get("isp") or conn.get("org"))


def _from_ipapi(d: dict) -> IpInfo:
    if d.get("error"):
        raise NetError("server", str(d.get("reason") or "ipapi.co: отказ"))
    return _build("ipapi.co", d.get("ip"), d.get("country_code"), d.get("country_name"),
                  d.get("city"), d.get("region"), offset_text(d.get("utc_offset")) or d.get("timezone"),
                  d.get("org"))


def _from_ipinfo(d: dict) -> IpInfo:
    if d.get("error") or d.get("status") == 429:
        raise NetError("server", "ipinfo.io: отказ")
    org = clean_text(d.get("org"))
    if org[:2] == "AS" and " " in org:      # 'AS12389 Rostelecom' -> 'Rostelecom'
        org = org.split(" ", 1)[1]
    return _build("ipinfo.io", d.get("ip"), d.get("country"), "", d.get("city"), d.get("region"),
                  d.get("timezone"), org)


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    parse: Callable[[dict], IpInfo]


SOURCES: tuple[Source, ...] = (
    Source("ipwho.is", "https://ipwho.is/", _from_ipwho),
    Source("ipapi.co", "https://ipapi.co/json/", _from_ipapi),
    Source("ipinfo.io", "https://ipinfo.io/json", _from_ipinfo),
)

# Хосты, отвечающие только по одному семейству адресов (для «второго» IP)
_V4_ONLY = "https://api.ipify.org"
_V6_ONLY = "https://api6.ipify.org"


class Fetcher:
    """Опрашивает источники: последний рабочий первым, упавшие — с «остыванием»."""

    def __init__(self, sources: tuple[Source, ...] = SOURCES,
                 clock: Callable[[], float] = time.monotonic,
                 getter: Callable[[str, float], dict] = http_json,
                 text_getter: Optional[Callable[[str, float], str]] = None) -> None:
        self.sources = sources
        self._clock = clock
        self._get = getter
        self._get_text = text_getter or self._default_text
        self._last_good: Optional[str] = None
        self._cooldown: dict[str, float] = {}
        self._other: tuple[str, float] = ("", 0.0)     # (адрес, когда проверен)

    @staticmethod
    def _default_text(url: str, timeout: float) -> str:
        return _open(url, timeout, limit=200).decode("ascii", "replace")

    # -- порядок опроса ------------------------------------------------------
    def order(self) -> list[Source]:
        now = self._clock()
        ordered = sorted(self.sources, key=lambda s: 0 if s.name == self._last_good else 1)
        ready = [s for s in ordered if self._cooldown.get(s.name, 0.0) <= now]
        return ready or ordered      # все «остыли» — пробуем всех, лишь бы что-то получить

    def fetch(self, want_other_family: bool = True) -> IpInfo:
        errors: list[NetError] = []
        for source in self.order():
            try:
                info = source.parse(self._get(source.url, TIMEOUT))
            except Exception as exc:  # noqa: BLE001
                err = classify_error(exc)
                errors.append(err)
                self._cooldown[source.name] = self._clock() + SOURCE_COOLDOWN
                log.info("источник %s не ответил: %s", source.name, err.detail)
                continue
            self._last_good = source.name
            self._cooldown.pop(source.name, None)
            info = replace(info, fetched_at=time.time())
            if want_other_family:
                info = replace(info, ip_other=self._other_family(info.ip))
            return info
        raise self._aggregate(errors)

    @staticmethod
    def _aggregate(errors: list[NetError]) -> NetError:
        if not errors:
            return NetError("unknown", "нет источников")
        kinds = {e.kind for e in errors}
        kind = errors[0].kind if len(kinds) == 1 else next(
            (k for k in ("server", "bad_response", "cert", "timeout") if k in kinds), "network")
        return NetError(kind, "; ".join(e.detail for e in errors))

    # -- второй адрес --------------------------------------------------------
    def _other_family(self, primary_ip: str) -> str:
        """IPv4 для IPv6-ответа и наоборот. Ошибка — не повод ронять обновление."""
        family = ip_family(primary_ip)
        if family not in (4, 6):
            return ""
        now = self._clock()
        cached, checked = self._other
        if cached and ip_family(cached) != family and now - checked < OTHER_FAMILY_TTL:
            return cached
        url = _V4_ONLY if family == 6 else _V6_ONLY
        try:
            other = clean_ip(self._get_text(url, OTHER_FAMILY_TIMEOUT))
        except Exception as exc:  # noqa: BLE001
            log.debug("второй адрес не получен: %s", classify_error(exc).detail)
            other = ""
        if other and ip_family(other) == family:
            other = ""
        self._other = (other, now)
        return other


# --------------------------------------------------------------------------
#  Флаги: скачиваем один раз, проверяем, что это настоящий PNG
# --------------------------------------------------------------------------

def flag_path(cc: str):
    return CACHE_DIR / f"{cc.lower()}.png"


def _verified_png(raw: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            if probe.format != "PNG" or not (8 <= probe.width <= 2000 and 8 <= probe.height <= 2000):
                return False
            probe.verify()
        with Image.open(io.BytesIO(raw)) as again:   # verify() делает файл непригодным
            again.load()
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_flag(cc: str) -> None:
    """Кладёт флаг страны в кэш, если его нет или он повреждён."""
    cc = clean_cc(cc)
    if not cc:
        return
    path = flag_path(cc)
    if path.exists() and path.stat().st_size > 0:
        try:
            if _verified_png(path.read_bytes()):
                return
        except OSError:
            pass
        log.info("флаг %s в кэше повреждён — скачиваю заново", cc)
        _unlink(path)
    raw = _open(FLAG_URL.format(cc=cc.lower()), 8.0, limit=MAX_FLAG_BYTES)
    if not _verified_png(raw):
        raise NetError("bad_response", f"флаг {cc}: это не PNG")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


def _unlink(path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def load_flag(cc: str) -> Optional[Image.Image]:
    """Картинка флага из кэша; повреждённый файл удаляем, чтобы он скачался заново."""
    cc = clean_cc(cc)
    if not cc:
        return None
    path = flag_path(cc)
    try:
        if path.exists() and path.stat().st_size > 0:
            with Image.open(path) as im:
                return im.convert("RGBA")
    except Exception:  # noqa: BLE001
        log.warning("флаг %s не читается — удаляю из кэша", cc, exc_info=True)
        _unlink(path)
    return None
