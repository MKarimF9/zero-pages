"""Blocks -> pages.                   >>> YOUR CODE: milestones 3 & 4 <<<

This is the heart of an e-reader, and the most interesting part to write.

Two steps, same as atomic14:
  1. break_lines: split one block's words into lines that fit the width
  2. paginate: stack lines (and images) onto pages until a page is full

Done when: `python3 -m pytest tests/test_layout.py` passes, and opening a
book in the app shows real pages in the browser preview.
"""
from __future__ import annotations

import unicodedata

from .blocks import Block, ImageBlock, TextBlock, Word
from .fonts import Fonts, has_arabic
from .page import Page, PlacedImage, PlacedWord

MARGIN = 20          # pixels around the page edge
PARAGRAPH_GAP = 8    # extra pixels between blocks
MAX_STRETCH = 1.6    # justify only if no gap grows beyond this many space widths
IMAGE_PLACEHOLDER_HEIGHT = 120  # until you implement real image sizing


# ------------------------------------------------------------------ direction

def _word_kind(text: str) -> str:
    """'R' if the word has right-to-left letters, 'L' if it has left-to-right
    letters or European digits, else 'N' (neutral: punctuation, "*", ...)."""
    kind = "N"
    for ch in text:
        bidi = unicodedata.bidirectional(ch)
        if bidi in ("R", "AL"):
            return "R"
        if bidi in ("L", "EN"):
            kind = "L"
    return kind


def is_rtl(words: list[Word]) -> bool:
    """A block is right-to-left if its first strongly-directional word is."""
    for word in words:
        kind = _word_kind(word.text)
        if kind != "N":
            return kind == "R"
    return False


def text_is_rtl(text: str) -> bool:
    return is_rtl([Word(t) for t in text.split()])


def _is_ornament(words: list[Word]) -> bool:
    """A short block with no letters, like "* * *": centered, not justified."""
    return len(words) <= 8 and all(_word_kind(w.text) == "N" for w in words)


def visual_order(line: list[Word], rtl: bool) -> list[list[Word]]:
    """Reorder a line (logical order) into visual left-to-right *runs*.

    In an RTL line the words run from right to left, but a stretch of
    left-to-right words (English, numbers) inside it keeps its own
    left-to-right order. Each returned run is drawn left to right.
    """
    if not rtl:
        return [line]
    runs: list[list[Word]] = []
    for word in line:
        if _word_kind(word.text) == "L" and runs and runs[-1] and \
                _word_kind(runs[-1][0].text) == "L":
            runs[-1].append(word)
        else:
            runs.append([word])
    runs.reverse()
    return runs


# ------------------------------------------------------------------ layout

def _line_width(line: list[Word], fonts: Fonts) -> float:
    return (sum(fonts.width(w.text, w.style) for w in line)
            + sum(fonts.width(" ", w.style) for w in line[:-1]))


def break_lines(words: list[Word], fonts: Fonts, max_width: int) -> list[list[Word]]:
    """Greedy line breaking. Lines stay in logical (reading) order, so this
    works the same for LTR and RTL text; paginate() does the visual placement."""
    lines: list[list[Word]] = []
    line: list[Word] = []
    width = 0.0
    for word in words:
        w = fonts.width(word.text, word.style)
        if not line:
            line, width = [word], w
            continue
        added = fonts.width(" ", word.style) + w
        if width + added <= max_width:
            line.append(word)
            width += added
        else:
            lines.append(line)
            line, width = [word], w
    if line:
        lines.append(line)
    return lines


