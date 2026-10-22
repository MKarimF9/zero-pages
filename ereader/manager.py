"""Managing the library from your phone: what the web page lists and changes.

The HTTP side lives in upload.py. This file only knows books, the reading state and
the cache, so it can be tested without a web server.

A book is named by its file name in the books folder (the same name the upload page
uses). Everything remembered about it (place, category, Up next, pin) is keyed in the
reading state by its full path, as the reader screens do.
"""
from __future__ import annotations

import threading
from pathlib import Path

from .cache import forget_book
from .library import BookIndex
from .state import ReadingState

MOVES = ("up", "down", "top")


class BookManager:
    def __init__(self, books_dir: str | Path, state: ReadingState, index: BookIndex | None = None):
        self.books_dir = Path(books_dir).expanduser()
        self.state = state
        self.index = index or BookIndex(state.path.parent / "library.json")
        self._lock = threading.Lock()   # web requests run in several threads

    def _path(self, name: str) -> Path:
        path = self.books_dir / name
        if not path.is_file():
            raise KeyError(name)
        return path

    def library(self) -> dict:
        """Every book with its status, plus the categories in use."""
        with self._lock:
            state = self.state
            queue = state.get_queue()
            books = []
            for info in self.index.scan(self.books_dir):
                key = str(info.path)
                status = state.status(key)
                books.append({
                    "name": info.path.name, "title": info.title, "author": info.author,
                    "size": info.path.stat().st_size, "status": status,
                    "progress": round(state.get_progress(key) * 100) if status != "new" else 0,
                    "chapter": state.get_chapter(key) if status != "new" else "",
                    "category": state.get_category(key), "pinned": state.is_pinned(key),
                    "queued": key in queue, "queue_position": queue.index(key) + 1 if key in queue else 0,
                })
            return {"books": books, "categories": state.categories()}

    def update(self, name: str, changes: dict) -> None:
        """Apply {category: str, queued: bool, pinned: bool, move: "up"|"down"|"top"}.
        Raises KeyError for an unknown book and ValueError for a bad value; nothing is
        changed unless every value is good."""
        key = str(self._path(name))
        allowed = {"category", "queued", "pinned", "move"}
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise ValueError("Nothing to change.")
        if "category" in changes and not isinstance(changes["category"], str):
            raise ValueError("The category must be text.")
        for flag in ("queued", "pinned"):
            if flag in changes and not isinstance(changes[flag], bool):
                raise ValueError(f"{flag} must be true or false.")
        if "move" in changes and changes["move"] not in MOVES:
            raise ValueError("move must be up, down or top.")
        with self._lock:
            state = self.state
            if "category" in changes:
                state.set_category(key, changes["category"])
            if "queued" in changes:
                state.set_queued(key, changes["queued"])
            if "pinned" in changes:
                state.set_pinned(key, changes["pinned"])
            if "move" in changes:
                state.move_queued(key, changes["move"])

    def delete(self, name: str) -> None:
        """Remove the book file and everything remembered about it."""
        path = self._path(name)
        with self._lock:
            path.unlink()
            self.state.forget(str(path))
            forget_book(self.state.path.parent, path)
