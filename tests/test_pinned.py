"""Pinned books. Run: python3 -m pytest tests/test_pinned.py -v"""
import json

from ereader.controls import Button as B
from ereader.fonts import Fonts
from ereader.screens import AppContext, LibraryScreen, menu_row_height
from ereader.state import ReadingState
from tests.test_library_sort import make_book


def make_ctx(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    make_book(books / "1.epub", "Banana", "Zed")
    make_book(books / "2.epub", "apple", "Yan")
    make_book(books / "3.epub", "Cherry", "Xia")
    make_book(books / "4.epub", "Quran", "Allah")
    state = ReadingState(tmp_path / "state" / "s.json")
    return AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15)), state, books


def titles(lib):
    return [t for t, _ in lib.meta]


def test_pin_state_order_persistence_and_forget(tmp_path):
    f = tmp_path / "s.json"
    state = ReadingState(f)
    assert state.get_pinned() == [] and not state.is_pinned("a")
    state.set_pinned("a", True); state.set_pinned("b", True); state.set_pinned("a", True)   # twice: no duplicate
    assert ReadingState(f).get_pinned() == ["a", "b"] and ReadingState(f).is_pinned("b")
    state.set_pinned("a", False); state.set_pinned("zzz", False)                            # unpin, and unpin a stranger
    assert state.get_pinned() == ["b"]
    state.set_position("b", 1, 0, 0.3)
    state.forget("b")                                                                      # a deleted book is unpinned
    assert ReadingState(f).get_pinned() == []


def test_old_state_files_without_pins_load(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"pages": {}, "positions": {}, "last_book": None}))
    state = ReadingState(f)
    assert state.get_pinned() == []
    state.set_pinned("a", True)
    assert ReadingState(f).get_pinned() == ["a"]


def test_pinned_books_are_first_whatever_the_sort(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_pinned(str(books / "4.epub"), True)
    for sort, rest in (("title", ["apple", "Banana", "Cherry"]), ("author", ["Cherry", "apple", "Banana"]),
                       ("recent", None)):
        state.set_setting("library_sort", sort)
        lib = LibraryScreen(ctx)
        assert titles(lib)[0] == "Quran" and lib.pinned_count == 1, sort
        if rest:
            assert titles(lib)[1:] == rest, sort


def test_several_pins_keep_the_order_they_were_pinned_in(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_pinned(str(books / "4.epub"), True)
    state.set_pinned(str(books / "2.epub"), True)
    state.set_setting("library_sort", "title")
    lib = LibraryScreen(ctx)
    assert titles(lib)[:2] == ["Quran", "apple"] and lib.pinned_count == 2
    assert titles(lib)[2:] == ["Banana", "Cherry"]


def test_a_filter_still_puts_pinned_first_and_hides_pinned_books_it_excludes(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_position(str(books / "1.epub"), 1, 0, 0.4)
    state.set_position(str(books / "3.epub"), 1, 0, 0.5)
    state.set_pinned(str(books / "3.epub"), True)
    state.set_setting("library_filter", "reading")
    assert titles(LibraryScreen(ctx)) == ["Cherry", "Banana"]
    state.set_pinned(str(books / "4.epub"), True)             # pinned but "new": excluded by the filter
    assert "Quran" not in titles(LibraryScreen(ctx))


def test_the_continue_card_goes_to_the_last_read_book_at_the_top_of_its_group(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    quran, banana = str(books / "4.epub"), str(books / "1.epub")
    state.set_pinned(quran, True)
    state.set_position(banana, 1, 0, 0.4)                     # last read: Banana, not pinned
    lib = LibraryScreen(ctx)
    assert titles(lib)[:2] == ["Quran", "Banana"] and lib.card_index == 1 and lib.has_card   # right under the pins
    state.set_position(quran, 1, 0, 0.2)                      # now the pinned Quran is the last read
    lib = LibraryScreen(ctx)
    assert titles(lib)[0] == "Quran" and lib.card_index == 0
    state.set_position(banana, 1, 0, 0.4)                     # and a second pinned book that is last read
    state.set_pinned(banana, True)
    lib = LibraryScreen(ctx)
    assert titles(lib)[:2] == ["Quran", "Banana"] and lib.card_index is None   # not the first of its group
    state.set_setting("library_sort", "title")
    assert LibraryScreen(ctx).card_index is None              # the card only exists when sorted by recent


def test_pinned_rows_and_cards_render_and_every_book_is_reachable(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_pinned(str(books / "4.epub"), True)
    state.set_pinned(str(books / "2.epub"), True)
    state.set_position(str(books / "4.epub"), 1, 0, 0.3, "A chapter")
    lib = LibraryScreen(ctx)
    seen = set()
    for _ in range(4):
        assert lib.render().size == (400, 300)
        seen.add(lib.cursor)
        lib.handle(B.DOWN)
    assert seen == {0, 1, 2, 3}


def test_the_menu_pins_and_unpins(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_position(str(books / "4.epub"), 1, 0, 0.3)
    reader = LibraryScreen(ctx).handle(B.SELECT)               # the Quran: opens first (last read)
    menu = reader.handle(B.SELECT)
    items = menu._items()
    pin = [i for i, row in enumerate(items) if row[0] == "pin"][0]
    assert items[pin][2] == "Pin to top" and items[-1][0] == "delete" and items[-2][0] == "library"
    menu.cursor = pin
    assert menu.handle(B.SELECT) is None and state.is_pinned(str(reader.path)) and menu.full_refresh is False
    assert menu._items()[pin][2] == "Unpin from top"
    assert menu.handle(B.SELECT) is None and not state.is_pinned(str(reader.path))
    assert menu.render().size == (400, 300)


def test_seven_menu_rows_still_fit(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    top = ctx.ui_fonts.line_height + 8 + 2
    footer_top = 300 - (ctx.ui_fonts.line_height + 8) - 2
    assert 6 + 7 * menu_row_height(ctx, 7, top) <= footer_top - top and menu_row_height(ctx, 7, top) >= 28
