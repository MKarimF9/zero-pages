"""Book covers as small 1-bit images for the library.

The cover is read from the EPUB, cropped to a 3:4 thumbnail, and dithered to
black and white (the only thing e-paper can show). Decoding a big JPEG on a
Pi Zero takes a while, so each thumbnail is made once and cached as a tiny PNG.
Books with no cover image get a generated one: a frame, a book icon and the title.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .blocks import Style
from .epub import Epub
from .fonts import Fonts
from .ui import BLACK, WHITE, draw_icon, wrap_text

THUMB_SIZE = (48, 64)


def _cache_name(path: Path, size: tuple[int, int]) -> str:
    stat = path.stat()
    key = f"{path}|{stat.st_mtime}|{stat.st_size}|{size[0]}x{size[1]}"
    return hashlib.sha1(key.encode()).hexdigest()[:16] + ".png"


def image_cover(data: bytes, size: tuple[int, int]) -> Image.Image:
    """Cover bytes -> size, 1-bit, dithered, with a thin frame."""
    img = Image.open(io.BytesIO(data))
    img.draft("L", (size[0] * 2, size[1] * 2))   # JPEG: decode at reduced size, much faster
    img = ImageOps.fit(img.convert("L"), size, Image.LANCZOS)  # crop to the thumbnail's shape
    img = ImageOps.autocontrast(img).convert("1")               # Floyd-Steinberg dithering
    ImageDraw.Draw(img).rectangle((0, 0, size[0] - 1, size[1] - 1), outline=BLACK)
    return img


def placeholder_cover(title: str, size: tuple[int, int]) -> Image.Image:
    """A cover for books without one: frame, icon, then the title."""
    w, h = size
    img = Image.new("1", size, WHITE)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w - 1, h - 1), outline=BLACK)
    d.rectangle((3, 3, w - 4, h - 4), outline=BLACK)
    draw_icon(d, "menu_book", w / 2, 14, 18, anchor="mm")
    fonts = Fonts(10)
    for i, line in enumerate(wrap_text(fonts, title, w - 12, 4, Style.BOLD)):
        font = fonts.get(Style.BOLD, line)
        d.text((w / 2, 27 + i * 9), line, font=font, fill=BLACK, anchor="mm")
    return img


def make_cover(path: Path, title: str, size: tuple[int, int] = THUMB_SIZE) -> Image.Image:
    epub = Epub(path)
    try:
        epub.open()
        if epub.cover:
            return image_cover(epub.read(epub.cover), size)
    except Exception:
        pass  # unreadable or undecodable cover: fall through to the placeholder
    finally:
        epub.close()
    return placeholder_cover(title, size)


def cached_cover(cache_dir: Path, path: Path, title: str, size: tuple[int, int] = THUMB_SIZE) -> Image.Image:
    """The thumbnail for a book, generated on first use and then read from disk."""
    file = cache_dir / "covers" / _cache_name(path, size)
    try:
        return Image.open(file).convert("1")
    except (FileNotFoundError, OSError):
        pass
    img = make_cover(path, title, size)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        img.save(file)
    except OSError:
        pass  # cache not writable: we just regenerate next time
    return img
