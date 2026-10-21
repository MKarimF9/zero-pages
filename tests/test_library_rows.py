"""Wrapped titles and mirrored Arabic rows. Run: python3 -m pytest tests/test_library_rows.py -v"""
from ereader.fonts import Fonts
from ereader.screens import AppContext, LibraryScreen
from ereader.state import ReadingState
from ereader.ui import wrap_text
from tests.make_epub import make_epub

LONG = "A Very Long Book Title That Cannot Possibly Fit On One Single Line Of The Library"


def make_library(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    make_epub(books / "a.epub")
    state = ReadingState(tmp_path / "state" / "s.json")
    ctx = AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15))
    return ctx, LibraryScreen(ctx)


def test_long_title_wraps_to_two_lines_then_ellipsis():
    f = Fonts(20)
    lines = wrap_text(f, LONG, 250, 2)
    assert len(lines) == 2 and lines[1].endswith("...") and all(f.width(l) <= 250 for l in lines)
    assert wrap_text(f, "Short", 250, 2) == ["Short"]


def test_arabic_rows_are_mirrored(tmp_path):
    _, lib = make_library(tmp_path)
    cover_x, x0, x1 = lib._columns(48, rtl=False)
    assert (cover_x, x0, x1) == (14, 74, 386)             # cover left, text to its right
    cover_x, x0, x1 = lib._columns(48, rtl=True)
    assert (cover_x, x0, x1) == (338, 14, 326)            # cover right, text to its left


def test_arabic_row_renders_with_cover_on_the_right(tmp_path):
    ctx, lib = make_library(tmp_path)
    lib.meta = [("كتاب مناقب الشافعي", "أبو بكر")]
    lib.cursor = 5 % 1   # selection is inverted: compare a non-selected render's cover side
    plain = LibraryScreen(ctx)
    plain.meta = lib.meta
    plain.books = plain.books * 2
    plain.meta = plain.meta * 2
    plain.cursor = 1     # row 0 is not selected, so ink is ink
    img = plain.render().convert("L")
    def ink(x0, x1):
        return sum(img.getpixel((x, y)) == 0 for x in range(x0, x1) for y in range(48, 110))
    assert ink(338, 386) > ink(14, 62)   # the cover frame and icon sit on the right


def test_two_line_title_row_renders_for_every_state(tmp_path):
    ctx, lib = make_library(tmp_path)
    book = str(lib.books[0])
    for setup in (lambda: None, lambda: ctx.state.set_position(book, 1, 0, 0.5),
                  lambda: ctx.state.set_position(book, 1, 0, 1.0)):
        setup()
        lib = LibraryScreen(ctx)
        lib.meta = [(LONG, "Someone")]
        assert lib.render().size == (400, 300)
