"""Captcha image generation.

Kept free of any discord.py or Red imports so the rendering can be exercised
from a plain script without standing up a bot.
"""

from __future__ import annotations

import io
import random
import secrets

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Characters that survive a distorted render unambiguously. Deliberately drops
# 0/O/Q, 1/I/J/L, 2/Z, 5/S and 8/B -- nobody should fail verification because
# of a font quirk.
ALPHABET = "ACDEFGHKMNPRTUVWXY34679"

_CHAR_WIDTH = 52
_HEIGHT = 96
_PADDING = 24
_FONT_SIZE = 52


def generate_code(length: int = 6) -> str:
    """Return a random code drawn from the unambiguous alphabet."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def render(code: str) -> io.BytesIO:
    """Render ``code`` as a distorted PNG, ready for ``discord.File``."""
    width = len(code) * _CHAR_WIDTH + _PADDING * 2
    image = _background(width, _HEIGHT)
    font = _load_font(_FONT_SIZE)

    for index, char in enumerate(code):
        glyph = _render_glyph(char, font)
        x = _PADDING + index * _CHAR_WIDTH - 12 + random.randint(-4, 4)
        y = random.randint(-6, 6)
        image.paste(glyph, (x, y), glyph)

    _add_noise(image)
    image = image.filter(ImageFilter.SMOOTH)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _load_font(size: int):
    """Load Pillow's bundled font.

    A hardcoded system font path would break for anyone installing this repo on
    another OS. ``load_default`` only grew a ``size`` argument in Pillow 10.1,
    hence the fallback.
    """
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _background(width: int, height: int) -> Image.Image:
    """A light vertical gradient, so the glyphs never sit on flat white."""
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    top = tuple(random.randint(228, 255) for _ in range(3))
    bottom = tuple(random.randint(198, 226) for _ in range(3))
    for y in range(height):
        ratio = y / max(height - 1, 1)
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)),
        )
    return image


def _render_glyph(char: str, font) -> Image.Image:
    """Draw a single character on its own tile and rotate it."""
    tile = Image.new("RGBA", (_CHAR_WIDTH + 24, _HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    colour = (*(random.randint(0, 90) for _ in range(3)), 255)
    # stroke_width thickens the bundled font, which renders too thin to stay
    # readable once the noise lines are drawn over it.
    draw.text((16, 18), char, font=font, fill=colour, stroke_width=1, stroke_fill=colour)
    return tile.rotate(random.uniform(-26, 26), resample=Image.BICUBIC, expand=False)


def _add_noise(image: Image.Image) -> None:
    """Streak lines and speckle over the glyphs to defeat naive OCR."""
    draw = ImageDraw.Draw(image)
    width, height = image.size

    for _ in range(random.randint(4, 7)):
        draw.line(
            [(random.randint(0, width), random.randint(0, height)) for _ in range(2)],
            fill=tuple(random.randint(90, 170) for _ in range(3)),
            width=random.randint(1, 2),
        )

    for _ in range(random.randint(2, 3)):
        x0 = random.randint(0, width // 2)
        y0 = random.randint(0, height // 2)
        draw.arc(
            [x0, y0, x0 + random.randint(60, width), y0 + random.randint(30, height)],
            start=random.randint(0, 180),
            end=random.randint(180, 360),
            fill=tuple(random.randint(90, 170) for _ in range(3)),
            width=2,
        )

    for _ in range(int(width * height * 0.02)):
        draw.point(
            (random.randint(0, width - 1), random.randint(0, height - 1)),
            fill=tuple(random.randint(80, 200) for _ in range(3)),
        )
