"""Headings marked by class. Run: python3 -m pytest tests/test_headings.py -v"""
from ereader.blocks import Style
from ereader.html_parser import looks_like_heading, parse_blocks
from ereader.fonts import Fonts
from ereader.layout import MARGIN, paginate
from ereader.page import PlacedWord


def texts(blocks):
    return [" ".join(w.text for w in b.words) for b in blocks]


def test_class_role_and_epub_type_mark_headings():
    for markup in ('<div class="chapter-title">X</div>', '<p class="subtitle">X</p>', '<div class="title">X</div>',
                   '<div class="Chapter_Title big">X</div>', '<div role="heading">X</div>',
                   '<p epub:type="title">X</p>', '<div class="heading-1">X</div>'):
        [block] = parse_blocks(markup)
        assert block.is_heading and all(w.style & Style.BOLD for w in block.words), markup


def test_ordinary_blocks_are_not_headings():
    for markup in ('<div>X</div>', '<p class="qverse-text">X</p>', '<div class="booktitle">X</div>',
                   '<p class="chapter">X</p>', '<div class="qtranslation">X</div>', '<p class="subtitled">X</p>'):
        [block] = parse_blocks(markup)
        assert not block.is_heading and not any(w.style & Style.BOLD for w in block.words), markup


def test_inline_spans_inside_a_heading_stay_part_of_it():
    [block] = parse_blocks('<div class="chapter-title">الفاتحة<span class="chapter-title-tr">- (The Opener)</span></div>')
    assert block.is_heading and [w.text for w in block.words] == ["الفاتحة", "-", "(The", "Opener)"]
    assert all(w.style & Style.BOLD for w in block.words)


def test_a_long_class_marked_block_is_a_paragraph_not_a_heading():
    long_text = " ".join(["word"] * 60)
    [block] = parse_blocks(f'<div class="title">{long_text}</div>')
    assert not block.is_heading and not any(w.style & Style.BOLD for w in block.words)
    [block] = parse_blocks('<div class="title">' + " ".join(["word"] * 40) + "</div>")   # at the limit: still a heading
    assert block.is_heading


def test_blocks_nested_in_a_heading_are_headings_and_nothing_leaks_out():
    blocks = parse_blocks('<div class="title"><p>one</p><p>two</p>tail</div><p>after</p><div>plain</div>')
    assert texts(blocks) == ["one", "two", "tail", "after", "plain"]
    assert [b.is_heading for b in blocks] == [True, True, True, False, False]
    assert not any(w.style & Style.BOLD for b in blocks[3:] for w in b.words)


def test_sloppy_html_cannot_leave_a_heading_open():
    blocks = parse_blocks('<div class="title"><p>one<p>two</div><p>after</p><p>and more</p>')
    assert [b.is_heading for b in blocks] == [True, True, False, False]


def test_real_heading_tags_still_work_and_bold_inside_normal_text_is_kept():
    [h, p] = parse_blocks("<h2>Chapter</h2><p>a <b>bold</b> c</p>")
    assert h.is_heading and not p.is_heading
    assert [w.style for w in p.words] == [Style.NORMAL, Style.BOLD, Style.NORMAL]


def test_looks_like_heading_helper():
    assert looks_like_heading([("class", "x chapter-title")]) and not looks_like_heading([("class", "x y")])
    assert not looks_like_heading([])


def test_a_class_heading_is_centered_on_the_page_like_a_normal_heading():
    html = '<div class="chapter-title">Surah Title</div><div class="qtranslation">' + "verse " * 5 + "</div>"
    [page] = paginate(parse_blocks(html), Fonts(22), 400, 300)
    heading = sorted((el for el in page.elements if isinstance(el, PlacedWord) and el.y == MARGIN), key=lambda e: e.x)
    left, last = heading[0].x, heading[-1]
    right = last.x + Fonts(22).width(last.word.text, last.word.style)
    assert abs((left - MARGIN) - (400 - MARGIN - right)) <= 2       # centered between the margins
