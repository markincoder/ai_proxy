"""PNG-капча с искажёнными буквами для формы поддержки."""

from __future__ import annotations

import io
import secrets
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_CAPTCHA_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CAPTCHA_LENGTH = 5

_FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/Arialbd.ttf"),
)


def generate_captcha_code(length: int = _CAPTCHA_LENGTH) -> str:
    return "".join(secrets.choice(_CAPTCHA_ALPHABET) for _ in range(length))


def normalize_captcha_answer(raw: str) -> str:
    return str(raw or "").strip().replace(" ", "").upper()


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def render_captcha_png(text: str) -> bytes:
    code = normalize_captcha_answer(text)
    width, height = 220, 80
    image = Image.new("RGB", (width, height), (245, 247, 252))
    draw = ImageDraw.Draw(image)

    for _ in range(260):
        draw.point(
            (secrets.randbelow(width), secrets.randbelow(height)),
            fill=(
                secrets.randbelow(80) + 160,
                secrets.randbelow(80) + 160,
                secrets.randbelow(80) + 170,
            ),
        )

    font = _load_font(40)
    x = 14
    for ch in code:
        layer = Image.new("RGBA", (52, 62), (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)
        color = (
            secrets.randbelow(90) + 20,
            secrets.randbelow(90) + 30,
            secrets.randbelow(100) + 40,
        )
        layer_draw.text((6, 4), ch, font=font, fill=color + (255,))
        angle = secrets.randbelow(41) - 20
        layer = layer.rotate(angle, expand=1, resample=Image.Resampling.BICUBIC)
        y = secrets.randbelow(14) + 8
        image.paste(layer, (x, y), layer)
        x += 36

    draw = ImageDraw.Draw(image)
    for _ in range(7):
        draw.line(
            (
                (secrets.randbelow(width), secrets.randbelow(height)),
                (secrets.randbelow(width), secrets.randbelow(height)),
            ),
            fill=(
                secrets.randbelow(100) + 80,
                secrets.randbelow(100) + 80,
                secrets.randbelow(100) + 90,
            ),
            width=secrets.randbelow(2) + 1,
        )

    for _ in range(4):
        cx = secrets.randbelow(width - 40) + 20
        cy = secrets.randbelow(height - 20) + 10
        rx = secrets.randbelow(18) + 10
        ry = secrets.randbelow(10) + 6
        draw.arc(
            (cx - rx, cy - ry, cx + rx, cy + ry),
            start=secrets.randbelow(360),
            end=secrets.randbelow(360) + 360,
            fill=(secrets.randbelow(80) + 100,) * 3,
            width=1,
        )

    image = image.filter(ImageFilter.GaussianBlur(radius=0.65))

    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
