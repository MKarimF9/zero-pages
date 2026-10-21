"""Sleep screen. Run: python3 -m pytest tests/test_sleep.py -v"""
import time

from ereader.controls import Button as B
from ereader.covers import placeholder_cover
from ereader.display import EPaperDisplay, PNGDisplay
from ereader.fonts import Fonts
from ereader.screens import (AppContext, LibraryOptionsScreen, LibraryScreen, ReaderScreen,
                             initial_screen)
from ereader.state import ReadingState
from tests.make_epub import make_epub
from tests.fakes import FakeDriver, image


def make_ctx(tmp_path, cover=None):
    books = tmp_path / "books"; books.mkdir()
    make_epub(books / "a.epub", cover=cover)
    state = ReadingState(tmp_path / "state" / "s.json")
    return AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15)), state, books


def test_reader_sleep_image_is_the_cover_full_screen(tmp_path):
    ctx, state, books = make_ctx(tmp_path, cover="epub3")
    reader = LibraryScreen(ctx).handle(B.SELECT)
    img = reader.sleep_image()
    assert img.size == (400, 300) and img.mode == "1"
    # the cover is centered with white on both sides
    assert all(img.getpixel((x, y)) == 1 for x in (0, 10, 389, 399) for y in range(0, 300, 20))
    assert any(img.getpixel((200, y)) == 0 for y in range(300))


def test_menu_and_contents_use_the_same_sleep_image(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    reader = LibraryScreen(ctx).handle(B.SELECT)
    menu = reader.handle(B.SELECT)
    menu.cursor = 1
    toc = menu.handle(B.SELECT)
    assert menu.sleep_image().tobytes() == reader.sleep_image().tobytes() == toc.sleep_image().tobytes()


def test_page_setting_keeps_the_current_page(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    state.set_setting("sleep_screen", "page")
    reader = LibraryScreen(ctx).handle(B.SELECT)
    assert reader.sleep_image() is None and LibraryScreen(ctx).sleep_image() is None


def test_library_sleeps_on_the_last_read_books_cover(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    assert LibraryScreen(ctx).sleep_image() is None              # nothing read yet
    state.set_position(str(books / "a.epub"), 1, 0, 0.3)
    assert LibraryScreen(ctx).sleep_image().size == (400, 300)
    state.set_position(str(tmp_path / "gone.epub"), 1, 0, 0.3)   # last book was deleted
    assert LibraryScreen(ctx).sleep_image() is None


def test_sleep_screen_option_cycles_and_persists(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    opts = LibraryScreen(ctx).handle(B.SELECT_LONG)
    assert isinstance(opts, LibraryOptionsScreen) and opts.render().size == (400, 300)
    opts.cursor = 2
    assert opts.handle(B.SELECT) is None and state.get_setting("sleep_screen") == "page"
    assert opts.handle(B.SELECT) is None and state.get_setting("sleep_screen") == "cover"


def test_placeholder_scales_up_for_the_sleep_screen():
    small = placeholder_cover("A Title", (48, 64))
    big = placeholder_cover("كتاب مناقب الشافعي للبيهقي", (210, 284))
    assert big.size == (210, 284) and big.getbbox() is not None
    ink = lambda im: sum(1 for p in im.getdata() if p == 0) / (im.width * im.height)
    assert 0.02 < ink(big) < 0.5 and small.size == (48, 64)


# ---------------------------------------------------------------- the display

def test_panel_shows_the_sleep_screen_then_sleeps():
    driver = FakeDriver()
    d = EPaperDisplay(driver, idle_sleep=0.05)
    d.set_idle_image(lambda: image(black_at=(5, 5)))
    d.show(image(), True)
    time.sleep(0.3)
    epd = driver.epd
    assert epd.calls == ["init", ("display", 15000), "init", ("display", 15000), "sleep"]
    d.show(image(), False)                      # woke up: the next page is a full refresh
    assert epd.calls[-2:] == ["init", ("display", 15000)]


def test_failing_or_missing_sleep_image_still_sleeps():
    for provider in (lambda: 1 / 0, lambda: None, None):
        driver = FakeDriver()
        d = EPaperDisplay(driver, idle_sleep=0.05)
        d.set_idle_image(provider)
        d.show(image(), True)
        time.sleep(0.25)
        assert driver.epd.calls == ["init", ("display", 15000), "sleep"]


def test_a_button_press_cancels_the_sleep_screen():
    driver = FakeDriver()
    d = EPaperDisplay(driver, idle_sleep=0.2)
    d.set_idle_image(lambda: image(black_at=(1, 1)))
    d.show(image(), True)
    time.sleep(0.12)
    d.show(image(), False)                      # restarts the countdown
    time.sleep(0.12)
    assert "sleep" not in driver.epd.calls and driver.epd.calls.count("init") == 1


def test_png_preview_writes_sleep_png(tmp_path):
    d = PNGDisplay(400, 300, out_dir=tmp_path)
    d.sleep()
    assert not (tmp_path / "sleep.png").exists()
    d.set_idle_image(lambda: image())
    d.sleep()
    assert (tmp_path / "sleep.png").exists()
