"""Статичные картинки для документации: плашка во всех состояниях и подсказка."""

from __future__ import annotations

import time

from PIL import Image

from . import i18n, netinfo, render
from .netinfo import IpInfo
from .state import Alert, Status, View


def make_preview(path: str, light: bool = False, lang: str = "ru") -> str:
    i18n.set_language(lang)
    dark = not light
    scale = 2.0
    for cc in ("NL", "RU", "US"):
        try:
            netinfo.ensure_flag(cc)
        except Exception:  # noqa: BLE001 — без сети картинки получатся с заглушками
            pass
    name = lambda cc, en: i18n.country_name(cc, en)
    ip = "203.0.113.42"
    views = [
        View(Status.OK, "NL", name("NL", "Netherlands"), ip),
        View(Status.OK, "US", name("US", "United States"), "2a01:4f8:c17:1234:abcd:ef01:2345:6789"),
        View(Status.OK, "NL", name("NL", "Netherlands"), ip, alert=Alert.CHANGED),
        View(Status.OK, "NL", name("NL", "Netherlands"), ip, alert=Alert.MISMATCH),
        View(Status.OK, "NL", name("NL", "Netherlands"), ip, stale=True),
        View(Status.LOADING),
        View(Status.ERROR),
    ]
    bg = (243, 243, 243, 255) if light else (32, 32, 36, 255)
    plates = [render.render_plate(v, scale, hover=(i == 0 and False), dark=dark) for i, v in enumerate(views)]

    info = IpInfo(ip="2001:db8::5", ip_other=ip, cc="NL", country_en="Netherlands", city="Rotterdam",
                  region="South Holland", tz="+01:00", isp="Snel.com B.V.", source="ipwho.is",
                  fetched_at=time.time())
    tip = render.render_tip(render.build_tip(info, views[0], "", "", True), scale, dark)

    width = max(max(p.width for p in plates), tip.width) + 60
    height = sum(p.height + 14 for p in plates) + tip.height + 70
    sheet = Image.new("RGBA", (width, height), bg)
    y = 20
    for plate in plates:
        sheet.alpha_composite(plate, (30, y))
        y += plate.height + 14
    sheet.alpha_composite(tip, (30, y + 20))
    sheet.convert("RGB").save(path)
    return path
