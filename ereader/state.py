"""Remembers your page in each book. Complete — no TODOs.

Saved as JSON in ~/.pi-ereader/state.json.

Note the save(): it writes to a temporary file, then swaps it into place
with os.replace(). If power dies mid-write, you keep the OLD file instead
of a half-written broken one. That's the same lesson as the SD card
corruption you hit: never assume a write finishes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PATH = Path("~/.pi-ereader/state.json").expanduser()
FINISHED_AT = 0.98  # progress at which a book counts as finished


class ReadingState:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path).expanduser()
        self._data = {"pages": {}, "positions": {}, "progress": {}, "chapters": {}, "settings": {}, "last_book": None}
        try:
            self._data.update(json.loads(self.path.read_text()))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def get_page(self, book: str) -> int:
        return int(self._data["pages"].get(book, 0))

    def set_page(self, book: str, page: int) -> None:
        self._data["pages"][book] = page
        self._data["last_book"] = book
        self.save()

    def get_position(self, book: str) -> tuple[int, int]:
        """(section, page-in-section) for the lazy reader."""
        section, page = self._data["positions"].get(book, (0, 0))
        return int(section), int(page)

    def set_position(self, book: str, section: int, page: int, progress: float = 0.0,
                     chapter: str = "") -> None:
        self._data["positions"][book] = [section, page]
        self._data["chapters"][book] = chapter
        self._data["progress"][book] = round(progress, 4)
        self._data["last_book"] = book
        self.save()

    def get_progress(self, book: str) -> float:
        """Fraction of the book read (0..1), for the library's progress bars."""
        return float(self._data["progress"].get(book, 0.0))

    def get_chapter(self, book: str) -> str:
        """Title of the chapter you were last in (for the library's Continue card)."""
        return self._data["chapters"].get(book, "")

    def status(self, book: str) -> str:
        """"new" (never opened), "reading" or "finished"."""
        if book not in self._data["positions"]:
            return "new"
        return "finished" if self.get_progress(book) >= FINISHED_AT else "reading"

    def get_setting(self, key: str, default=None):
        return self._data["settings"].get(key, default)

    def set_setting(self, key: str, value) -> None:
        self._data["settings"][key] = value
        self.save()

    @property
    def last_book(self) -> str | None:
        return self._data.get("last_book")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        os.replace(tmp, self.path)  # atomic on Linux
