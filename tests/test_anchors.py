"""Chapters that start in the middle of a long file. Run: python3 -m pytest tests/test_anchors.py -v"""
import zipfile

from ereader.book import LazyBook, MIN_CHUNK, _thin, find_anchor, heading_start, split_html
from ereader.controls import Button as B
from ereader.epub import Epub
from ereader.fonts import Fonts
from ereader.page import PlacedWord
from ereader.screens import AppContext, LibraryScreen
from ereader.state import ReadingState

FONTS = Fonts(22)


def verses(n, tag):
    return "".join(f'<div class="qtranslation">{tag} verse {i} alpha beta gamma delta epsilon zeta</div>' for i in range(n))


# like the Quran EPUB: the title comes first, then an *empty* anchor
BODY = ('<div class="qjuz"><a id="j1"></a><div class="subtitle">#1</div>'
        '<div class="chapter-title">FirstSurah</div><a id="ch1"></a>' + verses(40, "one") +
        '<div class="chapter-title">SecondSurah</div><a id="ch2"></a>' + verses(40, "two") +
        '<h2 id="ch3">ThirdSurah</h2>' + verses(40, "three") +             # the id is on the heading itself
        '<p>The very end of chapter three.</p><a id="ch4"></a><h2>FourthSurah</h2>' + verses(40, "four") +
        '</div>')

NCX = """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
<navPoint><navLabel><text>Juz 1</text></navLabel><content src="c.xhtml#j1"/></navPoint>
<navPoint><navLabel><text>First</text></navLabel><content src="c.xhtml#ch1"/></navPoint>
<navPoint><navLabel><text>Second</text></navLabel><content src="c.xhtml#ch2"/></navPoint>
<navPoint><navLabel><text>Third</text></navLabel><content src="c.xhtml#ch3"/></navPoint>
<navPoint><navLabel><text>Fourth</text></navLabel><content src="c.xhtml#ch4"/></navPoint>
<navPoint><navLabel><text>Nowhere</text></navLabel><content src="c.xhtml#does-not-exist"/></navPoint>
<navPoint><navLabel><text>Appendix</text></navLabel><content src="end.xhtml"/></navPoint>
</navMap></ncx>"""


def make_book_with_anchors(tmp_path, body=BODY):
    path = tmp_path / "anchors.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="content.opf"/></rootfiles></container>')
        z.writestr("content.opf",
                   '<package xmlns="http://www.idpf.org/2007/opf"><metadata/><manifest>'
                   '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="e" href="end.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>'
                   '<spine toc="ncx"><itemref idref="c"/><itemref idref="e"/></spine></package>')
        z.writestr("toc.ncx", NCX)
        z.writestr("c.xhtml", f"<html><body>{body}</body></html>")
        z.writestr("end.xhtml", "<html><body><h1>Appendix</h1><p>Notes.</p></body></html>")
    return path


def open_book(tmp_path, chunk_bytes=100_000):
    return LazyBook(make_book_with_anchors(tmp_path), FONTS, 400, 272, bottom_margin=6, chunk_bytes=chunk_bytes)


def titles_on_page(book):
    return " ".join(el.word.text for el in book.current.elements if isinstance(el, PlacedWord))


def toc_index(book, title):
    return [e.title for e in book.epub.toc].index(title)


# ---------------------------------------------------------------- the helpers

def test_find_anchor_matches_whole_ids_only():
    data = b'<p id="ch10">x</p><a name=\'ch1\'></a><div id="ch1">y</div>'
    assert find_anchor(data, "ch1") == data.index(b"<a name")          # not the longer "ch10"
    assert find_anchor(data, "ch10") == 0 and find_anchor(data, "nope") is None


def test_an_empty_anchor_after_a_title_pulls_the_title_in():
    data = b'<p>earlier text</p><div class="chapter-title">Surah <b>Two</b></div><a id="ch2"></a><p>v</p>'
    assert heading_start(data, data.index(b"<a id")) == data.index(b'<div class="chapter-title">')


def test_the_anchor_alone_is_used_when_the_block_before_is_not_a_heading():
    plain = b'<p>The very end.</p><a id="x"></a><h2>Next</h2>'
    assert heading_start(plain, plain.index(b"<a id")) == plain.index(b"<a id")         # an ordinary paragraph
    on_heading = b'<p>before</p><h2 id="x">Title</h2>'
    assert heading_start(on_heading, on_heading.index(b"<h2")) == on_heading.index(b"<h2")  # anchor IS the heading
    long_block = b'<div class="chapter-title">' + b"w " * 400 + b'</div><a id="y"></a>'
    assert heading_start(long_block, long_block.index(b"<a id")) == long_block.index(b"<a id")


def test_nested_blocks_are_matched_to_the_right_start():
    data = b'<div class="chapter-title"><div>inner</div> Name</div><a id="x"></a>'
    assert heading_start(data, data.index(b"<a id")) == 0


