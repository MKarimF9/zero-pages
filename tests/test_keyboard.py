"""Keyboard controls. Run: python3 -m pytest tests/test_keyboard.py -v"""
from ereader.controls import Button as B
from ereader.controls import KeyboardControls


def keys(*chunks: str):
    """A KeyboardControls fed these chunks, one per terminal read; "" = input closed."""
    kb = KeyboardControls()
    feed = iter(chunks)
    kb._read = lambda: next(feed, "")
    return kb


def test_letters_space_and_enter_still_work():
    kb = keys("w", "s", "k", "j", " ", "\r", "\n", "m")
    assert [kb.wait() for _ in range(8)] == [B.UP, B.DOWN, B.UP, B.DOWN, B.SELECT, B.SELECT,
                                              B.SELECT, B.SELECT_LONG]


def test_arrow_keys_in_both_terminal_styles():
    kb = keys("\x1b[A", "\x1b[B", "\x1bOA", "\x1bOB")
    assert [kb.wait() for _ in range(4)] == [B.UP, B.DOWN, B.UP, B.DOWN]


def test_several_keys_in_one_read_are_not_lost():
    kb = keys("ss\x1b[A\x1b[Bw ")   # typed quickly: all arrive together
    assert [kb.wait() for _ in range(6)] == [B.DOWN, B.DOWN, B.UP, B.DOWN, B.UP, B.SELECT]


def test_unknown_keys_and_sequences_are_ignored():
    kb = keys("x", "\x1b[H", "\x1b[5~", "\x1b", "z", "s")   # Home, PageUp, lone Escape...
    assert kb.wait() is B.DOWN                                # ...are skipped, "s" still works


def test_quit_and_closed_input():
    assert keys("q").wait() is B.QUIT
    assert keys("\x03").wait() is B.QUIT                      # Ctrl+C in raw mode
    assert keys().wait() is B.QUIT                            # input closed
