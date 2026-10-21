"""Milestone 6 logic, tested with fake hardware. Run: python3 -m pytest tests/test_hardware.py -v"""
import time

import pytest
from PIL import Image

from ereader.controls import Button, GPIOControls
from ereader.display import EPaperDisplay


class FakeEPD:
    width, height = 400, 300

    def __init__(self):
        self.calls = []

    def init(self): self.calls.append("init")
    def display(self, buf): self.calls.append(("display", len(buf)))
    def display_Partial(self, buf): self.calls.append(("partial", len(buf)))
    def sleep(self): self.calls.append("sleep")


class FakeDriver:
    def __init__(self): self.epd = FakeEPD()
    def EPD(self): return self.epd


def image(black_at=None):
    img = Image.new("1", (400, 300), 1)
    if black_at:
        img.putpixel(black_at, 0)
    return img


def make(idle_sleep=0):
    driver = FakeDriver()
    return EPaperDisplay(driver, idle_sleep=idle_sleep), driver.epd


def test_first_frame_is_full_even_if_partial_requested():
    d, epd = make()
    d.show(image(), full_refresh=False)
    assert epd.calls == ["init", ("display", 400 * 300 // 8)]


def test_partial_after_full():
    d, epd = make()
    d.show(image(), True)
    d.show(image(), False)
    assert epd.calls[-1] == ("partial", 15000)
    assert epd.calls.count("init") == 1


def test_full_refresh_reinitializes():
    d, epd = make()
    d.show(image(), True)
    d.show(image(), True)
    assert epd.calls.count("init") == 2


def test_sleep_then_next_frame_is_full():
    d, epd = make()
    d.show(image(), True)
    d.sleep()
    d.sleep()  # idempotent: the panel is only put to sleep once
    d.show(image(), False)
    assert epd.calls == ["init", ("display", 15000), "sleep", "init", ("display", 15000)]


def test_buffer_matches_panel_format():
    class Spy(FakeEPD):
        def display(self, buf): self.buf = buf
    driver = FakeDriver(); driver.epd = Spy()
    EPaperDisplay(driver, idle_sleep=0).show(image(black_at=(0, 0)), True)
    assert driver.epd.buf[0] == 0x7F and set(driver.epd.buf[1:]) == {0xFF}  # MSB first, 0 = black


def test_wrong_size_rejected():
    d, _ = make()
    with pytest.raises(ValueError):
        d.show(Image.new("1", (800, 480), 1), True)


def test_idle_timer_sleeps_panel():
    d, epd = make(idle_sleep=0.05)
    d.show(image(), True)
    time.sleep(0.3)
    assert epd.calls[-1] == "sleep"
    d.show(image(), False)           # woke from sleep, so this must be a full refresh
    assert epd.calls[-2:] == ["init", ("display", 15000)]


def test_new_frame_cancels_idle_sleep():
    d, epd = make(idle_sleep=0.2)
    d.show(image(), True)
    time.sleep(0.12)
    d.show(image(), False)           # restarts the countdown
    time.sleep(0.12)                 # 0.24s after the first frame, but only 0.12s after the last
    assert "sleep" not in epd.calls


# ---------------------------------------------------------------- buttons

class FakeGPIOButton:
    def __init__(self, pin, bounce_time, hold_time=None):
        self.pin, self.bounce_time, self.hold_time = pin, bounce_time, hold_time
        self.when_pressed = self.when_released = self.when_held = None


def make_controls():
    made = []
    def factory(pin, **kwargs):
        made.append(FakeGPIOButton(pin, **kwargs))
        return made[-1]
    return GPIOControls(5, 6, 13, button_factory=factory), made


def test_gpio_buttons_map_to_events():
    controls, made = make_controls()
    up, down, select = made
    assert [b.pin for b in made] == [5, 6, 13] and all(b.bounce_time > 0 for b in made)
    down.when_pressed()
    up.when_pressed()
    select.when_released()   # a tap: select is reported on release
    assert [controls.wait() for _ in range(3)] == [Button.DOWN, Button.UP, Button.SELECT]


def test_holding_select_is_a_long_press_and_not_also_a_tap():
    controls, made = make_controls()
    select = made[2]
    assert select.hold_time and select.hold_time > 0.3
    select.when_held()       # held long enough: reported immediately
    select.when_released()   # letting go afterwards must not add a SELECT
    select.when_released()   # ...but the next ordinary tap still works
    assert controls.wait() == Button.SELECT_LONG and controls.wait() == Button.SELECT
    assert controls._events.empty()


def test_keyboard_m_is_hold_select():
    from ereader.controls import KeyboardControls
    assert KeyboardControls.KEYMAP["m"] is Button.SELECT_LONG
