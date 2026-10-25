"""Press the reader's buttons from another process, e.g. an SSH session (remote.py).

The running reader listens on a Unix socket in ~/.pi-ereader/. Only your own user can
connect to it. A client sends one button name per line: UP, DOWN, SELECT or SELECT_LONG.
Quitting and switching off are not available this way.
"""
from __future__ import annotations

import os
import queue
import socket
import threading
from pathlib import Path

from .controls import Button, Controls

ALLOWED = {"UP": Button.UP, "DOWN": Button.DOWN, "SELECT": Button.SELECT, "SELECT_LONG": Button.SELECT_LONG}


def control_socket_path() -> Path:
    return Path.home() / ".pi-ereader" / "control.sock"


class SocketControls(Controls):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._events: queue.Queue[Button] = queue.Queue()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)   # left behind by a reader that was killed
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)         # the socket file is created for the owner only
        try:
            self._server.bind(str(self.path))
        finally:
            os.umask(old_umask)
        self._server.listen(4)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return   # closed
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn, conn.makefile("r", errors="ignore") as lines:
            while line := lines.readline(64):   # a line is never longer than a button name
                button = ALLOWED.get(line.strip().upper())
                if button:
                    self._events.put(button)

    def wait(self) -> Button:
        return self._events.get()

    def close(self) -> None:
        self._server.close()
        self.path.unlink(missing_ok=True)


def send(buttons: list[Button], path: str | Path | None = None) -> None:
    """Press buttons on the running reader. FileNotFoundError / ConnectionRefusedError
    if the reader isn't running."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect(str(path or control_socket_path()))
        s.sendall("".join(b.name + "\n" for b in buttons).encode())
