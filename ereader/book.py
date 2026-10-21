"""Lazy book: lay out a little of the book at a time.

Laying out a whole book up front is slow on a Pi Zero W, and so is laying
out one huge chapter. So the book is read in *units*:

  section  one spine item (a chapter file)
  chunk    a slice of a section, about CHUNK_BYTES long, cut at paragraph ends
           (most sections are small and are a single chunk)

Only the unit you're reading is laid out, and its neighbours stay cached so
turning a page across a boundary doesn't stall. Memory and the wait for the
first page therefore depend on CHUNK_BYTES, not on how long a chapter is.

Where you are is (section, chunk, page): `position` is (section, page) and
`chunk` is kept next to it, with the page counted within the chunk. The total
page count is unknown until every unit has been laid out, so the UI shows the
page within the unit.
"""
from __future__ import annotations

import re
import time
from bisect import bisect_right
from pathlib import Path

from .epub import Epub
from .fonts import Fonts
from .html_parser import parse_blocks
from .layout import paginate
from .page import Page

CHUNK_BYTES = 40_000   # target size of a chunk of a long section (about 100 pages of text)
_BLOCK_END = re.compile(rb"</(?:p|div|li|h[1-6]|blockquote|section|tr|table)\s*>", re.I)


def split_html(data: bytes, size: int) -> list[tuple[int, int]]:
    """Cut `data` into [(start, end)] chunks of about `size` bytes.

    Cuts go just after a block's closing tag, so no paragraph is split. If a
    stretch has no block end, it falls back to after any tag (a <br/> is a
    paragraph break anyway), and with no tags at all it leaves the text whole.
    Sections up to 1.5x `size` are not split. UTF-8 is safe to cut at tags,
    since multi-byte characters never contain the ASCII byte for ">".
    """
    n = len(data)
    if n <= size * 3 // 2:
        return [(0, n)]
    cuts, pos = [0], 0
    while n - pos > size * 3 // 2:
        target, limit = pos + size, pos + size * 2
        block = _BLOCK_END.search(data, target)
        tag = data.find(b">", target)
        if block and block.end() <= limit:
            cut = block.end()
        elif tag != -1 and tag + 1 <= limit:
            cut = tag + 1
        elif block:
            cut = block.end()   # a giant block: no better place
        elif tag != -1:
            cut = tag + 1
        else:
            break               # nothing to cut at: leave the rest whole
        cuts.append(cut)
        pos = cut
    cuts.append(n)
    return list(zip(cuts, cuts[1:]))


