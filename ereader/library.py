"""The list of books on disk, with their metadata cached.

Opening every EPUB to read its title is fine for one book and slow for fifty
on a Pi Zero, so the result is remembered in a JSON file next to the reading
state. A book is only re-read when its file changes (modification time or
size differs).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .epub import Epub


@dataclass
class BookInfo:
    path: Path
    title: str
    author: str


def read_metadata(path: Path) -> tuple[str, str]:
    """(title, author) from the EPUB itself."""
    epub = Epub(path)
    try:
        epub.open()
        return epub.title, epub.author
    except Exception:
        return path.stem, "(unreadable)"
    finally:
        epub.close()


class BookIndex:
    def __init__(self, cache_file: str | Path, read=read_metadata):
        self.cache_file = Path(cache_file).expanduser()
        self._read = read  # replaceable, so tests can count how often books are opened
        self._cache: dict[str, dict] = {}
        try:
            self._cache = json.loads(self.cache_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def scan(self, books_dir: str | Path) -> list[BookInfo]:
        """All .epub files in books_dir, sorted by file name."""
        books = []
        fresh: dict[str, dict] = {}
        for path in sorted(Path(books_dir).expanduser().glob("*.epub")):
            stat = path.stat()
            entry = self._cache.get(str(path))
            if not (entry and entry.get("mtime") == stat.st_mtime and entry.get("size") == stat.st_size):
                title, author = self._read(path)
                entry = {"mtime": stat.st_mtime, "size": stat.st_size, "title": title, "author": author}
            fresh[str(path)] = entry
            books.append(BookInfo(path, entry["title"], entry["author"]))
        if fresh != self._cache:  # something changed or a book was removed
            self._cache = fresh
            self._save()
        return books

    def _save(self) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache))
            os.replace(tmp, self.cache_file)  # atomic, like ReadingState.save
        except OSError:
            pass  # a cache that can't be written is just a slower library
