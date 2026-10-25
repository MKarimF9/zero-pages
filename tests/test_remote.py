"""Pressing the reader's buttons over SSH. Run: python3 -m pytest tests/test_remote.py -v"""
import os
import queue
import shutil
import socket
import tempfile
import threading
from pathlib import Path

from ereader.controls import Button as B, CombinedControls, Controls
from ereader.remote import SocketControls, send


def short_dir():
    return Path(tempfile.mkdtemp(prefix="pie", dir="/tmp"))   # a socket path must be short


class Scripted(Controls):
    def __init__(self): self.q = queue.Queue()
    def wait(self): return self.q.get()


def test_buttons_sent_to_the_socket_arrive_in_order():
    d = short_dir()
    try:
        controls = SocketControls(d / "c.sock")
        send([B.DOWN, B.DOWN, B.SELECT, B.SELECT_LONG, B.UP], d / "c.sock")
        assert [controls.wait() for _ in range(5)] == [B.DOWN, B.DOWN, B.SELECT, B.SELECT_LONG, B.UP]
        controls.close()
    finally:
        shutil.rmtree(d)


def test_quit_power_off_and_junk_are_ignored():
    d = short_dir()
    try:
        controls = SocketControls(d / "c.sock")
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(d / "c.sock"))
            s.sendall(b"QUIT\nPOWER_OFF\nnonsense\n" + b"x" * 500 + b"\nUP\n")
        assert controls.wait() == B.UP            # only the real button got through
        assert controls._events.empty()
        controls.close()
    finally:
        shutil.rmtree(d)


def test_only_the_owner_can_connect():
    d = short_dir()
    try:
        controls = SocketControls(d / "c.sock")
        assert (d / "c.sock").stat().st_mode & 0o777 == 0o600
        controls.close()
        assert not (d / "c.sock").exists()        # closing removes the file
    finally:
        shutil.rmtree(d)


def test_a_stale_socket_file_from_a_killed_reader_is_replaced():
    d = short_dir()
    try:
        (d / "c.sock").write_text("")
        controls = SocketControls(d / "c.sock")
        send([B.SELECT], d / "c.sock")
        assert controls.wait() == B.SELECT
        controls.close()
    finally:
        shutil.rmtree(d)


def test_sending_with_no_reader_running_fails_clearly():
    d = short_dir()
    try:
        try:
            send([B.UP], d / "nothing.sock")
        except FileNotFoundError:
            return
        raise AssertionError("expected FileNotFoundError")
    finally:
        shutil.rmtree(d)


def test_combined_controls_take_buttons_from_every_source():
    a, b = Scripted(), Scripted()
    combined = CombinedControls(a, b)
    a.q.put(B.UP)
    assert combined.wait() == B.UP
    b.q.put(B.DOWN)
    assert combined.wait() == B.DOWN
    a.q.put(B.SELECT); b.q.put(B.SELECT_LONG)
    assert sorted(combined.wait().name for _ in range(2)) == ["SELECT", "SELECT_LONG"]
