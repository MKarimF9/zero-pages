"""Hadith EPUB generator, nested contents lists, and the contents screen's jump between parts."""
import sys
import zipfile
from pathlib import Path

from ereader.blocks import Style, TextBlock, Word
from ereader.epub import Epub
from ereader.layout import _is_arabic_block
from ereader.screens import BY_PART, TocScreen
from ereader.controls import Button
from ereader.html_parser import parse_blocks

import ereader  # noqa: E402

sys.path.insert(0, str(Path(ereader.__file__).resolve().parents[1] / "tools"))
import make_hadith_epub as gen  # noqa: E402


def collection(key, title, chapters):
    """chapters: [(english, arabic, [(arabic, narrator, text), ...])] in the dataset's JSON shape."""
    hadiths, n = [], 0
    for ci, (_, _, items) in enumerate(chapters, 1):
        for arabic, narrator, text in items:
            n += 1
            hadiths.append({"id": 1000 + n, "idInBook": n, "chapterId": ci, "bookId": 1, "arabic": arabic,
                            "english": {"narrator": narrator, "text": text}})
    return {"metadata": {"length": n, "arabic": {"title": "عنوان"}, "english": {"title": title, "author": "An Author"}},
            "chapters": [{"id": i, "arabic": ar, "english": en} for i, (en, ar, _) in enumerate(chapters, 1)],
            "hadiths": hadiths}


def write_data(folder):
    import json
    folder.mkdir()
    (folder / "nawawi40.json").write_text(json.dumps(collection("nawawi40", "Forty", [
        ("Intentions", "كتـــاب النية", [("إنما الأعمال بالنيات", "Umar reported:", "Deeds by intentions (ﷺ).")])])))
    (folder / "riyad_assalihin.json").write_text(json.dumps(collection("riyad", "Riyad as-Salihin", [
        ("Manners", "الأدب", [("الحياء من الإيمان", "Ibn Umar:", "Modesty is faith."),
                              ("لا إله إلا الله", "", "")]),
        ("Dress", "اللباس", [("البسوا", "A:", "Wear white.")])])))
    return folder


def test_combined_book_nests_chapters_under_collections(tmp_path):
    out = tmp_path / "out"
    gen.build_all(write_data(tmp_path / "data"), out)
    book = Epub(out / "Hadith.epub").open()
    assert book.title == "Hadith"
    assert [(t.level, t.title) for t in book.toc] == [
        (0, "Forty Hadith of an-Nawawi"), (1, "1. Intentions"),
        (0, "Riyad as-Salihin"), (1, "1. Manners"), (1, "2. Dress")]
    assert (out / "individual" / "riyad_assalihin.epub").exists()


def test_individual_book_has_a_flat_contents_list(tmp_path):
    out = tmp_path / "out"
    gen.build_all(write_data(tmp_path / "data"), out, only="riyad_assalihin")
    book = Epub(out / "individual" / "riyad_assalihin.epub").open()
    assert book.title == "Riyad as-Salihin"
    assert [(t.level, t.title) for t in book.toc] == [(0, "1. Manners"), (0, "2. Dress")]
    assert not (out / "Hadith.epub").exists()


def test_every_hadith_is_arabic_then_english(tmp_path):
    out = tmp_path / "out"
    gen.build_all(write_data(tmp_path / "data"), out, only="riyad_assalihin")
    book = Epub(out / "individual" / "riyad_assalihin.epub").open()
    chapter = [t for t in book.toc if t.title.endswith("Manners")][0]
    blocks = parse_blocks(book.read(book.spine[chapter.section]).decode())
    texts = [" ".join(w.text for w in b.words) for b in blocks]
    assert texts[0] == "Manners" and texts[1] == "الأدب"          # centered headings
    assert texts[2] == "الحياء من الإيمان"
    assert texts[3] == "1. Ibn Umar: Modesty is faith."
    assert texts[4] == "لا إله إلا الله" and texts[5] == "2."        # no translation: the number still shows


def test_tatweel_is_removed_from_titles():
    assert gen.clean_title("كتـــــاب  أدب") == "كتاب أدب"


