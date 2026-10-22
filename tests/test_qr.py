"""QR code on the Manage books screen. Run: python3 -m pytest tests/test_qr.py -v"""
import ereader.screens as screens
from ereader.fonts import Fonts
from ereader.qr import QUIET, qr_image, qr_matrix
from ereader.screens import AppContext, UploadScreen
from ereader.state import ReadingState


def test_image_matches_the_matrix_with_a_white_quiet_zone():
    text = "http://192.168.1.50:8080/#1234"
    modules, img = qr_matrix(text), qr_image(text, 200)
    n = len(modules)
    scale = img.width // (n + 2 * QUIET)
    assert img.width == img.height == (n + 2 * QUIET) * scale and scale >= 4
    for y in range(n):
        for x in range(n):   # sample the middle of each module
            px = img.getpixel(((x + QUIET) * scale + scale // 2, (y + QUIET) * scale + scale // 2))
            assert (px == 0) == modules[y][x]
    for i in range(img.width):   # the quiet zone is white all round
        for j in range(QUIET * scale):
            assert img.getpixel((i, j)) == 255 and img.getpixel((j, i)) == 255
            assert img.getpixel((i, img.height - 1 - j)) == 255 and img.getpixel((img.width - 1 - j, i)) == 255


def test_image_uses_whole_pixels_and_never_exceeds_the_room():
    for room in (60, 120, 177, 300):
        img = qr_image("http://10.0.0.5:8080/#9999", room)
        assert img.width <= max(room, img.width) and img.mode == "1"
        if room >= 29 * 2:
            assert img.width <= room


def test_matrix_is_square_and_has_finder_patterns():
    m = qr_matrix("hello")
    n = len(m)
    assert all(len(r) == n for r in m) and n >= 21
    for r0, c0 in ((0, 0), (0, n - 7), (n - 7, 0)):   # the three 7x7 corner squares
        assert all(m[r0][c0 + i] for i in range(7)) and all(m[r0 + 6][c0 + i] for i in range(7))


class Screen:
    """An UploadScreen with the network and the QR drawing replaced, for the length of a with-block."""
    def __init__(self, tmp_path, pin):
        self.tmp_path, self.pin, self.seen = tmp_path, pin, []

    def __enter__(self):
        self.old = screens.local_ip, screens.qr_image
        real = screens.qr_image
        screens.local_ip = lambda: "192.168.1.50"
        screens.qr_image = lambda text, px: (self.seen.append(text), real(text, px))[1]
        books = self.tmp_path / "books"; books.mkdir()
        ctx = AppContext(books, Fonts(22), Fonts(20), ReadingState(self.tmp_path / "s.json"), 400, 300, Fonts(15))
        self.screen = UploadScreen(ctx, port=0)
        self.screen.server.pin = self.pin
        return self

    def __exit__(self, *exc):
        self.screen.close()
        screens.local_ip, screens.qr_image = self.old


def test_screen_qr_carries_address_and_pin(tmp_path):
    with Screen(tmp_path, "4821") as s:
        img = s.screen.render()
        assert img.size == (400, 300) and s.seen == [f"{s.screen.url}/#4821"]


def test_screen_qr_without_a_pin_is_just_the_address(tmp_path):
    with Screen(tmp_path, "") as s:
        s.screen.render()
        assert s.seen == [s.screen.url]


def test_page_takes_the_pin_from_the_address_and_wipes_it():
    from ereader.upload import PAGE
    assert r"/^#(\d{1,12})$/.exec(location.hash)" in PAGE
    assert "history.replaceState(null, '', location.pathname + location.search)" in PAGE
    assert PAGE.index("fromQr") < PAGE.index("let pin = sessionStorage")   # read before the PIN is first used
