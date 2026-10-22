"""Renaming a book from your phone. Run: python3 -m pytest tests/test_rename.py -v"""
import pytest

from ereader.screens import LibraryScreen
from ereader.state import ReadingState
from tests.test_manager import by_name, setup
from tests.test_pinned import make_ctx, titles


def test_title_state_persists_and_normalises(tmp_path):
    f = tmp_path / "s.json"
    state = ReadingState(f)
    assert state.get_title("a") == ""
    state.set_title("a", "  My   favourite\nbook ")
    assert ReadingState(f).get_title("a") == "My favourite book"
    state.set_title("x", "y" * 500)
    assert len(state.get_title("x")) == 200
    state.set_title("a", "   ")                      # empty goes back to the book's own name
    assert ReadingState(f).get_title("a") == ""


def test_forgetting_a_book_forgets_its_name(tmp_path):
    state = ReadingState(tmp_path / "s.json")
    state.set_title("a", "Mine")
    state.forget("a")
    assert state.get_title("a") == ""


def test_manager_renames_and_resets(tmp_path):
    mgr, state, books = setup(tmp_path)
    mgr.update("a.epub", {"title": "Alpha (Arabic)"})
    a = by_name(mgr.library())["a.epub"]
    assert (a["title"], a["renamed"], a["original"]) == ("Alpha (Arabic)", True, "Alpha")
    mgr.update("a.epub", {"title": ""})
    a = by_name(mgr.library())["a.epub"]
    assert (a["title"], a["renamed"], a["original"]) == ("Alpha", False, "Alpha")


def test_bad_title_changes_nothing(tmp_path):
    mgr, state, books = setup(tmp_path)
    with pytest.raises(ValueError):
        mgr.update("a.epub", {"title": 5, "pinned": True})
    assert not state.is_pinned(str(books / "a.epub")) and state.get_title(str(books / "a.epub")) == ""


def test_the_reader_library_shows_and_sorts_by_the_new_name(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_title(str(books / "1.epub"), "Aardvark")        # was "Banana"
    lib = LibraryScreen(ctx)
    assert lib.render().size == (400, 300)
    names = titles(lib)
    assert "Aardvark" in names and "Banana" not in names
