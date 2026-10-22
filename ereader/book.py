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

import posixpath
import re
import time
from bisect import bisect_left, bisect_right
from collections import OrderedDict, defaultdict
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

from . import images
from .blocks import ImageBlock
from .epub import Epub
from .fonts import Fonts
from .html_parser import parse_blocks
from .layout import paginate
from .page import Page

CHUNK_BYTES = 40_000   # target size of a chunk of a long section (about 100 pages of text)
_BLOCK_END = re.compile(rb"</(?:p|div|li|h[1-6]|blockquote|section|tr|table)\s*>", re.I)


MIN_CHUNK = 600   # a chapter cut closer than this to the previous cut shares its chunk
_ANCHOR_TAG = re.compile(rb"<(a|span)\b[^>]*?(/>|>\s*(</\1\s*>)?)", re.I)   # an empty <a id=...></a> or <span id=.../>
_BLOCK_CLOSE = re.compile(rb"</(div|h[1-6]|p)\s*>\s*$", re.I)
_HEADINGISH = re.compile(rb"title|heading|chapter|head", re.I)


def find_anchor(data: bytes, fragment: str) -> int | None:
    """Byte offset of the element carrying id="fragment" (or name="fragment"): the start of its tag."""
    pattern = re.compile(rb"""\b(?:id|name)\s*=\s*(["'])""" + re.escape(fragment.encode()) + rb"\1")
    match = pattern.search(data)
    return data.rfind(b"<", 0, match.start()) if match else None


def heading_start(data: bytes, pos: int) -> int:
    """Where a chapter really begins. Many books put an empty anchor *after* the
    chapter's title (`<div class="chapter-title">..</div><a id="ch2"></a>`); a jump
    to the anchor would then skip the title. If the anchor is an empty tag right
    after a short heading-looking block, start at that block instead."""
    if not _ANCHOR_TAG.match(data, pos):
        return pos
    base = max(0, pos - 3000)
    before = data[base:pos]
    closing = _BLOCK_CLOSE.search(before)
    if not closing:
        return pos
    name = closing.group(1).lower()
    depth, start = 0, None
    for m in reversed(list(re.finditer(rb"</?" + name + rb"\b[^>]*>", before[:closing.end()], re.I))):
        depth += 1 if m.group(0).startswith(b"</") else -1
        if depth == 0:
            start = m.start()
            break
    if start is None:
        return pos
    block = before[start:closing.end()]
    opening = re.match(rb"<[^>]*>", block).group(0)
    text = re.sub(rb"<[^>]+>", b"", block).strip()
    heading_like = name.startswith(b"h") or _HEADINGISH.search(opening)
    return base + start if heading_like and 0 < len(text) <= 300 else pos


def _thin(cuts: list[int], gap: int) -> list[int]:
    """Drop cuts that are within `gap` bytes of the previous kept cut (or of the start)."""
    kept, last = [], 0
    for c in cuts:
        if c - last >= gap:
            kept.append(c)
            last = c
    return kept


def _split_range(data: bytes, start: int, end: int, size: int) -> list[tuple[int, int]]:
    """Cut data[start:end] into chunks of about `size` bytes (see split_html)."""
    if end - start <= size * 3 // 2:
        return [(start, end)]
    cuts, pos = [start], start
    while end - pos > size * 3 // 2:
        target, limit = pos + size, pos + size * 2
        block = _BLOCK_END.search(data, target, end)
        tag = data.find(b">", target, end)
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
    cuts.append(end)
    return list(zip(cuts, cuts[1:]))


def split_html(data: bytes, size: int, cuts: list[int] | tuple[int, ...] = ()) -> list[tuple[int, int]]:
    """Cut `data` into [(start, end)] chunks of about `size` bytes.

    `cuts` are places where a chunk must begin (the starts of chapters). Between
    them, cuts go just after a block's closing tag, so no paragraph is split. If
    a stretch has no block end, it falls back to after any tag (a <br/> is a
    paragraph break anyway), and with no tags at all it leaves the text whole.
    Stretches up to 1.5x `size` are not split. UTF-8 is safe to cut at tags,
    since multi-byte characters never contain the ASCII byte for ">".
    """
    n = len(data)
    bounds = [0] + sorted({c for c in cuts if 0 < c < n}) + [n]
    chunks: list[tuple[int, int]] = []
    for a, b in zip(bounds, bounds[1:]):
        chunks.extend(_split_range(data, a, b, size))
    return chunks


