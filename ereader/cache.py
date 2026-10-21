"""Where each book's cached covers and pictures live, so they can be removed with the book.

    <cache dir>/covers/<book key>/<thumbnail>.png
    <cache dir>/images/<book key>/<picture>.png
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

KINDS = ("covers", "images")


def book_key(path: str | Path) -> str:
    return hashlib.sha1(str(path).encode()).hexdigest()[:12]


def book_cache_dir(cache_dir: Path, kind: str, path: str | Path) -> Path:
    return cache_dir / kind / book_key(path)


def forget_book(cache_dir: Path, path: str | Path) -> None:
    """Delete every cached cover and picture of a book."""
    for kind in KINDS:
        shutil.rmtree(book_cache_dir(cache_dir, kind, path), ignore_errors=True)
