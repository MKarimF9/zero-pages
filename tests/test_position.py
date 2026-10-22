"""Moving your place from the phone. Run: python3 -m pytest tests/test_position.py -v"""
import json
import shutil
import sys
from pathlib import Path

import ereader
from ereader.book import LazyBook
from ereader.fonts import Fonts
from ereader.manager import BookManager
from ereader.state import ReadingState
from tests.test_book import make_book
from tests.test_chunks import paragraphs
from tests.test_hadith import write_data
from tests.test_upload import Running

sys.path.insert(0, str(Path(ereader.__file__).resolve().parents[1] / "tools"))
import make_hadith_epub as gen  # noqa: E402

FONTS = Fonts(22)
LAYOUT = (FONTS, 400, 272, 6)


def big_book(tmp_path):
    return make_book(tmp_path, {n: paragraphs(60) for n in "abcd"})


def test_goto_fraction_lands_near_the_asked_place(tmp_path):
    book = LazyBook(big_book(tmp_path), FONTS, 400, 272, bottom_margin=6, chunk_bytes=2000)
    seen = []
    for f in (0.0, 0.1, 0.25, 0.4, 0.5, 0.75, 0.9, 1.0):
        book.goto_fraction(f)
        seen.append(book.progress)
        assert abs(book.progress - f) < 0.05, (f, book.progress)
    assert seen == sorted(seen)                      # moving forward never lands earlier
    book.goto_fraction(0.0)
    assert (book.position, book.chunk) == ((0, 0), 0)
    book.goto_fraction(1.0)
    assert book.progress > 0.99 and book.section == 3
    book.goto_fraction(-5); assert book.section == 0 and book.page == 0     # out-of-range values are clamped
    book.goto_fraction(7);  assert book.section == 3


def test_goto_fraction_skips_empty_files(tmp_path):
    book = LazyBook(make_book(tmp_path, {"a": "<p>one</p>", "b": "", "c": "<p>two</p>"}), FONTS, 400, 272)
    book.goto_fraction(0.6)
    assert not book.empty and book.section in (0, 2)


def hadith_manager(tmp_path, layout=LAYOUT):
    books = tmp_path / "books"; books.mkdir()
    out = tmp_path / "out"
    gen.build_all(write_data(tmp_path / "data"), out, only="riyad_assalihin")
    shutil.copy(out / "individual" / "riyad_assalihin.epub", books / "r.epub")
    state = ReadingState(tmp_path / "state" / "s.json")
    return BookManager(books, state, layout=layout), state, books


def test_chapters_lists_the_contents(tmp_path):
    mgr, _, _ = hadith_manager(tmp_path)
    assert mgr.chapters("r.epub") == [{"i": 0, "title": "1. Manners", "level": 0}, {"i": 1, "title": "2. Dress", "level": 0}]
    try:
        mgr.chapters("nope.epub")
    except KeyError:
        pass
    else:
        raise AssertionError


def test_set_position_by_chapter_matches_what_the_reader_would_do(tmp_path):
    mgr, state, books = hadith_manager(tmp_path)
    key = str(books / "r.epub")
    assert state.status(key) == "new"
    reply = mgr.set_position("r.epub", {"chapter": 1})
    assert reply["chapter"] == "2. Dress"
    book = LazyBook(books / "r.epub", *LAYOUT[:3], bottom_margin=6)
    book.goto_entry(1)
    assert state.get_position(key) == book.position and state.get_chunk(key) == book.chunk
    assert state.get_chapter(key) == "2. Dress" and state.last_book == key
    assert state.status(key) == "finished"           # the last page of this tiny book
    assert mgr.set_position("r.epub", {"chapter": 0})["progress"] < 100 and state.status(key) == "reading"


def test_set_position_by_percent(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    src = big_book(tmp_path); shutil.copy(src, books / "x.epub")
    state = ReadingState(tmp_path / "state" / "s.json")
    mgr = BookManager(books, state, layout=LAYOUT)
    reply = mgr.set_position("x.epub", {"percent": 50})
    assert abs(reply["progress"] - 50) <= 6 and abs(state.get_progress(str(books / "x.epub")) - 0.5) < 0.06


def test_bad_positions_change_nothing(tmp_path):
    mgr, state, books = hadith_manager(tmp_path)
    key = str(books / "r.epub")
    for bad in ({}, {"chapter": 5}, {"chapter": -1}, {"chapter": True}, {"chapter": "1"}, {"percent": -1},
                {"percent": 101}, {"percent": "x"}, {"percent": False}, {"chapter": 0, "percent": 5},
                {"other": 1}, [1], None):
        try:
            mgr.set_position("r.epub", bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad!r}")
    assert state.status(key) == "new"
    try:
        mgr.set_position("zzz.epub", {"chapter": 0})
    except KeyError:
        pass
    else:
        raise AssertionError


def test_no_layout_means_no_moving(tmp_path):
    mgr, state, books = hadith_manager(tmp_path, layout=None)
    try:
        mgr.set_position("r.epub", {"chapter": 0})
    except ValueError as e:
        assert "can't move" in str(e)
    else:
        raise AssertionError
    assert state.status(str(books / "r.epub")) == "new"


def test_api_chapters_and_position(tmp_path):
    mgr, state, books = hadith_manager(tmp_path)
    with Running(tmp_path, manager=mgr) as r:
        status, chapters = r.call("GET", "/api/chapters?name=r.epub")
        assert status == 200 and [c["title"] for c in chapters] == ["1. Manners", "2. Dress"]
        assert r.call("GET", "/api/chapters?name=r.epub", pin="0000")[0] == 403
        assert r.call("GET", "/api/chapters?name=nope.epub")[0] == 404
        assert r.call("GET", "/api/chapters?name=..%2Fr.epub")[0] == 400
        status, body = r.call("POST", "/api/position?name=r.epub", json.dumps({"chapter": 1}).encode())
        assert status == 200 and body["chapter"] == "2. Dress"
        assert state.get_chapter(str(books / "r.epub")) == "2. Dress"
        assert r.call("POST", "/api/position?name=r.epub", b'{"percent": 500}')[0] == 400
        assert r.call("POST", "/api/position?name=r.epub", b"nonsense")[0] == 400
        assert r.call("POST", "/api/position?name=nope.epub", b'{"chapter": 0}')[0] == 404
        assert r.call("POST", "/api/position?name=r.epub", b'{"chapter": 0}', pin="0000")[0] == 403


def test_upload_only_server_has_no_position_routes(tmp_path):
    with Running(tmp_path) as r:
        assert r.call("GET", "/api/chapters?name=a.epub")[0] == 404
        assert r.call("POST", "/api/position?name=a.epub", b"{}")[0] == 404
