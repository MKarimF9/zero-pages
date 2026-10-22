"""Add books over Wi-Fi. Run: python3 -m pytest tests/test_upload.py -v"""
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import quote

import ereader.screens as screens
from ereader.controls import Button as B
from ereader.fonts import Fonts
from ereader.screens import AppContext, LibraryOptionsScreen, LibraryScreen, UploadScreen
from ereader.state import ReadingState
from ereader.upload import MAX_FAILURES, UploadServer, safe_name
from tests.make_epub import make_epub


class Running:
    """An upload server on a free local port, for the length of a with-block."""
    def __init__(self, tmp_path, **kw):
        kw.setdefault("pin", "1234")
        self.dir = tmp_path / "books"
        self.server = UploadServer(self.dir, host="127.0.0.1", port=0, **kw)

    def __enter__(self):
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server.port}"
        return self

    def __exit__(self, *exc):
        self.server.stop()

    def call(self, method, path, body=None, pin="1234"):
        headers = {"X-PIN": pin} if pin is not None else {}
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def upload(self, name, data, pin="1234"):
        return self.call("POST", "/api/upload?name=" + quote(name), data, pin)

    def files(self):
        return sorted(p.name for p in self.dir.iterdir()) if self.dir.exists() else []


def epub_bytes(tmp_path, cover=None):
    return make_epub(tmp_path / "src.epub", cover=cover).read_bytes()


# ---------------------------------------------------------------- the server

def test_page_and_info_need_no_pin(tmp_path):
    with Running(tmp_path) as s:
        with urllib.request.urlopen(s.base + "/") as r:
            assert b"Your books" in r.read()
        status, info = s.call("GET", "/api/info", pin=None)
        assert status == 200 and info["pin_required"] is True and info["free_mb"] > 0


def test_upload_list_and_delete(tmp_path):
    data = epub_bytes(tmp_path)
    with Running(tmp_path) as s:
        assert s.upload("My Book.epub", data) == (200, {"ok": True, "name": "My Book.epub", "replaced": False, "size": len(data)})
        assert s.files() == ["My Book.epub"] and (s.dir / "My Book.epub").read_bytes() == data
        status, books = s.call("GET", "/api/books")
        assert status == 200 and books == [{"name": "My Book.epub", "size": len(data)}]
        assert s.server.added == ["My Book.epub"]
        assert s.call("POST", "/api/delete?name=" + quote("My Book.epub"))[0] == 200
        assert s.files() == [] and s.call("POST", "/api/delete?name=gone.epub")[0] == 404


def test_uploading_again_replaces(tmp_path):
    data = epub_bytes(tmp_path)
    with Running(tmp_path) as s:
        s.upload("a.epub", data)
        status, body = s.upload("a.epub", epub_bytes(tmp_path, cover="epub3"))
        assert status == 200 and body["replaced"] is True and s.files() == ["a.epub"]


def test_arabic_file_names_survive(tmp_path):
    with Running(tmp_path) as s:
        assert s.upload("كتاب مناقب الشافعي.epub", epub_bytes(tmp_path))[0] == 200
        assert s.files() == ["كتاب مناقب الشافعي.epub"]


def test_wrong_or_missing_pin_is_refused_and_saves_nothing(tmp_path):
    data = epub_bytes(tmp_path)
    with Running(tmp_path) as s:
        assert s.upload("a.epub", data, pin="0000")[0] == 403
        assert s.upload("a.epub", data, pin=None)[0] == 403
        assert s.call("GET", "/api/books", pin="nope")[0] == 403
        assert s.files() == []


def test_five_wrong_pins_lock_everything_for_a_while(tmp_path):
    now = [100.0]
    with Running(tmp_path, clock=lambda: now[0], lockout=60) as s:
        for _ in range(MAX_FAILURES):
            assert s.call("GET", "/api/books", pin="0000")[0] == 403
        status, body = s.call("GET", "/api/books", pin="1234")       # even the right PIN waits
        assert status == 429 and "seconds" in body["error"]
        now[0] += 61
        assert s.call("GET", "/api/books", pin="1234")[0] == 200


def test_no_pin_mode(tmp_path):
    with Running(tmp_path, pin="") as s:
        assert s.upload("a.epub", epub_bytes(tmp_path), pin=None)[0] == 200


