"""Bookmarks. Run: python3 -m pytest tests/test_bookmarks.py -v"""
import json

import ereader.book as bookmod
from ereader.book import LazyBook
from ereader.controls import Button as B
from ereader.fonts import Fonts
from ereader.page import PlacedWord
from ereader.screens import (AppContext, BookmarksScreen, LibraryScreen, MenuScreen, ReaderScreen,
                             effective_button)
from ereader.state import ReadingState
from tests.fakes import select_row
from tests.test_book import make_book
from tests.test_chunks import paragraphs


def make_ctx(tmp_path, sections=None):
    books = tmp_path / "books"; books.mkdir()
    make_book(books, sections or {"a": paragraphs(150), "b": paragraphs(60, 500)})   # writes books/lazy.epub
    state = ReadingState(tmp_path / "state" / "s.json")
    return AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15)), state


def open_reader(ctx):
    reader = LibraryScreen(ctx).handle(B.SELECT)
    assert isinstance(reader, ReaderScreen) and reader.error is None
    return reader


def words_on_page(book):
    return [el.word.text for el in book.current.elements if isinstance(el, PlacedWord)]


def turn(reader, n):
    for _ in range(n):
        reader.handle(B.DOWN)


# ---------------------------------------------------------------- state

def test_state_adds_sorts_removes_and_forgets_bookmarks(tmp_path):
    f = tmp_path / "s.json"
    state = ReadingState(f)
    a = {"section": 1, "chunk": 0, "fraction": 0.5, "chapter": "A", "progress": 0.3}
    b = {"section": 0, "chunk": 2, "fraction": 0.1, "chapter": "B", "progress": 0.1}
    state.add_bookmark("book", a); state.add_bookmark("book", b)
    assert [m["chapter"] for m in ReadingState(f).get_bookmarks("book")] == ["B", "A"]     # in reading order
    state.get_bookmarks("book")[0]["chapter"] = "changed"                                   # copies: no side effects
    assert ReadingState(f).get_bookmarks("book")[0]["chapter"] == "B"
    state.remove_bookmark("book", a)
    assert [m["chapter"] for m in state.get_bookmarks("book")] == ["B"]
    state.remove_bookmark("book", b)
    assert state.get_bookmarks("book") == [] and "book" not in ReadingState(f)._data["bookmarks"]
    state.add_bookmark("book", a); state.forget("book")
    assert ReadingState(f).get_bookmarks("book") == []


def test_old_state_files_without_bookmarks_load(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"pages": {}, "positions": {}, "last_book": None}))
    state = ReadingState(f)
    assert state.get_bookmarks("x") == []
    state.add_bookmark("x", {"section": 0, "chunk": 0, "fraction": 0.0, "chapter": "", "progress": 0.0})
    assert len(ReadingState(f).get_bookmarks("x")) == 1


# ---------------------------------------------------------------- the book

def test_a_bookmark_marks_one_page_and_goto_returns_to_it(tmp_path):
    ctx, state = make_ctx(tmp_path)
    book = open_reader(ctx).book
    for _ in range(7):
        book.next()
    here = (book.section, book.chunk, book.page)
    first_word = words_on_page(book)[0]
    mark = book.bookmark()
    assert book.is_bookmarked([mark]) and book.bookmarks_here([mark]) == [mark]
    book.next()
    assert not book.is_bookmarked([mark])                           # the next page isn't bookmarked
    book.goto(1)                                                    # somewhere else entirely
    book.goto_bookmark(mark)
    assert (book.section, book.chunk, book.page) == here and words_on_page(book)[0] == first_word
    assert book.is_bookmarked([mark])


def test_bookmarks_in_later_chunks_and_sections_work(tmp_path):
    old = bookmod.CHUNK_BYTES
    bookmod.CHUNK_BYTES = 2000                                      # small chunks, so a section has several
    try:
        ctx, state = make_ctx(tmp_path, {"a": paragraphs(400), "b": paragraphs(50, 900)})
        book = open_reader(ctx).book
        marks, seen = [], set()
        while book.next():
            if (book.section, book.chunk) not in seen and book.page == 1:
                seen.add((book.section, book.chunk))
                marks.append(book.bookmark())
        assert {m["section"] for m in marks} == {0, 1} and len({m["chunk"] for m in marks}) >= 3
        for mark in marks:
            book.goto(0)
            book.goto_bookmark(mark)
            assert book.is_bookmarked([mark]) and (book.section, book.chunk) == (mark["section"], mark["chunk"])
    finally:
        bookmod.CHUNK_BYTES = old


def test_a_bookmark_survives_a_font_size_change(tmp_path):
    ctx, state = make_ctx(tmp_path)
    book = open_reader(ctx).book
    for _ in range(9):
        book.next()
    marked_word = words_on_page(book)[0]
    mark = book.bookmark()
    book.set_fonts(Fonts(30))                                       # bigger text: pages move
    book.goto(1); book.goto_bookmark(mark)
    near = set()
    for _ in range(2):                                              # the marked text is on this page or the next
        near.update(words_on_page(book)); book.next()
    assert marked_word in near
    assert (book.section, book.chunk) == (mark["section"], mark["chunk"])


