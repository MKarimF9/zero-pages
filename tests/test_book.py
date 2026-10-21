"""Lazy loading. Run: python3 -m pytest tests/test_book.py -v"""
import zipfile

from ereader.book import LazyBook
from ereader.fonts import Fonts
from tests.make_epub import CONTAINER, make_epub

FONTS = Fonts(22)
WORDS = " ".join(["word"] * 400)  # several pages at 800x480


def make_book(tmp_path, sections: dict[str, str]):
    """EPUB whose spine is `sections` (name -> body html), in order."""
    path = tmp_path / "lazy.epub"
    manifest = "".join(f'<item id="{n}" href="{n}.xhtml" media-type="application/xhtml+xml"/>'
                       for n in sections)
    spine = "".join(f'<itemref idref="{n}"/>' for n in sections)
    opf = ('<package xmlns="http://www.idpf.org/2007/opf"><metadata/>'
           f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        for n, body in sections.items():
            z.writestr(f"OEBPS/{n}.xhtml", f"<html><body>{body}</body></html>")
    return path


def open_book(path, position=(0, 0)):
    return LazyBook(path, FONTS, 800, 480, position)


def test_only_current_section_is_laid_out(tmp_path):
    book = open_book(make_book(tmp_path, {"a": "<p>one</p>", "b": "<p>two</p>", "c": "<p>three</p>"}))
    assert set(book._cache) == {(0, 0)}
    book.next()
    assert set(book._cache) == {(0, 0), (1, 0)}


def test_walk_forward_then_back_visits_every_page(tmp_path):
    book = open_book(make_book(tmp_path, {"a": f"<p>{WORDS}</p>", "b": "<p>x</p>", "c": f"<p>{WORDS}</p>"}))
    forward = [book.position]
    while book.next():
        forward.append(book.position)
    assert [s for s, _ in forward] == sorted(s for s, _ in forward)
    assert {s for s, _ in forward} == {0, 1, 2}
    backward = [book.position]
    while book.prev():
        backward.append(book.position)
    assert backward == forward[::-1]
    assert book.position == (0, 0)


def test_cache_stays_small(tmp_path):
    book = open_book(make_book(tmp_path, {str(i): f"<p>{i}</p>" for i in "abcdefgh"}))
    while book.next():
        assert len(book._cache) <= 3
    assert book.position == (7, 0)


def test_empty_sections_are_skipped(tmp_path):
    book = open_book(make_book(tmp_path, {"a": "<p>one</p>", "b": "<p></p>", "c": "<p>three</p>"}))
    assert book.next() and book.position == (2, 0)
    assert book.prev() and book.position == (0, 0)


def test_restore_position_and_clamp(tmp_path):
    path = make_book(tmp_path, {"a": f"<p>{WORDS}</p>", "b": "<p>x</p>"})
    assert open_book(path, (0, 1)).position == (0, 1)
    assert open_book(path, (99, 99)).position == (1, 0)     # past the end -> last page
    assert open_book(path, (0, 99)).position == (0, len(open_book(path).pages) - 1)


def test_missing_file_in_spine_is_skipped(tmp_path):
    path = make_book(tmp_path, {"a": "<p>one</p>", "b": "<p>two</p>", "c": "<p>three</p>"})
    # rebuild the zip without section b, but keep it listed in the spine
    kept = tmp_path / "kept.epub"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(kept, "w") as dst:
        for item in src.infolist():
            if item.filename != "OEBPS/b.xhtml":
                dst.writestr(item, src.read(item.filename))
    book = open_book(kept)
    assert book.next() and book.position == (2, 0)


def test_progress_grows_from_zero_to_nearly_one(tmp_path):
    book = open_book(make_book(tmp_path, {"a": f"<p>{WORDS}</p>", "b": f"<p>{WORDS}</p>"}))
    seen = [book.progress]
    while book.next():
        seen.append(book.progress)
    assert 0 < seen[0] < 0.5 and seen == sorted(seen)
    assert seen[-1] == 1.0          # the very last page is 100%


def test_chapter_title_and_goto(tmp_path):
    book = open_book(make_epub(tmp_path / "t.epub"))
    assert book.chapter_title == "Chapter One" and book.chapter_index == 0
    book.goto(1)
    assert book.chapter_title == "Second Chapter" and book.position == (1, 0)


def test_changing_font_keeps_place(tmp_path):
    book = open_book(make_book(tmp_path, {"a": f"<p>{WORDS}</p>"}))
    book.page = len(book.pages) // 2
    before = book.page / len(book.pages)
    book.set_fonts(Fonts(32))
    assert len(book.pages) > 1 and abs(book.page / len(book.pages) - before) < 0.35