def test_cuts_are_forced_and_close_ones_thinned():
    data = ("<p>" + "word " * 50 + "</p>").encode() * 40
    forced = [data.index(b"<p>", 3000), data.index(b"<p>", 9000)]
    chunks = split_html(data, 100_000, forced)
    assert [a for a, _ in chunks] == [0] + forced and chunks[-1][1] == len(data)
    assert _thin([100, 700, 1000, 2000], MIN_CHUNK) == [700, 2000]      # 100 and 1000 are too close to a kept cut/start


# ---------------------------------------------------------------- reading a book with chapters in one file

def test_each_chapter_begins_its_own_chunk_with_its_title(tmp_path):
    book = open_book(tmp_path)
    plan = book._plan(0)
    assert len(plan) == 4                                  # First (with Juz 1), Second, Third, Fourth
    for title in ("Second", "Third", "Fourth"):
        book.goto_entry(toc_index(book, title))
        assert book.page == 0 and f"{title}Surah" in titles_on_page(book), title
    assert "The very end of chapter three" not in titles_on_page(book)   # not dragged into chapter four


def test_jumping_to_a_chapter_lands_on_it_and_names_it(tmp_path):
    book = open_book(tmp_path)
    book.goto_entry(toc_index(book, "Second"))
    assert book.chapter_title == "Second" and "SecondSurah" in titles_on_page(book)
    book.goto_entry(toc_index(book, "First"))
    assert book.chapter_title == "First" and "FirstSurah" in titles_on_page(book)
    assert (book.section, book.chunk) == (0, 0)
    book.goto_entry(toc_index(book, "Appendix"))
    assert book.section == 1 and book.chapter_title == "Appendix"


def test_status_title_follows_you_through_the_file(tmp_path):
    book = open_book(tmp_path)
    seen = []
    while True:
        seen.append(book.chapter_title)
        if not book.next():
            break
    order = [t for i, t in enumerate(seen) if i == 0 or t != seen[i - 1]]
    assert order == ["First", "Second", "Third", "Fourth", "Appendix"]   # in order, never the last one early


def test_the_right_chapter_is_current_after_going_back(tmp_path):
    book = open_book(tmp_path)
    while book.next():
        pass
    assert book.chapter_title == "Appendix"
    while book.prev() and book.section == 1:
        pass
    assert book.chapter_title == "Fourth"                  # the last chunk of the long file
    book.goto_entry(toc_index(book, "Third"))
    book.prev()
    assert book.chapter_title == "Second"


def test_a_missing_anchor_means_the_start_of_the_file(tmp_path):
    book = open_book(tmp_path)
    book.goto_entry(toc_index(book, "Nowhere"))
    assert (book.section, book.chunk, book.page) == (0, 0, 0)


def test_chapters_also_split_by_size_inside_a_long_chapter(tmp_path):
    book = open_book(tmp_path, chunk_bytes=1500)           # each chapter is longer than a chunk
    assert len(book._plan(0)) > 6
    book.goto_entry(toc_index(book, "Third"))
    assert "ThirdSurah" in titles_on_page(book) and book.chapter_title == "Third"
    trail = []
    while True:
        trail.append((book.chunk, book.chapter_title))
        if not book.next():
            break
    # chunks inside chapter Third (not its first one) still say "Third", and the names never go backwards
    third_chunks = {c for c, t in trail if t == "Third"}
    assert len(third_chunks) >= 2 and max(third_chunks) > min(third_chunks)
    order = [t for i, (c, t) in enumerate(trail) if i == 0 or t != trail[i - 1][1]]
    assert order == ["Third", "Fourth", "Appendix"]


def test_contents_screen_jumps_to_the_chosen_chapter(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    make_book_with_anchors(tmp_path).rename(books / "anchors.epub")
    state = ReadingState(tmp_path / "state" / "s.json")
    ctx = AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15))
    reader = LibraryScreen(ctx).handle(B.SELECT)
    menu = reader.handle(B.SELECT); menu.cursor = 1
    toc = menu.handle(B.SELECT)
    toc.cursor = 1 + toc_index(reader.book, "Third")
    assert toc.handle(B.SELECT) is reader
    assert reader.book.chapter_title == "Third" and "ThirdSurah" in titles_on_page(reader.book)
    assert ctx.state.get_chapter(str(reader.path)) == "Third"      # saved, so the library can show it
    menu = reader.handle(B.SELECT); menu.cursor = 1
    assert menu.handle(B.SELECT).cursor == 1 + toc_index(reader.book, "Third")   # contents opens on it


def test_entries_keep_their_fragments(tmp_path):
    epub = Epub(make_book_with_anchors(tmp_path)).open()
    assert [(e.title, e.fragment) for e in epub.toc][:3] == [("Juz 1", "j1"), ("First", "ch1"), ("Second", "ch2")]
