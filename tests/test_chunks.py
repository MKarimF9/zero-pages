"""Chunked layout of long chapters. Run: python3 -m pytest tests/test_chunks.py -v"""
import json

import ereader.book as bookmod
from ereader.book import LazyBook, split_html
from ereader.controls import Button as B
from ereader.fonts import Fonts
from ereader.page import PlacedWord
from ereader.screens import AppContext, LibraryScreen
from ereader.state import ReadingState
from tests.test_book import make_book

FONTS = Fonts(22)


def paragraphs(n, start=0):
    return "".join(f"<p>w{i} alpha beta gamma delta epsilon zeta eta theta</p>" for i in range(start, start + n))


def words_of(book):
    """Walk the whole book forward; return every placed word and the (section, chunk, page) trail."""
    out, trail = [], []
    while True:
        out += [el.word.text for el in book.current.elements if isinstance(el, PlacedWord)]
        trail.append((book.section, book.chunk, book.page))
        if not book.next():
            return out, trail


def open_big(tmp_path, sections, chunk_bytes=2000, **kw):
    path = make_book(tmp_path, sections)
    return LazyBook(path, FONTS, 400, 272, bottom_margin=6, chunk_bytes=chunk_bytes, **kw)


# ---------------------------------------------------------------- split_html

def test_small_sections_are_not_split():
    assert split_html(b"<p>hi</p>" * 10, 1000) == [(0, 90)]
    assert split_html(b"", 1000) == [(0, 0)]


def test_cuts_fall_right_after_a_paragraph_and_cover_everything():
    data = paragraphs(300).encode()
    chunks = split_html(data, 2000)
    assert len(chunks) > 5
    assert chunks[0][0] == 0 and chunks[-1][1] == len(data)
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:]))           # contiguous, no gaps
    assert all(data[:end].endswith(b"</p>") for _, end in chunks[:-1])     # no paragraph is split
    assert all(end - start <= 2000 * 2 for start, end in chunks)           # roughly the target size


def test_arabic_text_is_never_cut_inside_a_character():
    data = ("<p>" + "بسم الله الرحمن الرحيم " * 10 + "</p>").encode() * 80
    chunks = split_html(data, 3001)  # an odd size, so a naive byte cut would land mid-character
    assert len(chunks) > 3
    assert "".join(data[a:b].decode("utf-8") for a, b in chunks).encode() == data  # strict decode


def test_without_block_ends_it_cuts_after_any_tag_then_gives_up():
    br = b"some words here<br/>" * 500
    chunks = split_html(br, 2000)
    assert len(chunks) > 3 and all(br[:end].endswith(b">") for _, end in chunks[:-1])
    assert split_html(b"just text with no tags at all " * 500, 2000) == [(0, 15000)]


# ---------------------------------------------------------------- LazyBook

def test_only_the_first_chunk_is_laid_out_at_start(tmp_path):
    book = open_big(tmp_path, {"a": paragraphs(400)})
    assert len(book._plan(0)) > 5
    assert set(book._cache) == {(0, 0)}      # the rest of the chapter has not been touched


def test_every_word_is_reached_in_order_and_back_again(tmp_path):
    book = open_big(tmp_path, {"a": paragraphs(400)})
    words, trail = words_of(book)
    expected = [t for i in range(400) for t in (f"w{i} alpha beta gamma delta epsilon zeta eta theta").split()]
    assert words == expected                  # nothing lost or repeated across chunk boundaries
    assert len({c for _, c, _ in trail}) == len(book._plan(0))   # every chunk was visited
    back = [(book.section, book.chunk, book.page)]
    while book.prev():
        back.append((book.section, book.chunk, book.page))
    assert back == trail[::-1]


def test_memory_stays_bounded_while_reading_a_long_chapter(tmp_path):
    book = open_big(tmp_path, {"a": paragraphs(600)})
    biggest = 0
    while book.next():
        biggest = max(biggest, len(book._cache))
    assert len(book._plan(0)) > 8 and biggest <= 3     # current chunk and its two neighbours at most


