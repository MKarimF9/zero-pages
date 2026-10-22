"""Categories and the Up next list. Run: python3 -m pytest tests/test_categories.py -v"""
import json

from ereader.controls import Button as B
from ereader.screens import LibraryOptionsScreen, LibraryScreen, filter_choices
from ereader.state import ReadingState
from tests.test_pinned import make_ctx, titles


def test_category_state_persists_and_normalises(tmp_path):
    f = tmp_path / "s.json"
    state = ReadingState(f)
    assert state.get_category("a") == "" and state.categories() == []
    state.set_category("a", "  Hadith   books ")
    state.set_category("b", "Quran"); state.set_category("c", "hadith books")
    assert ReadingState(f).get_category("a") == "Hadith books"
    assert state.get_category("c") == "Hadith books"           # same name in another case: one category
    assert state.categories() == ["Hadith books", "Quran"]
    state.set_category("a", "")                      # clearing removes it
    assert state.get_category("a") == ""
    state.set_category("x", "y" * 100)
    assert len(state.get_category("x")) == 40        # a sane limit


def test_queue_order_and_moves(tmp_path):
    f = tmp_path / "s.json"
    state = ReadingState(f)
    for b in "abc":
        state.set_queued(b, True)
    state.set_queued("a", True)                      # twice: still once
    assert state.get_queue() == ["a", "b", "c"] and state.is_queued("b")
    state.move_queued("c", "up");   assert state.get_queue() == ["a", "c", "b"]
    state.move_queued("a", "down"); assert state.get_queue() == ["c", "a", "b"]
    state.move_queued("b", "top");  assert state.get_queue() == ["b", "c", "a"]
    state.move_queued("b", "up");   assert state.get_queue() == ["b", "c", "a"]   # already first
    state.move_queued("zzz", "top")                                              # a stranger: ignored
    state.set_queued("c", False)
    assert ReadingState(f).get_queue() == ["b", "a"]


def test_forget_clears_category_and_queue(tmp_path):
    state = ReadingState(tmp_path / "s.json")
    state.set_category("a", "Quran"); state.set_queued("a", True)
    state.forget("a")
    assert state.get_category("a") == "" and not state.is_queued("a") and state.categories() == []


def test_old_state_files_load(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"pages": {}, "positions": {}, "last_book": None}))
    state = ReadingState(f)
    assert state.get_queue() == [] and state.categories() == []


def test_filter_choices_list_categories(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    assert list(filter_choices(state)) == ["all", "reading", "new", "finished", "want"]
    state.set_category(str(books / "4.epub"), "Quran")
    state.set_category(str(books / "2.epub"), "Arabic")
    assert list(filter_choices(state))[-2:] == ["cat:Arabic", "cat:Quran"]
    assert filter_choices(state)["cat:Quran"] == "Quran"


def test_library_shows_one_category(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_category(str(books / "4.epub"), "Quran")
    state.set_category(str(books / "1.epub"), "Quran")
    state.set_setting("library_filter", "cat:Quran")
    assert sorted(titles(LibraryScreen(ctx))) == ["Banana", "Quran"]


def test_up_next_filter_follows_your_queue_order(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    for name in ("3.epub", "1.epub", "4.epub"):
        state.set_queued(str(books / name), True)
    state.set_setting("library_filter", "want")
    assert titles(LibraryScreen(ctx)) == ["Cherry", "Banana", "Quran"]   # queue order, not alphabetical
    state.move_queued(str(books / "4.epub"), "top")
    assert titles(LibraryScreen(ctx)) == ["Quran", "Cherry", "Banana"]


def test_a_vanished_category_falls_back_to_all(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_setting("library_filter", "cat:Gone")
    lib = LibraryScreen(ctx)
    assert lib.filter == "all" and len(lib.books) == 4


def test_options_screen_cycles_through_categories(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_category(str(books / "4.epub"), "Quran")
    opts = LibraryOptionsScreen(ctx)
    opts.cursor = 1                                   # the "Show" row
    seen = []
    for _ in range(7):
        opts.handle(B.SELECT)
        seen.append(state.get_setting("library_filter"))
    assert seen == ["reading", "new", "finished", "want", "cat:Quran", "all", "reading"]
