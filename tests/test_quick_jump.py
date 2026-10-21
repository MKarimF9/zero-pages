"""Jumping through a long contents list. Run: python3 -m pytest tests/test_quick_jump.py -v"""
import zipfile

from ereader.controls import Button as B
from ereader.fonts import Fonts
from ereader.screens import (TOC_STEPS, AppContext, LibraryScreen, TocScreen, _jump, effective_button)
from ereader.state import ReadingState
from tests.fakes import select_row


def make_long_book(path, chapters=60):
    """An EPUB with one chapter per file and a contents entry for each."""
    ids = range(1, chapters + 1)
    manifest = "".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in ids)
    spine = "".join(f'<itemref idref="c{i}"/>' for i in ids)
    ncx = "".join(f'<navPoint><navLabel><text>Chapter {i}</text></navLabel><content src="c{i}.xhtml"/></navPoint>' for i in ids)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
        z.writestr("content.opf", '<package xmlns="http://www.idpf.org/2007/opf"><metadata/>'
                   f'<manifest>{manifest}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
                   f'<spine toc="ncx">{spine}</spine></package>')
        z.writestr("toc.ncx", f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>{ncx}</navMap></ncx>')
        for i in ids:
            z.writestr(f"c{i}.xhtml", f"<html><body><h1>Chapter {i}</h1><p>Text of chapter {i}.</p></body></html>")


def contents_screen(tmp_path, chapters=60):
    books = tmp_path / "books"; books.mkdir()
    make_long_book(books / "long.epub", chapters)
    state = ReadingState(tmp_path / "state" / "s.json")
    ctx = AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15))
    reader = LibraryScreen(ctx).handle(B.SELECT)
    menu = reader.handle(B.SELECT)
    select_row(menu, "toc")
    return ctx, reader, menu.handle(B.SELECT)


def test_jump_steps_clamp_at_the_ends_then_wrap():
    count = 61
    assert _jump(5, count, B.DOWN, 10) == 15 and _jump(15, count, B.UP, 10) == 5
    assert _jump(55, count, B.DOWN, 10) == 60                       # stops at the last row, doesn't skip past it
    assert _jump(60, count, B.DOWN, 10) == 0                        # a second press wraps to the top
    assert _jump(4, count, B.UP, 10) == 0 and _jump(0, count, B.UP, 10) == 60
    assert _jump(7, count, B.DOWN, 1) == 8 and _jump(0, count, B.UP, 1) == 60     # step 1: plain wrap-around
    assert _jump(7, count, B.SELECT, 10) == 7                       # other buttons don't move it


def test_hold_select_cycles_the_jump_size_and_shows_it(tmp_path):
    ctx, reader, toc = contents_screen(tmp_path)
    assert toc.step == 1 and effective_button(B.SELECT_LONG, toc) is B.SELECT_LONG
    seen = []
    for _ in range(len(TOC_STEPS) + 1):
        seen.append(toc.step)
        assert toc.handle(B.SELECT_LONG) is None and toc.full_refresh is False
        assert toc.render().size == (400, 300)
    assert seen == [1, 5, 10, 25, 1]                                # round and round


def test_a_long_list_can_be_crossed_in_a_few_presses_and_the_right_chapter_opens(tmp_path):
    ctx, reader, toc = contents_screen(tmp_path, chapters=60)
    toc.cursor = 0
    for _ in range(3):
        toc.handle(B.SELECT_LONG)                                   # 1 -> 5 -> 10 -> 25
    assert toc.step == 25
    toc.handle(B.DOWN); toc.handle(B.DOWN)                          # rows 25, then 50
    assert toc.cursor == 50
    toc.handle(B.SELECT_LONG)                                       # back to 1: fine-tune
    toc.handle(B.UP)
    assert toc.cursor == 49 and toc.entries[toc.cursor - 1].title == "Chapter 49"
    assert toc.handle(B.SELECT) is reader
    assert reader.book.chapter_title == "Chapter 49" and reader.book.section == 48


def test_back_and_ordinary_selection_still_work(tmp_path):
    ctx, reader, toc = contents_screen(tmp_path)
    toc.cursor = 0
    result = toc.handle(B.SELECT)                                   # "Back" returns to the menu
    assert result is toc.menu and result.full_refresh and result is not reader
    toc.cursor = 3
    assert toc.handle(B.SELECT) is reader and reader.book.chapter_title == "Chapter 3"


def test_the_current_chapter_is_marked_with_an_arrow_not_a_bookmark(tmp_path):
    ctx, reader, toc = contents_screen(tmp_path)
    corner = lambda img: sum(1 for p in img.convert("L").crop((0, 38, 12, 120)).tobytes() if p < 128)
    assert corner(toc.render()) > 0                                 # the marker for the current chapter is drawn
