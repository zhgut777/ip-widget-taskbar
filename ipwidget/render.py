"""Отрисовка плашки и карточки-подсказки на Pillow (без Win32, проверяется в тестах).

Все размеры задаются в логических пикселях и умножаются на масштаб экрана.
Текст и флаги рисуются через маски с корректной (непремультиплицированной)
альфой — иначе по краям букв появляется тёмная кайма.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from . import i18n
from .i18n import t
from .netinfo import IpInfo, load_flag, shorten_ip
from .state import Alert, Status, View

RGBA = tuple[int, int, int, int]

BAR_H = 34             # высота плашки, логических px
BAR_MAX_W = 300        # предельная ширина плашки
TIP_W = 308            # ширина карточки-подсказки
SS = 4                 # суперсэмплинг для скруглений


@dataclass(frozen=True)
class Theme:
    title: RGBA
    ip: RGBA
    dot: RGBA
    sub: RGBA
    placeholder: RGBA
    hover: RGBA
    error: RGBA
    warn: RGBA
    bad: RGBA
    tip_bg: RGBA
    tip_border: RGBA
    divider: RGBA


DARK = Theme(
    title=(244, 244, 247, 255), ip=(208, 213, 221, 255), dot=(150, 155, 163, 255),
    sub=(160, 165, 174, 255), placeholder=(70, 74, 84, 255), hover=(255, 255, 255, 30),
    error=(255, 118, 118, 255), warn=(255, 196, 77, 255), bad=(255, 99, 99, 255),
    tip_bg=(40, 41, 46, 246), tip_border=(255, 255, 255, 34), divider=(255, 255, 255, 24))

LIGHT = Theme(
    title=(26, 27, 31, 255), ip=(56, 60, 68, 255), dot=(110, 115, 124, 255),
    sub=(96, 101, 110, 255), placeholder=(198, 202, 210, 255), hover=(0, 0, 0, 24),
    error=(190, 40, 40, 255), warn=(176, 110, 0, 255), bad=(200, 40, 40, 255),
    tip_bg=(250, 250, 251, 246), tip_border=(0, 0, 0, 40), divider=(0, 0, 0, 26))


def theme_for(dark: bool) -> Theme:
    return DARK if dark else LIGHT


# --------------------------------------------------------------------------
#  Шрифты и примитивы
# --------------------------------------------------------------------------

_FONT_FILES = {
    "semibold": ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"),
    "regular": ("segoeui.ttf", "arial.ttf"),
    "mono": ("consola.ttf", "cour.ttf"),
}
_fonts: dict = {}


def font(kind: str, size_px: int):
    key = (kind, size_px)
    if key not in _fonts:
        found = None
        for name in _FONT_FILES[kind]:
            try:
                found = ImageFont.truetype(name, size_px)
                break
            except OSError:
                continue
        _fonts[key] = found or ImageFont.load_default(size_px)
    return _fonts[key]


_MEASURE = ImageDraw.Draw(Image.new("L", (1, 1)))


def text_width(text: str, fnt) -> float:
    return _MEASURE.textlength(text, font=fnt) if text else 0.0


def fit_text(text: str, fnt, max_w: float) -> str:
    """Обрезает строку с многоточием, чтобы она влезла в max_w."""
    if not text or text_width(text, fnt) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if text_width(text[:mid].rstrip() + "…", fnt) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + "…") if lo else "…"


def wrap_text(text: str, fnt, max_w: float, max_lines: int = 2) -> list[str]:
    """Перенос по словам; лишнее в последней строке заменяется многоточием."""
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and text_width(trial, fnt) > max_w:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fit_text(lines[-1] + "…", fnt, max_w)
    return [fit_text(line, fnt, max_w) for line in lines] or [""]


def draw_text(img: Image.Image, xy: tuple, text: str, fnt, fill: RGBA, anchor: str = "ls") -> None:
    """Текст с правильной альфой: маска глифов -> цветной слой -> alpha_composite."""
    if not text:
        return
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).text(xy, text, font=fnt, fill=255, anchor=anchor)
    if fill[3] < 255:
        mask = mask.point(lambda v, a=fill[3]: v * a // 255)
    layer = Image.new("RGBA", img.size, fill[:3] + (0,))
    layer.putalpha(mask)
    img.alpha_composite(layer)


def rounded_mask(w: int, h: int, radius: int) -> Image.Image:
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=255)
    return mask.resize((w, h), Image.LANCZOS)


def rounded_box(w: int, h: int, radius: int, fill: Optional[RGBA] = None,
                outline: Optional[RGBA] = None, outline_w: int = 1) -> Image.Image:
    """RGBA-картинка скруглённой рамки/заливки со сглаживанием."""
    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=fill, outline=outline,
        width=max(1, outline_w * SS) if outline else 0)
    return big.resize((w, h), Image.LANCZOS)


def dim(color: RGBA, factor: float) -> RGBA:
    return color[:3] + (int(color[3] * factor),)


_flag_cache: dict = {}


def flag_image(cc: str, box_w: int, box_h: int, radius: int) -> Optional[Image.Image]:
    """Флаг, вписанный в рамку и с уже скруглёнными углами (в альфа-канале)."""
    key = (cc, box_w, box_h, radius)
    if key in _flag_cache:
        return _flag_cache[key]
    flag = load_flag(cc) if cc else None
    if flag is None or not flag.height:
        return None
    ratio = min(box_w / flag.width, box_h / flag.height)
    size = (max(1, int(flag.width * ratio)), max(1, int(flag.height * ratio)))
    flag = flag.resize(size, Image.LANCZOS)
    flag.putalpha(rounded_mask(size[0], size[1], radius))
    if len(_flag_cache) >= 16:
        _flag_cache.pop(next(iter(_flag_cache)))
    _flag_cache[key] = flag
    return flag


def clear_caches() -> None:
    """Сбрасывает кэш флагов (например, после того как скачался новый)."""
    _flag_cache.clear()


def _px(value: float, scale: float) -> int:
    return int(round(value * scale))


# --------------------------------------------------------------------------
#  Плашка
# --------------------------------------------------------------------------

def _alert_colors(alert: Alert, theme: Theme) -> Optional[tuple[RGBA, RGBA]]:
    if alert == Alert.MISMATCH:
        c = theme.bad
    elif alert == Alert.CHANGED:
        c = theme.warn
    else:
        return None
    return c[:3] + (46,), c[:3] + (170,)


def plate_texts(view: View) -> tuple[str, str]:
    """(название страны, IP для плашки) на языке интерфейса."""
    if view.status == Status.LOADING:
        return t("loading"), ""
    if view.status == Status.ERROR:
        return t("no_data"), ""
    return view.country or t("unknown_country"), shorten_ip(view.ip)


def render_plate(view: View, scale: float, hover: bool = False, dark: bool = True,
                 max_width: Optional[int] = None) -> Image.Image:
    """Плашка «флаг · страна · IP». Фон прозрачный; при наведении — мягкая подсветка."""
    th = theme_for(dark)
    s = scale
    pad = max(2, _px(3, s))                       # запас под сглаживание подсветки
    h = _px(BAR_H, s)
    gap, padx = _px(8, s), _px(12, s)
    flag_w, flag_h = _px(26, s), _px(17, s)
    dot_w = _px(17, s)                            # слот под разделитель «·»
    limit = max_width if max_width is not None else _px(BAR_MAX_W, s)

    title, ip_text = plate_texts(view)
    f_title = font("semibold", max(9, _px(12.5, s)))
    f_ip = font("mono", max(8, _px(12.5, s)))
    f_dot = font("regular", max(8, _px(12, s)))

    ip_w = text_width(ip_text, f_ip)
    fixed = padx * 2 + flag_w + gap + ((dot_w + ip_w) if ip_text else 0)
    title_w = text_width(title, f_title)
    if fixed + title_w > limit:                   # режем название, но не IP
        title = fit_text(title, f_title, max(0.0, limit - fixed))
        title_w = text_width(title, f_title)
    w = int(round(fixed + title_w))

    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    radius = _px(9, s)
    # Заливка с alpha=1 невидима, но Windows не пропускает клики сквозь полностью
    # прозрачные пиксели: без неё плашка нажималась бы только по буквам.
    out.alpha_composite(rounded_box(w, h, radius, fill=(0, 0, 0, 1)), (pad, pad))
    colors = _alert_colors(view.alert, th)
    if colors:
        out.alpha_composite(rounded_box(w, h, radius, fill=colors[0], outline=colors[1],
                                        outline_w=max(1, _px(1, s))), (pad, pad))
    if hover:
        out.alpha_composite(rounded_box(w, h, radius, fill=th.hover), (pad, pad))

    fade = 0.55 if view.stale else 1.0
    col_title = th.error if view.status == Status.ERROR else dim(th.title, fade)
    col_ip, col_dot = dim(th.ip, fade), dim(th.dot, fade)

    fx, fy = pad + padx, pad + (h - flag_h) // 2
    flag = None
    if view.status == Status.OK and view.cc:
        flag = flag_image(view.cc, flag_w, flag_h, max(2, _px(3, s)))
    if flag is not None:
        if view.stale:
            flag = flag.copy()
            flag.putalpha(flag.getchannel("A").point(lambda v: int(v * 0.6)))
        out.alpha_composite(flag, (fx + (flag_w - flag.width) // 2, fy + (flag_h - flag.height) // 2))
    else:
        ph = Image.new("RGBA", (flag_w, flag_h), th.placeholder[:3] + (0,))
        ph.putalpha(rounded_mask(flag_w, flag_h, max(2, _px(4, s))).point(
            lambda v, a=th.placeholder[3]: v * a // 255))
        out.alpha_composite(ph, (fx, fy))

    # Одна базовая линия для всех трёх надписей, посчитанная по высоте заглавной
    # буквы, а не по самой строке: «Россия» и «Чили» не должны «прыгать» из-за 'у'/'р'.
    cap = -f_title.getbbox("H", anchor="ls")[1]
    baseline = pad + h / 2 + cap / 2
    x = fx + flag_w + gap
    draw_text(out, (x, baseline), title, f_title, col_title)
    if ip_text:
        x = int(x + title_w)
        draw_text(out, (x + (dot_w - text_width("·", f_dot)) / 2, baseline), "·", f_dot, col_dot)
        draw_text(out, (x + dot_w, baseline), ip_text, f_ip, col_ip)
    return out


# --------------------------------------------------------------------------
#  Карточка-подсказка
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Note:
    text: str
    kind: str = "info"        # info | warn | bad


@dataclass(frozen=True)
class TipData:
    cc: str
    title: str
    subtitle: str
    rows: tuple[tuple[str, str, bool], ...]    # (подпись, значение, моноширинный шрифт?)
    notes: tuple[Note, ...] = ()
    hint: str = ""


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "—"


def build_tip(info: Optional[IpInfo], view: View, error_text: str = "", expected_name: str = "",
              show_both: bool = True) -> TipData:
    """Содержимое подсказки по текущему состоянию (чистая функция для тестов)."""
    notes: list[Note] = []
    if info is None:
        reason = error_text or t("err_unknown")
        notes.append(Note(t("tip_failed", reason=reason), "bad"))
        return TipData("", t("no_data"), "", (), tuple(notes), t("tip_hint"))

    title = i18n.country_name(info.cc, info.country_en)
    subtitle = ", ".join(dict.fromkeys(p for p in (info.city, info.region) if p))  # без дублей

    rows: list[tuple[str, str, bool]] = []
    if show_both and info.ipv4 and info.ipv6:
        rows.append((t("tip_ipv4"), info.ipv4, True))
        rows.append((t("tip_ipv6"), info.ipv6, True))
    else:
        rows.append((t("tip_ip"), info.ip, True))
    if info.isp:
        rows.append((t("tip_isp"), info.isp, False))
    if info.tz:
        rows.append((t("tip_tz"), f"UTC{info.tz}" if info.tz[:1] in "+-" else info.tz, False))
    if info.source:
        rows.append((t("tip_source"), info.source, False))

    if view.stale:
        notes.append(Note(t("tip_stale", reason=error_text or t("err_unknown"),
                            time=_clock(info.fetched_at)), "warn"))
    else:
        notes.append(Note(t("tip_updated", time=_clock(info.fetched_at)), "info"))
    if view.alert == Alert.MISMATCH:
        notes.append(Note(t("tip_mismatch", name=expected_name or "?"), "bad"))
    elif view.alert == Alert.CHANGED:
        notes.append(Note(t("tip_changed"), "warn"))
    return TipData(info.cc, title, subtitle, tuple(rows), tuple(notes), t("tip_hint"))


def render_tip(data: TipData, scale: float, dark: bool = True) -> Image.Image:
    """Карточка с подробностями: флаг, страна, адреса, провайдер, время обновления."""
    th = theme_for(dark)
    s = scale
    w = _px(TIP_W, s)
    pad = _px(14, s)
    inner = w - 2 * pad
    label_w = _px(92, s)
    f_title = font("semibold", max(11, _px(15, s)))
    f_sub = font("regular", max(9, _px(11.5, s)))
    f_label = font("regular", max(9, _px(11.5, s)))
    f_value = font("regular", max(9, _px(12, s)))
    f_mono = font("mono", max(9, _px(12, s)))
    f_note = font("regular", max(9, _px(11, s)))
    line = _px(20, s)
    flag_w, flag_h = _px(44, s), _px(30, s)

    # --- вычисляем высоту -------------------------------------------------
    value_w = inner - label_w
    note_lines = [(n, wrap_text(n.text, f_note, inner - _px(14, s))) for n in data.notes]
    head_h = max(flag_h, _px(38, s))
    h = pad + head_h + (_px(10, s) if data.rows else 0)
    h += line * len(data.rows)
    h += _px(8, s) + sum(len(lines) * _px(16, s) for _n, lines in note_lines)
    h += (_px(8, s) + _px(14, s)) if data.hint else 0
    h += pad - _px(2, s)

    # --- фон ------------------------------------------------------------------
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.alpha_composite(rounded_box(w, h, _px(9, s), fill=th.tip_bg, outline=th.tip_border,
                                    outline_w=max(1, _px(1, s))))

    # --- шапка ------------------------------------------------------------------
    y = pad
    flag = flag_image(data.cc, flag_w, flag_h, max(2, _px(4, s))) if data.cc else None
    if flag is not None:
        out.alpha_composite(flag, (pad + (flag_w - flag.width) // 2, y + (flag_h - flag.height) // 2))
    else:
        ph = Image.new("RGBA", (flag_w, flag_h), th.placeholder[:3] + (0,))
        ph.putalpha(rounded_mask(flag_w, flag_h, max(2, _px(5, s))))
        out.alpha_composite(ph, (pad, y))
    tx = pad + flag_w + _px(12, s)
    text_w = w - pad - tx
    draw_text(out, (tx, y + _px(15 if data.subtitle else 22, s)),
              fit_text(data.title, f_title, text_w), f_title, th.title)
    if data.subtitle:
        draw_text(out, (tx, y + _px(33, s)), fit_text(data.subtitle, f_sub, text_w), f_sub, th.sub)
    y += head_h

    # --- строки «подпись — значение» -----------------------------------------
    if data.rows:
        y += _px(10, s)
        ImageDraw.Draw(out).line((pad, y - _px(5, s), w - pad, y - _px(5, s)), fill=th.divider,
                                 width=max(1, _px(1, s)))
        for label, value, mono in data.rows:
            base = y + _px(13, s)
            draw_text(out, (pad, base), label, f_label, th.sub)
            fnt = f_mono if mono else f_value
            draw_text(out, (pad + label_w, base), fit_text(value, fnt, value_w), fnt, th.title)
            y += line

    # --- пояснения ----------------------------------------------------------------
    y += _px(8, s)
    for note, lines in note_lines:
        color = {"warn": th.warn, "bad": th.bad}.get(note.kind, th.sub)
        dot = _px(5, s)
        ImageDraw.Draw(out).ellipse((pad, y + _px(5, s), pad + dot, y + _px(5, s) + dot), fill=color)
        for text_line in lines:
            draw_text(out, (pad + _px(14, s), y + _px(12, s)), text_line, f_note, color)
            y += _px(16, s)
    if data.hint:
        y += _px(8, s)
        draw_text(out, (pad, y + _px(10, s)), fit_text(data.hint, f_note, inner), f_note,
                  dim(th.sub, 0.8))
    return out


def premultiplied_bgra(img: Image.Image) -> bytes:
    """Байты BGRA с премультиплицированной альфой — формат UpdateLayeredWindow."""
    return img.convert("RGBa").tobytes("raw", "BGRa")
