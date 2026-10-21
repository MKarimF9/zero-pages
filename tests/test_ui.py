"""Screens: library, reader, menu, contents. Run: python3 -m pytest tests/test_ui.py -v"""
import json

from ereader.controls import Button as B
from ereader.fonts import Fonts
from ereader.screens import (AppContext, LibraryScreen, MenuScreen, ReaderScreen, TocScreen,
                             initial_screen)
from ereader.state import ReadingState
from tests.fakes import select_row
from tests.make_epub import make_epub


def make_ctx(tmp_path):
    books = tmp_path / "books"
    books.mkdir()
    make_epub(books / "test.epub")
    state = ReadingState(tmp_path / "state.json")
    return AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15)), state


def open_reader(tmp_path):
    ctx, state = make_ctx(tmp_path)
    reader = LibraryScreen(ctx).handle(B.SELECT)
    assert isinstance(reader, ReaderScreen) and reader.error is None
    return ctx, state, reader


def test_every_screen_renders(tmp_path):
    ctx, state, reader = open_reader(tmp_path)
    lib = LibraryScreen(ctx)
    assert lib.meta == [("Test Book", "Ada Lovelace")]
    menu = reader.handle(B.SELECT)
    assert isinstance(menu, MenuScreen)
    menu.cursor = 1
    toc = menu.handle(B.SELECT)
    assert isinstance(toc, TocScreen)
    for screen in (lib, reader, menu, toc):
        assert screen.render().size == (400, 300)


def test_menu_resume_and_library(tmp_path):
    ctx, state, reader = open_reader(tmp_path)
    menu = reader.handle(B.SELECT)
    assert menu.handle(B.SELECT) is reader and reader.full_refresh  # Resume clears ghosting
    menu = reader.handle(B.SELECT)
    select_row(menu, "library")
    assert isinstance(menu.handle(B.SELECT), LibraryScreen) and reader.book is None


def test_font_family_cycles_and_is_remembered(tmp_path):
    ctx, state, reader = open_reader(tmp_path)
    assert ctx.fonts.family == "Literata"
    menu = reader.handle(B.SELECT)
    select_row(menu, "family")
    for expected in ("Noto Serif", "Charis SIL", "Literata"):
        assert menu.handle(B.SELECT) is None and ctx.fonts.family == expected
    assert state.get_setting("font_family") == "Literata" and ctx.fonts.size == 22
    assert reader.book.fonts is ctx.fonts


def test_font_size_cycles_and_is_remembered(tmp_path):
    ctx, state, reader = open_reader(tmp_path)
    menu = reader.handle(B.SELECT)
    select_row(menu, "font")
    assert menu.handle(B.SELECT) is None and ctx.fonts.size == 24
    assert state.get_setting("font_size") == 24
    assert reader.book.fonts is ctx.fonts  # the book was re-laid out with the new font
    assert menu.full_refresh is False       # changing it is a cheap partial refresh


def test_toc_jump_and_back(tmp_path):
    ctx, state, reader = open_reader(tmp_path)
    menu = reader.handle(B.SELECT)
    menu.cursor = 1
    toc = menu.handle(B.SELECT)
    assert toc.cursor == 1                  # starts on the current chapter
    toc.handle(B.DOWN)                      # "Second Chapter"
    assert toc.handle(B.SELECT) is reader and reader.book.position == (1, 0)
    assert state.get_position(str(reader.path)) == (1, 0)  # a jump is saved right away

    menu = MenuScreen(ctx, reader)
    menu.cursor = 1
    toc = menu.handle(B.SELECT)
    toc.cursor = 0                          # "Back"
    assert toc.handle(B.SELECT) is menu


def test_quitting_from_a_menu_still_saves(tmp_path):
    ctx, state, reader = open_reader(tmp_path)
    reader.book.goto(1)
    reader.unsaved_turns = 1
    menu = reader.handle(B.SELECT)
    menu.close()                            # main.py calls close() on whatever screen is active
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["positions"][str(reader.path)] == [1, 0] and saved["progress"][str(reader.path)] > 0


def test_library_puts_last_read_first_and_shows_progress(tmp_path):
    ctx, state = make_ctx(tmp_path)
    make_epub(ctx.books_dir / "aaa.epub")
    state.set_position(str(ctx.books_dir / "test.epub"), 1, 0, 0.5)
    lib = LibraryScreen(ctx)
    assert lib.books[0].name == "test.epub" and state.get_progress(str(lib.books[0])) == 0.5
    lib.render()


def test_opens_last_book_straight_away(tmp_path):
    ctx, state = make_ctx(tmp_path)
    assert isinstance(initial_screen(ctx), LibraryScreen)       # nothing read yet
    state.set_position(str(ctx.books_dir / "test.epub"), 1, 0, 0.5)
    screen = initial_screen(ctx)
    assert isinstance(screen, ReaderScreen) and screen.book.position == (1, 0)
    assert isinstance(initial_screen(ctx, "library"), LibraryScreen)  # --start library


def test_missing_last_book_falls_back_to_library(tmp_path):
    ctx, state = make_ctx(tmp_path)
    state.set_position(str(ctx.books_dir / "deleted.epub"), 3, 1, 0.2)
    assert isinstance(initial_screen(ctx), LibraryScreen)
