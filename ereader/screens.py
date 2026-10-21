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
from .covers import THUMB_SIZE, cached_cover
from .layout import MARGIN, text_is_rtl
from .library import BookIndex
from .page import Page, PlacedImage, PlacedWord
from .state import ReadingState
from .upload import PORT, UploadServer, local_ip
from .ui import (BLACK, PAD, STATUS_BAR_HEIGHT, WHITE, draw_icon, draw_progress,
                 draw_text, list_frame, wrap_text)

FULL_REFRESH_EVERY = 8  # partial refreshes allowed before a full one
SAVE_EVERY = 5          # page turns between progress saves (each save writes the SD card)
FONT_SIZES = (18, 20, 22, 24, 28, 32)
CARD_THUMB = (57, 76)   # bigger cover for the Continue reading card
CARD_HEIGHT = 96


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
    handles_long_press = False  # does holding select mean something here?

    def sleep_image(self) -> Image.Image | None:
        """What to leave on the panel when it goes idle; None keeps the current page."""
        return None

    @abstractmethod
    def render(self) -> Image.Image: ...

    @abstractmethod
    def handle(self, button: Button) -> "Screen | None": ...

    def close(self) -> None:
        """Called when the app quits. Screens with state to flush override this."""


def effective_button(button: Button, screen: Screen) -> Button:
    """Holding select only means something on the library; elsewhere it's a plain select."""
    if button is Button.SELECT_LONG and not screen.handles_long_press:
        return Button.SELECT
    return button


SLEEP_SCREENS = {"cover": "Cover", "page": "Page"}
SORTS = {"recent": "Recent", "title": "Title", "author": "Author"}
FILTERS = {"all": "All", "reading": "Reading", "new": "New", "finished": "Finished"}


def sort_books(infos: list, sort: str, state: ReadingState) -> list:
    """Order the library: most recently read first, or by title or author."""
    if sort == "title":
        return sorted(infos, key=lambda b: b.title.casefold())
    if sort == "author":
        return sorted(infos, key=lambda b: (b.author.casefold(), b.title.casefold()))
    return sorted(infos, key=lambda b: (-state.get_opened(str(b.path)),
                                        str(b.path) != state.last_book, b.title.casefold()))


