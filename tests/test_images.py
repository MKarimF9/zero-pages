"""Pictures in books. Run: python3 -m pytest tests/test_images.py -v"""
import io
import zipfile

from PIL import Image, ImageDraw

from ereader import images
from ereader.blocks import ImageBlock, TextBlock, Word
from ereader.book import LazyBook
from ereader.controls import Button as B
from ereader.epub import Epub
from ereader.fonts import Fonts
from ereader.html_parser import parse_blocks
from ereader.layout import MARGIN, fit_size, paginate
from ereader.page import PlacedImage
from ereader.screens import AppContext, LibraryScreen
from ereader.state import ReadingState

FONTS = Fonts(22)


# ---------------------------------------------------------------- making test pictures and books

def photo(w=160, h=120, mode="RGB"):
    """A smooth diagonal gradient: lots of mid-grays, like a photo."""
    img = Image.new("L", (w, h))
    img.putdata([(x * 255 // (w - 1) + y * 255 // (h - 1)) // 2 for y in range(h) for x in range(w)])
    return img.convert(mode)


def diagram(w=200, h=100):
    """White with black lines: what a figure or a scanned page looks like."""
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, w - 10, h - 10), outline=0, width=2)
    d.line((10, 10, w - 10, h - 10), fill=0, width=2)
    return img


def encode(img, fmt="PNG"):
    out = io.BytesIO()
    img.save(out, fmt)
    return out.getvalue()


def make_epub_with(tmp_path, body, files, chapter="OEBPS/text/a.xhtml"):
    """An EPUB whose single chapter (at `chapter`) has `body`; `files` = extra {zip path: bytes}."""
    path = tmp_path / "pics.epub"
    href = chapter.split("OEBPS/", 1)[1]
    opf = ('<package xmlns="http://www.idpf.org/2007/opf"><metadata/><manifest>'
           f'<item id="a" href="{href}" media-type="application/xhtml+xml"/></manifest>'
           '<spine><itemref idref="a"/></spine></package>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        z.writestr(chapter, f"<html><body>{body}</body></html>")
        for name, data in files.items():
            z.writestr(name, data)
    return path


def open_book(path, cache_dir=None):
    return LazyBook(path, FONTS, 400, 272, bottom_margin=6, cache_dir=cache_dir)


def placed_images(book):
    return [el for page in book.pages for el in page.elements if isinstance(el, PlacedImage)]


# ---------------------------------------------------------------- parsing and layout

def test_parser_keeps_the_alt_text():
    [block] = parse_blocks('<img src="a.png" alt="  A map of Mecca "/>')
    assert (block.src, block.alt, block.size) == ("a.png", "A map of Mecca", None)


def test_fit_size_shrinks_but_never_enlarges():
    assert fit_size((800, 600), 360, 260) == (347, 260)      # limited by the height
    assert fit_size((800, 100), 360, 260) == (360, 45)       # limited by the width
    assert fit_size((50, 40), 360, 260) == (50, 40)          # small pictures stay small


def test_pictures_are_scaled_and_centered_on_the_page():
    [page] = paginate([ImageBlock("p.png", size=(1000, 500))], FONTS, 400, 300)
    [pic] = page.elements
    assert (pic.width, pic.height) == (360, 180)
    assert pic.x == MARGIN                                    # exactly as wide as the text area
    [page] = paginate([ImageBlock("p.png", size=(100, 100))], FONTS, 400, 300)
    assert page.elements[0].x == (400 - 100) // 2             # a small one is centered


def test_a_picture_that_does_not_fit_starts_a_new_page_and_a_tall_one_gets_its_own():
    text = TextBlock([Word(w) for w in ("word " * 120).split()])
    pages = paginate([text, ImageBlock("p.png", size=(360, 240))], FONTS, 400, 300)
    assert len(pages) >= 2 and isinstance(pages[-1].elements[-1], PlacedImage)
    [page] = paginate([ImageBlock("tall.png", size=(300, 3000))], FONTS, 400, 300)
    pic = page.elements[0]
    assert pic.height <= 300 - 2 * MARGIN and pic.y == MARGIN  # shrunk to fit one page


def test_pictures_without_a_size_keep_the_labelled_box():
    [page] = paginate([ImageBlock("x.png", alt="Figure 1")], FONTS, 400, 300)
    assert page.elements[0].alt == "Figure 1"


# ---------------------------------------------------------------- finding pictures in the book

def test_relative_paths_are_resolved_and_sizes_measured(tmp_path):
    path = make_epub_with(tmp_path, '<p>Before</p><img src="../images/pic%201.png" alt="Fig"/><p>After</p>',
                          {"OEBPS/images/pic 1.png": encode(photo(300, 200))})
    book = open_book(path)
    [pic] = placed_images(book)
    assert pic.src == "OEBPS/images/pic 1.png"                   # "../" and %20 handled
    assert (pic.width, pic.height) == (300, 200) and pic.alt == "Fig"


def test_missing_svg_and_inline_data_pictures_become_boxes(tmp_path):
    body = ('<img src="gone.png" alt="missing"/><img src="art.svg" alt="vector"/>'
            '<img src="data:image/png;base64,AAAA" alt="inline"/><img src="notimage.png" alt="broken"/>')
    path = make_epub_with(tmp_path, body, {"OEBPS/text/art.svg": b"<svg/>", "OEBPS/text/notimage.png": b"not a picture"})
    book = open_book(path)
    boxes = placed_images(book)
    assert [b.alt for b in boxes] == ["missing", "vector", "inline", "broken"]
    assert all(b.height == 120 for b in boxes)                    # the labelled-box height


def test_natural_size_reads_only_the_header(tmp_path):
    path = make_epub_with(tmp_path, "<p>x</p>", {"OEBPS/text/big.png": encode(photo(640, 480))})
    epub = Epub(path).open()
    assert images.natural_size(epub, "OEBPS/text/big.png") == (640, 480)
    assert images.natural_size(epub, "OEBPS/text/nope.png") is None
    assert images.natural_size(epub, "OEBPS/text/x.svg") is None


# ---------------------------------------------------------------- drawing pictures

def make_book_with_picture(tmp_path, img, fmt="PNG", name="pic.png", cache_dir=None):
    path = make_epub_with(tmp_path, f'<img src="{name}" alt="A picture"/>', {f"OEBPS/text/{name}": encode(img, fmt)})
    book = open_book(path, cache_dir)
    return book, placed_images(book)[0]


def test_photos_are_dithered_to_black_and_white_at_the_asked_size(tmp_path):
    book, pic = make_book_with_picture(tmp_path, photo(200, 150))
    out = book.image(pic.src, 100, 75)
    assert out.mode == "1" and out.size == (100, 75)
    black = sum(1 for p in out.convert('L').tobytes() if p == 0) / (100 * 75)
    assert 0.2 < black < 0.8                                      # dots imitate the gray ramp


def test_diagrams_stay_crisp_not_dithered(tmp_path):
    book, pic = make_book_with_picture(tmp_path, diagram(200, 100))
    assert images._is_line_art(diagram()) and not images._is_line_art(photo().convert("L"))
    out = book.image(pic.src, 200, 100)
    assert out.getpixel((0, 0)) == 255 and out.getpixel((100, 50)) in (0, 255)
    white = sum(1 for p in out.convert('L').tobytes() if p == 255) / (200 * 100)
    assert white > 0.85                                           # the white background stays clean white


def test_transparent_pictures_get_a_white_background(tmp_path):
    clear = Image.new("RGBA", (60, 40), (0, 0, 0, 0))             # fully transparent black
    ImageDraw.Draw(clear).rectangle((20, 10, 40, 30), fill=(0, 0, 0, 255))
    book, pic = make_book_with_picture(tmp_path, clear)
    out = book.image(pic.src, 60, 40)
    assert out.getpixel((2, 2)) == 255 and out.getpixel((30, 20)) == 0   # not a black square


def test_jpegs_and_palette_gifs_work(tmp_path):
    for fmt, name, img in (("JPEG", "p.jpg", photo(200, 150)), ("GIF", "p.gif", photo(80, 60, "P"))):
        book, pic = make_book_with_picture(tmp_path, img, fmt, name)
        assert book.image(pic.src, pic.width, pic.height).size == (pic.width, pic.height), fmt


def test_drawn_pictures_are_cached_on_disk_and_in_memory(tmp_path):
    cache = tmp_path / "cache"
    book, pic = make_book_with_picture(tmp_path, photo(200, 150), cache_dir=cache)
    first = book.image(pic.src, 100, 75)
    [file] = list((cache / "images").glob("*.png"))
    stamp = file.stat().st_mtime_ns
    book._image_cache.clear()                                     # a new session reads the disk copy
    assert book.image(pic.src, 100, 75).tobytes() == first.tobytes() and file.stat().st_mtime_ns == stamp
    book.image(pic.src, 50, 40)                                   # another size is another cache entry
    assert len(list((cache / "images").glob("*.png"))) == 2


def test_memory_keeps_only_a_few_pictures(tmp_path):
    book, pic = make_book_with_picture(tmp_path, photo(200, 150))
    for width in range(20, 20 + 10):
        book.image(pic.src, width, 15)
    assert len(book._image_cache) == 6


def test_unreadable_pictures_return_none(tmp_path):
    book, pic = make_book_with_picture(tmp_path, photo(40, 30))
    assert book.image("OEBPS/text/not-there.png", 10, 10) is None


# ---------------------------------------------------------------- on the reader's screen

def test_reader_draws_the_picture_instead_of_the_placeholder(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    import shutil
    shutil.move(str(make_epub_with(tmp_path, '<img src="d.png" alt="Fig"/>', {"OEBPS/text/d.png": encode(diagram(300, 200))})),
                str(books / "pics.epub"))
    state = ReadingState(tmp_path / "state" / "s.json")
    ctx = AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15))
    reader = LibraryScreen(ctx).handle(B.SELECT)
    assert reader.error is None
    pic = placed_images(reader.book)[0]
    frame = reader.render().convert("L")
    region = frame.crop((pic.x, pic.y, pic.x + pic.width, pic.y + pic.height))
    black = sum(1 for p in region.convert('L').tobytes() if p < 128)
    assert black > 100                                            # the diagram's lines, not an empty box
    assert (tmp_path / "state" / "images").exists()               # drawn once, cached next to the state
