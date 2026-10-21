"""Book list and metadata cache. Run: python3 -m pytest tests/test_library.py -v"""
import os

from ereader.library import BookIndex, read_metadata
from tests.make_epub import make_epub


def counting_reader():
    calls = []
    def read(path):
        calls.append(path.name)
        return read_metadata(path)
    return read, calls


def test_reads_each_book_once(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    make_epub(books / "a.epub"); make_epub(books / "b.epub")
    cache = tmp_path / "cache" / "library.json"
    read, calls = counting_reader()

    first = BookIndex(cache, read).scan(books)
    assert [(b.title, b.author) for b in first] == [("Test Book", "Ada Lovelace")] * 2
    assert sorted(calls) == ["a.epub", "b.epub"]

    calls.clear()
    second = BookIndex(cache, read).scan(books)      # a fresh process: cache comes from disk
    assert calls == [] and [b.path.name for b in second] == ["a.epub", "b.epub"]


def test_changed_file_is_read_again(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    make_epub(books / "a.epub"); make_epub(books / "b.epub")
    cache = tmp_path / "library.json"
    read, calls = counting_reader()
    BookIndex(cache, read).scan(books)
    calls.clear()
    stat = (books / "b.epub").stat()
    os.utime(books / "b.epub", (stat.st_atime, stat.st_mtime + 10))
    BookIndex(cache, read).scan(books)
    assert calls == ["b.epub"]


def test_new_and_removed_books(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    make_epub(books / "a.epub")
    cache = tmp_path / "library.json"
    read, calls = counting_reader()
    BookIndex(cache, read).scan(books)
    make_epub(books / "c.epub"); (books / "a.epub").unlink()
    calls.clear()
    infos = BookIndex(cache, read).scan(books)
    assert [b.path.name for b in infos] == ["c.epub"] and calls == ["c.epub"]
    assert "a.epub" not in cache.read_text()         # removed books leave the cache


def test_unreadable_book_and_corrupt_cache(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    (books / "broken.epub").write_bytes(b"not a zip")
    cache = tmp_path / "library.json"
    cache.write_text("{ this is not json")
    [info] = BookIndex(cache).scan(books)
    assert (info.title, info.author) == ("broken", "(unreadable)")
