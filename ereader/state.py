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
import time
from pathlib import Path

DEFAULT_PATH = Path("~/.pi-ereader/state.json").expanduser()
FINISHED_AT = 0.98  # progress at which a book counts as finished


class ReadingState:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path).expanduser()
        self._data = {"pages": {}, "positions": {}, "progress": {}, "chapters": {}, "chunks": {}, "opened": {}, "pinned": [], "bookmarks": {}, "categories": {}, "queue": [], "settings": {}, "last_book": None}
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

    def get_chunk(self, book: str) -> int:
        """Which chunk of the saved section you were in (0 for most sections)."""
        return int(self._data["chunks"].get(book, 0))

    def set_position(self, book: str, section: int, page: int, progress: float = 0.0,
                     chapter: str = "", chunk: int = 0) -> None:
        """page counts within the chunk (a long section is read in chunks)."""
        self._data["positions"][book] = [section, page]
        self._data["chunks"][book] = chunk
        self._data["chapters"][book] = chapter
        self._data["opened"][book] = time.time()
        self._data["progress"][book] = round(progress, 4)
        self._data["last_book"] = book
        self.save()

    def get_progress(self, book: str) -> float:
        """Fraction of the book read (0..1), for the library's progress bars."""
        return float(self._data["progress"].get(book, 0.0))

    def get_bookmarks(self, book: str) -> list[dict]:
        """Bookmarks of a book, in reading order. Each is {section, chunk, fraction (of the
        chunk, so it survives a change of font size), chapter, progress}."""
        return [dict(b) for b in self._data.get("bookmarks", {}).get(book, [])]

    def add_bookmark(self, book: str, bookmark: dict) -> None:
        marks = self._data.setdefault("bookmarks", {}).setdefault(book, [])
        marks.append(dict(bookmark))
        marks.sort(key=lambda b: (b["section"], b["chunk"], b["fraction"]))
        self.save()

    def remove_bookmark(self, book: str, bookmark: dict) -> None:
        key = lambda b: (b["section"], b["chunk"], b["fraction"])
        marks = self._data.get("bookmarks", {}).get(book, [])
        marks[:] = [b for b in marks if key(b) != key(bookmark)]
        if not marks:
            self._data.get("bookmarks", {}).pop(book, None)
        self.save()

    def get_pinned(self) -> list[str]:
        """Pinned books, in the order they were pinned."""
        return list(self._data.get("pinned", []))

    def is_pinned(self, book: str) -> bool:
        return book in self._data.get("pinned", [])

    def set_pinned(self, book: str, pinned: bool) -> None:
        books = self._data.setdefault("pinned", [])
        if pinned and book not in books:
            books.append(book)
        elif not pinned and book in books:
            books.remove(book)
        self.save()

    def get_category(self, book: str) -> str:
        """The shelf you put a book on ("Quran", "Hadith"...), or "" for none."""
        return self._data.get("categories", {}).get(book, "")

    def set_category(self, book: str, category: str) -> None:
        category = " ".join(category.split())[:40]
        table = self._data.setdefault("categories", {})
        for existing in table.values():   # "quran" joins the "Quran" you already have
            if existing.casefold() == category.casefold():
                category = existing
                break
        if category:
            table[book] = category
        else:
            table.pop(book, None)
        self.save()

    def categories(self) -> list[str]:
        """Every category in use, alphabetically."""
        return sorted({c for c in self._data.get("categories", {}).values() if c}, key=str.casefold)

    def get_queue(self) -> list[str]:
        """The "Up next" list: books you want to read, in the order you want to read them."""
        return list(self._data.get("queue", []))

    def is_queued(self, book: str) -> bool:
        return book in self._data.get("queue", [])

    def set_queued(self, book: str, queued: bool) -> None:
        queue = self._data.setdefault("queue", [])
        if queued and book not in queue:
            queue.append(book)
        elif not queued and book in queue:
            queue.remove(book)
        self.save()

    def move_queued(self, book: str, direction: str) -> None:
        """Move a book in the Up next list: "up", "down" or "top"."""
        queue = self._data.get("queue", [])
        if book not in queue:
            return
        i = queue.index(book)
        j = {"up": max(i - 1, 0), "down": min(i + 1, len(queue) - 1), "top": 0}[direction]
        queue.insert(j, queue.pop(i))
        self.save()

    def forget(self, book: str) -> None:
        """Remove everything remembered about a book (its place, progress, chapter...)."""
        for table in ("pages", "positions", "progress", "chapters", "chunks", "opened", "bookmarks"):
            self._data.get(table, {}).pop(book, None)
        if self._data.get("last_book") == book:
            self._data["last_book"] = None
        if book in self._data.get("pinned", []):
            self._data["pinned"].remove(book)
        if book in self._data.get("queue", []):
            self._data["queue"].remove(book)
        self._data.get("categories", {}).pop(book, None)
        self.save()

    def get_opened(self, book: str) -> float:
        """When the book was last read (a timestamp); 0 if never."""
        return float(self._data["opened"].get(book, 0.0))

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
