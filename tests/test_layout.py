"""Milestones 3 & 4. Run: python3 -m pytest tests/test_layout.py -v"""
from ereader.blocks import ImageBlock, TextBlock, Word
from ereader.fonts import Fonts
from ereader.layout import MARGIN, break_lines, paginate, text_is_rtl
from ereader.page import PlacedWord

FONTS = Fonts(22)
LOREM = ("the quick brown fox jumps over the lazy dog and keeps running far away "
         "into the forest where nobody can find it ever again ").split() * 20


def line_width(line):
    return (sum(FONTS.width(w.text, w.style) for w in line)
            + FONTS.width(" ") * (len(line) - 1))


# ---------------------------------------------------------------- break_lines

def test_lines_fit_and_keep_order():
    words = [Word(t) for t in LOREM]
    lines = break_lines(words, FONTS, 400)
    assert len(lines) > 1
    assert all(line_width(line) <= 400 for line in lines)
    assert [w for line in lines for w in line] == words  # nothing lost, nothing reordered


def test_greedy_fills_lines():
    words = [Word(t) for t in LOREM]
    lines = break_lines(words, FONTS, 400)
    for line, nxt in zip(lines, lines[1:]):
        # the next line's first word should NOT have fit on this line
        assert line_width(line + [nxt[0]]) > 400


def test_long_word_gets_own_line():
    words = [Word("a"), Word("supercalifragilisticexpialidocious" * 3), Word("b")]
    lines = break_lines(words, FONTS, 200)
    assert [len(line) for line in lines] == [1, 1, 1]


def test_empty():
    assert break_lines([], FONTS, 400) == []


# ---------------------------------------------------------------- paginate

def test_paginate_stays_inside_page():
    blocks = [TextBlock([Word(t) for t in LOREM]) for _ in range(3)]
    pages = paginate(blocks, FONTS, 800, 480)
    assert len(pages) > 1
    for page in pages:
        assert page.elements, "no empty pages"
        for el in page.elements:
            assert MARGIN <= el.x and el.y >= MARGIN
            assert el.y + FONTS.line_height <= 480 - MARGIN + FONTS.line_height // 2


def test_paginate_keeps_every_word():
    blocks = [TextBlock([Word(t) for t in LOREM])]
    pages = paginate(blocks, FONTS, 800, 480)
    placed = [el.word.text for p in pages for el in p.elements if isinstance(el, PlacedWord)]
    assert placed == LOREM


def test_paginate_handles_images():
    blocks = [TextBlock([Word("hi")]), ImageBlock("pic.png"), TextBlock([Word("bye")])]
    pages = paginate(blocks, FONTS, 800, 480)
    assert sum(len(p.elements) for p in pages) == 3


# ---------------------------------------------------------------- polish

def rows(page):
    """Placed words grouped by line, each line as [(x, word)] left to right."""
    out = {}
    for el in page.elements:
        if isinstance(el, PlacedWord):
            out.setdefault(el.y, []).append((el.x, el.word))
    return [sorted(v, key=lambda t: t[0]) for _, v in sorted(out.items())]


def right_edge(line):
    x, word = line[-1]
    return x + FONTS.width(word.text, word.style)


def test_body_text_is_justified():
    [page, *_] = paginate([TextBlock([Word(t) for t in LOREM])], FONTS, 800, 480)
    lines = rows(page)
    for line in lines[:-1]:
        assert line[0][0] == MARGIN
        assert abs(right_edge(line) - (800 - MARGIN)) <= 2


def test_rtl_is_justified_flush_right():
    arabic = ("بسم الله الرحمن الرحيم الحمد لله رب العالمين " * 20).split()
    [page, *_] = paginate([TextBlock([Word(t) for t in arabic])], FONTS, 400, 300)
    lines = rows(page)
    assert text_is_rtl("بسم الله") and not text_is_rtl("hello world")
    for line in lines[:-1]:
        assert abs(right_edge(line) - (400 - MARGIN)) <= 2 and line[0][0] == MARGIN


