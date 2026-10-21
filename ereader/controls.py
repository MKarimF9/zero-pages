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
    QUIT = auto()  # dev only; the real device has no quit button


class Controls(ABC):
    @abstractmethod
    def wait(self) -> Button:
        """Block until a button is pressed, then return it."""


class KeyboardControls(Controls):
    """w/k = up, s/j = down, space/enter = select, q = quit."""

    KEYMAP = {
        "w": Button.UP, "k": Button.UP,
        "s": Button.DOWN, "j": Button.DOWN,
        " ": Button.SELECT, "\r": Button.SELECT, "\n": Button.SELECT,
        "q": Button.QUIT, "\x03": Button.QUIT,  # \x03 = Ctrl+C in raw mode
    }

    def wait(self) -> Button:
        while True:
            ch = self._getch()
            if ch == "":
                return Button.QUIT  # input closed
            if ch in self.KEYMAP:
                return self.KEYMAP[ch]

    @staticmethod
    def _getch() -> str:
        """Read one keypress without waiting for Enter."""
        if not sys.stdin.isatty():
            return sys.stdin.read(1)
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
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
                 bounce_time: float = 0.05, button_factory=None):
        if button_factory is None:
            from gpiozero import Button as GPIOButton
            button_factory = GPIOButton
        self._events: queue.Queue[Button] = queue.Queue()
        self._buttons = []  # keep references: gpiozero closes a button when it's garbage-collected
        for pin, button in ((up_pin, Button.UP), (down_pin, Button.DOWN),
                            (select_pin, Button.SELECT)):
            gpio_button = button_factory(pin, bounce_time=bounce_time)
            gpio_button.when_pressed = lambda b=button: self._events.put(b)
            self._buttons.append(gpio_button)

    def wait(self) -> Button:
        return self._events.get()
