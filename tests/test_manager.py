"""Managing the library from the phone. Run: python3 -m pytest tests/test_manager.py -v"""
import json

from ereader.manager import BookManager
from ereader.state import ReadingState
from tests.test_library_sort import make_book
from tests.test_upload import Running


def setup(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    make_book(books / "a.epub", "Alpha", "Zed")
    make_book(books / "b.epub", "Beta", "Yan")
    state = ReadingState(tmp_path / "state" / "s.json")
    return BookManager(books, state), state, books


def by_name(lib):
    return {b["name"]: b for b in lib["books"]}


def test_library_lists_books_with_state(tmp_path):
    mgr, state, books = setup(tmp_path)
    state.set_position(str(books / "a.epub"), 2, 0, 0.42, "Chapter 3")
    state.set_category(str(books / "b.epub"), "Quran")
    state.set_queued(str(books / "b.epub"), True)
    state.set_pinned(str(books / "a.epub"), True)
    lib = mgr.library()
    a, b = by_name(lib)["a.epub"], by_name(lib)["b.epub"]
    assert (a["title"], a["author"], a["status"], a["progress"], a["chapter"], a["pinned"]) == ("Alpha", "Zed", "reading", 42, "Chapter 3", True)
    assert (b["status"], b["progress"], b["chapter"], b["category"], b["queued"], b["queue_position"]) == ("new", 0, "", "Quran", True, 1)
    assert lib["categories"] == ["Quran"]


def test_update_applies_changes(tmp_path):
    mgr, state, books = setup(tmp_path)
    mgr.update("a.epub", {"category": " Hadith ", "queued": True, "pinned": True})
    mgr.update("b.epub", {"queued": True})
    key = str(books / "a.epub")
    assert (state.get_category(key), state.is_queued(key), state.is_pinned(key)) == ("Hadith", True, True)
    mgr.update("b.epub", {"move": "top"})
    assert state.get_queue() == [str(books / "b.epub"), key]


def test_bad_updates_change_nothing(tmp_path):
    mgr, state, books = setup(tmp_path)
    for bad in ({}, {"category": 5}, {"queued": "yes"}, {"pinned": 1}, {"move": "sideways"},
                {"author": "x"}, {"category": "ok", "queued": "yes"}, ["category"], None):
        try:
            mgr.update("a.epub", bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad!r}")
    assert state.get_category(str(books / "a.epub")) == ""        # the half-good request changed nothing
    try:
        mgr.update("zzz.epub", {"queued": True})
    except KeyError:
        pass
    else:
        raise AssertionError("an unknown book was accepted")


def test_delete_removes_file_and_everything_remembered(tmp_path):
    mgr, state, books = setup(tmp_path)
    key = str(books / "a.epub")
    state.set_position(key, 1, 0, 0.5); state.set_category(key, "Quran"); state.set_queued(key, True); state.set_pinned(key, True)
    cached = state.path.parent / "covers" / "x"
    from ereader.cache import book_cache_dir
    cached = book_cache_dir(state.path.parent, "covers", books / "a.epub"); cached.mkdir(parents=True); (cached / "t.png").write_bytes(b"x")
    mgr.delete("a.epub")
    assert not (books / "a.epub").exists() and not cached.exists()
    assert state.status(key) == "new" and state.get_category(key) == "" and not state.is_queued(key) and not state.is_pinned(key)
    assert (books / "b.epub").exists()


# ---- over HTTP

def run(tmp_path):
    mgr, state, books = setup(tmp_path)
    return Running(tmp_path, manager=mgr), state, books


def test_api_library_needs_the_pin(tmp_path):
    srv, _, _ = run(tmp_path)
    with srv as r:
        assert r.call("GET", "/api/library", pin="0000")[0] == 403
        status, body = r.call("GET", "/api/library")
        assert status == 200 and {b["name"] for b in body["books"]} == {"a.epub", "b.epub"}


def test_api_update_and_errors(tmp_path):
    srv, state, books = run(tmp_path)
    with srv as r:
        ok = r.call("POST", "/api/update?name=a.epub", json.dumps({"queued": True, "category": "Quran"}).encode())
        assert ok == (200, {"ok": True}) and state.is_queued(str(books / "a.epub"))
        assert r.call("POST", "/api/update?name=a.epub", b'{"queued": "maybe"}')[0] == 400
        assert r.call("POST", "/api/update?name=a.epub", b"not json")[0] == 400
        assert r.call("POST", "/api/update?name=a.epub", b"")[0] == 400
        assert r.call("POST", "/api/update?name=a.epub", b"x" * 5000)[0] == 400           # too large
        assert r.call("POST", "/api/update?name=nope.epub", b'{"queued": true}')[0] == 404
        assert r.call("POST", "/api/update?name=..%2Fa.epub", b'{"queued": true}')[0] == 400   # not a plain name
        assert r.call("POST", "/api/update?name=a.epub", b'{"queued": true}', pin="0000")[0] == 403


def test_api_delete_forgets_state(tmp_path):
    srv, state, books = run(tmp_path)
    key = str(books / "a.epub")
    state.set_category(key, "Quran")
    with srv as r:
        assert r.call("POST", "/api/delete?name=a.epub")[0] == 200
        assert r.call("POST", "/api/delete?name=a.epub")[0] == 404
    assert state.get_category(key) == "" and not (books / "a.epub").exists()


def test_without_a_manager_the_new_routes_do_not_exist(tmp_path):
    with Running(tmp_path) as r:
        assert r.call("GET", "/api/library")[0] == 404
        assert r.call("POST", "/api/update?name=a.epub", b"{}")[0] == 404


def test_page_never_puts_book_text_into_html():
    from ereader.upload import PAGE
    assert "innerHTML = ''" in PAGE and PAGE.count("innerHTML") == PAGE.count("innerHTML = ''")   # only ever cleared
    assert "textContent: b.title" in PAGE


def test_add_books_comes_before_the_book_list_on_the_page():
    from ereader.upload import PAGE
    assert PAGE.index("Add books</summary>") < PAGE.index('id="chips"') < PAGE.index('id="books"')
