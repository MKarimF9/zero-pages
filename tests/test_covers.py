"""Covers. Run: python3 -m pytest tests/test_covers.py -v"""
from PIL import Image

from ereader.covers import THUMB_SIZE, cached_cover, make_cover, placeholder_cover
from ereader.epub import Epub
from ereader.ui import wrap_text
from ereader.fonts import Fonts
from tests.make_epub import make_epub


def test_cover_found_in_epub3_and_epub2(tmp_path):
    for kind in ("epub3", "epub2"):
        book = Epub(make_epub(tmp_path / f"{kind}.epub", cover=kind)).open()
        assert book.cover == "OEBPS/images/cover.png", kind
    assert Epub(make_epub(tmp_path / "plain.epub")).open().cover is None


def test_thumbnail_is_small_one_bit_and_really_the_cover(tmp_path):
    path = make_epub(tmp_path / "c.epub", cover="epub3")
    thumb = make_cover(path, "Test Book")
    assert thumb.size == THUMB_SIZE and thumb.mode == "1"
    # the gradient goes dark -> light left to right, so the left is mostly black
    left = sum(thumb.getpixel((x, y)) == 0 for x in range(3, 12) for y in range(3, 60))
    right = sum(thumb.getpixel((x, y)) == 0 for x in range(36, 45) for y in range(3, 60))
    assert left > right * 2


def test_placeholder_for_books_without_a_cover(tmp_path):
    thumb = make_cover(make_epub(tmp_path / "p.epub"), "A Fairly Long Title For A Small Cover")
    assert thumb.size == THUMB_SIZE and thumb.getbbox() is not None
    assert thumb.tobytes() != placeholder_cover("Another title", THUMB_SIZE).tobytes()


def test_arabic_placeholder_draws(tmp_path):
    thumb = placeholder_cover("كتاب مناقب الشافعي للبيهقي", THUMB_SIZE)
    assert sum(thumb.getpixel((x, y)) == 0 for x in range(6, 42) for y in range(24, 62)) > 30


def test_cache_is_written_once_then_read(tmp_path):
    path = make_epub(tmp_path / "c.epub", cover="epub3")
    cache = tmp_path / "cache"
    first = cached_cover(cache, path, "T")
    [file] = list((cache / "covers").rglob("*.png"))
    stamp = file.stat().st_mtime_ns
    second = cached_cover(cache, path, "T")
    assert file.stat().st_mtime_ns == stamp and first.tobytes() == second.tobytes()
    assert Image.open(file).mode == "1"


def test_broken_cover_falls_back_to_placeholder(tmp_path):
    import zipfile
    path = make_epub(tmp_path / "bad.epub", cover="epub3")
    broken = tmp_path / "broken.epub"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(broken, "w") as dst:
        for item in src.infolist():
            data = b"not an image" if item.filename.endswith("cover.png") else src.read(item.filename)
            dst.writestr(item, data)
    assert make_cover(broken, "Title").size == THUMB_SIZE


def test_wrap_text():
    f = Fonts(10)
    lines = wrap_text(f, "one two three four five six seven eight nine ten", 60, 3)
    assert len(lines) == 3 and all(f.width(l) <= 60 for l in lines) and lines[-1].endswith("...")
    assert wrap_text(f, "short", 60, 3) == ["short"]


def test_cache_key_depends_on_version_and_size(tmp_path, monkeypatch=None):
    from ereader import covers
    path = make_epub(tmp_path / "c.epub")
    a = covers._cache_name(path, (48, 64))
    assert a != covers._cache_name(path, (210, 280))        # each size is cached separately
    old = covers.COVER_VERSION
    try:
        covers.COVER_VERSION = old + 1
        assert covers._cache_name(path, (48, 64)) != a       # a new version remakes the thumbnails
    finally:
        covers.COVER_VERSION = old
