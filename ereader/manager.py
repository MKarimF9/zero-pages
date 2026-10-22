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

from .book import LazyBook
from .cache import forget_book
from .cards import CardStore
from .epub import Epub
from .fonts import Fonts
from .library import BookIndex
from .state import ReadingState

MOVES = ("up", "down", "top")


class BookManager:
    def __init__(self, books_dir: str | Path, state: ReadingState, index: BookIndex | None = None,
                 layout: tuple[Fonts, int, int, int] | None = None, cards: CardStore | None = None):
        """layout = (fonts, page width, page height, bottom margin) as the reader lays pages out;
        needed only to move your place (set_position)."""
        self.books_dir = Path(books_dir).expanduser()
        self.state = state
        self.layout = layout
        self.cards = cards or CardStore(state.path.parent / "cards.json")   # flash cards, edited from the same page
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
                custom = state.get_title(key)
                books.append({
                    "name": info.path.name, "title": custom or info.title, "author": info.author,
                    "renamed": bool(custom), "original": info.title,
                    "size": info.path.stat().st_size, "status": status,
                    "progress": round(state.get_progress(key) * 100) if status != "new" else 0,
                    "chapter": state.get_chapter(key) if status != "new" else "",
                    "category": state.get_category(key), "pinned": state.is_pinned(key),
                    "queued": key in queue, "queue_position": queue.index(key) + 1 if key in queue else 0,
                })
            return {"books": books, "categories": state.categories()}

    def update(self, name: str, changes: dict) -> None:
        """Apply {title: str, category: str, queued: bool, pinned: bool, move: "up"|"down"|"top"}.
        An empty title goes back to the name inside the book.
        Raises KeyError for an unknown book and ValueError for a bad value; nothing is
        changed unless every value is good."""
        key = str(self._path(name))
        allowed = {"title", "category", "queued", "pinned", "move"}
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise ValueError("Nothing to change.")
        if "title" in changes and not isinstance(changes["title"], str):
            raise ValueError("The name must be text.")
        if "category" in changes and not isinstance(changes["category"], str):
            raise ValueError("The category must be text.")
        for flag in ("queued", "pinned"):
            if flag in changes and not isinstance(changes[flag], bool):
                raise ValueError(f"{flag} must be true or false.")
        if "move" in changes and changes["move"] not in MOVES:
            raise ValueError("move must be up, down or top.")
        with self._lock:
            state = self.state
            if "title" in changes:
                state.set_title(key, changes["title"])
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

    def chapters(self, name: str) -> list[dict]:
        """The book's contents list: [{i, title, level}], i being its index for set_position."""
        path = self._path(name)
        epub = Epub(path)
        try:
            epub.open()
            return [{"i": i, "title": e.title, "level": e.level} for i, e in enumerate(epub.toc)]
        except Exception:
            return []
        finally:
            epub.close()

    def set_position(self, name: str, where: dict) -> dict:
        """Move your place in a book: {chapter: index into chapters()} or {percent: 0..100}.
        The book then continues from there, and counts as the one you last read."""
        path = self._path(name)
        if not self.layout:
            raise ValueError("This reader can't move your place.")
        if not isinstance(where, dict) or len(where) != 1 or not set(where) <= {"chapter", "percent"}:
            raise ValueError("Give a chapter or a percent.")
        fonts, width, height, bottom = self.layout
        with self._lock:
            try:
                book = LazyBook(path, fonts, width, height, bottom_margin=bottom,
                                cache_dir=self.state.path.parent)
            except Exception as e:
                raise ValueError(f"Couldn't open the book: {e}") from e
            try:
                if "chapter" in where:
                    i = where["chapter"]
                    if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(book.epub.toc):
                        raise ValueError("No such chapter.")
                    book.goto_entry(i)
                else:
                    pct = where["percent"]
                    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
                        raise ValueError("The percent must be between 0 and 100.")
                    book.goto_fraction(pct / 100)
                if book.empty:
                    raise ValueError("That book has no pages to show.")
                section, page = book.position
                self.state.set_position(str(path), section, page, book.progress, book.chapter_title, book.chunk)
                return {"progress": round(book.progress * 100), "chapter": book.chapter_title}
            finally:
                book.close()
