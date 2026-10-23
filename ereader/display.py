"""Where finished page images go.

Same idea as atomic14's Renderer/Board split: the rest of the app only
calls display.show(image, full_refresh). It never knows whether the image
ends up in a PNG file or on e-paper. Swapping hardware = swapping this class.

  PNGDisplay     complete, use it now (no screen needed)
  EPaperDisplay  Waveshare 4.2" V2 (400x300), milestone 6
"""
from __future__ import annotations

import sys
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from PIL import Image

PREVIEW_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>pi-ereader preview</title>
<style>
  body { background:#888; margin:0; display:flex; justify-content:center; padding:24px; }
  img  { image-rendering: pixelated; box-shadow: 0 2px 12px #0006; max-width:100%; }
</style></head>
<body><img id="p" src="page.png">
<script>
  // reload the image every second so you see each new frame
  setInterval(() => { document.getElementById('p').src = 'page.png?t=' + Date.now(); }, 1000);
</script></body></html>
"""


class Display(ABC):
    width: int
    height: int

    @abstractmethod
    def show(self, image: Image.Image, full_refresh: bool) -> None:
        """Put `image` on the screen. full_refresh=True clears ghosting (slow);
        False means a partial refresh (fast)."""

    def set_idle_image(self, provider) -> None:
        """`provider()` returns the image to leave on screen when the display
        goes idle (a sleep screen), or None to keep the current page."""
        self._idle_provider = provider

    def sleep(self) -> None:
        """Power down the display when idle. No-op by default."""


class PNGDisplay(Display):
    """Saves every frame to preview/page.png.

    View it live: in a second SSH session run
        cd ~/pi-ereader/preview && python3 -m http.server 8000
    then open http://books.local:8000 on your Mac.
    """

    def __init__(self, width: int = 800, height: int = 480, out_dir: str | Path = "preview"):
        self.width, self.height = width, height
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "index.html").write_text(PREVIEW_HTML)
        self.frames = 0

    def sleep(self) -> None:
        """For development: write what the sleep screen would be to preview/sleep.png."""
        image = getattr(self, "_idle_provider", lambda: None)()
        if image is not None:
            image.save(self.out_dir / "sleep.png")

    def show(self, image: Image.Image, full_refresh: bool) -> None:
        self.frames += 1
        image.save(self.out_dir / "page.png")
        kind = "FULL" if full_refresh else "partial"
        print(f"[display] frame {self.frames}: {kind} refresh", flush=True)


def _load_waveshare_driver():
    """Import Waveshare's epd4in2_V2 module. It isn't pip-installable: copy
    `waveshare_epd/` from their repo into pi-ereader/lib/ (see README)."""
    try:
        from waveshare_epd import epd4in2_V2
    except ImportError:
        lib = Path(__file__).resolve().parents[1] / "lib"
        sys.path.insert(0, str(lib))
        try:
            from waveshare_epd import epd4in2_V2
        except ImportError as e:
            raise RuntimeError(
                "Waveshare driver not found. Copy RaspberryPi_JetsonNano/python/lib/waveshare_epd "
                f"from github.com/waveshareteam/e-Paper into {lib}/ and install "
                "python3-spidev, python3-gpiozero and python3-rpi.gpio. "
                f"(Import error: {e})") from e
    return epd4in2_V2


class EPaperDisplay(Display):
    """Waveshare 4.2" V2 e-paper (400x300, black and white).

    Three kinds of refresh. A partial one is fast (no flash) but leaves a little
    ghosting. When the caller asks for a full refresh (a new screen, or every few
    page turns) we do a *fast* one instead: about 1.5 s with a single flash. Only
    every FAST_BEFORE_FULL-th such request is a true full refresh (several flashes,
    about 3 s) that wipes the ghosting away.
    The panel's image RAM is lost when it is powered off, so the first frame after
    sleeping is never a partial one, whatever the caller asked for.

    To avoid leaving the panel powered for hours (bad for e-paper), it goes
    to sleep by itself after `idle_sleep` seconds without a new frame. The
    image stays visible while asleep.
    """

    IDLE_SLEEP_SECONDS = 60
    FAST_BEFORE_FULL = 6   # fast refreshes in a row before one true full refresh

    def __init__(self, driver=None, idle_sleep: float = IDLE_SLEEP_SECONDS):
        self._epd = (driver or _load_waveshare_driver()).EPD()
        self.width, self.height = self._epd.width, self._epd.height
        self._idle_sleep = idle_sleep
        self._lock = threading.Lock()      # the idle timer runs on another thread
        self._awake = False
        self._idle_token: object | None = None
        self._idle_provider = None
        self._fast_runs = self.FAST_BEFORE_FULL   # the first frame is a true full refresh
        self._fast_mode = False                   # is the controller set up for fast refresh?
        self._last: list[int] | None = None       # the frame on the panel, for the next partial

    def show(self, image: Image.Image, full_refresh: bool) -> None:
        if image.size != (self.width, self.height):
            raise ValueError(f"image is {image.size}, panel is {(self.width, self.height)}")
        # PIL's packed 1-bit format (MSB first, 1 = white) is exactly what the
        # panel wants, and is much faster than the driver's getbuffer() loop.
        buf = list(image.convert("1").tobytes())
        with self._lock:
            self._idle_token = None  # cancel a pending idle sleep
            if self._awake and not full_refresh:
                self._partial(buf)
            elif self._fast_runs >= self.FAST_BEFORE_FULL:
                self._epd.init()
                self._epd.display(buf)
                self._fast_runs, self._fast_mode = 0, False
            else:
                self._epd.init_fast(self._epd.Seconds_1_5S)
                self._epd.display_Fast(buf)
                self._fast_runs += 1
                self._fast_mode = True
            self._awake = True
            self._last = buf
            if self._idle_sleep:
                token = self._idle_token = object()
                timer = threading.Timer(self._idle_sleep, self._sleep_if_idle, args=(token,))
                timer.daemon = True
                timer.start()

    def _partial(self, buf: list[int]) -> None:
        if self._fast_mode:
            # The controller is set up for fast refresh: set it up for partial ones again
            # and give it the old frame to compare against, so nothing depends on whether
            # the reset kept the image RAM.
            self._epd.init()
            self._epd.send_command(0x26)
            self._epd.send_data2(self._last)
            self._fast_mode = False
        self._epd.display_Partial(buf)

    def _sleep_if_idle(self, token: object) -> None:
        # Make the sleep screen first, without the lock: it can take a while on a
        # Pi and a button press in the meantime must not have to wait for it.
        image = None
        if self._idle_provider and token is self._idle_token:
            try:
                image = self._idle_provider()
            except Exception:
                image = None  # no sleep screen is better than no sleep
        with self._lock:
            if token is not self._idle_token:  # a newer frame arrived: stay awake
                return
            if image is not None and image.size == (self.width, self.height) and self._awake:
                self._epd.init()
                self._epd.display(list(image.convert("1").tobytes()))  # true full refresh, then power down
                self._fast_runs, self._fast_mode = 0, False
            self._sleep_locked()

    def _sleep_locked(self) -> None:
        self._idle_token = None
        if self._awake:
            self._epd.sleep()
            self._awake = False

    def sleep(self) -> None:
        with self._lock:
            self._sleep_locked()
