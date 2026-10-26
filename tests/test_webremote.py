"""The phone buttons page. Run: python3 -m pytest tests/test_webremote.py -v"""
import json
import urllib.error
import urllib.request

from ereader.controls import Button as B
from ereader.webremote import QueueControls, WebRemote, load_pin


def start(pin="123456", lockout=60.0, clock=None):
    controls = QueueControls()
    kwargs = {"clock": clock} if clock else {}
    remote = WebRemote(controls, pin, host="127.0.0.1", port=0, lockout=lockout, **kwargs)
    remote.start()
    return remote, controls


def call(remote, path, pin="123456", method="GET"):
    request = urllib.request.Request(f"http://127.0.0.1:{remote.port}{path}", method=method,
                                     data=b"" if method == "POST" else None,
                                     headers={"X-PIN": pin} if pin is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_the_page_needs_no_pin_but_everything_else_does():
    remote, controls = start()
    try:
        status, body = call(remote, "/", pin=None)
        assert status == 200 and b"E-reader remote" in body
        assert call(remote, "/api/check", pin=None)[0] == 403
        assert call(remote, "/api/press?button=up", pin=None, method="POST")[0] == 403
        assert controls._events.empty()
    finally:
        remote.stop()


def test_pressing_buttons_reaches_the_reader():
    remote, controls = start()
    try:
        for name in ("up", "down", "select", "hold"):
            assert call(remote, f"/api/press?button={name}", method="POST")[0] == 200
        assert [controls.wait() for _ in range(4)] == [B.UP, B.DOWN, B.SELECT, B.SELECT_LONG]
    finally:
        remote.stop()


def test_quit_and_power_off_are_not_available():
    remote, controls = start()
    try:
        for name in ("quit", "power_off", "poweroff", "", "UP "):
            assert call(remote, f"/api/press?button={name}", method="POST")[0] == 400
        assert call(remote, "/api/press?button=up")[0] == 404          # a GET doesn't press anything
        assert controls._events.empty()
    finally:
        remote.stop()


def test_five_wrong_pins_lock_even_the_right_one_out_for_a_while():
    now = [0.0]
    remote, controls = start(lockout=60, clock=lambda: now[0])
    try:
        assert [call(remote, "/api/check", pin="0")[0] for _ in range(5)] == [403] * 5
        assert call(remote, "/api/check")[0] == 429                    # locked, though the PIN is right
        now[0] = 61
        assert call(remote, "/api/check")[0] == 200
    finally:
        remote.stop()


def test_the_phone_page_has_only_the_buttons_and_no_screen_picture():
    remote, controls = start()
    try:
        status, body = call(remote, "/", pin=None)
        assert status == 200 and b"<img" not in body
        assert call(remote, "/api/screen.png")[0] == 404          # the screen is not sent to the phone
        assert call(remote, "/api/check")[0] == 200               # the PIN check the page unlocks with
    finally:
        remote.stop()


def test_the_pin_is_created_once_and_kept_private(tmp_path):
    path = tmp_path / "state" / "remote-pin"
    pin = load_pin(path)
    assert pin.isdigit() and len(pin) == 6
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_pin(path) == pin                                       # the same one next time
    path.write_text("not a pin")
    assert load_pin(path).isdigit()                                    # a damaged file is replaced


def test_the_page_keeps_the_pin_on_the_phone():
    from ereader.webremote import PAGE
    assert "localStorage" in PAGE and "sessionStorage" not in PAGE
