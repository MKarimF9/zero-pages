"""A QR code as a 1-bit picture, for the Manage books screen.

The encoder is Project Nayuki's qrcodegen.py (MIT license, in this folder).
"""
from __future__ import annotations

from PIL import Image

from .qrcodegen import QrCode

QUIET = 2   # white modules around the code; the screen around it is white too, so 2 is plenty


def qr_matrix(text: str) -> list[list[bool]]:
    """Dark modules of the code for `text` (True = dark). Medium error correction: the
    e-paper's grey-ish blacks and any ghosting cost some contrast."""
    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    return [[qr.get_module(x, y) for x in range(qr.get_size())] for y in range(qr.get_size())]


def qr_image(text: str, max_px: int) -> Image.Image:
    """The code with its quiet zone, as large as fits in max_px with whole pixels per module
    (blurry, uneven modules scan badly). A 1-bit image: black modules on white."""
    modules = qr_matrix(text)
    cells = len(modules) + 2 * QUIET
    scale = max(1, max_px // cells)
    img = Image.new("1", (cells * scale, cells * scale), 255)
    px = img.load()
    for y, row in enumerate(modules):
        for x, dark in enumerate(row):
            if dark:
                for dy in range(scale):
                    for dx in range(scale):
                        px[(x + QUIET) * scale + dx, (y + QUIET) * scale + dy] = 0
    return img
