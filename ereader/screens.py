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
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageDraw

from .blocks import Style
from .book import LazyBook
from .controls import Button
from .fonts import FAMILIES, Fonts
from .cache import forget_book
from .covers import THUMB_SIZE, cached_cover
from .layout import MARGIN, text_is_rtl
from .library import BookIndex
from .manager import BookManager
from .page import Page, PlacedImage, PlacedWord
from .qr import qr_image
from .state import ReadingState
from .upload import PORT, UploadServer, local_ip
from .ui import (BLACK, PAD, STATUS_BAR_HEIGHT, WHITE, draw_icon, draw_progress,
                 draw_text, fit_text, list_frame, wrap_text)

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
FILTERS = {"all": "All", "reading": "Reading", "new": "New", "finished": "Finished", "want": "Up next"}
CATEGORY_PREFIX = "cat:"


def filter_choices(state: ReadingState) -> dict[str, str]:
    """Library filters: the fixed ones, then one per category you have made."""
    return {**FILTERS, **{CATEGORY_PREFIX + c: c for c in state.categories()}}


def matches_filter(key: str, book: str, state: ReadingState) -> bool:
    if key == "all":
        return True
    if key == "want":
        return state.is_queued(book)
    if key.startswith(CATEGORY_PREFIX):
        return state.get_category(book) == key[len(CATEGORY_PREFIX):]
    return state.status(book) == key


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


SCROLL_AFTER = 7   # menus with more rows than this scroll instead of shrinking


def _menu_top(ctx: "AppContext") -> int:
    """Where the rows of a menu begin (just under the header)."""
    return ctx.ui_fonts.line_height + 8 + 2


def _footer_top(ctx: "AppContext") -> int:
    return ctx.height - (ctx.ui_fonts.line_height + 8) - 2


