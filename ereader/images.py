"""Pictures inside books, made ready for e-paper.

A picture is shrunk to the size the layout gave it and turned into pure black
and white. Photos are dithered (a pattern of dots imitates grays); diagrams and
scanned text, which are nearly all black or white already, are simply
thresholded so lines stay crisp. Decoding a big JPEG on a Pi Zero is slow, so
the result is cached on disk and the next visit to the page reads a tiny PNG.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageOps

from .epub import Epub

CACHE_VERSION = 1            # bump when pictures are drawn differently, so cached ones are remade
FORMATS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")  # SVG and others are not drawn
LINE_ART_SHARE = 0.92        # this share of near-black/near-white pixels = a diagram, not a photo


def natural_size(epub: Epub, path: str) -> tuple[int, int] | None:
    """(width, height) of an image in the EPUB, read from its header; None if it
    is missing, a format we don't draw, or not a readable image."""
    if not path.lower().endswith(FORMATS):
        return None
    try:
        with epub.open_file(path) as f, Image.open(f) as img:
            return img.size
    except Exception:
        return None


def _flatten(img: Image.Image) -> Image.Image:
    """Grayscale on a white background (transparent areas become white, not black)."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        page = Image.new("RGBA", rgba.size, "white")
        page.alpha_composite(rgba)
        return page.convert("L")
    return img.convert("L")


def _is_line_art(gray: Image.Image) -> bool:
    hist = gray.histogram()
    return (sum(hist[:40]) + sum(hist[216:])) >= LINE_ART_SHARE * (gray.width * gray.height)


def render(epub: Epub, path: str, size: tuple[int, int], cache_dir: Path | None = None) -> Image.Image | None:
    """The image at `size`, 1-bit; None if it can't be read."""
    cached = None
    if cache_dir is not None:
        stat = epub.path.stat()
        key = f"{CACHE_VERSION}|{epub.path}|{stat.st_mtime}|{stat.st_size}|{path}|{size[0]}x{size[1]}"
        cached = cache_dir / "images" / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".png")
        try:
            return Image.open(cached).convert("1")
        except (FileNotFoundError, OSError):
            pass
    try:
        img = Image.open(io.BytesIO(epub.read(path)))
        img.draft("L", (size[0] * 2, size[1] * 2))                  # JPEG: decode at reduced size
        gray = _flatten(img).resize(size, Image.LANCZOS)
        if _is_line_art(gray):
            out = gray.convert("1", dither=Image.Dither.NONE)       # crisp lines
        else:
            out = ImageOps.autocontrast(gray, cutoff=1).convert("1")  # photo: Floyd-Steinberg dithering
    except Exception:
        return None
    if cached is not None:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            out.save(cached)
        except OSError:
            pass  # no cache is just slower next time
    return out
