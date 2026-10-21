"""What the layout engine produces: pages of positioned elements.

Layout decides *where* everything goes. Rendering just draws it.
Complete — no TODOs here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .blocks import Word


@dataclass
class PlacedWord:
    x: int
    y: int
    word: Word


@dataclass
class PlacedImage:
    x: int
    y: int
    width: int
    height: int
    src: str
    alt: str = ""


@dataclass
class Page:
    elements: list[PlacedWord | PlacedImage] = field(default_factory=list)
