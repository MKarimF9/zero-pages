"""Milestone 6 logic, tested with fake hardware. Run: python3 -m pytest tests/test_hardware.py -v"""
import time

import pytest
from PIL import Image

from ereader.controls import Button, GPIOControls
from ereader.display import EPaperDisplay
from tests.fakes import FakeDriver, FakeEPD, image


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


def test_later_full_requests_are_fast_refreshes():
    d, epd = make()
    d.show(image(), True)            # the first frame is a true full refresh
    epd.calls.clear()
    d.show(image(), True)            # a screen switch: fast, one init_fast and no plain init
    assert epd.calls == [("init_fast", 0), ("fast", 15000)]


def test_every_sixth_fast_refresh_is_a_true_full_one():
    d, epd = make()
    d.show(image(), True)
    epd.calls.clear()
    for _ in range(EPaperDisplay.FAST_BEFORE_FULL):
        d.show(image(), True)
    assert epd.calls.count("init") == 0 and len(epd.calls) == 2 * EPaperDisplay.FAST_BEFORE_FULL
    epd.calls.clear()
    d.show(image(), True)            # ghosting is wiped now
    assert epd.calls == ["init", ("display", 15000)]
    epd.calls.clear()
    d.show(image(), True)            # and the count starts again
    assert epd.calls == [("init_fast", 0), ("fast", 15000)]


def test_partial_after_fast_resets_the_controller_and_restores_the_old_frame():
    d, epd = make()
    d.show(image(), True)
    d.show(image(), True)            # fast
    epd.calls.clear()
    d.show(image(), False)
    assert epd.calls == ["init", ("command", 0x26), ("data", 15000), ("partial", 15000)]
    epd.calls.clear()
    d.show(image(), False)           # the controller is already set up for partial: nothing extra
    assert epd.calls == [("partial", 15000)]


def test_partial_after_true_full_needs_no_reset():
    d, epd = make()
    d.show(image(), True)
    epd.calls.clear()
    d.show(image(), False)
    assert epd.calls == [("partial", 15000)]


def test_sleep_screen_is_a_true_full_refresh_and_restarts_the_count():
    d, epd = make(idle_sleep=0.05)
    d.set_idle_image(lambda: image(black_at=(1, 1)))
    d.show(image(), True)
    d.show(image(), True)            # fast
    time.sleep(0.3)
    assert epd.calls[-3:] == ["init", ("display", 15000), "sleep"]
    epd.calls.clear()
    d.show(image(), True)            # after the sleep screen the next screen switch is fast again
    assert epd.calls == [("init_fast", 0), ("fast", 15000)]


def test_sleep_then_next_frame_is_fast_not_partial():
    d, epd = make()
    d.show(image(), True)
    d.sleep()
    d.sleep()  # idempotent: the panel is only put to sleep once
    epd.calls.clear()
    d.show(image(), False)
    assert epd.calls == [("init_fast", 0), ("fast", 15000)]


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
    d.show(image(), False)           # woke from sleep, so this can't be a partial refresh
    assert epd.calls[-2:] == [("init_fast", 0), ("fast", 15000)]


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
