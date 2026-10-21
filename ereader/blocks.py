"""Data structures produced by the HTML parser.

A chapter's XHTML becomes a flat list of blocks:
  - TextBlock: one paragraph/heading, made of styled words
  - ImageBlock: one image

This mirrors atomic14's design: parse into blocks first, lay out later.
Keeping parsing and layout separate means you can test each on its own.

These are complete — you don't need to change them to get started.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Flag, auto


class Style(Flag):
    """Inline text style. Flags combine: Style.BOLD | Style.ITALIC."""
    NORMAL = 0
    BOLD = auto()
    ITALIC = auto()


@dataclass
class Word:
    text: str
    style: Style = Style.NORMAL


@dataclass
class TextBlock:
    words: list[Word] = field(default_factory=list)
    is_heading: bool = False


@dataclass
class ImageBlock:
    src: str                                # as written in the HTML; made a full zip path by LazyBook
    alt: str = ""
    size: tuple[int, int] | None = None     # natural pixel size, once the image has been found


Block = TextBlock | ImageBlock
