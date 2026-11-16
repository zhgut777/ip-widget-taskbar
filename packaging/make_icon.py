"""Рисует icon.ico (глобус + «сетевая» точка) средствами Pillow.

    py packaging/make_icon.py [icon.ico] [preview.png]

Для мелких размеров (16–32 px) рисуется упрощённый вариант: меньше линий,
толще штрихи — иначе сетка глобуса превращается в кашу.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BIG = 1024
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

BG_TOP = (30, 64, 175)       # глубокий синий
BG_BOTTOM = (14, 165, 233)   # голубой
LINE = (255, 255, 255)
DOT = (74, 222, 128)         # зелёный «онлайн»
DOT_RING = (15, 23, 42)


def _gradient(size: int) -> Image.Image:
    """Диагональный градиент, сверху-слева тёмный."""
    vertical = Image.linear_gradient("L").resize((size, size), Image.BILINEAR)
    horizontal = vertical.transpose(Image.Transpose.ROTATE_90).transpose(
        Image.Transpose.FLIP_LEFT_RIGHT)
    grad = Image.blend(horizontal, vertical, 0.5)
    top = Image.new("RGB", (size, size), BG_TOP)
    bottom = Image.new("RGB", (size, size), BG_BOTTOM)
    return Image.composite(bottom, top, grad)


def _rounded_mask(size: int, radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def draw_master(simple: bool) -> Image.Image:
    s = BIG
    base = _gradient(s).convert("RGBA")

    # мягкий блик сверху
    gloss = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gloss)
    gd.ellipse((-s * 0.3, -s * 0.9, s * 1.3, s * 0.45), fill=(255, 255, 255, 34))
    base = Image.alpha_composite(base, gloss)

    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    cx = cy = s / 2
    r = s * 0.335
    w = int(s * (0.060 if simple else 0.042))   # толщина линий

    # глобус: контур
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=LINE, width=w)
    # экватор и меридиан
    d.line((cx - r, cy, cx + r, cy), fill=LINE, width=w)
    d.line((cx, cy - r, cx, cy + r), fill=LINE, width=w)
    # боковые меридианы (эллипс)
    ex = r * 0.52
    d.ellipse((cx - ex, cy - r, cx + ex, cy + r), outline=LINE, width=w)
    if not simple:
        # параллели
        for k in (-0.5, 0.5):
            y = cy + r * k
            half = (r * r - (r * k) ** 2) ** 0.5
            d.line((cx - half, y, cx + half, y), fill=LINE, width=int(w * 0.8))

    # тень под глобусом
    shadow = layer.filter(ImageFilter.GaussianBlur(s * 0.012))
    shadow_px = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    shadow_px.paste((8, 20, 60, 120), mask=shadow.getchannel("A"))
    shadow_px = shadow_px.transform((s, s), Image.AFFINE, (1, 0, 0, 0, 1, -s * 0.012))
    base = Image.alpha_composite(base, shadow_px)
    base = Image.alpha_composite(base, layer)

    # зелёная точка «онлайн» справа внизу
    dot = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dot)
    dr = s * 0.125
    dx, dy = cx + r * 0.78, cy + r * 0.78
    ring = dr * 1.28
    dd.ellipse((dx - ring, dy - ring, dx + ring, dy + ring), fill=DOT_RING + (255,))
    dd.ellipse((dx - dr, dy - dr, dx + dr, dy + dr), fill=DOT + (255,))
    base = Image.alpha_composite(base, dot)

    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(base, mask=_rounded_mask(s, s * 0.225))
    return out


def build(path: Path, preview: Path | None = None) -> None:
    detailed = draw_master(simple=False)
    simple = draw_master(simple=True)
    frames = []
    for n in SIZES:
        src = simple if n <= 32 else detailed
        frames.append(src.resize((n, n), Image.LANCZOS))
    frames[-1].save(path, format="ICO", sizes=[(n, n) for n in SIZES],
                    append_images=frames[:-1])
    if preview:
        sheet = Image.new("RGBA", (256 + 40 + 128 + 20 + 48 + 20 + 32 + 20 + 16 + 40, 300), (32, 32, 36, 255))
        x = 20
        for n in (256, 128, 48, 32, 16):
            f = next(fr for fr in frames if fr.width == n)
            sheet.alpha_composite(f, (x, 20))
            x += n + 20
        sheet.save(preview)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "icon.ico"
    prev = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    build(target, prev)
    print(f"icon: {target} ({target.stat().st_size} bytes)")