class LazyBook:
    def __init__(self, path: str | Path, fonts: Fonts, width: int, height: int,
                 position: tuple[int, int] = (0, 0), bottom_margin: int | None = None,
                 chunk: int = 0, chunk_bytes: int | None = None):
        self.epub = Epub(path).open()
        self.fonts, self.width, self.height = fonts, width, height
        self.bottom_margin = bottom_margin
        self.chunk_bytes = chunk_bytes or CHUNK_BYTES
        self.rtl = self.epub.rtl
        sizes = [self.epub.section_size(i) for i in range(len(self.epub.spine))]
        self._section_start = [sum(sizes[:i]) for i in range(len(sizes))]  # bytes before each section
        self._sizes, self._total = sizes, sum(sizes)
        self._toc_sections = [e.section for e in self.epub.toc]
        self._plans: dict[int, list[tuple[int, int]]] = {}   # section -> its chunks (byte ranges)
        self._cache: dict[tuple[int, int], list[Page]] = {}  # (section, chunk) -> laid-out pages
        self._slot: tuple[int, bytes] | None = None          # last small section read, to avoid re-reading
        self.section = 0
        self.chunk = 0
        self.page = 0
        try:
            self._seek(position[0], position[1], chunk)
        except Exception:
            self.close()
            raise

    # ------------------------------------------------------------ public

    @property
    def section_count(self) -> int:
        return len(self.epub.spine)

    @property
    def pages(self) -> list[Page]:
        """Pages of the current chunk."""
        return self._unit(self.section, self.chunk)

    @property
    def current(self) -> Page:
        return self.pages[self.page]

    @property
    def position(self) -> tuple[int, int]:
        """(section, page): the page counts within the current chunk (see `chunk`)."""
        return self.section, self.page

    @property
    def empty(self) -> bool:
        return not self.pages

    @property
    def progress(self) -> float:
        """Fraction of the book read, 0..1; 1.0 on the very last page. Estimated
        from byte offsets (every section's size is known without laying it out),
        so it's approximate but costs nothing."""
        if not self._total:
            return self.section / max(1, self.section_count)
        plan = self._plan(self.section)
        start, end = plan[self.chunk]
        pages = self.pages
        within = (self.page + 1) / len(pages) if pages else 0.0  # end of this page: last page = 100%
        in_section = (start + within * (end - start)) / max(1, plan[-1][1])
        return (self._section_start[self.section] + in_section * self._sizes[self.section]) / self._total

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
        self._seek(section, 0, 0)
        self._trim()

    def set_fonts(self, fonts: Fonts) -> None:
        """Change the font and re-lay-out, staying at about the same place.
        Chunks are cut by byte offsets, so they don't change with the font."""
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
        found = self._neighbor(+1)
        if not found:
            return False
        self.section, self.chunk, pages = found
        self.page = 0
        self._trim()
        return True

    def prev(self) -> bool:
        """Go to the previous page. False at the start of the book."""
        if self.page > 0:
            self.page -= 1
            return True
        found = self._neighbor(-1)
        if not found:
            return False
        self.section, self.chunk, pages = found
        self.page = len(pages) - 1
        self._trim()
        return True

    def close(self) -> None:
        self.epub.close()
        self._cache.clear()
        self._slot = None

    # ------------------------------------------------------------ internals

    def _neighbor(self, step: int) -> tuple[int, int, list[Page]] | None:
        """The nearest unit with pages after (step=+1) or before (-1) the
        current one, as (section, chunk, pages); None at the ends of the book."""
        sec, chunk = self.section, self.chunk
        while True:
            chunk += step
            while True:  # step across section boundaries
                n = len(self._plan(sec))
                if 0 <= chunk < n:
                    break
                sec += step
                if not 0 <= sec < self.section_count:
                    return None
                chunk = 0 if step > 0 else len(self._plan(sec)) - 1
            pages = self._unit(sec, chunk)
            if pages:
                return sec, chunk, pages

    def _seek(self, section: int, page: int, chunk: int) -> None:
        """Jump to a saved position (clamped); land on the first real page
        at or after it, or the last one if the book ends first."""
        section = max(0, min(section, self.section_count - 1))
        for sec in range(section, self.section_count):
            plan = self._plan(sec)
            for c in range(chunk if sec == section else 0, len(plan)):
                pages = self._unit(sec, c)
                if pages:
                    exact = sec == section and c == chunk
                    self.section, self.chunk = sec, c
                    self.page = max(0, min(page if exact else 0, len(pages) - 1))
                    return
        for sec in range(section, -1, -1):  # trailing units were empty: last real page before
            plan = self._plan(sec)
            start = min(chunk, len(plan)) - 1 if sec == section else len(plan) - 1
            for c in range(start, -1, -1):
                pages = self._unit(sec, c)
                if pages:
                    self.section, self.chunk, self.page = sec, c, len(pages) - 1
                    return
        # no pages anywhere: stay at the start, `empty` will be True

    def _read(self, section: int) -> bytes | None:
        """Raw bytes of a section, or None if the spine lists a file the zip lacks."""
        if self._slot and self._slot[0] == section:
            return self._slot[1]
        try:
            data = self.epub.read(self.epub.spine[section])
        except KeyError:
            return None
        self._slot = (section, data) if len(data) <= 4 * self.chunk_bytes else None  # never hold a big one
        return data

    def _plan(self, section: int) -> list[tuple[int, int]]:
        """The chunks of a section as byte ranges; always at least one."""
        if section not in self._plans:
            data = self._read(section)
            self._plans[section] = [(0, 0)] if data is None else split_html(data, self.chunk_bytes)
        return self._plans[section]

    def _unit(self, section: int, chunk: int) -> list[Page]:
        """Lay out one chunk (cached)."""
        key = (section, chunk)
        if key not in self._cache:
            t0 = time.perf_counter()
            plan = self._plan(section)
            data = self._read(section)
            if data is None:
                self._cache[key] = []
            else:
                start, end = plan[chunk]
                html = data[start:end].decode("utf-8", errors="replace")
                self._cache[key] = paginate(parse_blocks(html), self.fonts, self.width,
                                            self.height, bottom_margin=self.bottom_margin)
            part = f" part {chunk + 1}/{len(plan)}" if len(plan) > 1 else ""
            print(f"[reader] section {section + 1}/{self.section_count}{part}: "
                  f"{len(self._cache[key])} pages in {time.perf_counter() - t0:.2f}s", flush=True)
        return self._cache[key]

    def _trim(self) -> None:
        """Keep the current unit, the chunks next to it, and the edge chunk of
        each neighbouring section; drop the rest."""
        def keep(sec: int, chunk: int) -> bool:
            if sec == self.section:
                return abs(chunk - self.chunk) <= 1
            plan = self._plans.get(sec)
            if sec == self.section - 1 and plan:
                return chunk == len(plan) - 1
            return sec == self.section + 1 and chunk == 0
        for key in [k for k in self._cache if not keep(*k)]:
            del self._cache[key]
