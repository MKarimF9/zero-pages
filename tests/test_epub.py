"""Milestone 1. Run: python3 -m pytest tests/test_epub.py -v"""
from ereader.epub import Epub
from tests.make_epub import make_epub


def open_book(tmp_path):
    return Epub(make_epub(tmp_path / "test.epub")).open()


def test_finds_opf(tmp_path):
    assert open_book(tmp_path)._opf_path == "OEBPS/content.opf"


def test_metadata(tmp_path):
    book = open_book(tmp_path)
    assert book.title == "Test Book"
    assert book.author == "Ada Lovelace"


def test_resolve_is_relative_to_opf(tmp_path):
    book = open_book(tmp_path)
    assert book._resolve("text/ch1.xhtml") == "OEBPS/text/ch1.xhtml"
    assert book._resolve("../images/cover.jpg") == "images/cover.jpg"


def test_spine_order_and_full_paths(tmp_path):
    book = open_book(tmp_path)
    # reading order comes from the SPINE, not the manifest;
    # the unknown idref is skipped; %20 is decoded
    assert book.spine == ["OEBPS/text/ch1.xhtml", "OEBPS/text/chapter 2.xhtml"]


def test_can_read_chapter(tmp_path):
    book = open_book(tmp_path)
    assert b"bright" in book.read(book.spine[0])


def test_toc_from_ncx(tmp_path):
    book = open_book(tmp_path)
    # sorted by spine position, whitespace collapsed, entry outside the spine dropped
    assert [(e.title, e.section) for e in book.toc] == [("Chapter One", 0), ("Second Chapter", 1)]


def test_section_size_and_direction(tmp_path):
    book = open_book(tmp_path)
    assert book.section_size(0) > 0
    assert book.rtl is False