def test_headings_and_ornaments_are_centered():
    blocks = [TextBlock([Word("Chapter"), Word("One")], is_heading=True),
              TextBlock([Word("*"), Word("*"), Word("*")])]
    [page] = paginate(blocks, FONTS, 800, 480)
    for line in rows(page):
        left, right = line[0][0], right_edge(line)
        assert abs((left - MARGIN) - (800 - MARGIN - right)) <= 2


def test_bottom_margin_gives_more_room():
    blocks = [TextBlock([Word(t) for t in LOREM])]
    tall = paginate(blocks, FONTS, 800, 480, bottom_margin=0)
    normal = paginate(blocks, FONTS, 800, 480)
    assert len(rows(tall[0])) > len(rows(normal[0]))


def test_arabic_blocks_get_extra_line_height():
    from ereader.blocks import Style, TextBlock, Word
    from ereader.fonts import Fonts
    from ereader.layout import paginate
    f = Fonts(22)
    latin = TextBlock([Word("hello", Style.NORMAL)])
    arabic = TextBlock([Word("بِسْمِ", Style.NORMAL)])
    def second_y(block):
        pages = paginate([block, block], f, 400, 300)
        ys = sorted({e.y for e in pages[0].elements})
        return ys[1] - ys[0]
    assert second_y(arabic) > second_y(latin)


def _verse_pages(n_filler_lines):
    from ereader.blocks import Style, TextBlock, Word
    from ereader.fonts import Fonts
    from ereader.layout import paginate
    f = Fonts(22)
    filler = TextBlock([Word("x", Style.NORMAL)])
    arabic = TextBlock([Word("بِسْمِ", Style.NORMAL), Word("ٱللَّهِ", Style.NORMAL)])
    english = TextBlock([Word("In", Style.NORMAL), Word("the", Style.NORMAL), Word("name", Style.NORMAL)])
    blocks = [filler] * n_filler_lines + [arabic, english]
    return paginate(blocks, f, 400, 300), f


def _page_of(pages, word):
    return [i for i, p in enumerate(pages) for e in p.elements if getattr(e, "word", None) and e.word.text == word]


def test_verse_and_translation_share_a_page():
    for n in range(1, 9):   # whatever space is left where the verse starts
        pages, _ = _verse_pages(n)
        assert _page_of(pages, "بِسْمِ")[0] == _page_of(pages, "In")[0], n


def test_verse_longer_than_a_page_still_flows():
    from ereader.blocks import Style, TextBlock, Word
    from ereader.fonts import Fonts
    from ereader.layout import paginate
    arabic = TextBlock([Word("كلمة", Style.NORMAL)] * 600)
    english = TextBlock([Word("end", Style.NORMAL)])
    pages = paginate([arabic, english], Fonts(22), 400, 300)
    assert len(pages) > 1 and _page_of(pages, "end")


def test_verse_too_tall_for_a_page_is_shrunk_to_fit_with_its_translation():
    from ereader.blocks import Style, TextBlock, Word
    from ereader.fonts import Fonts
    from ereader.layout import paginate
    f = Fonts(22)
    filler = TextBlock([Word("x", Style.NORMAL)])
    for n in range(20, 80, 2):   # find a verse that overflows a page at 22 but not smaller
        arabic = TextBlock([Word("وَقَالَ", Style.NORMAL)] * n)
        english = TextBlock([Word("end", Style.NORMAL)] + [Word("word", Style.NORMAL)] * (n // 2))
        pages = paginate([filler, arabic, english], f, 400, 300)
        shrunk = [e for p in pages for e in p.elements if getattr(e, "size", 0)]
        if shrunk:
            break
    assert shrunk, "no verse needed shrinking"
    assert len({i for i, p in enumerate(pages) if any(getattr(e, "size", 0) for e in p.elements)}) == 1
    assert _page_of(pages, "وَقَالَ")[0] == _page_of(pages, "end")[0]
    assert 14 <= shrunk[0].size < 22
    assert all(e.size == shrunk[0].size for e in shrunk)


def test_scaled_fonts_are_remembered():
    from ereader.fonts import Fonts
    f = Fonts(22)
    assert f.scaled(22) is f and f.scaled(16) is f.scaled(16) and f.scaled(16).size == 16
