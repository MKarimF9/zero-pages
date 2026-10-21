"""Screens = states in a state machine.

Each screen knows how to draw itself (render) and what a button press does
(handle). handle() returns the next screen, or None to stay. The main loop
in main.py never needs to know which screen is active.

  LibraryScreen  books with author and reading progress, last-read book first
  ReaderScreen   a page of text plus a status bar (chapter, page, progress)
  MenuScreen     SELECT while reading: resume, contents, font size, library
  TocScreen      table of contents; pick a chapter to jump to it

Everything works with three buttons: up, down, select.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from .blocks import Style
from .book import LazyBook
from .controls import Button
from .fonts import FAMILIES, Fonts
from .layout import MARGIN, text_is_rtl
from .library import BookIndex
from .page import Page, PlacedImage, PlacedWord
from .state import ReadingState
from .ui import (BLACK, PAD, STATUS_BAR_HEIGHT, WHITE, draw_icon, draw_progress,
                 draw_text, list_frame)

FULL_REFRESH_EVERY = 8  # partial refreshes allowed before a full one
SAVE_EVERY = 5          # page turns between progress saves (each save writes the SD card)
FONT_SIZES = (18, 20, 22, 24, 28, 32)


@dataclass
class AppContext:
    books_dir: Path
    fonts: Fonts       # book text; replaced when the font size changes
    ui_fonts: Fonts    # menus and lists
    state: ReadingState
    width: int
    height: int
    status_fonts: Fonts | None = None  # small text: status bar, author names

    @property
    def small(self) -> Fonts:
        return self.status_fonts or self.ui_fonts


def blank(ctx: AppContext) -> Image.Image:
    return Image.new("1", (ctx.width, ctx.height), WHITE)


def draw_message(ctx: AppContext, lines: list[str]) -> Image.Image:
    img = blank(ctx)
    d = ImageDraw.Draw(img)
    y = 40
    for i, line in enumerate(lines):
        style = Style.BOLD if i == 0 else Style.NORMAL
        d.text((40, y), line, font=ctx.ui_fonts.get(style), fill=BLACK)
        y += ctx.ui_fonts.line_height
    return img


class Screen(ABC):
    full_refresh: bool = True  # should the next frame clear ghosting?

    @abstractmethod
    def render(self) -> Image.Image: ...

    @abstractmethod
    def handle(self, button: Button) -> "Screen | None": ...

    def close(self) -> None:
        """Called when the app quits. Screens with state to flush override this."""


def _move(cursor: int, count: int, button: Button) -> int:
    """UP/DOWN move a list cursor, wrapping around."""
    if button is Button.UP:
        return (cursor - 1) % count
    if button is Button.DOWN:
        return (cursor + 1) % count
    return cursor


# --------------------------------------------------------------------------- Library

class LibraryScreen(Screen):
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        index = BookIndex(ctx.state.path.parent / "library.json")  # next to the reading state
        infos = index.scan(ctx.books_dir)
        infos.sort(key=lambda b: str(b.path) != ctx.state.last_book)  # last-read first (stable)
        self.infos = infos
        self.books = [b.path for b in infos]
        self.meta = [(b.title, b.author) for b in infos]  # (title, author)
        self.cursor = 0
        self.full_refresh = True

    @property
    def _row_h(self) -> int:
        return self.ctx.ui_fonts.line_height + self.ctx.small.line_height + 8

    @property
    def _rows_per_page(self) -> int:
        body = self.ctx.height - 2 * (self.ctx.ui_fonts.line_height + 8) - 4
        return max(1, body // self._row_h)

    def render(self) -> Image.Image:
        ctx = self.ctx
        if not self.books:
            return draw_message(ctx, ["No books found", f"Copy .epub files into {ctx.books_dir}"])

        per = self._rows_per_page
        start = (self.cursor // per) * per
        pages = (len(self.books) - 1) // per + 1
        img = blank(ctx)
        d, top, _ = list_frame(img, ctx.ui_fonts, "Library", f"{start // per + 1}/{pages}",
                              icon="library_books")
        ui, small, row_h, w = ctx.ui_fonts, ctx.small, self._row_h, ctx.width

        for row, i in enumerate(range(start, min(start + per, len(self.books)))):
            y0 = top + row * row_h
            selected = i == self.cursor
            fg = WHITE if selected else BLACK
            if selected:  # inverted bar = the e-paper version of a highlight
                d.rectangle((0, y0, w, y0 + row_h - 1), fill=BLACK)
            title, author = self.meta[i]
            draw_text(d, ui, title, PAD, w - PAD, y0 + ui.line_height / 2 + 1, fill=fg)
            draw_text(d, small, author, PAD, w - PAD, y0 + ui.line_height + small.line_height / 2 - 4,
                      fill=fg)
            progress = ctx.state.get_progress(str(self.books[i]))
            if progress > 0.005:
                bar_y = y0 + row_h - 9
                draw_progress(d, PAD, w - PAD - 36, bar_y, 4, progress, fill=fg, rtl=text_is_rtl(title))
                d.text((w - PAD, bar_y + 2), f"{round(progress * 100)}%", font=small.get(),
                       fill=fg, anchor="rm")
        return img

    def handle(self, button: Button) -> Screen | None:
        if not self.books:
            return None
        if button is Button.SELECT:
            return ReaderScreen(self.ctx, self.books[self.cursor])
        self.cursor = _move(self.cursor, len(self.books), button)
        self.full_refresh = False  # only the highlight moved: partial refresh is fine
        return None


# --------------------------------------------------------------------------- Reader

@dataclass
class Status:
    chapter: str
    page: int       # 1-based, within the current section
    pages: int
    progress: float
    rtl: bool


def draw_status_bar(ctx: AppContext, d: ImageDraw.ImageDraw, st: Status) -> None:
    """Progress bar with the chapter title on one side and page/percent on the
    other. For right-to-left books everything is mirrored, including the
    direction the bar fills."""
    small = ctx.small
    y0 = ctx.height - STATUS_BAR_HEIGHT
    draw_progress(d, MARGIN, ctx.width - MARGIN, y0 + 3, 4, st.progress, rtl=st.rtl)

    info = f"{st.page}/{st.pages}  ·  {round(st.progress * 100)}%"
    info_w = small.width(info)
    base_y = y0 + 8 + small.baseline
    left, right = MARGIN, ctx.width - MARGIN
    if st.rtl:
        draw_text(d, small, info, left, left + info_w + 1, base_y, align="left", baseline=True)
        draw_text(d, small, st.chapter, left + info_w + 12, right, base_y, align="right", baseline=True)
    else:
        draw_text(d, small, info, right - info_w - 1, right, base_y, align="right", baseline=True)
        draw_text(d, small, st.chapter, left, right - info_w - 12, base_y, align="left", baseline=True)


def draw_page(ctx: AppContext, page: Page, status: Status) -> Image.Image:
    img = blank(ctx)
    d = ImageDraw.Draw(img)
    base = ctx.fonts.baseline
    for el in page.elements:
        if isinstance(el, PlacedWord):
            d.text((el.x, el.y + base), el.word.text, font=ctx.fonts.get(el.word.style, el.word.text),
                   fill=BLACK, anchor="ls")  # same baseline for Arabic and Latin words
        elif isinstance(el, PlacedImage):
            # TODO (stretch): load the image from the zip, convert("1"), paste it here
            d.rectangle((el.x, el.y, el.x + el.width, el.y + el.height), outline=BLACK)
            d.text((el.x + 8, el.y + 8), "[image]", font=ctx.ui_fonts.get(), fill=BLACK)
    draw_status_bar(ctx, d, status)
    return img


class ReaderScreen(Screen):
    """Reads a book one section at a time (see book.py)."""

    def __init__(self, ctx: AppContext, path: Path):
        self.ctx = ctx
        self.path = path
        self.error: str | None = None
        self.book: LazyBook | None = None
        t0 = time.perf_counter()
        try:
            # the status bar owns the bottom STATUS_BAR_HEIGHT pixels
            self.book = LazyBook(path, ctx.fonts, ctx.width, ctx.height - STATUS_BAR_HEIGHT,
                                 position=ctx.state.get_position(str(path)), bottom_margin=6)
        except NotImplementedError as e:
            self.error = str(e)  # tells you which function to write next
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        print(f"[reader] opened in {time.perf_counter() - t0:.2f}s", flush=True)

        self.turns_since_full = 0
        self.unsaved_turns = 0
        self.full_refresh = True

    def _save_progress(self) -> None:
        if self.book and not self.book.empty and self.unsaved_turns:
            section, page = self.book.position
            self.ctx.state.set_position(str(self.path), section, page, self.book.progress)
            self.unsaved_turns = 0

    def close(self) -> None:
        """Save where we are, then release the book (on leaving or quitting)."""
        self._save_progress()
        if self.book:
            self.book.close()
            self.book = None

    def resume(self) -> "ReaderScreen":
        """Come back from a menu: the whole screen changed, so clear ghosting."""
        self.full_refresh = True
        self.turns_since_full = 0
        return self

    def render(self) -> Image.Image:
        if self.error:
            return draw_message(self.ctx, ["Not implemented yet:", self.error, "",
                                           "Press select to go back."])
        if not self.book or self.book.empty:
            return draw_message(self.ctx, ["This book has no pages.", "Press select to go back."])
        book = self.book
        status = Status(chapter=book.chapter_title or self.path.stem, page=book.page + 1,
                        pages=len(book.pages), progress=book.progress, rtl=book.rtl)
        return draw_page(self.ctx, book.current, status)

    def handle(self, button: Button) -> Screen | None:
        if self.error or not self.book or self.book.empty:
            self.close()
            return LibraryScreen(self.ctx)
        if button is Button.SELECT:
            return MenuScreen(self.ctx, self)

        try:
            moved = self.book.next() if button is Button.DOWN else self.book.prev()
        except Exception as e:  # a section failed to lay out
            self.error = f"{type(e).__name__}: {e}"
            return None
        if not moved:
            return None  # at the start/end: nothing to do

        # Partial refreshes are fast but leave ghosting, so every
        # FULL_REFRESH_EVERY turns do a full (slow, flashing) one.
        self.turns_since_full += 1
        if self.turns_since_full >= FULL_REFRESH_EVERY:
            self.full_refresh = True
            self.turns_since_full = 0
        else:
            self.full_refresh = False

        # Save every SAVE_EVERY turns, not every turn: each save is an SD-card
        # write. Leaving the book or quitting saves too (see close()).
        self.unsaved_turns += 1
        if self.unsaved_turns >= SAVE_EVERY:
            self._save_progress()
        return None


# --------------------------------------------------------------------------- Menu

class MenuScreen(Screen):
    """Opened with SELECT while reading."""

    def __init__(self, ctx: AppContext, reader: ReaderScreen):
        self.ctx = ctx
        self.reader = reader
        self.cursor = 0
        self.full_refresh = True

    def _items(self) -> list[tuple[str, str, str, str]]:
        """(key, icon, label, value) rows."""
        items = [("resume", "play_arrow", "Resume", "")]
        if self.reader.book.epub.toc:
            items.append(("toc", "toc", "Table of contents", ""))
        items.append(("family", "text_fields", "Font", self.ctx.fonts.family))
        items.append(("font", "format_size", "Font size", str(self.ctx.fonts.size)))
        items.append(("library", "local_library", "Back to library", ""))
        return items

    def render(self) -> Image.Image:
        ctx = self.ctx
        img = blank(ctx)
        percent = f"{round(self.reader.book.progress * 100)}%"
        d, top, _ = list_frame(img, ctx.ui_fonts, "Menu", percent, icon="menu_book")
        ui, w = ctx.ui_fonts, ctx.width
        row_h = ui.line_height + 12
        for i, (_, icon, label, value) in enumerate(self._items()):
            y0 = top + 6 + i * row_h
            selected = i == self.cursor
            fg = WHITE if selected else BLACK
            if selected:
                d.rectangle((0, y0, w, y0 + row_h - 1), fill=BLACK)
            draw_icon(d, icon, PAD, y0 + row_h / 2, 22, fill=fg)
            draw_text(d, ui, label, PAD + 32, w - PAD - 50, y0 + row_h / 2, fill=fg, align="left")
            if value:
                d.text((w - PAD, y0 + row_h / 2), value, font=ui.get(), fill=fg, anchor="rm")
        return img

    def handle(self, button: Button) -> Screen | None:
        items = self._items()
        if button is not Button.SELECT:
            self.cursor = _move(self.cursor, len(items), button)
            self.full_refresh = False
            return None

        key = items[self.cursor][0]
        if key == "resume":
            return self.reader.resume()
        if key == "toc":
            return TocScreen(self.ctx, self.reader, self)
        if key in ("family", "font"):
            self._change_font(next_family=key == "family")
            self.full_refresh = False
            return None
        self.reader.close()  # "library"
        return LibraryScreen(self.ctx)

    def _change_font(self, next_family: bool) -> None:
        """Cycle the font family or size, re-lay-out the book, remember the choice."""
        family, size = self.ctx.fonts.family, self.ctx.fonts.size
        if next_family:
            names = list(FAMILIES)
            family = names[(names.index(family) + 1) % len(names)]
        else:
            bigger = [s for s in FONT_SIZES if s > size]
            size = bigger[0] if bigger else FONT_SIZES[0]
        self.ctx.fonts = Fonts(size, family)
        self.reader.book.set_fonts(self.ctx.fonts)
        self.ctx.state.set_setting("font_family", family)
        self.ctx.state.set_setting("font_size", size)
        self.reader.unsaved_turns += 1  # the page number changed: save on exit

    def close(self) -> None:
        self.reader.close()


class TocScreen(Screen):
    """Table of contents. The first row goes back to the menu."""

    def __init__(self, ctx: AppContext, reader: ReaderScreen, menu: MenuScreen):
        self.ctx, self.reader, self.menu = ctx, reader, menu
        self.entries = reader.book.epub.toc
        self.cursor = 1 + reader.book.chapter_index  # start on the current chapter
        self.full_refresh = True

    @property
    def _row_h(self) -> int:
        return self.ctx.ui_fonts.line_height + 6

    @property
    def _rows_per_page(self) -> int:
        body = self.ctx.height - 2 * (self.ctx.ui_fonts.line_height + 8) - 4
        return max(1, body // self._row_h)

    def render(self) -> Image.Image:
        ctx = self.ctx
        per, count = self._rows_per_page, len(self.entries) + 1
        start = (self.cursor // per) * per
        img = blank(ctx)
        d, top, _ = list_frame(img, ctx.ui_fonts, "Contents", f"{start // per + 1}/{(count - 1) // per + 1}",
                              icon="toc")
        ui, w, row_h = ctx.ui_fonts, ctx.width, self._row_h
        current = 1 + self.reader.book.chapter_index
        for row, i in enumerate(range(start, min(start + per, count))):
            y0 = top + row * row_h
            selected = i == self.cursor
            fg = WHITE if selected else BLACK
            if selected:
                d.rectangle((0, y0, w, y0 + row_h - 1), fill=BLACK)
            mid = y0 + row_h / 2
            if i == 0:
                draw_icon(d, "arrow_back", PAD, mid, 20, fill=fg)
                draw_text(d, ui, "Back", PAD + 26, w - PAD, mid, fill=fg)
            else:
                if i == current:  # bookmark marks the chapter you're reading
                    draw_icon(d, "bookmark", 2, mid, 18, fill=fg)
                draw_text(d, ui, self.entries[i - 1].title, PAD + 8, w - PAD, mid, fill=fg)
        return img

    def handle(self, button: Button) -> Screen | None:
        if button is not Button.SELECT:
            self.cursor = _move(self.cursor, len(self.entries) + 1, button)
            self.full_refresh = False
            return None
        if self.cursor == 0:
            self.menu.full_refresh = True
            return self.menu
        self.reader.book.goto(self.entries[self.cursor - 1].section)
        self.reader.unsaved_turns += 1
        self.reader._save_progress()
        return self.reader.resume()

    def close(self) -> None:
        self.reader.close()


def initial_screen(ctx: AppContext, start: str = "last") -> Screen:
    """Where the app opens. "last" goes straight into the book you were
    reading (the library is one menu step away); anything else, or no usable
    last book, opens the library."""
    last = ctx.state.last_book
    if start == "last" and last and Path(last).is_file():
        return ReaderScreen(ctx, Path(last))
    return LibraryScreen(ctx)
