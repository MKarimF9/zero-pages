"""Reading status and the Continue card. Run: python3 -m pytest tests/test_status.py -v"""
from ereader.controls import Button as B
from ereader.fonts import Fonts
from ereader.screens import AppContext, LibraryScreen
from ereader.state import FINISHED_AT, ReadingState
from tests.make_epub import make_epub


def make_ctx(tmp_path, names=("a.epub",)):
    books = tmp_path / "books"
    books.mkdir()
    for n in names:
        make_epub(books / n)
    state = ReadingState(tmp_path / "state" / "state.json")
    return AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15)), state, books


def test_status_new_reading_finished(tmp_path):
    state = ReadingState(tmp_path / "s.json")
    assert state.status("b") == "new"
    state.set_position("b", 3, 1, 0.4)
    assert state.status("b") == "reading"
    state.set_position("b", 9, 0, FINISHED_AT)
    assert state.status("b") == "finished"


def test_chapter_is_remembered(tmp_path):
    state = ReadingState(tmp_path / "s.json")
    state.set_position("b", 3, 1, 0.4, "Chapter Three")
    assert ReadingState(tmp_path / "s.json").get_chapter("b") == "Chapter Three"
    assert state.get_chapter("unknown") == ""


def test_card_only_for_the_book_in_progress(tmp_path):
    ctx, state, books = make_ctx(tmp_path, ("a.epub", "b.epub"))
    assert not LibraryScreen(ctx).has_card                       # nothing opened yet
    state.set_position(str(books / "b.epub"), 1, 0, 0.3, "Second Chapter")
    lib = LibraryScreen(ctx)
    assert lib.has_card and lib.books[0].name == "b.epub"
    state.set_position(str(books / "b.epub"), 1, 0, 1.0)         # finished: no card, still first
    lib = LibraryScreen(ctx)
    assert not lib.has_card and lib.books[0].name == "b.epub"
    lib.render()


def test_every_book_is_reachable_across_pages(tmp_path):
    names = [f"book{i}.epub" for i in range(9)]
    ctx, state, books = make_ctx(tmp_path, names)
    state.set_position(str(books / "book4.epub"), 1, 0, 0.5, "A chapter")
    lib = LibraryScreen(ctx)
    pages = lib._pages()
    assert pages[0][0] == 0 and pages[-1][1] == 9 and len(pages) > 1
    assert all(a < b for a, b in pages) and all(p[1] == q[0] for p, q in zip(pages, pages[1:]))
    # the card is tall, so the first page holds fewer books than later ones
    assert pages[0][1] - pages[0][0] < pages[1][1] - pages[1][0]
    seen = set()
    for _ in range(9):
        lib.render()
        seen.add(lib.cursor)
        lib.handle(B.DOWN)
    assert seen == set(range(9))


def test_all_three_states_render(tmp_path):
    ctx, state, books = make_ctx(tmp_path, ("a.epub", "b.epub", "c.epub"))
    state.set_position(str(books / "a.epub"), 1, 0, 1.0)
    state.set_position(str(books / "b.epub"), 1, 0, 0.5, "Chapter")
    for _ in range(3):
        lib = LibraryScreen(ctx)
        assert lib.render().size == (400, 300)
        lib.handle(B.DOWN)
