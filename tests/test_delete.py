"""Deleting the book you're reading. Run: python3 -m pytest tests/test_delete.py -v"""
import os
import shutil

from ereader.cache import book_cache_dir, forget_book
from ereader.controls import Button as B
from ereader.fonts import Fonts
from ereader.screens import (AppContext, DeleteBookScreen, LibraryScreen, MenuScreen, ReaderScreen,
                             menu_row_height)
from ereader.state import ReadingState
from tests.fakes import select_row
from tests.make_epub import make_epub


def make_ctx(tmp_path, names=("a.epub", "b.epub")):
    books = tmp_path / "books"; books.mkdir()
    for n in names:
        make_epub(books / n, cover="epub3")
    state = ReadingState(tmp_path / "state" / "s.json")
    return AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15)), state, books


def open_delete_screen(ctx):
    reader = LibraryScreen(ctx).handle(B.SELECT)
    assert isinstance(reader, ReaderScreen) and reader.error is None
    menu = reader.handle(B.SELECT)
    items = menu._items()
    assert items[-1][0] == "delete" and items[-1][2] == "Delete this book"   # the last row
    select_row(menu, "delete")
    screen = menu.handle(B.SELECT)
    assert isinstance(screen, DeleteBookScreen)
    return reader, menu, screen


def test_the_long_menu_scrolls_and_keeps_the_cursor_in_view(tmp_path):
    from ereader.screens import menu_capacity, menu_window
    ctx, state, books = make_ctx(tmp_path)
    reader = LibraryScreen(ctx).handle(B.SELECT)
    menu = reader.handle(B.SELECT)
    n = len(menu._items())
    top = ctx.ui_fonts.line_height + 8 + 2
    cap = menu_capacity(ctx, top)
    assert n > 7 and 5 <= cap < n                                   # more rows than fit: it scrolls
    assert menu.render().size == (400, 300)
    seen = set()
    for _ in range(n + 2):                                          # all the way round, with wrap-around
        assert menu.offset <= menu.cursor < menu.offset + cap
        seen.add(menu.cursor)
        menu.render()
        menu.handle(B.DOWN)
    assert seen == set(range(n))
    menu.cursor = 0; menu.offset = 0
    menu.handle(B.UP)                                               # up from the first row wraps to the last
    assert menu.cursor == n - 1 and menu.offset == n - cap
    assert menu_window(ctx, 5, 4, 3) == 0                           # short menus never scroll


def test_the_confirm_screen_starts_on_keep_and_cancel_changes_nothing(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    reader, menu, screen = open_delete_screen(ctx)
    assert screen.cursor == 0 and screen.render().size == (400, 300)
    assert screen.handle(B.SELECT) is menu                       # "Keep the book"
    assert (books / "a.epub").exists() and reader.book is not None


def test_pressing_up_or_down_then_select_deletes_only_after_choosing_delete(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    reader, menu, screen = open_delete_screen(ctx)
    assert screen.handle(B.DOWN) is None and screen.cursor == 1
    assert screen.handle(B.UP) is None and screen.cursor == 0
    assert (books / "a.epub").exists()


def test_deleting_removes_the_file_the_memory_and_the_caches(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    reader, menu, screen = open_delete_screen(ctx)
    path = reader.path
    reader.handle(B.DOWN); reader.unsaved_turns = 3                 # there is a place that would be saved
    state.set_position(str(path), 1, 0, 0.4, "Chapter", 0)
    cache = tmp_path / "state"
    for kind in ("covers", "images"):                               # pretend pictures were cached
        folder = book_cache_dir(cache, kind, path); folder.mkdir(parents=True); (folder / "x.png").write_bytes(b"png")
    other = books / "b.epub"
    other_dir = book_cache_dir(cache, "covers", other); other_dir.mkdir(parents=True); (other_dir / "y.png").write_bytes(b"png")

    screen.handle(B.DOWN)
    result = screen.handle(B.SELECT)
    assert isinstance(result, LibraryScreen)
    assert not path.exists() and other.exists()                     # only that file
    assert [p.name for p in result.books] == ["b.epub"]
    assert reader.book is None
    assert state.last_book is None and state.get_progress(str(path)) == 0 and state.status(str(path)) == "new"
    assert not book_cache_dir(cache, "covers", path).exists() and not book_cache_dir(cache, "images", path).exists()
    assert (other_dir / "y.png").exists()                           # the other book's cache is untouched


def test_the_forgotten_book_is_not_saved_again_on_quit(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    reader, menu, screen = open_delete_screen(ctx)
    path = str(reader.path)
    reader.unsaved_turns = 5
    screen.handle(B.DOWN); screen.handle(B.SELECT)
    reader.close()                                                  # closing a gone book writes nothing
    assert ReadingState(tmp_path / "state" / "s.json").get_position(path) == (0, 0)


def test_quitting_on_the_confirm_screen_keeps_and_saves_the_book(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    reader, menu, screen = open_delete_screen(ctx)
    reader.book.goto(1); reader.unsaved_turns = 1
    screen.close()
    assert (books / "a.epub").exists() and state.get_position(str(reader.path)) == (1, 0)


def test_a_file_that_cannot_be_deleted_is_reported_and_the_reader_survives(tmp_path):
    if os.geteuid() == 0:
        return                                                      # root ignores permissions
    ctx, state, books = make_ctx(tmp_path)
    reader, menu, screen = open_delete_screen(ctx)
    screen.handle(B.DOWN)
    os.chmod(books, 0o500)                                          # folder not writable: unlink fails
    try:
        assert screen.handle(B.SELECT) is None
    finally:
        os.chmod(books, 0o700)
    assert "Couldn't delete" in screen.error and screen.cursor == 0
    assert (books / "a.epub").exists() and reader.book is not None and screen.render().size == (400, 300)
    assert screen.handle(B.SELECT) is menu                          # and you can carry on reading


def test_only_epubs_in_the_books_folder_can_be_deleted(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    reader, menu, screen = open_delete_screen(ctx)
    outside = tmp_path / "elsewhere.epub"
    shutil.copy(reader.path, outside)
    reader.path = outside                                           # not in the books folder
    screen.handle(B.DOWN)
    assert screen.handle(B.SELECT) is None and outside.exists() and "can't be deleted" in screen.error


def test_state_forget_and_cache_forget(tmp_path):
    state = ReadingState(tmp_path / "s.json")
    state.set_position("b", 3, 1, 0.5, "Ch", 2)
    state.set_position("keep", 1, 0, 0.1)
    state.forget("b")
    fresh = ReadingState(tmp_path / "s.json")
    assert fresh.get_position("b") == (0, 0) and fresh.get_chunk("b") == 0 and fresh.get_chapter("b") == ""
    assert fresh.last_book == "keep" or fresh.last_book is None
    assert fresh.get_position("keep") == (1, 0)
    folder = book_cache_dir(tmp_path, "covers", "b"); folder.mkdir(parents=True)
    forget_book(tmp_path, "b")
    forget_book(tmp_path, "never-cached")                           # nothing to remove: no error
    assert not folder.exists()