def test_nested_nav_document_levels(tmp_path):
    """An EPUB 3 book with only a nav document: nested lists give levels."""
    path = tmp_path / "nav.epub"
    nav = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
           '<nav epub:type="toc"><ol><li><a href="a.xhtml">Part</a><ol><li><a href="b.xhtml">Chapter</a></li>'
           '<li><a href="c.xhtml">Chapter 2</a></li></ol></li><li><a href="d.xhtml">Part 2</a></li></ol></nav></body></html>')
    opf = ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:title>T</dc:title></metadata><manifest><item id="n" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           + "".join(f'<item id="{x}" href="{x}.xhtml" media-type="application/xhtml+xml"/>' for x in "abcd") +
           '</manifest><spine>' + "".join(f'<itemref idref="{x}"/>' for x in "abcd") + '</spine></package>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("content.opf", opf)
        z.writestr("nav.xhtml", nav)
        for x in "abcd":
            z.writestr(f"{x}.xhtml", f"<html><body><p>{x}</p></body></html>")
    book = Epub(path).open()
    assert [(t.level, t.title) for t in book.toc] == [(0, "Part"), (1, "Chapter"), (1, "Chapter 2"), (0, "Part 2")]


def test_english_with_one_arabic_word_is_not_an_arabic_block():
    english = TextBlock([Word(w) for w in "Messenger of Allah ( ﷺ ) said".split()])
    arabic = TextBlock([Word(w) for w in "قال رسول الله ﷺ".split()])
    assert not _is_arabic_block(english) and _is_arabic_block(arabic)


class FakeBook:
    chapter_index = 0

    def __init__(self, toc):
        self.epub = type("E", (), {"toc": toc})()


def toc_screen(entries):
    ctx = type("C", (), {})()
    reader = type("R", (), {"book": FakeBook(entries)})()
    return TocScreen(ctx, reader, None)


def entry(title, level):
    from ereader.epub import TocEntry
    return TocEntry(title, 0, "", level)


def test_part_jump_moves_between_top_level_entries_and_wraps():
    screen = toc_screen([entry("A", 0), entry("a1", 1), entry("a2", 1), entry("B", 0), entry("b1", 1)])
    assert BY_PART in screen.steps
    screen.step, screen.cursor = BY_PART, 0                 # on Back
    seen = []
    for _ in range(4):
        screen.handle(Button.DOWN)
        seen.append(screen.cursor)
    assert seen == [1, 4, 0, 1]                              # A, B, Back, A again
    screen.handle(Button.UP)
    assert screen.cursor == 0
    screen.handle(Button.UP)
    assert screen.cursor == 4                                # up from Back wraps to the last part


def test_no_part_jump_for_a_flat_contents_list():
    screen = toc_screen([entry("A", 0), entry("B", 0)])
    assert BY_PART not in screen.steps


def test_status_title_names_the_collection(tmp_path):
    from ereader.book import LazyBook
    from ereader.fonts import Fonts
    out = tmp_path / "out"
    gen.build_all(write_data(tmp_path / "data"), out)
    book = LazyBook(out / "Hadith.epub", Fonts(22), 400, 276)
    book.goto_entry(3)    # "1. Manners", under Riyad as-Salihin
    assert book.chapter_title == "Riyad as-Salihin › 1. Manners"
    book.goto_entry(0)    # a collection itself has no parent
    assert book.chapter_title == "Forty Hadith of an-Nawawi"


def test_flat_contents_titles_are_unchanged(tmp_path):
    from ereader.book import LazyBook
    from ereader.fonts import Fonts
    out = tmp_path / "out"
    gen.build_all(write_data(tmp_path / "data"), out, only="riyad_assalihin")
    book = LazyBook(out / "individual" / "riyad_assalihin.epub", Fonts(22), 400, 276)
    book.goto_entry(1)
    assert book.chapter_title == "2. Dress"


def test_status_bar_shortens_the_collection_before_the_chapter():
    from ereader.fonts import Fonts
    from ereader.screens import fit_chapter
    f = Fonts(15)
    title = "Mishkat al-Masabih › 5. The Chapters on Marriage"
    wide = f.width(title) + 5
    assert fit_chapter(f, title, wide) == title                    # fits: untouched
    narrow = fit_chapter(f, title, wide - 40)
    assert narrow.endswith("› 5. The Chapters on Marriage")        # the chapter survives whole
    assert "..." in narrow and f.width(narrow) <= wide - 40
    assert fit_chapter(f, "Just a chapter title that is long", 60) == "Just a chapter title that is long"  # no collection: draw_text cuts it
    tiny = fit_chapter(f, "Sahih Muslim › " + "a very long chapter name " * 3, 200)
    assert tiny.startswith("Sahih") and f.width(tiny) <= 200
