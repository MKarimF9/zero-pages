"""Flash cards and quotes: what is stored, and when a card is due again.

A card has a front, an optional back, an optional source ("Sahih Bukhari 1"), and
belongs to a deck. A quote is a card with no back. The cards live in one JSON file
next to the reading state, written the same safe way (temp file, then swap).

Review uses Leitner boxes: a card you know moves up a box and comes back later; a
card you forgot goes back to box 0 and comes back at once. No clock tricks beyond that.
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from .images import prepare_picture

MAX_CARDS = 5000
MAX_FRONT, MAX_BACK, MAX_SOURCE, MAX_DECK = 1000, 2000, 200, 40
BOX_DAYS = (0, 1, 3, 7, 14, 30)   # days until a card in each box comes back
GRACE = 4 * 3600                  # a card due "tomorrow" is due from 4 hours early, so a daily habit works
FIELDS = ("deck", "front", "back", "source")
SIDES = {"front": "image", "back": "back_image"}   # which side a picture belongs to: the card's flag for it


def plain(text: str) -> str:
    """Anki fields hold a little HTML: line breaks and tags become plain text."""
    text = re.sub(r"(?i)<br\s*/?>|</div>|</p>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def clean_text(text: str, limit: int) -> str:
    """Trim each line, keep line breaks (at most one blank line in a row), cut to `limit`."""
    lines, blank = [], False
    for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(line.split())
        if line:
            lines.append(line)
            blank = False
        elif lines and not blank:
            lines.append("")
            blank = True
    return "\n".join(lines).strip()[:limit].strip()


class CardStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()   # the reader and the web page both use one store
        self.images_dir = self.path.parent / "card-images"   # <card id>-front.png, <card id>-back.png
        self._cards: list[dict] = []
        try:
            data = json.loads(self.path.read_text())
            self._cards = [c for c in data.get("cards", []) if isinstance(c, dict) and c.get("id")]
        except (FileNotFoundError, json.JSONDecodeError, AttributeError, OSError):
            pass

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"cards": self._cards}, ensure_ascii=False))
            os.replace(tmp, self.path)   # atomic, like ReadingState.save
        except OSError:
            pass   # a read-only card or full disk shouldn't crash the reader

    # ---- reading

    def all(self) -> list[dict]:
        with self._lock:
            return [dict(c) for c in self._cards]

    def get(self, card_id: str) -> dict | None:
        with self._lock:
            return next((dict(c) for c in self._cards if c["id"] == card_id), None)

    def decks(self, now: float | None = None) -> list[dict]:
        """[{name, total, due}] alphabetically."""
        now = time.time() if now is None else now
        with self._lock:
            table: dict[str, dict] = {}
            for c in self._cards:
                row = table.setdefault(c["deck"], {"name": c["deck"], "total": 0, "due": 0})
                row["total"] += 1
                row["due"] += c.get("due", 0) <= now
            return sorted(table.values(), key=lambda r: r["name"].casefold())

    def deck_cards(self, deck: str) -> list[dict]:
        """Every card of a deck, oldest first."""
        with self._lock:
            return [dict(c) for c in self._cards if c["deck"] == deck]

    def due(self, deck: str, now: float | None = None) -> list[dict]:
        """Cards of a deck that are due, the most overdue first."""
        now = time.time() if now is None else now
        with self._lock:
            return sorted((dict(c) for c in self._cards if c["deck"] == deck and c.get("due", 0) <= now),
                          key=lambda c: c.get("due", 0))

    # ---- changing

    def _deck_name(self, deck: str) -> str:
        deck = " ".join(str(deck).split())[:MAX_DECK]
        if not deck:
            raise ValueError("Give the card a deck.")
        for c in self._cards:   # "quotes" joins the "Quotes" you already have
            if c["deck"].casefold() == deck.casefold():
                return c["deck"]
        return deck

    def _checked(self, fields: dict) -> dict:
        """The cleaned values of the fields given, or ValueError. Nothing is stored here."""
        out = {}
        for key, value in fields.items():
            if key not in FIELDS:
                raise ValueError("Nothing to change.")
            if not isinstance(value, str):
                raise ValueError(f"The {key} must be text.")
            out[key] = value
        if "deck" in out:
            out["deck"] = self._deck_name(out["deck"])
        if "front" in out:
            out["front"] = clean_text(out["front"], MAX_FRONT)
            if not out["front"]:
                raise ValueError("The front of the card is empty.")
        if "back" in out:
            out["back"] = clean_text(out["back"], MAX_BACK)
        if "source" in out:
            out["source"] = " ".join(out["source"].split())[:MAX_SOURCE]
        return out

    def add(self, deck: str, front: str, back: str = "", source: str = "") -> dict:
        with self._lock:
            if len(self._cards) >= MAX_CARDS:
                raise ValueError(f"That is the most cards the reader keeps ({MAX_CARDS}).")
            card = self._checked({"deck": deck, "front": front, "back": back, "source": source})
            card.update(id=secrets.token_hex(4), box=0, due=0, added=time.time())
            self._cards.append(card)
            self._save()
            return dict(card)

    def update(self, card_id: str, changes: dict) -> None:
        """Change deck, front, back and/or source. KeyError for an unknown card, ValueError
        for a bad value; nothing changes unless every value is good."""
        with self._lock:
            card = next((c for c in self._cards if c["id"] == card_id), None)
            if card is None:
                raise KeyError(card_id)
            if not isinstance(changes, dict) or not changes:
                raise ValueError("Nothing to change.")
            card.update(self._checked(changes))
            self._save()

    def delete(self, card_id: str) -> None:
        with self._lock:
            before = len(self._cards)
            self._cards = [c for c in self._cards if c["id"] != card_id]
            if len(self._cards) == before:
                raise KeyError(card_id)
            self._save()
            self._remove_files(card_id)

    def delete_deck(self, deck: str) -> int:
        with self._lock:
            gone = [c["id"] for c in self._cards if c["deck"] == deck]
            self._cards = [c for c in self._cards if c["deck"] != deck]
            self._save()
            for card_id in gone:
                self._remove_files(card_id)
            return len(gone)

    # ---- pictures

    def picture_path(self, card: dict, side: str) -> Path | None:
        """The kept picture of one side ("front" or "back") of a card, if it has one."""
        if side in SIDES and card.get(SIDES[side]):
            path = self.images_dir / f"{card['id']}-{side}.png"
            return path if path.is_file() else None
        return None

    def _remove_files(self, card_id: str) -> None:
        for side in SIDES:
            (self.images_dir / f"{card_id}-{side}.png").unlink(missing_ok=True)

    def set_picture(self, card_id: str, side: str, data: bytes) -> None:
        """Keep a picture (the bytes of a PNG, JPEG, GIF, WebP or BMP) on one side of a card,
        replacing the old one. KeyError for an unknown card, ValueError for a bad side or picture."""
        if side not in SIDES:
            raise ValueError("The side must be front or back.")
        with self._lock:
            card = next((c for c in self._cards if c["id"] == card_id), None)
            if card is None:
                raise KeyError(card_id)
            gray = prepare_picture(data)
            try:
                self.images_dir.mkdir(parents=True, exist_ok=True)
                tmp = self.images_dir / f".{card_id}-{side}.tmp"
                gray.save(tmp, format="PNG", optimize=True)
                os.replace(tmp, self.images_dir / f"{card_id}-{side}.png")   # whole or not at all
            except OSError as e:
                raise ValueError(f"Couldn't save the picture: {e.strerror or e}") from e
            card[SIDES[side]] = True
            self._save()

    def remove_picture(self, card_id: str, side: str) -> None:
        if side not in SIDES:
            raise ValueError("The side must be front or back.")
        with self._lock:
            card = next((c for c in self._cards if c["id"] == card_id), None)
            if card is None:
                raise KeyError(card_id)
            card.pop(SIDES[side], None)
            (self.images_dir / f"{card_id}-{side}.png").unlink(missing_ok=True)
            self._save()

    def review(self, card_id: str, good: bool, now: float | None = None) -> None:
        """Record an answer: a known card moves up a box, a forgotten one goes back to box 0."""
        now = time.time() if now is None else now
        with self._lock:
            card = next((c for c in self._cards if c["id"] == card_id), None)
            if card is None:
                return   # deleted from the phone while you were reviewing it
            if good:
                card["box"] = min(card.get("box", 0) + 1, len(BOX_DAYS) - 1)
                card["due"] = now + BOX_DAYS[card["box"]] * 86400 - GRACE
            else:
                card["box"], card["due"] = 0, now
            self._save()

    def import_text(self, deck: str, text: str) -> int:
        """Add one card per line: front TAB back TAB source (back and source optional). Anki's
        "Notes in plain text" export has this shape. Returns how many cards were added; a bad
        line stops the import with ValueError (the cards before it are kept)."""
        added = 0
        with self._lock:
            for number, line in enumerate(str(text).replace("\r\n", "\n").split("\n"), 1):
                if not line.strip() or line.startswith("#"):   # Anki writes "#separator:tab" etc. first
                    continue
                parts = [plain(p).strip() for p in line.split("\t")]
                try:
                    self.add(deck, parts[0], parts[1] if len(parts) > 1 else "",
                             parts[2] if len(parts) > 2 else "")
                except ValueError as e:
                    raise ValueError(f"Line {number}: {e} ({added} cards were added before it.)") from e
                added += 1
        return added