def test_a_stale_bookmark_is_clamped_not_a_crash(tmp_path):
    ctx, state = make_ctx(tmp_path)
    book = open_reader(ctx).book
    book.goto_bookmark({"section": 99, "chunk": 99, "fraction": 0.999, "chapter": "", "progress": 0})
    assert 0 <= book.section < book.section_count and 0 <= book.page < len(book.pages)


# ---------------------------------------------------------------- the menu and the list

def test_menu_adds_and_removes_the_bookmark_for_this_page(tmp_path):
    ctx, state = make_ctx(tmp_path)
    reader = open_reader(ctx)
    turn(reader, 4)
    path = str(reader.path)
    menu = reader.handle(B.SELECT)
    rows = {r[0]: r for r in menu._items()}
    assert rows["bookmark"][2] == "Bookmark this page" and rows["bookmarks"][3] == ""
    select_row(menu, "bookmark")
    assert menu.handle(B.SELECT) is None and menu.full_refresh is False
    rows = {r[0]: r for r in menu._items()}
    assert rows["bookmark"][2] == "Remove bookmark" and rows["bookmarks"][3] == "1"
    assert len(state.get_bookmarks(path)) == 1
    assert menu.handle(B.SELECT) is None and state.get_bookmarks(path) == []     # the same row again removes it
    assert menu.render().size == (400, 300)


def test_the_bookmark_list_jumps_and_removes(tmp_path):
    ctx, state = make_ctx(tmp_path)
    reader = open_reader(ctx)
    path = str(reader.path)
    spots = []
    for n in (3, 9, 14):
        reader.book.goto(0)
        turn(reader, n)
        state.add_bookmark(path, reader.book.bookmark())
        spots.append((reader.book.section, reader.book.chunk, reader.book.page))
    menu = reader.handle(B.SELECT)
    select_row(menu, "bookmarks")
    assert menu._items()[menu.cursor][3] == "3"
    screen = menu.handle(B.SELECT)
    assert isinstance(screen, BookmarksScreen) and screen.cursor == 1 and screen.render().size == (400, 300)
    screen.handle(B.DOWN)                                           # the second bookmark
    assert screen.handle(B.SELECT) is reader and reader.full_refresh
    assert (reader.book.section, reader.book.chunk, reader.book.page) == spots[1]
    assert state.get_position(path) == (reader.book.section, reader.book.page)    # saved right away

    screen = BookmarksScreen(ctx, reader, reader.handle(B.SELECT))
    screen.cursor = 2
    assert screen.handle(B.SELECT_LONG) is None                     # hold: remove the second one
    assert [(m["section"], m["page"] if "page" in m else None) for m in state.get_bookmarks(path)] == [(0, None), (0, None)]
    assert len(state.get_bookmarks(path)) == 2 and screen.cursor == 2
    assert screen.handle(B.SELECT_LONG) is None and screen.handle(B.SELECT_LONG) is None  # ... and the rest
    assert state.get_bookmarks(path) == [] and screen.cursor == 0
    assert screen.render().size == (400, 300)                       # the empty list explains itself
    assert screen.handle(B.SELECT_LONG) is screen.menu              # on "Back" a hold just goes back


def test_the_back_row_and_a_hold_on_it(tmp_path):
    ctx, state = make_ctx(tmp_path)
    reader = open_reader(ctx)
    menu = reader.handle(B.SELECT)
    screen = BookmarksScreen(ctx, reader, menu)
    assert screen.cursor == 0 and screen.handle(B.SELECT) is menu and menu.full_refresh
    assert effective_button(B.SELECT_LONG, screen) is B.SELECT_LONG     # this screen uses the hold


def test_a_long_bookmark_list_scrolls(tmp_path):
    ctx, state = make_ctx(tmp_path)
    reader = open_reader(ctx)
    for i in range(14):
        state.add_bookmark(str(reader.path), {"section": 0, "chunk": 0, "fraction": i / 20, "chapter": f"Chapter {i}",
                                              "progress": i / 20})
    screen = BookmarksScreen(ctx, reader, reader.handle(B.SELECT))
    seen = set()
    for _ in range(16):
        assert screen.render().size == (400, 300)
        seen.add(screen.cursor)
        screen.handle(B.DOWN)
    assert seen == set(range(15))                                   # Back + 14 bookmarks, all reachable


def test_the_page_shows_a_bookmark_mark_only_when_bookmarked(tmp_path):
    ctx, state = make_ctx(tmp_path)
    reader = open_reader(ctx)
    turn(reader, 2)
    corner = lambda img: sum(1 for p in img.convert("L").crop((340, 0, 390, 20)).tobytes() if p < 128)
    plain = corner(reader.render())
    state.add_bookmark(str(reader.path), reader.book.bookmark())
    assert corner(reader.render()) > plain + 20                     # the mark is drawn in the top corner
    reader.handle(B.DOWN)
    assert corner(reader.render()) <= plain + 5                     # and not on the next page


def test_deleting_a_book_forgets_its_bookmarks(tmp_path):
    ctx, state = make_ctx(tmp_path)
    reader = open_reader(ctx)
    state.add_bookmark(str(reader.path), reader.book.bookmark())
    state.forget(str(reader.path))
    assert state.get_bookmarks(str(reader.path)) == []