def menu_row_height(ctx: "AppContext", count: int, top: int) -> int:
    """Rows are comfortably tall, but shrink so that `count` of them fit above the footer."""
    return min(ctx.ui_fonts.line_height + 12, (_footer_top(ctx) - top - 6) // count)


def menu_capacity(ctx: "AppContext", top: int) -> int:
    """How many rows of a scrolling menu are visible at once."""
    return max(1, (_footer_top(ctx) - top - 6) // (ctx.ui_fonts.line_height + 8))


def menu_window(ctx: "AppContext", count: int, cursor: int, offset: int) -> int:
    """The first visible row of a scrolling menu, moved just enough to keep the cursor in view."""
    if count <= SCROLL_AFTER:
        return 0
    cap = menu_capacity(ctx, _menu_top(ctx))
    if cursor < offset:
        offset = cursor
    elif cursor >= offset + cap:
        offset = cursor - cap + 1
    return max(0, min(offset, count - cap))


def _draw_menu_rows(ctx: "AppContext", d: ImageDraw.ImageDraw, top: int,
                    rows: list[tuple[str, str, str]], cursor: int, offset: int = 0) -> None:
    """Rows of (icon, label, value), the cursor row inverted. Shared by the reader
    menu, the library options and the bookmark list. Long menus scroll, with a
    scrollbar on the right."""
    ui, w = ctx.ui_fonts, ctx.width
    count = len(rows)
    scrolling = count > SCROLL_AFTER
    if scrolling:
        row_h, cap = ui.line_height + 8, menu_capacity(ctx, top)
        shown = range(offset, min(count, offset + cap))
    else:
        row_h, cap = menu_row_height(ctx, count, top), count
        shown = range(count)
    bar_w = w - (9 if scrolling else 0)   # leave room for the scrollbar
    for pos, i in enumerate(shown):
        icon, label, value = rows[i]
        y0 = top + 6 + pos * row_h
        fg = WHITE if i == cursor else BLACK
        if i == cursor:
            d.rectangle((0, y0, bar_w, y0 + row_h - 1), fill=BLACK)
        draw_icon(d, icon, PAD, y0 + row_h / 2, 22, fill=fg)
        draw_text(d, ui, label, PAD + 32, bar_w - PAD - 90, y0 + row_h / 2, fill=fg, align="left")
        if value:
            d.text((bar_w - PAD, y0 + row_h / 2), value, font=ui.get(), fill=fg, anchor="rm")
    if scrolling:
        track_y, track_h = top + 6, cap * row_h
        d.rectangle((w - 6, track_y, w - 3, track_y + track_h - 1), outline=BLACK)
        thumb_h = max(8, track_h * cap // count)
        thumb_y = track_y + (track_h - thumb_h) * offset // max(1, count - cap)
        d.rectangle((w - 6, thumb_y, w - 3, thumb_y + thumb_h - 1), fill=BLACK)


TOC_STEPS = (1, 5, 10, 25)   # how many rows up/down move in a long list; hold select to change
MAX_TOC_LINES = 3            # a long chapter title wraps onto at most this many lines
BY_PART = 0                  # extra step for a contents list with parts: up/down move between top-level entries


def _jump(cursor: int, count: int, button: Button, step: int) -> int:
    """UP/DOWN move a list cursor by `step` rows. With a step of 1 it wraps around;
    with a bigger step it stops at the ends first (so you can't skip past them), and
    pressing again at an end wraps to the other end."""
    if button is Button.UP:
        return count - 1 if cursor == 0 else max(cursor - step, 0)
    if button is Button.DOWN:
        return 0 if cursor == count - 1 else min(cursor + step, count - 1)
    return cursor


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
        self.filters = filter_choices(ctx.state)
        self.filter = ctx.state.get_setting("library_filter", "all")
        if self.filter not in self.filters:   # a category that no longer exists
            self.filter = "all"
        infos = [replace(b, title=ctx.state.get_title(str(b.path)) or b.title)   # names you gave from your phone
                 for b in index.scan(ctx.books_dir)]
        if self.filter != "all":
            infos = [b for b in infos if matches_filter(self.filter, str(b.path), ctx.state)]
        infos = sort_books(infos, self.sort, ctx.state)
        if self.filter == "want":   # your Up next order, not the sort order
            queue = {b: i for i, b in enumerate(ctx.state.get_queue())}
            infos.sort(key=lambda b: queue.get(str(b.path), len(queue)))
        pin_order = {b: i for i, b in enumerate(ctx.state.get_pinned())}   # pinned books first, in pin order
        infos.sort(key=lambda b: pin_order.get(str(b.path), len(pin_order)))  # (stable: the rest keep their order)
        self.pinned_count = sum(str(b.path) in pin_order for b in infos)
        self.infos = infos
        self.books = [b.path for b in infos]
        self.meta = [(b.title, b.author) for b in infos]  # (title, author)
        # The last-read book becomes the "Continue reading" card (only when sorted by recent, and
        # unless it's finished) if it is the first of the pinned books or the first of the rest.
        self.card_index: int | None = None
        last = ctx.state.last_book
        if self.sort == "recent" and last:
            for i in {0, self.pinned_count}:
                if i < len(infos) and str(infos[i].path) == last and ctx.state.status(last) == "reading":
                    self.card_index = i
        self.cursor = 0
        self.full_refresh = True
        self._covers: dict[tuple[int, tuple[int, int]], Image.Image] = {}  # made when first shown

    # ---- layout: the card is taller than a row, so pages are built by height

    @property
    def _row_h(self) -> int:
        return THUMB_SIZE[1] + 8   # the cover sets the row height

    @property
    def has_card(self) -> bool:
        return self.card_index is not None

    def _height(self, i: int) -> int:
        return CARD_HEIGHT if i == self.card_index else self._row_h

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
                return draw_message(ctx, ["No books match", f"Showing: {self.filters[self.filter]}",
                                          "", "Hold select to change the filter."])
            return draw_message(ctx, ["No books found", f"Copy .epub files into {ctx.books_dir}"])

        pages = self._pages()
        page = next(n for n, (a, b) in enumerate(pages) if a <= self.cursor < b)
        img = blank(ctx)
        tag = f"{self.filters[self.filter]} · " if self.filter != "all" else ""
        d, y, _ = list_frame(img, ctx.ui_fonts, "Library",
                             f"{tag}{SORTS[self.sort]}  {page + 1}/{len(pages)}", icon="library_books",
                             labels=("Up", "Open · hold: options", "Down"), hint_fonts=ctx.small)
        for i in range(*pages[page]):
            height = self._height(i)
            selected = i == self.cursor
            fg = WHITE if selected else BLACK
            if selected:  # inverted bar = the e-paper version of a highlight
                d.rectangle((0, y, ctx.width, y + height - 1), fill=BLACK)
            if i == self.card_index:
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

    def _pin_mark(self, d, i: int, y0: int, x0: int, x1: int, rtl: bool, fg: int) -> tuple[int, int]:
        """Draw the pin on a pinned book's row (at the text's far end) and give back
        the text area without the space it takes."""
        if not self.ctx.state.is_pinned(str(self.books[i])):
            return x0, x1
        if rtl:
            draw_icon(d, "push_pin", x0, y0 + 14, 16, fill=fg)
            return x0 + 20, x1
        draw_icon(d, "push_pin", x1 - 16, y0 + 14, 16, fill=fg)
        return x0, x1 - 20

    def _draw_row(self, img, d, i, y0, height, fg, selected) -> None:
        ui, small = self.ctx.ui_fonts, self.ctx.small
        title, author = self.meta[i]
        rtl = text_is_rtl(title)
        align = "right" if rtl else "left"
        cover_x, x0, x1 = self._columns(THUMB_SIZE[0], rtl)
        self._paste_cover(img, d, i, THUMB_SIZE, cover_x, y0, height, selected)
        x0, x1 = self._pin_mark(d, i, y0, x0, x1, rtl, fg)
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
        x0, x1 = self._pin_mark(d, i, y0, x0, x1, rtl, fg)
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
        choices = filter_choices(state)
        return [("sort", "sort", "Sort by", SORTS[state.get_setting("library_sort", "recent")]),
                ("filter", "filter_list", "Show", choices.get(state.get_setting("library_filter", "all"), "All")),
                ("sleep", "bedtime", "Sleep screen", SLEEP_SCREENS[state.get_setting("sleep_screen", "cover")]),
                ("upload", "cloud_upload", "Manage books over Wi-Fi", ""),
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
            state.set_setting("library_filter", _next(filter_choices(state), state.get_setting("library_filter", "all")))
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
                self.server = UploadServer(ctx.books_dir, port=port,   # a fresh random PIN each time
                                           manager=BookManager(ctx.books_dir, ctx.state, layout=(
                                               ctx.fonts, ctx.width, ctx.height - STATUS_BAR_HEIGHT, 6)))
                self.server.start()
            except OSError as e:
                self.error = f"Couldn't start the server: {e.strerror or e}"

    @property
    def url(self) -> str:
        return f"http://{self.ip}:{self.server.port}" if self.server else ""

    def render(self) -> Image.Image:
        ctx, ui, small = self.ctx, self.ctx.ui_fonts, self.ctx.small
        img = blank(ctx)
        d, top, bottom = list_frame(img, ui, "Manage books", icon="wifi" if self.server else "wifi_off",
                               labels=("Refresh", "Done", "Refresh"))
        w, x = ctx.width, PAD
        if self.error:
            draw_text(d, ui, self.error, x, w - x, top + 30, align="left")
            draw_text(d, small, "Connect the Pi to Wi-Fi, then try again.", x, w - x, top + 60, align="left")
            return img
        # The QR code carries the address and the PIN (after the #, so it never leaves the phone),
        # so one scan opens the page unlocked. The PIN is still printed for typing by hand.
        link = f"{self.url}/#{self.server.pin}" if self.server.pin else self.url
        qr = qr_image(link, min(bottom - top - 8, w // 2 - PAD))
        img.paste(qr, (w - PAD - qr.width, top + (bottom - top - qr.height) // 2))
        text_w = w - qr.width - 3 * PAD
        draw_text(d, small, "Scan with your phone,", x, x + text_w, top + 16, align="left")
        draw_text(d, small, "or type this PIN:", x, x + text_w, top + 36, align="left")
        pin_font = ctx.fonts
        d.text((x, top + 66), " ".join(self.server.pin), font=pin_font.get(Style.BOLD), fill=BLACK, anchor="lm")
        for i, line in enumerate(wrap_text(small, self.url, text_w, 3)):
            draw_text(d, small, line, x, x + text_w, top + 100 + i * 20, align="left")
        added = len(self.server.added)
        text = f"Added this time: {added}" if added else "Waiting for books..."
        draw_text(d, small, text, x, x + text_w, bottom - 14, align="left")
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
    bookmarked: bool = False


MIN_PART_WIDTH = 100  # px kept for the collection name in "Collection › Chapter" when the chapter is long


def fit_chapter(fonts: Fonts, text: str, max_width: float) -> str:
    """Fit "Collection › Chapter" into max_width by shortening the collection first, so the
    chapter stays readable. Anything else is cut at the end as usual."""
    if fonts.width(text) <= max_width or " › " not in text:
        return text
    parent, child = text.split(" › ", 1)
    sep = " › "
    room = max_width - fonts.width(sep + child)
    if room < MIN_PART_WIDTH:   # a very long chapter: keep a stub of the collection, cut the chapter
        room = MIN_PART_WIDTH
        child = fit_text(fonts, child, Style.NORMAL, max_width - MIN_PART_WIDTH - fonts.width(sep))
    return fit_text(fonts, parent, Style.NORMAL, room) + sep + child


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
        chapter = fit_chapter(small, st.chapter, right - left - info_w - 12)
        draw_text(d, small, chapter, left + info_w + 12, right, base_y, align="right", baseline=True)
    else:
        draw_text(d, small, info, right - info_w - 1, right, base_y, align="right", baseline=True)
        chapter = fit_chapter(small, st.chapter, right - left - info_w - 12)
        draw_text(d, small, chapter, left, right - info_w - 12, base_y, align="left", baseline=True)


def draw_page(ctx: AppContext, page: Page, status: Status, load_image=None) -> Image.Image:
    """load_image(src, width, height) -> 1-bit picture or None (LazyBook.image)."""
    img = blank(ctx)
    d = ImageDraw.Draw(img)
    for el in page.elements:
        if isinstance(el, PlacedWord):
            fonts = ctx.fonts.scaled(el.size) if el.size else ctx.fonts  # a shrunk verse has its own size
            d.text((el.x, el.y + fonts.baseline), el.word.text, font=fonts.get(el.word.style, el.word.text),
                   fill=BLACK, anchor="ls")  # same baseline for Arabic and Latin words
        elif isinstance(el, PlacedImage):
            picture = load_image(el.src, el.width, el.height) if load_image else None
            if picture is not None:
                img.paste(picture, (el.x, el.y))
            else:  # missing or unsupported (SVG...): a box with the alt text
                d.rectangle((el.x, el.y, el.x + el.width - 1, el.y + el.height - 1), outline=BLACK)
                draw_text(d, ctx.small, el.alt or "[image]", el.x + 8, el.x + el.width - 8,
                          el.y + el.height / 2, align="center")
    if status.bookmarked:  # a small bookmark in the top corner, in the margin above the text
        draw_icon(d, "bookmark", ctx.width - MARGIN - 8, 11, 18, anchor="mm")
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
                                 chunk=ctx.state.get_chunk(str(path)), cache_dir=ctx.state.path.parent)
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
        return cover_frame(self.ctx, self.path, self.ctx.state.get_title(str(self.path)) or self.book.epub.title)

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
                        pages=len(book.pages), progress=book.progress, rtl=book.rtl,
                        bookmarked=book.is_bookmarked(self.ctx.state.get_bookmarks(str(self.path))))
        return draw_page(self.ctx, book.current, status, book.image)

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
        self.offset = 0   # first visible row (the menu scrolls)
        self.full_refresh = True

    def _items(self) -> list[tuple[str, str, str, str]]:
        """(key, icon, label, value) rows."""
        state, path = self.ctx.state, str(self.reader.path)
        marks = state.get_bookmarks(path)
        here = self.reader.book.is_bookmarked(marks)
        items = [("resume", "play_arrow", "Resume", "")]
        if self.reader.book.epub.toc:
            items.append(("toc", "toc", "Table of contents", ""))
        items.append(("bookmarks", "bookmarks", "Bookmarks", str(len(marks)) if marks else ""))
        items.append(("bookmark", "bookmark_remove" if here else "bookmark_add",
                      "Remove bookmark" if here else "Bookmark this page", ""))
        items.append(("library", "local_library", "Back to library", ""))
        items.append(("family", "text_fields", "Font", self.ctx.fonts.family))
        items.append(("font", "format_size", "Font size", str(self.ctx.fonts.size)))
        items.append(("pin", "push_pin", "Unpin from top" if state.is_pinned(path) else "Pin to top", ""))
        items.append(("delete", "delete", "Delete this book", ""))  # last, and asks first
        return items

    def render(self) -> Image.Image:
        ctx = self.ctx
        img = blank(ctx)
        percent = f"{round(self.reader.book.progress * 100)}%"
        d, top, _ = list_frame(img, ctx.ui_fonts, "Menu", percent, icon="menu_book")
        items = self._items()
        self.offset = menu_window(ctx, len(items), self.cursor, self.offset)
        _draw_menu_rows(ctx, d, top, [i[1:] for i in items], self.cursor, self.offset)
        return img

    def handle(self, button: Button) -> Screen | None:
        items = self._items()
        if button is not Button.SELECT:
            self.cursor = _move(self.cursor, len(items), button)
            self.offset = menu_window(self.ctx, len(items), self.cursor, self.offset)
            self.full_refresh = False
            return None

        key = items[self.cursor][0]
        if key == "resume":
            return self.reader.resume()
        if key == "toc":
            return TocScreen(self.ctx, self.reader, self)
        if key == "bookmarks":
            return BookmarksScreen(self.ctx, self.reader, self)
        if key == "bookmark":
            state, path, book = self.ctx.state, str(self.reader.path), self.reader.book
            marks = state.get_bookmarks(path)
            if book.is_bookmarked(marks):
                for mark in book.bookmarks_here(marks):
                    state.remove_bookmark(path, mark)
            else:
                state.add_bookmark(path, book.bookmark())
            self.full_refresh = False
            return None
        if key in ("family", "font"):
            self._change_font(next_family=key == "family")
            self.full_refresh = False
            return None
        if key == "pin":
            path = str(self.reader.path)
            self.ctx.state.set_pinned(path, not self.ctx.state.is_pinned(path))
            self.full_refresh = False
            return None
        if key == "delete":
            return DeleteBookScreen(self.ctx, self.reader, self)
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
    """Table of contents. The first row goes back to the menu. Hold select to make
    up/down jump 1, 5, 10 or 25 rows at a time, for books with many chapters."""

    handles_long_press = True

    def __init__(self, ctx: AppContext, reader: ReaderScreen, menu: MenuScreen):
        self.ctx, self.reader, self.menu = ctx, reader, menu
        self.entries = reader.book.epub.toc
        self.cursor = 1 + reader.book.chapter_index  # start on the current chapter
        self.step = 1
        self.steps = TOC_STEPS + ((BY_PART,) if any(e.level for e in self.entries) else ())
        self._page_list: list[tuple[int, int]] | None = None   # built when first drawn
        self._lines: list[list[str]] = []
        self.full_refresh = True

    @property
    def _row_h(self) -> int:
        """Height of a one-line row."""
        return self.ctx.ui_fonts.line_height + 6

    @property
    def _line_h(self) -> int:
        """Height of each extra line of a wrapped title (a little tighter than a row)."""
        return self.ctx.ui_fonts.line_height - 6

    def _wrapped(self, i: int) -> list[str]:
        """Lines of row i (row 0 is Back). A long title wraps onto up to MAX_TOC_LINES lines;
        only beyond that is it cut with "..."."""
        if i == 0:
            return ["Back"]
        entry = self.entries[i - 1]
        room = self.ctx.width - PAD - (PAD + 8 + entry.level * 14)
        return wrap_text(self.ctx.ui_fonts, entry.title, room, MAX_TOC_LINES) or [entry.title]

    def _height(self, i: int) -> int:
        return self._row_h + (len(self._lines[i]) - 1) * self._line_h

    def _pages(self) -> list[tuple[int, int]]:
        """(first row, one past the last row) of each page. Rows are different heights now,
        so a page is as many rows as fit."""
        if self._page_list is None:
            ctx = self.ctx
            self._lines = [self._wrapped(i) for i in range(len(self.entries) + 1)]
            body = ctx.height - 2 * (ctx.ui_fonts.line_height + 8) - 4
            pages, start, used = [], 0, 0
            for i in range(len(self._lines)):
                h = self._height(i)
                if i > start and used + h > body:
                    pages.append((start, i))
                    start, used = i, 0
                used += h
            pages.append((start, len(self._lines)))
            self._page_list = pages
        return self._page_list

    def _move_by_part(self, button: Button) -> int:
        """Row of the previous/next top-level entry (row 0, Back, counts as one), wrapping."""
        tops = [0] + [i + 1 for i, e in enumerate(self.entries) if e.level == 0]
        if button is Button.DOWN:
            later = [r for r in tops if r > self.cursor]
            return later[0] if later else tops[0]
        earlier = [r for r in tops if r < self.cursor]
        return earlier[-1] if earlier else tops[-1]

    def render(self) -> Image.Image:
        ctx = self.ctx
        pages = self._pages()
        number = next(n for n, (a, b) in enumerate(pages) if a <= self.cursor < b)
        start, end = pages[number]
        img = blank(ctx)
        tag = "jump: parts   " if self.step == BY_PART else f"jump {self.step}   " if self.step > 1 else ""
        d, top, _ = list_frame(img, ctx.ui_fonts, "Contents", f"{tag}{number + 1}/{len(pages)}",
                              icon="toc", labels=("Up", "Open · hold: jump size", "Down"), hint_fonts=ctx.small)
        ui, w = ctx.ui_fonts, ctx.width
        current = 1 + self.reader.book.chapter_index
        y0 = top
        for i in range(start, end):
            row_h = self._height(i)
            selected = i == self.cursor
            fg = WHITE if selected else BLACK
            if selected:
                d.rectangle((0, y0, w, y0 + row_h - 1), fill=BLACK)
            first = y0 + self._row_h / 2   # the middle of the first line
            if i == 0:
                draw_icon(d, "arrow_back", PAD, first, 20, fill=fg)
                draw_text(d, ui, "Back", PAD + 26, w - PAD, first, fill=fg)
            else:
                if i == current:  # an arrow marks the chapter you're reading (a bookmark means a bookmark)
                    draw_icon(d, "play_arrow", 0, first, 16, fill=fg)
                x = PAD + 8 + self.entries[i - 1].level * 14   # chapters sit under their part
                for k, line in enumerate(self._lines[i]):
                    draw_text(d, ui, line, x, w - PAD, first + k * self._line_h, fill=fg)
            y0 += row_h
        return img

    def handle(self, button: Button) -> Screen | None:
        if button is Button.SELECT_LONG:  # hold select: change how far up/down jump
            self.step = self.steps[(self.steps.index(self.step) + 1) % len(self.steps)]
            self.full_refresh = False
            return None
        if button is not Button.SELECT:
            if self.step == BY_PART:
                self.cursor = self._move_by_part(button)
            else:
                self.cursor = _jump(self.cursor, len(self.entries) + 1, button, self.step)
            self.full_refresh = False
            return None
        if self.cursor == 0:
            self.menu.full_refresh = True
            return self.menu
        self.reader.book.goto_entry(self.cursor - 1)
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


class BookmarksScreen(Screen):
    """The bookmarks of the open book. Select goes to one; hold select removes it."""

    handles_long_press = True

    def __init__(self, ctx: AppContext, reader: ReaderScreen, menu: MenuScreen):
        self.ctx, self.reader, self.menu = ctx, reader, menu
        self.cursor = 1 if self._marks() else 0   # row 0 is "Back"
        self.offset = 0
        self.full_refresh = True

    def _marks(self) -> list[dict]:
        return self.ctx.state.get_bookmarks(str(self.reader.path))

    def _rows(self) -> list[tuple[str, str, str]]:
        rows = [("arrow_back", "Back", "")]
        for mark in self._marks():
            percent = f"{round(mark['progress'] * 100)}%"
            rows.append(("bookmark", mark.get("chapter") or percent, percent))
        return rows

    def render(self) -> Image.Image:
        ctx = self.ctx
        rows = self._rows()
        img = blank(ctx)
        d, top, _ = list_frame(img, ctx.ui_fonts, "Bookmarks", str(len(rows) - 1) if len(rows) > 1 else "",
                               icon="bookmarks", labels=("Up", "Go · hold: remove", "Down"), hint_fonts=ctx.small)
        self.offset = menu_window(ctx, len(rows), self.cursor, self.offset)
        _draw_menu_rows(ctx, d, top, rows, self.cursor, self.offset)
        if len(rows) == 1:
            row_h = menu_row_height(ctx, 1, top)
            draw_text(d, ctx.small, "No bookmarks yet.", PAD, ctx.width - PAD, top + 6 + row_h + 22, align="left")
            draw_text(d, ctx.small, "Use \"Bookmark this page\" in the menu.", PAD, ctx.width - PAD,
                      top + 6 + row_h + 44, align="left")
        return img

    def handle(self, button: Button) -> Screen | None:
        rows = self._rows()
        if button is Button.SELECT_LONG and self.cursor > 0:  # remove the selected bookmark
            self.ctx.state.remove_bookmark(str(self.reader.path), self._marks()[self.cursor - 1])
            self.cursor = min(self.cursor, len(self._marks()))
            self.full_refresh = False
            return None
        if button not in (Button.SELECT, Button.SELECT_LONG):
            self.cursor = _move(self.cursor, len(rows), button)
            self.offset = menu_window(self.ctx, len(rows), self.cursor, self.offset)
            self.full_refresh = False
            return None
        if self.cursor == 0:
            self.menu.full_refresh = True
            return self.menu
        self.reader.book.goto_bookmark(self._marks()[self.cursor - 1])
        self.reader.unsaved_turns += 1
        self.reader._save_progress()
        return self.reader.resume()

    def sleep_image(self) -> Image.Image | None:
        return self.reader.sleep_image()

    def close(self) -> None:
        self.reader.close()


class DeleteBookScreen(Screen):
    """Asks before deleting the book you're reading. "Keep the book" is selected
    first, so a stray press can't delete anything."""

    def __init__(self, ctx: AppContext, reader: ReaderScreen, menu: MenuScreen):
        self.ctx, self.reader, self.menu = ctx, reader, menu
        self.cursor = 0  # 0 = keep, 1 = delete
        self.error = ""
        self.full_refresh = True

    @property
    def title(self) -> str:
        state, path = self.reader.ctx.state, self.reader.path
        return state.get_title(str(path)) or (self.reader.book.epub.title if self.reader.book else "") or path.stem

    def _deletable(self) -> bool:
        """Only a plain .epub that sits directly in the books folder."""
        path = self.reader.path
        try:
            return (path.suffix.lower() == ".epub" and path.is_file()
                    and path.resolve().parent == Path(self.ctx.books_dir).expanduser().resolve())
        except OSError:
            return False

    def render(self) -> Image.Image:
        ctx, ui, small = self.ctx, self.ctx.ui_fonts, self.ctx.small
        img = blank(ctx)
        d, top, _ = list_frame(img, ui, "Delete this book?", icon="delete")
        x, w, y = PAD, ctx.width, top + 8
        for line in wrap_text(ui, self.title, w - 2 * x, 2, Style.BOLD):
            draw_text(d, ui, line, x, w - x, y + 12, Style.BOLD)
            y += 26
        message = self.error or "It is removed from the reader and can't be undone."
        for line in wrap_text(small, message, w - 2 * x, 2):
            draw_text(d, small, line, x, w - x, y + 10)
            y += 20
        _draw_menu_rows(ctx, d, y + 6, [("arrow_back", "Keep the book", ""), ("delete", "Delete it", "")],
                        self.cursor)
        return img

    def handle(self, button: Button) -> Screen | None:
        if button is not Button.SELECT:
            self.cursor = _move(self.cursor, 2, button)
            self.error = ""
            self.full_refresh = False
            return None
        if self.cursor == 0:
            self.menu.full_refresh = True
            return self.menu
        return self._delete()

    def _delete(self) -> Screen | None:
        ctx, reader = self.ctx, self.reader
        path = reader.path
        if not self._deletable():
            self.error = "This file can't be deleted from here."
        else:
            try:
                path.unlink()
            except OSError as e:
                self.error = f"Couldn't delete it: {e.strerror or e}"
        if self.error:
            self.cursor = 0
            self.full_refresh = False
            return None
        reader.unsaved_turns = 0   # don't write a saved place for a book that's gone
        reader.close()
        ctx.state.forget(str(path))
        forget_book(ctx.state.path.parent, path)
        return LibraryScreen(ctx)

    def sleep_image(self) -> Image.Image | None:
        return self.reader.sleep_image()

    def close(self) -> None:
        self.reader.close()

