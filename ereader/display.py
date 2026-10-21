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

    Full refresh (slow, flashes) clears ghosting; partial refresh is fast.
    The panel's image RAM is lost when it is powered off, so after sleeping
    the next frame is always a full refresh, whatever the caller asked for.

    To avoid leaving the panel powered for hours (bad for e-paper), it goes
    to sleep by itself after `idle_sleep` seconds without a new frame. The
    image stays visible while asleep.
    """

    IDLE_SLEEP_SECONDS = 60

    def __init__(self, driver=None, idle_sleep: float = IDLE_SLEEP_SECONDS):
        self._epd = (driver or _load_waveshare_driver()).EPD()
        self.width, self.height = self._epd.width, self._epd.height
        self._idle_sleep = idle_sleep
        self._lock = threading.Lock()      # the idle timer runs on another thread
        self._awake = False
        self._idle_token: object | None = None

    def show(self, image: Image.Image, full_refresh: bool) -> None:
        if image.size != (self.width, self.height):
            raise ValueError(f"image is {image.size}, panel is {(self.width, self.height)}")
        # PIL's packed 1-bit format (MSB first, 1 = white) is exactly what the
        # panel wants, and is much faster than the driver's getbuffer() loop.
        buf = list(image.convert("1").tobytes())
        with self._lock:
            self._idle_token = None  # cancel a pending idle sleep
            if full_refresh or not self._awake:
                self._epd.init()
                self._epd.display(buf)
                self._awake = True
            else:
                self._epd.display_Partial(buf)
            if self._idle_sleep:
                token = self._idle_token = object()
                timer = threading.Timer(self._idle_sleep, self._sleep_if_idle, args=(token,))
                timer.daemon = True
                timer.start()

    def _sleep_if_idle(self, token: object) -> None:
        with self._lock:
            if token is self._idle_token:  # no newer frame since this timer started
                self._sleep_locked()

    def _sleep_locked(self) -> None:
        self._idle_token = None
        if self._awake:
            self._epd.sleep()
            self._awake = False

    def sleep(self) -> None:
        with self._lock:
            self._sleep_locked()
