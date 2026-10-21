"""Library sort, filter and options. Run: python3 -m pytest tests/test_library_sort.py -v"""
import zipfile

from ereader.controls import Button as B
from ereader.fonts import Fonts
from ereader.screens import (AppContext, LibraryOptionsScreen, LibraryScreen, ReaderScreen,
                             effective_button, sort_books)
from ereader.state import ReadingState
from tests.make_epub import make_epub


def make_book(path, title, author):
    src = make_epub(path)
    with zipfile.ZipFile(src) as z:
        files = {i.filename: z.read(i.filename) for i in z.infolist()}
    opf = files["OEBPS/content.opf"].replace(b"Test Book", title.encode()).replace(b"Ada Lovelace", author.encode())
    files["OEBPS/content.opf"] = opf
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


def make_ctx(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    make_book(books / "1.epub", "Banana", "Zed")
    make_book(books / "2.epub", "apple", "Yan")
    make_book(books / "3.epub", "Cherry", "Xia")
    state = ReadingState(tmp_path / "state" / "s.json")
    return AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15)), state, books


def titles(lib):
    return [t for t, _ in lib.meta]


def test_sort_by_title_author_and_recent(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_setting("library_sort", "title")
    assert titles(LibraryScreen(ctx)) == ["apple", "Banana", "Cherry"]      # case-insensitive
    state.set_setting("library_sort", "author")
    assert titles(LibraryScreen(ctx)) == ["Cherry", "apple", "Banana"]      # Xia, Yan, Zed
    state.set_setting("library_sort", "recent")
    state.set_position(str(books / "1.epub"), 1, 0, 0.3)
    state.set_position(str(books / "3.epub"), 1, 0, 0.3)
    state._data["opened"][str(books / "1.epub")] = 100.0
    state._data["opened"][str(books / "3.epub")] = 200.0
    assert titles(LibraryScreen(ctx)) == ["Cherry", "Banana", "apple"]      # newest first, unread last


def test_filters_by_status(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_position(str(books / "1.epub"), 1, 0, 0.4)
    state.set_position(str(books / "2.epub"), 1, 0, 1.0)
    for flt, expected in (("all", 3), ("reading", 1), ("finished", 1), ("new", 1)):
        state.set_setting("library_filter", flt)
        assert len(LibraryScreen(ctx).books) == expected, flt
    state.set_setting("library_filter", "reading")
    assert titles(LibraryScreen(ctx)) == ["Banana"]


def test_card_only_when_sorted_by_recent(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_position(str(books / "1.epub"), 1, 0, 0.4)
    assert LibraryScreen(ctx).has_card
    state.set_setting("library_sort", "title")
    assert not LibraryScreen(ctx).has_card


def test_empty_filter_shows_a_message_and_options_still_open(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_setting("library_filter", "finished")
    lib = LibraryScreen(ctx)
    assert lib.books == [] and lib.render().size == (400, 300)
    assert lib.handle(B.DOWN) is None and lib.handle(B.SELECT) is None
    assert isinstance(lib.handle(B.SELECT_LONG), LibraryOptionsScreen)


def test_options_cycle_persist_and_go_back(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    lib = LibraryScreen(ctx)
    opts = lib.handle(B.SELECT_LONG)
    assert isinstance(opts, LibraryOptionsScreen) and opts.render().size == (400, 300)
    for expected in ("title", "author", "recent"):
        assert opts.handle(B.SELECT) is None
        assert state.get_setting("library_sort") == expected
    opts.handle(B.DOWN)
    assert opts.handle(B.SELECT) is None and state.get_setting("library_filter") == "reading"
    opts.handle(B.DOWN)
    back = opts.handle(B.SELECT)
    assert isinstance(back, LibraryScreen) and back.filter == "reading"


def test_header_and_hints_render(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_setting("library_filter", "new")
    state.set_setting("library_sort", "title")
    assert LibraryScreen(ctx).render().size == (400, 300)


def test_hold_select_is_plain_select_outside_the_library(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    lib = LibraryScreen(ctx)
    assert effective_button(B.SELECT_LONG, lib) is B.SELECT_LONG
    reader = lib.handle(B.SELECT)
    assert isinstance(reader, ReaderScreen)
    assert effective_button(B.SELECT_LONG, reader) is B.SELECT
    assert effective_button(B.DOWN, reader) is B.DOWN
    position = reader.book.position
    assert reader.handle(B.SELECT_LONG) is None and reader.book.position == position  # never turns a page


def test_sort_books_does_not_modify_its_input(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    infos = LibraryScreen(ctx).infos
    before = list(infos)
    sort_books(infos, "title", state)
    assert infos == before
