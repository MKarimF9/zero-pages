"""pi-ereader entry point.

    python3 main.py                          # PNG preview + keyboard (development)
    python3 main.py --display epaper --controls gpio   # the real device (Waveshare 4.2" V2)

The main loop is the whole app in five lines:
    draw the current screen -> show it -> wait for a button -> let the
    screen decide what's next -> repeat
"""
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from ereader.controls import Button, CombinedControls, GPIOControls, KeyboardControls
from ereader.display import EPaperDisplay, PNGDisplay
from ereader.fonts import DEFAULT_FAMILY, Fonts
from ereader.power import can_power_off, power_off
from ereader.remote import SocketControls, control_socket_path
from ereader.screens import AppContext, effective_button, initial_screen
from ereader.state import ReadingState


def main() -> None:
    ap = argparse.ArgumentParser(description="Pi Zero W e-paper ebook reader")
    ap.add_argument("--books", default="~/books", help="folder with .epub files")
    ap.add_argument("--start", choices=["last", "library"], default="last",
                    help="open the last book you were reading (default) or the library")
    ap.add_argument("--display", choices=["png", "epaper"], default="png")
    ap.add_argument("--controls", choices=["keyboard", "gpio"], default="keyboard")
    ap.add_argument("--width", type=int, default=800, help="PNG preview only; e-paper uses the panel's size")
    ap.add_argument("--height", type=int, default=480, help="PNG preview only")
    ap.add_argument("--font-size", type=int, default=None,
                    help="book text size (default: the last size chosen in the reader menu, else 22)")
    args = ap.parse_args()

    display = (PNGDisplay(args.width, args.height) if args.display == "png"
               else EPaperDisplay())
    # The buttons also listen on a private socket, so `python3 remote.py` over SSH can press them too.
    controls = (KeyboardControls() if args.controls == "keyboard"
                else CombinedControls(GPIOControls(up_pin=5, down_pin=6, select_pin=13),  # pick your pins
                                      SocketControls(control_socket_path())))

    state = ReadingState()
    font_size = args.font_size or state.get_setting("font_size", 22)
    ctx = AppContext(
        books_dir=Path(args.books).expanduser(),
        fonts=Fonts(font_size, state.get_setting("font_family", DEFAULT_FAMILY)),
        ui_fonts=Fonts(20),
        status_fonts=Fonts(15),
        state=state,
        width=display.width,   # the panel's own size when using e-paper
        height=display.height,
    )

    if args.controls == "keyboard":
        print("Keys: w/k = up, s/j = down, space/enter = select, m = hold select (options), q = quit")

    # systemd stops the service with SIGTERM: turn it into a normal exit so the
    # finally block below saves progress and puts the panel to sleep.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    screen = initial_screen(ctx, args.start)
    switching_off = False
    try:
        while True:
            display.show(screen.render(), full_refresh=screen.full_refresh)
            display.set_idle_image(screen.sleep_image)  # shown if the panel goes idle
            button = controls.wait()
            if button is Button.QUIT:
                break
            if button is Button.POWER_OFF:
                if can_power_off():
                    switching_off = True
                    break
                print("[power] holding select can't switch the Pi off: sudo needs a password", flush=True)
                continue
            screen = screen.handle(effective_button(button, screen)) or screen
    finally:
        screen.close()  # e.g. save reading progress
        display.sleep(with_image=switching_off)   # the sleep screen stays up while the Pi is off
    if switching_off and not power_off():
        print("[power] the poweroff command failed", flush=True)


if __name__ == "__main__":
    main()