def test_chunks_across_sections_stay_bounded_and_connected(tmp_path):
    book = open_big(tmp_path, {"a": paragraphs(5), "b": paragraphs(300), "c": paragraphs(5, 900)})
    words, trail = words_of(book)
    assert [s for s, _, _ in trail] == sorted(s for s, _, _ in trail)
    assert {s for s, _, _ in trail} == {0, 1, 2}
    assert words[0] == "w0" and words[-9] == "w904"      # the section after the long one follows on
    assert len(book._cache) <= 4
    while book.prev():
        assert len(book._cache) <= 4
    assert (book.section, book.chunk, book.page) == trail[0]


def test_empty_chunks_are_skipped(tmp_path):
    body = paragraphs(60) + "<p> </p>" * 700 + paragraphs(60, 1000)
    book = open_big(tmp_path, {"a": body})
    plan = book._plan(0)
    assert any(not book._unit(0, c) for c in range(len(plan)))            # at least one chunk is blank
    words, trail = words_of(book)
    assert words[0] == "w0" and "w1059" in words and "w59" in words       # walked across the blank part
    while book.prev():
        pass
    assert (book.section, book.chunk, book.page) == trail[0]


def test_restore_position_with_chunk_and_clamping(tmp_path):
    book = open_big(tmp_path, {"a": paragraphs(400)})
    for _ in range(40):
        book.next()
    here = (book.section, book.chunk, book.page)
    assert here[1] > 0
    again = open_big(tmp_path, {"a": paragraphs(400)}, position=(here[0], here[2]), chunk=here[1])
    assert (again.section, again.chunk, again.page) == here
    far = open_big(tmp_path, {"a": paragraphs(400)}, position=(0, 0), chunk=999)   # past the end
    assert far.chunk == len(far._plan(0)) - 1 and far.page == len(far.pages) - 1


def test_progress_is_monotonic_and_ends_at_one(tmp_path):
    book = open_big(tmp_path, {"a": paragraphs(400)})
    seen = [book.progress]
    while book.next():
        seen.append(book.progress)
    assert 0 < seen[0] < 0.05 and seen == sorted(seen) and seen[-1] == 1.0
    assert all(b - a < 0.05 for a, b in zip(seen, seen[1:]))   # no jumps at chunk boundaries


def test_changing_font_keeps_the_chunk(tmp_path):
    book = open_big(tmp_path, {"a": paragraphs(400)})
    for _ in range(50):
        book.next()
    chunk = book.chunk
    book.set_fonts(Fonts(30))
    assert book.chunk == chunk and 0 <= book.page < len(book.pages)


# ---------------------------------------------------------------- saving and restoring

def make_ctx(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    make_book(books, {"a": paragraphs(400)})   # writes books/lazy.epub
    state = ReadingState(tmp_path / "state" / "s.json")
    return AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15)), state


def test_reader_saves_and_restores_the_chunk(tmp_path):
    old = bookmod.CHUNK_BYTES
    bookmod.CHUNK_BYTES = 2000
    try:
        ctx, state = make_ctx(tmp_path)
        reader = LibraryScreen(ctx).handle(B.SELECT)
        while reader.book.chunk < 2:
            reader.handle(B.DOWN)
        for _ in range(7):
            reader.handle(B.DOWN)
        place = (reader.book.section, reader.book.chunk, reader.book.page)
        reader.close()
        saved = json.loads((tmp_path / "state" / "s.json").read_text())
        assert saved["chunks"][str(ctx.books_dir / "lazy.epub")] == place[1]

        fresh = AppContext(ctx.books_dir, Fonts(22), Fonts(20),
                           ReadingState(tmp_path / "state" / "s.json"), 400, 300, Fonts(15))
        again = LibraryScreen(fresh).handle(B.SELECT)   # a new session reads the saved state
        assert (again.book.section, again.book.chunk, again.book.page) == place
        assert again.book.progress > 0
    finally:
        bookmod.CHUNK_BYTES = old


def test_old_state_files_without_chunks_still_load(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"pages": {}, "positions": {"b": [3, 2]}, "last_book": "b"}))
    state = ReadingState(f)
    assert state.get_position("b") == (3, 2) and state.get_chunk("b") == 0
    state.set_position("b", 4, 1, 0.5, "Ch", chunk=2)
    assert ReadingState(f).get_chunk("b") == 2