def _place_line(line: list[Word], fonts: Fonts, y: int, left: int, right: int,
                rtl: bool, justify: bool = False, center: bool = False) -> list[PlacedWord]:
    """Position one line: flush left for LTR, flush right for RTL, centered
    for headings. With justify, spread the extra space over the gaps (unless
    that would leave gaps ugly-wide, then it stays ragged)."""
    runs = visual_order(line, rtl)
    flat = [w for run in runs for w in run]
    space = fonts.width(" ", flat[0].style)
    total = _line_width(flat, fonts)
    extra_per_gap = 0.0
    if justify and len(flat) > 1:
        extra = (right - left) - total
        if 0 < extra <= MAX_STRETCH * space * (len(flat) - 1):
            extra_per_gap = extra / (len(flat) - 1)
            total += extra
    if center:
        x = left + max(0.0, (right - left - total) / 2)
    elif rtl:
        x = max(left, right - total)
    else:
        x = left
    placed = []
    for i, word in enumerate(flat):
        placed.append(PlacedWord(round(x), y, word))
        x += fonts.width(word.text, word.style)
        if i < len(flat) - 1:
            x += fonts.width(" ", word.style) + extra_per_gap
    return placed


def fit_size(natural: tuple[int, int], max_w: int, max_h: int) -> tuple[int, int]:
    """Shrink (never enlarge) an image to fit in max_w x max_h, keeping its shape."""
    w, h = natural
    scale = min(max_w / w, max_h / h, 1.0)
    return max(1, round(w * scale)), max(1, round(h * scale))


def paginate(blocks: list[Block], fonts: Fonts, width: int, height: int,
             margin: int = MARGIN, bottom_margin: int | None = None) -> list[Page]:
    """Stack lines and images onto pages.

    RTL blocks are flush right with their words in right-to-left order.
    Body text is justified (the last line of a paragraph is not); headings
    and "* * *" ornaments are centered, with extra space around headings.
    `bottom_margin` defaults to `margin`; pass less when something else
    (a status bar) already takes room at the bottom.
    """
    left, right = margin, width - margin
    bottom = height - (margin if bottom_margin is None else bottom_margin)
    usable_width = right - left
    lh = fonts.line_height

    pages: list[Page] = []
    page = Page()
    y = margin

    def new_page() -> None:
        nonlocal page, y
        if page.elements:
            pages.append(page)
        page, y = Page(), margin

    for block in blocks:
        if isinstance(block, ImageBlock):
            if block.size:  # a real picture: scaled to fit the page, centered
                w, h = fit_size(block.size, usable_width, bottom - margin)
                if y + h > bottom and page.elements:
                    new_page()
                page.elements.append(PlacedImage(left + (usable_width - w) // 2, y, w, h,
                                                 block.src, block.alt))
                y += h + PARAGRAPH_GAP
                continue
            if y + IMAGE_PLACEHOLDER_HEIGHT > bottom:  # not found or unsupported: a labelled box
                new_page()
            page.elements.append(PlacedImage(left, y, usable_width,
                                             IMAGE_PLACEHOLDER_HEIGHT, block.src, block.alt))
            y += IMAGE_PLACEHOLDER_HEIGHT + PARAGRAPH_GAP
            continue

        lines = break_lines(block.words, fonts, usable_width)
        if not lines:
            continue
        rtl = is_rtl(block.words)
        center = block.is_heading or _is_ornament(block.words)
        if block.is_heading:
            # Space above a heading (not at the top of a page), and don't strand
            # it at the bottom: it needs room for its lines plus one more line.
            gap = PARAGRAPH_GAP if y > margin else 0
            if y + gap + (len(lines) + 1) * lh > bottom:
                new_page()
            elif gap:
                y += gap
        pad = fonts.arabic_pad if any(has_arabic(w.text) for w in block.words) else 0
        for i, line in enumerate(lines):
            if y + lh + pad > bottom:
                new_page()
            page.elements.extend(_place_line(line, fonts, y + pad * 2 // 3, left, right, rtl,
                                             justify=not center and i < len(lines) - 1,
                                             center=center))
            y += lh + pad
        y += PARAGRAPH_GAP * (2 if block.is_heading else 1)

    if page.elements:
        pages.append(page)
    return pages


__all__ = ["break_lines", "paginate", "fit_size", "Page", "PlacedImage", "PlacedWord",
           "TextBlock", "ImageBlock", "MARGIN", "PARAGRAPH_GAP"]
