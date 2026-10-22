"""Long titles wrap in the contents list. Run: python3 -m pytest tests/test_toc_wrap.py -v"""
from ereader.controls import Button as B
from ereader.epub import TocEntry
from ereader.fonts import Fonts
from ereader.screens import MAX_TOC_LINES, AppContext, TocScreen

LONG = "The Book Pertaining to the Remembrance of Allah, Supplication, Repentance and Seeking Forgiveness"


class FakeBook:
    chapter_index = 0

    def __init__(self, toc):
        self.epub = type("E", (), {"toc": toc})()


def screen(tmp_path, titles, levels=None):
    ctx = AppContext(tmp_path, Fonts(22), Fonts(20), None, 400, 300, Fonts(15))
    entries = [TocEntry(t, i, "", (levels or [0] * len(titles))[i]) for i, t in enumerate(titles)]
    return TocScreen(ctx, type("R", (), {"book": FakeBook(entries)})(), None)


def test_short_titles_keep_the_one_line_height(tmp_path):
    scr = screen(tmp_path, ["One", "Two"])
    scr._pages()
    assert all(len(lines) == 1 for lines in scr._lines)
    assert all(scr._height(i) == scr._row_h for i in range(3))


def test_a_long_title_wraps_onto_extra_lines(tmp_path):
    scr = screen(tmp_path, ["Short", LONG])
    scr._pages()
    lines = scr._lines[2]
    assert 1 < len(lines) <= MAX_TOC_LINES
    assert scr._height(2) == scr._row_h + (len(lines) - 1) * scr._line_h
    ui, room = scr.ctx.ui_fonts, scr.ctx.width - 2 * 12 - 8
    assert all(ui.width(line) <= room + 1 for line in lines)


def test_a_medium_title_wraps_in_full_with_no_ellipsis(tmp_path):
    title = "Establishing the Prayer and the Sunnah Regarding Them"
    scr = screen(tmp_path, [title])
    scr._pages()
    assert len(scr._lines[1]) == 2 and " ".join(scr._lines[1]) == title


def test_only_beyond_the_limit_is_a_title_cut(tmp_path):
    scr = screen(tmp_path, [LONG + " " + LONG])
    scr._pages()
    assert len(scr._lines[1]) == MAX_TOC_LINES and scr._lines[1][-1].endswith("...")


def test_indented_entries_have_less_room(tmp_path):
    title = "Chapters Regarding the Funerals of the People"
    flat = screen(tmp_path, [title]); nested = screen(tmp_path, [title], levels=[3])
    flat._pages(); nested._pages()
    assert len(nested._lines[1]) >= len(flat._lines[1])


def test_pages_cover_every_row_once_and_fit_the_screen(tmp_path):
    titles = [LONG if i % 3 == 0 else f"{i}. Chapter {i}" for i in range(60)]
    scr = screen(tmp_path, titles)
    pages = scr._pages()
    assert pages[0][0] == 0 and pages[-1][1] == 61
    assert all(a[1] == b[0] for a, b in zip(pages, pages[1:])) and all(a < b for a, b in pages)
    body = 300 - 2 * (scr.ctx.ui_fonts.line_height + 8) - 4
    for a, b in pages:
        assert sum(scr._height(i) for i in range(a, b)) <= body or b - a == 1


def test_every_cursor_position_renders_and_stays_on_screen(tmp_path):
    scr = screen(tmp_path, [LONG if i % 2 else f"Chapter {i}" for i in range(30)])
    seen = set()
    for cursor in range(31):
        scr.cursor = cursor
        assert scr.render().size == (400, 300)
        seen.add(next(n for n, (a, b) in enumerate(scr._pages()) if a <= cursor < b))
    assert seen == set(range(len(scr._pages())))      # every page is reachable


def test_moving_down_walks_every_row_in_order(tmp_path):
    scr = screen(tmp_path, [LONG, "b", LONG, "d"])
    scr.cursor = 0
    visited = []
    for _ in range(5):
        scr.handle(B.DOWN)
        visited.append(scr.cursor)
    assert visited == [1, 2, 3, 4, 0]