class LazyBook:
    def __init__(self, path: str | Path, fonts: Fonts, width: int, height: int,
                 position: tuple[int, int] = (0, 0), bottom_margin: int | None = None,
                 chunk: int = 0, chunk_bytes: int | None = None, cache_dir: Path | None = None):
        self.epub = Epub(path).open()
        self.fonts, self.width, self.height = fonts, width, height
        self.bottom_margin = bottom_margin
        self.chunk_bytes = chunk_bytes or CHUNK_BYTES
        self.cache_dir = cache_dir  # where drawn pictures are cached (None: not cached)
        self._image_sizes: dict[str, tuple[int, int] | None] = {}
        self._image_cache: OrderedDict[tuple, Image.Image | None] = OrderedDict()  # last few drawn pictures
        self.rtl = self.epub.rtl
        sizes = [self.epub.section_size(i) for i in range(len(self.epub.spine))]
        self._section_start = [sum(sizes[:i]) for i in range(len(sizes))]  # bytes before each section
        self._sizes, self._total = sizes, sum(sizes)
        self._toc_sections = [e.section for e in self.epub.toc]
        self._toc_by_section: dict[int, list[int]] = defaultdict(list)  # section -> indexes into epub.toc
        for i, entry in enumerate(self.epub.toc):
            self._toc_by_section[entry.section].append(i)
        self._entry_chunk: dict[int, int] = {}   # toc index -> chunk it starts in (filled when its section is planned)
        self._entry_found: set[int] = set()      # toc indexes whose #anchor was really found in the file
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

    def _chapter_pos(self) -> int:
        """Index into epub.toc of the chapter you are in, or -1 before the first one.
        Chapters that start inside this section are compared by chunk, so a file
        holding many chapters shows the right one."""
        if not self.epub.toc:
            return -1
        here = self._toc_by_section.get(self.section, [])
        if here:
            self._plan(self.section)
            # the entry starting latest at or before this chunk; among those in the same
            # chunk, one whose anchor was found beats one that fell back to the file's start
            ranked = [(self._entry_chunk.get(i, 0), i in self._entry_found, i) for i in here
                      if self._entry_chunk.get(i, 0) <= self.chunk]
            if ranked:
                return max(ranked)[2]
        return bisect_left(self._toc_sections, self.section) - 1   # the last chapter of an earlier section

    @property
    def chapter_title(self) -> str:
        """Title of the table-of-contents entry for where you are. A chapter listed under
        a part or collection is shown with it: "Sahih Muslim › 1. The Book of Faith"."""
        i = self._chapter_pos()
        if i < 0:
            return ""
        entry = self.epub.toc[i]
        for parent in reversed(self.epub.toc[:i]):
            if parent.level < entry.level:
                return f"{parent.title} › {entry.title}"
        return entry.title

    @property
    def chapter_index(self) -> int:
        """Index into epub.toc of the current chapter (0 if before the first)."""
        return max(0, self._chapter_pos())

    def bookmark(self) -> dict:
        """A bookmark for the page you are on. It stores where in the chunk the page is
        as a fraction, not a page number, so it still fits after a font change."""
        n = len(self.pages) or 1
        return {"section": self.section, "chunk": self.chunk, "fraction": round(self.page / n, 6),
                "chapter": self.chapter_title, "progress": round(self.progress, 4)}

    def _bookmark_page(self, mark: dict) -> int | None:
        """The page of the current chunk that a bookmark falls on (None: another chunk)."""
        n = len(self.pages)
        if (mark["section"], mark["chunk"]) != (self.section, self.chunk) or not n:
            return None
        return min(int(mark["fraction"] * n + 1e-3), n - 1)

    def bookmarks_here(self, marks: list[dict]) -> list[dict]:
        return [m for m in marks if self._bookmark_page(m) == self.page]

    def is_bookmarked(self, marks: list[dict]) -> bool:
        return bool(self.bookmarks_here(marks))

    def goto_bookmark(self, mark: dict) -> None:
        section = max(0, min(mark["section"], self.section_count - 1))
        chunk = max(0, min(mark["chunk"], len(self._plan(section)) - 1))
        pages = self._unit(section, chunk)
        page = min(int(mark["fraction"] * len(pages) + 1e-3), len(pages) - 1) if pages else 0
        self._seek(section, page, chunk)
        self._trim()

    def goto_entry(self, index: int) -> None:
        """Jump to the start of a table-of-contents entry, even if it is in the
        middle of a long file."""
        entry = self.epub.toc[index]
        self._plan(entry.section)
        self._seek(entry.section, 0, self._entry_chunk.get(index, 0))
        self._trim()

    def goto_fraction(self, fraction: float) -> None:
        """Jump to about `fraction` (0..1) of the book, by the same byte offsets that
        `progress` is estimated from, so reading `progress` afterwards gives about the
        same number back."""
        fraction = max(0.0, min(1.0, fraction))
        if not self._total:
            return self.goto(int(fraction * (self.section_count - 1)))
        target = fraction * self._total
        section = max(0, bisect_right(self._section_start, target) - 1)
        while section < self.section_count - 1 and not self._sizes[section]:
            section += 1   # an empty file holds nothing to land on
        plan = self._plan(section)
        offset = (target - self._section_start[section]) / max(1, self._sizes[section]) * plan[-1][1]
        chunk = max((c for c, (start, _) in enumerate(plan) if start <= offset), default=0)
        start, end = plan[chunk]
        within = (offset - start) / max(1, end - start)
        pages = self._unit(section, chunk)
        page = int(within * len(pages)) if pages else 0
        self._seek(section, page, chunk)
        self._trim()

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
        self._image_cache.clear()
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
            if data is None:
                self._plans[section] = [(0, 0)]
            else:
                # chapters that start inside this file begin a chunk of their own
                starts = {}
                for i in self._toc_by_section.get(section, []):
                    fragment = self.epub.toc[i].fragment
                    pos = find_anchor(data, fragment) if fragment else None
                    if pos is not None:
                        starts[i] = heading_start(data, pos)
                plan = split_html(data, self.chunk_bytes, _thin(sorted(set(starts.values())), MIN_CHUNK))
                chunk_starts = [a for a, _ in plan]
                for i in self._toc_by_section.get(section, []):   # a missing anchor means the file's start
                    self._entry_chunk[i] = bisect_right(chunk_starts, starts.get(i, 0)) - 1
                    if i in starts:
                        self._entry_found.add(i)
                self._plans[section] = plan
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
                blocks = parse_blocks(html)
                self._find_images(section, blocks)
                self._cache[key] = paginate(blocks, self.fonts, self.width,
                                            self.height, bottom_margin=self.bottom_margin)
            part = f" part {chunk + 1}/{len(plan)}" if len(plan) > 1 else ""
            print(f"[reader] section {section + 1}/{self.section_count}{part}: "
                  f"{len(self._cache[key])} pages in {time.perf_counter() - t0:.2f}s", flush=True)
        return self._cache[key]

    def _find_images(self, section: int, blocks: list) -> None:
        """Turn each picture's src into a full path inside the EPUB and learn its
        size, so layout can reserve the right space. Pictures that can't be
        found or drawn keep size None and show as a labelled box."""
        base = posixpath.dirname(self.epub.spine[section])
        for block in blocks:
            if isinstance(block, ImageBlock):
                src = block.src.split("#")[0].split("?")[0]
                if src.startswith("data:") or not src:
                    block.size = None
                    continue
                block.src = posixpath.normpath(posixpath.join(base, unquote(src)))
                if block.src not in self._image_sizes:
                    self._image_sizes[block.src] = images.natural_size(self.epub, block.src)
                block.size = self._image_sizes[block.src]

    def image(self, src: str, width: int, height: int) -> Image.Image | None:
        """A picture drawn at width x height as a 1-bit image (None if it can't be);
        the last few are kept in memory so turning back is instant."""
        key = (src, width, height)
        if key not in self._image_cache:
            self._image_cache[key] = images.render(self.epub, src, (width, height), self.cache_dir)
            while len(self._image_cache) > 6:
                self._image_cache.popitem(last=False)
        else:
            self._image_cache.move_to_end(key)
        return self._image_cache[key]

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
