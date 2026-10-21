"""Drawing helpers shared by the screens: text that fits and aligns (Arabic
goes right), progress bars, the three-button hint bar, and the list frame.

Everything here draws into a 1-bit PIL image, so it works the same for the
PNG preview and the e-paper panel.
"""
from __future__ import annotations

from pathlib import Path

from PIL import ImageDraw, ImageFont

from .blocks import Style
from .fonts import Fonts
from .layout import text_is_rtl

WHITE, BLACK = 1, 0
PAD = 14                  # side padding for UI screens
STATUS_BAR_HEIGHT = 28    # room the reader keeps free at the bottom of each page


# Google's Material Symbols (Apache 2.0): a font where each icon is a glyph.
# assets/MaterialSymbols-subset.ttf holds only the icons below; if you add one,
# regenerate it with tools/make_icon_font.py. Values are codepoints from
# Google's .codepoints file.
ICON_FONT = Path(__file__).resolve().parents[1] / "assets" / "MaterialSymbols-subset.ttf"
ICONS = {
    "arrow_upward": "\ue5d8",
    "arrow_downward": "\ue5db",
    "radio_button_checked": "\ue837",
    "play_arrow": "\ue037",
    "toc": "\ue8de",
    "format_size": "\ue245",
    "text_fields": "\ue262",
    "local_library": "\ue54b",
    "arrow_back": "\ue5c4",
    "bookmark": "\ue8e7",
    "sort": "\ue164",
    "wifi": "\ue63e",
    "wifi_off": "\ue648",
    "cloud_upload": "\ue2c3",
    "bedtime": "\uf159",
    "filter_list": "\ue152",
    "tune": "\ue429",
    "check_circle": "\uf0be",
    "library_books": "\ue02f",
    "menu_book": "\uea19",
}
_icon_fonts: dict[int, ImageFont.FreeTypeFont | None] = {}


def _icon_font(size: int):
    if size not in _icon_fonts:
        try:
            _icon_fonts[size] = ImageFont.truetype(str(ICON_FONT), size)
        except OSError:
            _icon_fonts[size] = None  # font file missing: icons are simply left out
    return _icon_fonts[size]


def draw_icon(d: ImageDraw.ImageDraw, name: str, x: float, y: float, size: int = 20,
              fill: int = BLACK, anchor: str = "lm") -> float:
    """Draw a Material Symbol with its anchor point at (x, y) ("lm" = left,
    vertically centered). Returns its width so callers can place text beside it."""
    font = _icon_font(size)
    if font is None:
        return 0
    d.text((x, y), ICONS[name], font=font, fill=fill, anchor=anchor)
    return font.getlength(ICONS[name])


def fit_text(fonts: Fonts, text: str, style: Style, max_width: float, ellipsis: str = "...") -> str:
    """`text` cut with an ellipsis so it is at most max_width pixels wide."""
    text = " ".join(text.split())
    if fonts.width(text, style) <= max_width:
        return text
    lo, hi = 0, len(text)  # longest prefix that still fits with the ellipsis
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fonts.width(text[:mid].rstrip() + ellipsis, style) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ellipsis


def wrap_text(fonts: Fonts, text: str, max_width: float, max_lines: int,
              style: Style = Style.NORMAL) -> list[str]:
    """Greedy word wrap into at most max_lines lines; the last line is cut
    with an ellipsis if the text doesn't fit. Lines are in reading order."""
    words, lines, line = text.split(), [], ""
    for i, word in enumerate(words):
        trial = f"{line} {word}".strip()
        if fonts.width(trial, style) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
        if len(lines) == max_lines - 1:  # the last allowed line takes everything left
            line = " ".join([line] + words[i + 1:]) if i + 1 < len(words) else line
            break
    if line:
        lines.append(line)
    return [fit_text(fonts, l, style, max_width) for l in lines[:max_lines]]


def draw_text(d: ImageDraw.ImageDraw, fonts: Fonts, text: str, x0: float, x1: float, y: float,
              style: Style = Style.NORMAL, fill: int = BLACK, align: str = "auto",
              baseline: bool = False) -> None:
    """Draw one line of text inside [x0, x1], cutting it with "..." if needed.
    align: "left", "right", "center", or "auto" (right for Arabic, else left).
    y is the vertical middle of the line, or the baseline if baseline=True."""
    text = fit_text(fonts, text, style, x1 - x0)
    if not text:
        return
    width = fonts.width(text, style)
    if align == "auto":
        align = "right" if text_is_rtl(text) else "left"
    x = {"left": x0, "right": x1 - width, "center": (x0 + x1 - width) / 2}[align]
    d.text((x, y), text, font=fonts.get(style, text), fill=fill,
           anchor="ls" if baseline else "lm")


def draw_progress(d: ImageDraw.ImageDraw, x0: float, x1: float, y: float, height: int,
                  fraction: float, fill: int = BLACK, rtl: bool = False) -> None:
    """Outlined bar filled to `fraction`; filled from the right for RTL books."""
    fraction = max(0.0, min(1.0, fraction))
    d.rectangle((x0, y, x1, y + height), outline=fill)
    inner = (x1 - x0 - 2) * fraction
    if inner >= 1:
        if rtl:
            d.rectangle((x1 - 1 - inner, y + 1, x1 - 1, y + height - 1), fill=fill)
        else:
            d.rectangle((x0 + 1, y + 1, x0 + 1 + inner, y + height - 1), fill=fill)


def draw_hints(d: ImageDraw.ImageDraw, fonts: Fonts, width: int, mid_y: float,
               labels: tuple[str, str, str] = ("Up", "Select", "Down")) -> None:
    """The three buttons as icons with labels, evenly spaced."""
    size = 20
    font = fonts.get()
    for center, label, icon in zip((width / 6, width / 2, width * 5 / 6), labels,
                                   ("arrow_upward", "radio_button_checked", "arrow_downward")):
        label_w = fonts.width(label)
        icon_w = size if _icon_font(size) else 0
        x = center - (icon_w + 4 + label_w) / 2
        draw_icon(d, icon, x, mid_y, size)
        d.text((x + icon_w + 4, mid_y), label, font=font, fill=BLACK, anchor="lm")


def list_frame(img, ctx_fonts: Fonts, title: str, right: str = "",
               labels: tuple[str, str, str] = ("Up", "Select", "Down"),
               icon: str | None = None, hint_fonts: Fonts | None = None
               ) -> tuple[ImageDraw.ImageDraw, int, int]:
    """Header (title + page indicator) and footer (button hints) for a list
    screen. Returns (draw, body_top, body_bottom): where the rows go."""
    d = ImageDraw.Draw(img)
    w, h = img.size
    bar = ctx_fonts.line_height + 8
    x = PAD
    if icon:
        x += draw_icon(d, icon, PAD, bar / 2, 22) + 6
    d.text((x, bar / 2), title, font=ctx_fonts.get(Style.BOLD, title), fill=BLACK, anchor="lm")
    if right:
        d.text((w - PAD, bar / 2), right, font=ctx_fonts.get(), fill=BLACK, anchor="rm")
    d.line((0, bar, w, bar), fill=BLACK, width=2)
    d.line((0, h - bar, w, h - bar), fill=BLACK, width=2)
    draw_hints(d, hint_fonts or ctx_fonts, w, h - bar / 2 + 1, labels)
    return d, bar + 2, h - bar - 2
