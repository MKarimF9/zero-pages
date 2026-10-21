"""Lazy book: lay out one section (spine item) at a time.

Laying out a whole book up front is slow on a Pi Zero W. Instead we keep
the EPUB open, lay out only the section being read, and keep its neighbours
cached so turning a page across a section boundary doesn't stall.

A position is (section, page): the index into epub.spine, and the page
within that section's layout. The total page count is unknown until every
section has been laid out, so the UI shows section and page-in-section.
"""
from __future__ import annotations

import time
from bisect import bisect_right
from pathlib import Path

from .epub import Epub
from .fonts import Fonts
from .html_parser import parse_blocks
from .layout import paginate
from .page import Page


class LazyBook:
    def __init__(self, path: str | Path, fonts: Fonts, width: int, height: int,
                 position: tuple[int, int] = (0, 0), bottom_margin: int | None = None):
        self.epub = Epub(path).open()
        self.fonts, self.width, self.height = fonts, width, height
        self.bottom_margin = bottom_margin
        self.rtl = self.epub.rtl
        sizes = [self.epub.section_size(i) for i in range(len(self.epub.spine))]
        self._section_start = [sum(sizes[:i]) for i in range(len(sizes))]  # bytes before each section
        self._sizes, self._total = sizes, sum(sizes)
        self._toc_sections = [e.section for e in self.epub.toc]
        self._cache: dict[int, list[Page]] = {}
        self.section = 0
        self.page = 0
        try:
            self._seek(*position)
        except Exception:
            self.close()
            raise

    # ------------------------------------------------------------ public

    @property
    def section_count(self) -> int:
        return len(self.epub.spine)

    @property
    def pages(self) -> list[Page]:
        """Pages of the current section."""
        return self._pages(self.section)

    @property
    def current(self) -> Page:
        return self.pages[self.page]

    @property
    def position(self) -> tuple[int, int]:
        return self.section, self.page

    @property
    def empty(self) -> bool:
        return not self.pages

    @property
    def progress(self) -> float:
        """Fraction of the book read, 0..1; 1.0 on the very last page. Estimated from file sizes (every
        section's size is known without laying it out), so it's approximate
        but costs nothing."""
        if not self._total:
            return self.section / max(1, self.section_count)
        pages = self.pages
        within = (self.page + 1) / len(pages) if pages else 0.0  # end of this page: last page = 100%
        return (self._section_start[self.section] + within * self._sizes[self.section]) / self._total

    @property
    def chapter_title(self) -> str:
        """Title of the table-of-contents entry at or before this section."""
        i = bisect_right(self._toc_sections, self.section) - 1
        return self.epub.toc[i].title if i >= 0 else ""

    @property
    def chapter_index(self) -> int:
        """Index into epub.toc of the current chapter (0 if before the first)."""
        return max(0, bisect_right(self._toc_sections, self.section) - 1)

    def goto(self, section: int) -> None:
        """Jump to the first page of a section."""
        self._seek(section, 0)
        self._trim()

    def set_fonts(self, fonts: Fonts) -> None:
        """Change the font and re-lay-out, staying at about the same place."""
        old = len(self.pages) or 1
        fraction = self.page / old
        self.fonts = fonts
        self._cache.clear()
        self.page = min(int(fraction * len(self.pages)), max(0, len(self.pages) - 1))

    def next(self) -> bool:
        """Go to the next page. False at the end of the book."""
        if self.page + 1 < len(self.pages):
            self.page += 1
            return True
        for sec in range(self.section + 1, self.section_count):
            if self._pages(sec):
                self.section, self.page = sec, 0
                self._trim()
                return True
        return False

    def prev(self) -> bool:
        """Go to the previous page. False at the start of the book."""
        if self.page > 0:
            self.page -= 1
            return True
        for sec in range(self.section - 1, -1, -1):
            pages = self._pages(sec)
            if pages:
                self.section, self.page = sec, len(pages) - 1
                self._trim()
                return True
        return False

    def close(self) -> None:
        self.epub.close()
        self._cache.clear()

    # ------------------------------------------------------------ internals

    def _seek(self, section: int, page: int) -> None:
        """Jump to a saved position (clamped); land on the first real page
        at or after it, or the last one if the book ends first."""
        section = max(0, min(section, self.section_count - 1))
        for sec in range(section, self.section_count):
            pages = self._pages(sec)
            if pages:
                self.section = sec
                self.page = max(0, min(page if sec == section else 0, len(pages) - 1))
                return
        for sec in range(section - 1, -1, -1):  # trailing sections were empty
            pages = self._pages(sec)
            if pages:
                self.section, self.page = sec, len(pages) - 1
                return
        # no pages anywhere: stay at (0, 0), `empty` will be True

    def _pages(self, section: int) -> list[Page]:
        if section not in self._cache:
            t0 = time.perf_counter()
            try:
                html = self.epub.read(self.epub.spine[section]).decode("utf-8", errors="replace")
            except KeyError:  # listed in the spine but missing from the zip
                self._cache[section] = []
            else:
                self._cache[section] = paginate(parse_blocks(html), self.fonts,
                                                self.width, self.height,
                                                bottom_margin=self.bottom_margin)
            print(f"[reader] section {section + 1}/{self.section_count}: "
                  f"{len(self._cache[section])} pages in {time.perf_counter() - t0:.2f}s",
                  flush=True)
        return self._cache[section]

    def _trim(self) -> None:
        """Keep only the current section and its neighbours in memory."""
        for sec in [s for s in self._cache if abs(s - self.section) > 1]:
            del self._cache[sec]