def test_unsafe_names_are_rejected(tmp_path):
    data = epub_bytes(tmp_path)
    with Running(tmp_path) as s:
        for bad in ("../evil.epub", "a/b.epub", "a\\b.epub", ".hidden.epub", "book.txt", ".epub", "x\x00.epub", ""):
            assert s.upload(bad, data)[0] == 400, repr(bad)
        assert s.files() == []
    assert safe_name("Fine Name.EPUB") == "Fine Name.EPUB" and safe_name("a" * 300 + ".epub") is None


def test_files_that_are_not_epubs_are_thrown_away(tmp_path):
    import io, zipfile
    zip_only = io.BytesIO()
    with zipfile.ZipFile(zip_only, "w") as z:
        z.writestr("hello.txt", "not a book")
    with Running(tmp_path) as s:
        assert s.upload("a.epub", b"this is plain text, not a zip file")[0] == 400
        assert s.upload("b.epub", zip_only.getvalue())[0] == 400
        assert s.files() == []                       # no book and no leftover temporary file


def test_size_limit_and_empty_upload(tmp_path):
    data = epub_bytes(tmp_path)
    with Running(tmp_path, max_bytes=len(data) - 1) as s:
        assert s.upload("a.epub", data)[0] == 413 and s.files() == []
    with Running(tmp_path) as s:
        assert s.upload("a.epub", b"")[0] == 400


def test_a_cut_off_upload_leaves_nothing_behind(tmp_path):
    with Running(tmp_path) as s:
        sock = socket.create_connection(("127.0.0.1", s.server.port), timeout=10)
        sock.sendall(b"POST /api/upload?name=a.epub HTTP/1.0\r\nX-PIN: 1234\r\nContent-Length: 5000\r\n\r\nPK-only-a-few-bytes")
        sock.shutdown(socket.SHUT_WR)               # the phone lost Wi-Fi mid-upload
        reply = sock.recv(4096)
        sock.close()
        assert b"400" in reply.split(b"\r\n")[0] and s.files() == []


# ---------------------------------------------------------------- the e-reader screen

def make_ctx(tmp_path):
    books = tmp_path / "books"; books.mkdir()
    state = ReadingState(tmp_path / "state" / "s.json")
    return AppContext(books, Fonts(22), Fonts(20), state, 400, 300, Fonts(15))


def test_upload_screen_serves_then_stops(tmp_path):
    ctx = make_ctx(tmp_path)
    old = screens.local_ip
    screens.local_ip = lambda: "192.168.1.5"
    try:
        screen = UploadScreen(ctx, port=0)
        assert screen.error == "" and screen.server and screen.url.startswith("http://192.168.1.5:")
        assert screen.render().size == (400, 300) and len(screen.server.pin) == 4
        port = screen.server.port
        with Running(tmp_path) as s:       # a real upload shows up on the screen's counter
            pass
        screen.server.added.append("x.epub")
        screen.render()
        assert screen.handle(B.DOWN) is None and screen.full_refresh is False
        assert isinstance(screen.handle(B.SELECT), LibraryScreen) and screen.server is None
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            assert False, "the server should have stopped"
        except OSError:
            pass
    finally:
        screens.local_ip = old


def test_no_wifi_shows_a_message_and_starts_nothing(tmp_path):
    ctx = make_ctx(tmp_path)
    old = screens.local_ip
    screens.local_ip = lambda: None
    try:
        screen = UploadScreen(ctx, port=0)
        assert screen.server is None and "Wi-Fi" in screen.error and screen.render().size == (400, 300)
        assert isinstance(screen.handle(B.SELECT), LibraryScreen)
    finally:
        screens.local_ip = old


def test_port_already_in_use_is_reported_not_fatal(tmp_path):
    ctx = make_ctx(tmp_path)
    busy = socket.socket(); busy.bind(("0.0.0.0", 0)); busy.listen(1)
    old = screens.local_ip
    screens.local_ip = lambda: "192.168.1.5"
    try:
        screen = UploadScreen(ctx, port=busy.getsockname()[1])
        assert screen.server is None and "start the server" in screen.error
        screen.render()
    finally:
        screens.local_ip = old
        busy.close()


def test_library_options_open_the_upload_screen(tmp_path):
    ctx = make_ctx(tmp_path)
    old = screens.local_ip
    screens.local_ip = lambda: None
    try:
        opts = LibraryScreen(ctx).handle(B.SELECT_LONG)
        assert isinstance(opts, LibraryOptionsScreen) and opts.render().size == (400, 300)
        opts.cursor = 3
        assert isinstance(opts.handle(B.SELECT), UploadScreen)
    finally:
        screens.local_ip = old
