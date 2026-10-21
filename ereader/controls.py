"""Input: three buttons, same as atomic14's UP / DOWN / SELECT.

  KeyboardControls  complete: use your keyboard over SSH while developing
  GPIOControls      three push buttons on GPIO pins (milestone 6)
"""
from __future__ import annotations

import queue
import sys
from abc import ABC, abstractmethod
from enum import Enum, auto


class Button(Enum):
    UP = auto()
    DOWN = auto()
    SELECT = auto()
    SELECT_LONG = auto()  # hold select (keyboard: m): options on screens that have them
    QUIT = auto()  # dev only; the real device has no quit button


class Controls(ABC):
    @abstractmethod
    def wait(self) -> Button:
        """Block until a button is pressed, then return it."""


class KeyboardControls(Controls):
    """w/k or Up arrow = up, s/j or Down arrow = down, space/enter = select,
    m = hold select, q = quit."""

    KEYMAP = {
        "w": Button.UP, "k": Button.UP,
        "s": Button.DOWN, "j": Button.DOWN,
        " ": Button.SELECT, "\r": Button.SELECT, "\n": Button.SELECT,
        "m": Button.SELECT_LONG,
        "q": Button.QUIT, "\x03": Button.QUIT,  # \x03 = Ctrl+C in raw mode
        # Arrow keys arrive as three characters: ESC [ A (or ESC O A in some terminals)
        "\x1b[A": Button.UP, "\x1bOA": Button.UP,
        "\x1b[B": Button.DOWN, "\x1bOB": Button.DOWN,
    }

    def __init__(self) -> None:
        self._buffer = ""  # keys read but not yet handed out (fast typing, arrow sequences)

    def wait(self) -> Button:
        while True:
            key = self._next_key()
            if key is None:
                return Button.QUIT  # input closed
            if key in self.KEYMAP:
                return self.KEYMAP[key]  # anything else (other keys, Home, F-keys...) is ignored

    def _next_key(self) -> str | None:
        """One key from the buffer: a single character, or a whole arrow-key sequence."""
        if not self._buffer:
            self._buffer = self._read()
            if not self._buffer:
                return None
        if self._buffer[:2] in ("\x1b[", "\x1bO") and len(self._buffer) >= 3:
            key, self._buffer = self._buffer[:3], self._buffer[3:]
        else:
            key, self._buffer = self._buffer[0], self._buffer[1:]
        return key

    @staticmethod
    def _read() -> str:
        """Read whatever the user typed, without waiting for Enter. Several
        characters can come back at once (an arrow key is three). "" = input closed."""
        if not sys.stdin.isatty():
            return sys.stdin.read(1)
        import os
        import select
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            data = os.read(fd, 32)  # straight from the terminal: no hidden Python buffer
            if data == b"\x1b" and select.select([fd], [], [], 0.05)[0]:
                data += os.read(fd, 32)  # the rest of an arrow sequence that arrived a moment later
            return data.decode("utf-8", errors="ignore")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


class GPIOControls(Controls):
    """Three push buttons on BCM pins, each wired between the pin and GND
    (gpiozero enables the Pi's internal pull-up, so no resistor is needed).

    The Waveshare HAT uses BCM 17, 25, 8, 24, 18 and the SPI pins 10, 11, so
    keep the buttons off those. main.py uses 5, 6 and 13 (header pins 29, 31, 33).

    Presses go into a queue from gpiozero's callback thread; wait() blocks on
    it, so the main loop sleeps instead of polling. Presses made during a slow
    e-paper refresh are kept and handled afterwards.
    """

    def __init__(self, up_pin: int, down_pin: int, select_pin: int,
                 bounce_time: float = 0.05, hold_time: float = 0.7, button_factory=None):
        if button_factory is None:
            from gpiozero import Button as GPIOButton
            button_factory = GPIOButton
        self._events: queue.Queue[Button] = queue.Queue()
        self._buttons = []  # keep references: gpiozero closes a button when it's garbage-collected
        for pin, button in ((up_pin, Button.UP), (down_pin, Button.DOWN)):
            gpio_button = button_factory(pin, bounce_time=bounce_time)
            gpio_button.when_pressed = lambda b=button: self._events.put(b)
            self._buttons.append(gpio_button)

        # Select tells a tap from a hold: held for hold_time -> SELECT_LONG at that
        # moment; released earlier -> SELECT (so a tap is reported on release).
        select = button_factory(select_pin, bounce_time=bounce_time, hold_time=hold_time)
        self._select_held = False
        select.when_held = self._on_select_held
        select.when_released = self._on_select_released
        self._buttons.append(select)

    def _on_select_held(self) -> None:
        self._select_held = True
        self._events.put(Button.SELECT_LONG)

    def _on_select_released(self) -> None:
        if self._select_held:
            self._select_held = False  # the hold was already reported
        else:
            self._events.put(Button.SELECT)

    def wait(self) -> Button:
        return self._events.get()