def cover_frame(ctx: AppContext, path: Path, title: str) -> Image.Image:
    """The book's cover centered on a white screen: the sleep screen."""
    height = ctx.height - 16
    size = (height * 3 // 4, height)
    cover = cached_cover(ctx.state.path.parent, path, title, size)
    img = blank(ctx)
    img.paste(cover, ((ctx.width - size[0]) // 2, 8))
    return img


def _next(options: dict, current: str) -> str:
    keys = list(options)
    return keys[(keys.index(current) + 1) % len(keys)] if current in keys else keys[0]


def _draw_menu_rows(ctx: "AppContext", d: ImageDraw.ImageDraw, top: int,
                    rows: list[tuple[str, str, str]], cursor: int) -> None:
    """Rows of (icon, label, value), the cursor row inverted. Shared by the
    reader menu and the library options."""
    ui, w = ctx.ui_fonts, ctx.width
    row_h = ui.line_height + 12
    for i, (icon, label, value) in enumerate(rows):
        y0 = top + 6 + i * row_h
        fg = WHITE if i == cursor else BLACK
        if i == cursor:
            d.rectangle((0, y0, w, y0 + row_h - 1), fill=BLACK)
        draw_icon(d, icon, PAD, y0 + row_h / 2, 22, fill=fg)
        draw_text(d, ui, label, PAD + 32, w - PAD - 90, y0 + row_h / 2, fill=fg, align="left")
        if value:
            d.text((w - PAD, y0 + row_h / 2), value, font=ui.get(), fill=fg, anchor="rm")


def _move(cursor: int, count: int, button: Button) -> int:
    """UP/DOWN move a list cursor, wrapping around."""
    if button is Button.UP:
        return (cursor - 1) % count
    if button is Button.DOWN:
        return (cursor + 1) % count
    return cursor


# --------------------------------------------------------------------------- Library

class LibraryScreen(Screen):
    """Books with cover, author and status. If you're partway through one, it
    sits on top as a larger "Continue reading" card. Hold select for options."""

    handles_long_press = True

    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        index = BookIndex(ctx.state.path.parent / "library.json")  # next to the reading state
        self.sort = ctx.state.get_setting("library_sort", "recent")
        self.filter = ctx.state.get_setting("library_filter", "all")
        infos = index.scan(ctx.books_dir)
        if self.filter != "all":
            infos = [b for b in infos if ctx.state.status(str(b.path)) == self.filter]
        infos = sort_books(infos, self.sort, ctx.state)
        self.infos = infos
        self.books = [b.path for b in infos]
        self.meta = [(b.title, b.author) for b in infos]  # (title, author)
        # the last-read book becomes the card (only when sorted by recent), unless it's finished
        self.has_card = self.sort == "recent" and bool(infos) and str(infos[0].path) == ctx.state.last_book \
            and ctx.state.status(str(infos[0].path)) == "reading"
        self.cursor = 0
        self.full_refresh = True
        self._covers: dict[tuple[int, tuple[int, int]], Image.Image] = {}  # made when first shown

    # ---- layout: the card is taller than a row, so pages are built by height

    @property
    def _row_h(self) -> int:
        return THUMB_SIZE[1] + 8   # the cover sets the row height

    def _height(self, i: int) -> int:
        return CARD_HEIGHT if (i == 0 and self.has_card) else self._row_h

    def _pages(self) -> list[tuple[int, int]]:
        """[(first, end)] book indexes for each screenful, filled greedily."""
        body = self.ctx.height - 2 * (self.ctx.ui_fonts.line_height + 8) - 4
        pages, start, used = [], 0, 0
        for i in range(len(self.books)):
            if used + self._height(i) > body and i > start:
                pages.append((start, i))
                start, used = i, 0
            used += self._height(i)
        pages.append((start, len(self.books)))
        return pages

    def _cover(self, i: int, size: tuple[int, int]) -> Image.Image:
        if (i, size) not in self._covers:
            self._covers[(i, size)] = cached_cover(self.ctx.state.path.parent, self.books[i],
                                                   self.meta[i][0], size)
        return self._covers[(i, size)]

    # ---- drawing

    def render(self) -> Image.Image:
        ctx = self.ctx
        if not self.books:
            if self.filter != "all":
                return draw_message(ctx, ["No books match", f"Showing: {FILTERS[self.filter]} books",
                                          "", "Hold select to change the filter."])
            return draw_message(ctx, ["No books found", f"Copy .epub files into {ctx.books_dir}"])

        pages = self._pages()
        page = next(n for n, (a, b) in enumerate(pages) if a <= self.cursor < b)
        img = blank(ctx)
        tag = f"{FILTERS[self.filter]} · " if self.filter != "all" else ""
        d, y, _ = list_frame(img, ctx.ui_fonts, "Library",
                             f"{tag}{SORTS[self.sort]}  {page + 1}/{len(pages)}", icon="library_books",
                             labels=("Up", "Open · hold: options", "Down"), hint_fonts=ctx.small)
        for i in range(*pages[page]):
            height = self._height(i)
            selected = i == self.cursor
            fg = WHITE if selected else BLACK
            if selected:  # inverted bar = the e-paper version of a highlight
                d.rectangle((0, y, ctx.width, y + height - 1), fill=BLACK)
            if i == 0 and self.has_card:
                self._draw_card(img, d, i, y, height, fg, selected)
            else:
                self._draw_row(img, d, i, y, height, fg, selected)
            y += height
        return img

    def _columns(self, cover_w: int, rtl: bool) -> tuple[int, int, int]:
        """(cover x, text left, text right). Arabic rows are mirrored: the
        cover moves to the right and the text is right-aligned beside it."""
        w = self.ctx.width
        if rtl:
            cover_x = w - PAD - cover_w
            return cover_x, PAD, cover_x - 12
        return PAD, PAD + cover_w + 12, w - PAD

    def _paste_cover(self, img, d, i, size, x, y0, height, selected) -> None:
        cover = self._cover(i, size)
        cy = y0 + (height - cover.height) // 2
        if selected:  # a white mat keeps the cover legible on the inverted bar
            d.rectangle((x - 2, cy - 2, x + cover.width + 1, cy + cover.height + 1), fill=WHITE)
        img.paste(cover, (x, cy))

    def _draw_status(self, d, book: Path, x0: float, x1: float, y: float, fg: int, rtl: bool) -> None:
        """New badge, progress bar with percent, or Finished mark."""
        ctx, small = self.ctx, self.ctx.small
        status = ctx.state.status(str(book))
        if status == "reading":
            progress = ctx.state.get_progress(str(book))
            label = f"{round(progress * 100)}%"
            if rtl:   # percent on the left, bar fills from the right
                d.text((x0, y + 2), label, font=small.get(), fill=fg, anchor="lm")
                draw_progress(d, x0 + 36, x1, y, 4, progress, fill=fg, rtl=True)
            else:
                draw_progress(d, x0, x1 - 36, y, 4, progress, fill=fg)
                d.text((x1, y + 2), label, font=small.get(), fill=fg, anchor="rm")
        elif status == "finished":
            text_w = small.width("Finished", Style.BOLD)
            x = x1 - (20 + text_w) if rtl else x0
            x += draw_icon(d, "check_circle", x, y + 2, 16, fill=fg) + 4
            d.text((x, y + 2), "Finished", font=small.get(Style.BOLD), fill=fg, anchor="lm")
        else:
            label_w = small.width("NEW", Style.BOLD)
            left = x1 - (label_w + 10) if rtl else x0
            d.rounded_rectangle((left, y - 6, left + label_w + 10, y + 10), radius=3, outline=fg)
            d.text((left + 5, y + 2), "NEW", font=small.get(Style.BOLD), fill=fg, anchor="lm")

    def _draw_row(self, img, d, i, y0, height, fg, selected) -> None:
        ui, small = self.ctx.ui_fonts, self.ctx.small
        title, author = self.meta[i]
        rtl = text_is_rtl(title)
        align = "right" if rtl else "left"
        cover_x, x0, x1 = self._columns(THUMB_SIZE[0], rtl)
        self._paste_cover(img, d, i, THUMB_SIZE, cover_x, y0, height, selected)
        lines = wrap_text(ui, title, x1 - x0, 2)  # long titles take two lines, then "..."
        if len(lines) == 1:
            draw_text(d, ui, lines[0], x0, x1, y0 + 16, fill=fg, align=align)
            draw_text(d, small, author, x0, x1, y0 + 38, fill=fg, align=align)
            status_y = y0 + height - 18
        else:
            # two title lines leave no room for the author: the title matters more
            for n, line in enumerate(lines):
                draw_text(d, ui, line, x0, x1, y0 + 15 + n * 24, fill=fg, align=align)
            status_y = y0 + height - 14
        self._draw_status(d, self.books[i], x0, x1, status_y, fg, rtl)

    def _draw_card(self, img, d, i, y0, height, fg, selected) -> None:
        """Larger cover, "Continue reading", title, chapter and progress."""
        ui, small, state = self.ctx.ui_fonts, self.ctx.small, self.ctx.state
        title, _ = self.meta[i]
        rtl = text_is_rtl(title)
        align = "right" if rtl else "left"
        cover_x, x0, x1 = self._columns(CARD_THUMB[0], rtl)
        self._paste_cover(img, d, i, CARD_THUMB, cover_x, y0, height, selected)
        label = "CONTINUE READING"
        label_w = small.width(label, Style.BOLD)
        lx = x1 - (label_w + 18) if rtl else x0 - 2
        draw_icon(d, "play_arrow", lx, y0 + 11, 16, fill=fg)
        d.text((lx + 18, y0 + 11), label, font=small.get(Style.BOLD), fill=fg, anchor="lm")
        lines = wrap_text(ui, title, x1 - x0, 2)
        for n, line in enumerate(lines):
            draw_text(d, ui, line, x0, x1, y0 + 31 + n * 24, fill=fg, align=align)
        chapter = state.get_chapter(str(self.books[i]))
        if chapter:
            draw_text(d, small, chapter, x0, x1, y0 + 31 + len(lines) * 24 + 4, fill=fg, align=align)
        self._draw_status(d, self.books[i], x0, x1, y0 + height - 12, fg, rtl)

    def sleep_image(self) -> Image.Image | None:
        """The cover of the book you were last reading."""
        last = self.ctx.state.last_book
        if self.ctx.state.get_setting("sleep_screen", "cover") != "cover" or not last or not Path(last).is_file():
            return None
        title = next((b.title for b in self.infos if str(b.path) == last), Path(last).stem)
        return cover_frame(self.ctx, Path(last), title)

    def handle(self, button: Button) -> Screen | None:
        if button is Button.SELECT_LONG:
            return LibraryOptionsScreen(self.ctx)
        if not self.books:
            return None
        if button is Button.SELECT:
            return ReaderScreen(self.ctx, self.books[self.cursor])
        self.cursor = _move(self.cursor, len(self.books), button)
        self.full_refresh = False  # only the highlight moved: partial refresh is fine
        return None


class LibraryOptionsScreen(Screen):
    """Sort and filter the library, choose the sleep screen, add books over Wi-Fi.
    Choices are remembered."""

    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.cursor = 0
        self.full_refresh = True

    def _rows(self) -> list[tuple[str, str, str, str]]:
        """(key, icon, label, value)"""
        state = self.ctx.state
        return [("sort", "sort", "Sort by", SORTS[state.get_setting("library_sort", "recent")]),
                ("filter", "filter_list", "Show", FILTERS[state.get_setting("library_filter", "all")]),
                ("sleep", "bedtime", "Sleep screen", SLEEP_SCREENS[state.get_setting("sleep_screen", "cover")]),
                ("upload", "cloud_upload", "Add books over Wi-Fi", ""),
                ("back", "arrow_back", "Back to library", "")]

    def render(self) -> Image.Image:
        img = blank(self.ctx)
        d, top, _ = list_frame(img, self.ctx.ui_fonts, "Library options", icon="tune")
        _draw_menu_rows(self.ctx, d, top, [r[1:] for r in self._rows()], self.cursor)
        return img

    def handle(self, button: Button) -> Screen | None:
        rows = self._rows()
        if button is not Button.SELECT:
            self.cursor = _move(self.cursor, len(rows), button)
            self.full_refresh = False
            return None
        state, key = self.ctx.state, rows[self.cursor][0]
        if key == "sort":
            state.set_setting("library_sort", _next(SORTS, state.get_setting("library_sort", "recent")))
        elif key == "filter":
            state.set_setting("library_filter", _next(FILTERS, state.get_setting("library_filter", "all")))
        elif key == "sleep":
            state.set_setting("sleep_screen", _next(SLEEP_SCREENS, state.get_setting("sleep_screen", "cover")))
        elif key == "upload":
            return UploadScreen(self.ctx)
        else:
            return LibraryScreen(self.ctx)
        self.full_refresh = False
        return None


class UploadScreen(Screen):
    """Add books over Wi-Fi. The upload server runs only while this screen is
    open: it shows the address and a PIN to type on the page; select stops it."""

    def __init__(self, ctx: AppContext, port: int = PORT):
        self.ctx = ctx
        self.server: UploadServer | None = None
        self.ip = local_ip()
        self.error = ""
        self.full_refresh = True
        if self.ip is None:
            self.error = "No Wi-Fi connection found."
        else:
            try:
                self.server = UploadServer(ctx.books_dir, port=port)  # a fresh random PIN each time
                self.server.start()
            except OSError as e:
                self.error = f"Couldn't start the server: {e.strerror or e}"

    @property
    def url(self) -> str:
        return f"http://{self.ip}:{self.server.port}" if self.server else ""

    def render(self) -> Image.Image:
        ctx, ui, small = self.ctx, self.ctx.ui_fonts, self.ctx.small
        img = blank(ctx)
        d, top, _ = list_frame(img, ui, "Add books", icon="wifi" if self.server else "wifi_off",
                               labels=("Refresh", "Done", "Refresh"))
        w, x = ctx.width, PAD
        if self.error:
            draw_text(d, ui, self.error, x, w - x, top + 30, align="left")
            draw_text(d, small, "Connect the Pi to Wi-Fi, then try again.", x, w - x, top + 60, align="left")
            return img
        draw_text(d, small, "On your phone or computer (same Wi-Fi), open:", x, w - x, top + 16, align="left")
        draw_text(d, ui, self.url, x, w - x, top + 44, Style.BOLD, align="left")
        draw_text(d, small, "and type this PIN:", x, w - x, top + 80, align="left")
        pin_font = ctx.fonts
        d.text((x, top + 112), "  ".join(self.server.pin), font=pin_font.get(Style.BOLD), fill=BLACK, anchor="lm")
        added = len(self.server.added)
        text = f"Added this time: {added}" if added else "Waiting for books..."
        draw_text(d, small, text, x, w - x, top + 150, align="left")
        return img

    def handle(self, button: Button) -> Screen | None:
        if button is Button.SELECT:
            self.close()
            return LibraryScreen(self.ctx)
        self.full_refresh = False  # up/down just redraw, to show the new count
        return None

    def close(self) -> None:
        if self.server:
            self.server.stop()
            self.server = None


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
                                 position=ctx.state.get_position(str(path)), bottom_margin=6,
                                 chunk=ctx.state.get_chunk(str(path)))
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
            self.ctx.state.set_position(str(self.path), section, page, self.book.progress,
                                        self.book.chapter_title, self.book.chunk)
            self.unsaved_turns = 0

    def close(self) -> None:
        """Save where we are, then release the book (on leaving or quitting)."""
        self._save_progress()
        if self.book:
            self.book.close()
            self.book = None

    def sleep_image(self) -> Image.Image | None:
        """The cover of this book, unless the sleep screen is set to keep the page."""
        if self.ctx.state.get_setting("sleep_screen", "cover") != "cover" or not self.book:
            return None
        return cover_frame(self.ctx, self.path, self.book.epub.title)

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

        if button not in (Button.UP, Button.DOWN):
            return None
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
        _draw_menu_rows(ctx, d, top, [i[1:] for i in self._items()], self.cursor)
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

    def sleep_image(self) -> Image.Image | None:
        return self.reader.sleep_image()

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

    def sleep_image(self) -> Image.Image | None:
        return self.reader.sleep_image()

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
