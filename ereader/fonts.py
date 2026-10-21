"""Font loading and text measurement. Complete — no TODOs.

Layout needs to know how wide each word is in pixels. That depends on the
font AND the style (bold words are wider), so all measuring goes through here.

The fonts are bundled in assets/fonts so the Pi needs nothing installed:
Latin text uses Literata, Noto Serif or Charis SIL (chosen in the reader
menu); Arabic text always uses Noto Naskh Arabic. If a bundled file is
missing we fall back to DejaVu, then to Pillow's built-in font, so things
still run.
"""
from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

from .blocks import Style

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
DEJAVU_DIR = Path("/usr/share/fonts/truetype/dejavu")  # fallback if assets/fonts is missing

# Book fonts, shipped in assets/fonts (see tools/make_fonts.py). The menu lets
# you switch between them. Value = file name prefix: <prefix>-Regular.ttf etc.
FAMILIES = {
    "Literata": "Literata",
    "Noto Serif": "NotoSerif",
    "Charis SIL": "CharisSIL",
}
DEFAULT_FAMILY = "Literata"
STYLE_SUFFIX = {
    Style.NORMAL: "Regular",
    Style.BOLD: "Bold",
    Style.ITALIC: "Italic",
    Style.BOLD | Style.ITALIC: "BoldItalic",
}
DEJAVU_FILES = {
    Style.NORMAL: "DejaVuSerif.ttf",
    Style.BOLD: "DejaVuSerif-Bold.ttf",
    Style.ITALIC: "DejaVuSerif-Italic.ttf",
    Style.BOLD | Style.ITALIC: "DejaVuSerif-BoldItalic.ttf",
}

# Arabic always uses Noto Naskh Arabic (bundled). The rest are fallbacks, tried
# in order, (regular, bold) per entry, if the bundled files are missing.
ARABIC_FONTS = [
    (str(ASSETS_DIR / "NotoNaskhArabic-Regular.ttf"), str(ASSETS_DIR / "NotoNaskhArabic-Bold.ttf")),
    ("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"),
    ("/usr/share/fonts/truetype/fonts-hosny-amiri/Amiri-Regular.ttf",
     "/usr/share/fonts/truetype/fonts-hosny-amiri/Amiri-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Damascus.ttc",   # macOS, last resort
     "/System/Library/Fonts/Supplemental/Damascus.ttc"),
]

# Arabic, Arabic Supplement, Arabic Extended-A, Presentation Forms A and B
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
                  (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


WIDTH_CACHE_MAX = 20000  # entries; a few MB at most


def has_arabic(text: str) -> bool:
    return any(lo <= ord(ch) <= hi for ch in text for lo, hi in _ARABIC_RANGES)


def _load(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)  # Pillow >= 10.1
        except TypeError:
            return ImageFont.load_default()


def _load_arabic(size: int) -> dict[bool, ImageFont.FreeTypeFont] | None:
    """{bold: font} for the first installed Arabic font, or None."""
    for regular, bold in ARABIC_FONTS:
        try:
            return {False: ImageFont.truetype(regular, size),
                    True: ImageFont.truetype(bold, size)}
        except OSError:
            continue
    return None


def _load_latin(family: str, style: Style, size: int):
    """Bundled font for `style`; DejaVu Serif if the file is missing."""
    path = ASSETS_DIR / f"{FAMILIES[family]}-{STYLE_SUFFIX[style]}.ttf"
    if not path.exists():
        path = DEJAVU_DIR / DEJAVU_FILES[style]
    return _load(path, size)


class Fonts:
    def __init__(self, size: int = 22, family: str = DEFAULT_FAMILY):
        self.size = size
        self.family = family if family in FAMILIES else DEFAULT_FAMILY
        self._fonts = {style: _load_latin(self.family, style, size) for style in STYLE_SUFFIX}
        self._arabic = _load_arabic(size)  # None: Arabic falls back to the Latin font
        self._widths: dict[tuple[Style, str], float] = {}

    def get(self, style: Style = Style.NORMAL, text: str = ""):
        """Font for `style`. Pass the text too and Arabic gets the Arabic font
        (Arabic has no italics, so only bold is honored there)."""
        if self._arabic and text and has_arabic(text):
            return self._arabic[bool(style & Style.BOLD)]
        return self._fonts.get(style, self._fonts[Style.NORMAL])

    def width(self, text: str, style: Style = Style.NORMAL) -> float:
        """Pixel width of `text` when drawn in `style`. Remembered: books repeat
        the same words, and measuring dominates layout time on a Pi Zero."""
        key = (style, text)
        w = self._widths.get(key)
        if w is None:
            if len(self._widths) >= WIDTH_CACHE_MAX:
                self._widths.clear()  # cheap bound on memory
            w = self._widths[key] = self.get(style, text).getlength(text)
        return w

    @property
    def baseline(self) -> int:
        """Distance from the top of a line to the text baseline. Drawing every
        word with anchor "ls" at y + baseline keeps the Arabic font and the
        Latin font (digits, English) on the same baseline."""
        try:
            return int(self._fonts[Style.NORMAL].getmetrics()[0])
        except AttributeError:  # Pillow's old bitmap fallback font
            return int(self.size * 0.8)

    @property
    def line_height(self) -> int:
        """Vertical distance between the tops of two consecutive lines."""
        return int(self.size * 1.4)
