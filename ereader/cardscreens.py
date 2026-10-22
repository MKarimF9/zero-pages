"""The reader's side of flash cards and quotes: pick a deck, review it, or browse it.

Cards are added from the phone page (upload.py). Three buttons are enough:
    deck list    up/down move, select opens a deck
    deck         Review (cards that are due), Browse all, Back
    review       select shows the answer; then up = forgot, down = got it
    browse       up/down page through every card; select shows the answer
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from .blocks import Style, Word
from .controls import Button
from .fonts import Fonts, has_arabic
from .layout import _place_line, break_lines, is_rtl
from .screens import (AppContext, LibraryScreen, Screen, _draw_menu_rows, _move, blank, card_store,
                      draw_text, list_frame, menu_window)
from .ui import BLACK, PAD

SIZES = (28, 26, 24, 22, 20, 18, 16, 14)   # card text: the biggest of these that fits
GAP = 10                                    # space around the line between front and back


# ------------------------------------------------------------------ drawing a card

def _lines(fonts: Fonts, text: str, width: int) -> list[tuple[list[Word], bool, bool]]:
    """Wrap text: (words, right-to-left?, has Arabic?) per line. A blank line is ([], False, False)."""
    out = []
    for paragraph in text.split("\n"):
        words = [Word(w) for w in paragraph.split()]
        if not words:
            out.append(([], False, False))
            continue
        rtl = is_rtl(words)
        for line in break_lines(words, fonts, width):
            out.append((line, rtl, any(has_arabic(w.text) for w in line)))
    return out


def _height(fonts: Fonts, lines: list) -> int:
    return sum((fonts.line_height + (fonts.arabic_pad if arabic else 0)) if words else fonts.line_height // 2
               for words, _, arabic in lines)


def _fit(fonts: Fonts, texts: list[str], width: int, height: int) -> tuple[Fonts, list[list]]:
    """The biggest size at which every text fits in `height` (together), and their lines. At the
    smallest size, text that still doesn't fit is cut with "…"."""
    base = fonts
    for size in SIZES:
        f = base.scaled(size)
        laid = [_lines(f, t, width) for t in texts]
        if sum(_height(f, lines) for lines in laid) <= height:
            return f, laid
    f = base.scaled(SIZES[-1])
    laid = [_lines(f, t, width) for t in texts]
    room = height
    for lines in laid:
        share = max(1, room * len(lines) // max(1, sum(len(x) for x in laid)))   # a fair share of the room
        while len(lines) > 1 and _height(f, lines) > share:
            lines.pop()
            lines[-1][0].append(Word("…"))
        room -= _height(f, lines)
    return f, laid


def _draw_lines(d: ImageDraw.ImageDraw, f: Fonts, lines: list, left: int, right: int, y: int) -> int:
    """Draw wrapped lines (Arabic flush right, in word order) from y; returns the y below them."""
    for words, rtl, arabic in lines:
        if not words:
            y += f.line_height // 2
            continue
        pad = f.arabic_pad if arabic else 0
        for placed in _place_line(words, f, y + pad * 2 // 3, left, right, rtl):
            d.text((placed.x, placed.y + f.baseline), placed.word.text, font=f.get(placed.word.style, placed.word.text),
                   fill=BLACK, anchor="ls")
        y += f.line_height + pad
    return y


def draw_card(ctx: AppContext, d: ImageDraw.ImageDraw, card: dict, top: int, bottom: int, revealed: bool) -> None:
    """The front of a card; with `revealed`, a line and the back under it. A card with no back
    is just its front (a quote), with the source under it."""
    left, right = PAD, ctx.width - PAD
    two_sided = bool(card.get("back"))
    show_back = revealed and two_sided
    source = card.get("source", "") if (show_back or not two_sided) else ""
    src_h = ctx.small.line_height + 6 if source else 0
    texts = [card["front"]] + ([card["back"]] if show_back else [])
    room = bottom - top - src_h - (2 * GAP + 1 if show_back else 0) - 8
    f, laid = _fit(ctx.fonts, texts, right - left, room)
    total = sum(_height(f, lines) for lines in laid) + (2 * GAP + 1 if show_back else 0)
    y = top + 4 + max(0, (bottom - top - src_h - 8 - total) // 2)   # centered between the bars
    y = _draw_lines(d, f, laid[0], left, right, y)
    if show_back:
        y += GAP
        d.line((left, y, right, y), fill=BLACK)
        y = _draw_lines(d, f, laid[1], left, right, y + GAP + 1)
    if source:
        draw_text(d, ctx.small, source, left, right, bottom - src_h / 2 - 2, align="auto")


# ------------------------------------------------------------------ screens

class DeckListScreen(Screen):
    """Your decks, each with how many cards are due."""

    def __init__(self, ctx: AppContext):
        self.ctx, self.cursor, self.offset = ctx, 0, 0
        self.decks = card_store(ctx).decks()
        self.full_refresh = True

    def _rows(self) -> list[tuple[str, str, str]]:
        rows = [("library_books", d["name"], f"{d['due']} due" if d["due"] else str(d["total"])) for d in self.decks]
        return rows + [("arrow_back", "Back to library", "")]

    def render(self) -> Image.Image:
        ctx = self.ctx
        img = blank(ctx)
        d, top, _ = list_frame(img, ctx.ui_fonts, "Cards", icon="library_books")
        rows = self._rows()
        self.offset = menu_window(ctx, len(rows), self.cursor, self.offset)
        _draw_menu_rows(ctx, d, top, rows, self.cursor, self.offset)
        if not self.decks:
            draw_text(d, ctx.small, "No cards yet.", PAD, ctx.width - PAD, top + 80, align="left")
            draw_text(d, ctx.small, "Add them from your phone: Manage books over Wi-Fi,", PAD, ctx.width - PAD,
                      top + 104, align="left")
            draw_text(d, ctx.small, "then the Cards tab.", PAD, ctx.width - PAD, top + 126, align="left")
        return img

    def handle(self, button) -> Screen | None:
        rows = self._rows()
        if button is not Button.SELECT:
            self.cursor = _move(self.cursor, len(rows), button)
            self.full_refresh = False
            return None
        if self.cursor < len(self.decks):
            return DeckScreen(self.ctx, self.decks[self.cursor]["name"])
        return LibraryScreen(self.ctx)


class DeckScreen(Screen):
    """One deck: review what is due, browse everything, or go back."""

    def __init__(self, ctx: AppContext, deck: str):
        self.ctx, self.deck, self.cursor = ctx, deck, 0
        self.full_refresh = True

    def _rows(self) -> list[tuple[str, str, str]]:
        store = card_store(self.ctx)
        due, total = len(store.due(self.deck)), len(store.deck_cards(self.deck))
        return [("play_arrow", "Review", f"{due} due" if due else "none due"),
                ("menu_book", "Browse all", str(total)),
                ("arrow_back", "Back to decks", "")]

    def render(self) -> Image.Image:
        img = blank(self.ctx)
        d, top, _ = list_frame(img, self.ctx.ui_fonts, self.deck, icon="library_books")
        _draw_menu_rows(self.ctx, d, top, self._rows(), self.cursor)
        return img

    def handle(self, button) -> Screen | None:
        if button is not Button.SELECT:
            self.cursor = _move(self.cursor, 3, button)
            self.full_refresh = False
            return None
        if self.cursor == 0:
            return ReviewScreen(self.ctx, self.deck)
        if self.cursor == 1:
            return BrowseScreen(self.ctx, self.deck)
        return DeckListScreen(self.ctx)


class ReviewScreen(Screen):
    """The cards of a deck that are due, one at a time. A forgotten card comes back before you finish."""

    def __init__(self, ctx: AppContext, deck: str):
        self.ctx, self.deck = ctx, deck
        self.store = card_store(ctx)
        self.queue = [c["id"] for c in self.store.due(deck)]
        self.done = 0           # cards answered "got it"
        self.revealed = False
        self.full_refresh = True

    def _card(self) -> dict | None:
        """The card in front of you; one deleted from the phone meanwhile is skipped."""
        while self.queue:
            card = self.store.get(self.queue[0])
            if card:
                return card
            self.queue.pop(0)
        return None

    def render(self) -> Image.Image:
        ctx = self.ctx
        card = self._card()
        img = blank(ctx)
        if card is None:
            d, top, bottom = list_frame(img, ctx.ui_fonts, self.deck, icon="library_books",
                                        labels=("", "Done", ""))
            text = f"{self.done} cards done." if self.done else "Nothing is due right now."
            draw_text(d, ctx.ui_fonts, "All done" if self.done else "Nothing due", PAD, ctx.width - PAD,
                      (top + bottom) / 2 - 14, Style.BOLD, align="center")
            draw_text(d, ctx.small, text, PAD, ctx.width - PAD, (top + bottom) / 2 + 14, align="center")
            return img
        two_sided = bool(card.get("back"))
        labels = (("Forgot", "Hide", "Got it") if self.revealed else
                  ("Exit", "Show answer", "Skip")) if two_sided else ("Exit", "Next", "Skip")
        d, top, bottom = list_frame(img, ctx.ui_fonts, self.deck, f"{len(self.queue)} left",
                                    labels=labels, icon="library_books", hint_fonts=ctx.small)
        draw_card(ctx, d, card, top, bottom, self.revealed)
        return img

    def handle(self, button) -> Screen | None:
        card = self._card()
        if card is None:
            return DeckScreen(self.ctx, self.deck)
        self.full_refresh = False
        two_sided = bool(card.get("back"))
        if button is Button.SELECT:
            if not two_sided:   # a quote: seen
                self.store.review(card["id"], True)
                self.queue.pop(0)
                self.done += 1
            else:
                self.revealed = not self.revealed
        elif button is Button.UP:
            if self.revealed and two_sided:
                self.store.review(card["id"], False)   # forgot: it comes round again at the end
                self.queue.append(self.queue.pop(0))
                self.revealed = False
            else:
                return DeckScreen(self.ctx, self.deck)
        elif button is Button.DOWN:
            if self.revealed and two_sided:
                self.store.review(card["id"], True)
                self.queue.pop(0)
                self.done += 1
            else:                                       # skip: later, without recording anything
                self.queue.append(self.queue.pop(0))
            self.revealed = False
        return None


class BrowseScreen(Screen):
    """Every card of a deck in order. After the last one comes an "End" page; select leaves."""

    def __init__(self, ctx: AppContext, deck: str):
        self.ctx, self.deck = ctx, deck
        self.store = card_store(ctx)
        self.cards = self.store.deck_cards(deck)
        self.index, self.revealed = 0, False
        self.full_refresh = True

    def render(self) -> Image.Image:
        ctx = self.ctx
        img = blank(ctx)
        n = len(self.cards)
        if self.index >= n:   # the End page (also what an empty deck shows)
            d, top, bottom = list_frame(img, ctx.ui_fonts, self.deck, icon="library_books",
                                        labels=("Previous", "Back", "First"), hint_fonts=ctx.small)
            draw_text(d, ctx.ui_fonts, "End of deck" if n else "No cards", PAD, ctx.width - PAD,
                      (top + bottom) / 2, Style.BOLD, align="center")
            return img
        card = self.cards[self.index]
        label = ("Hide" if self.revealed else "Show answer") if card.get("back") else "Back"
        d, top, bottom = list_frame(img, ctx.ui_fonts, self.deck, f"{self.index + 1}/{n}",
                                    labels=("Previous", label, "Next"), icon="library_books", hint_fonts=ctx.small)
        draw_card(ctx, d, card, top, bottom, self.revealed)
        return img

    def handle(self, button) -> Screen | None:
        n = len(self.cards)
        self.full_refresh = False
        if button is Button.SELECT:
            if self.index < n and self.cards[self.index].get("back"):
                self.revealed = not self.revealed
                return None
            return DeckScreen(self.ctx, self.deck)
        self.index = _move(self.index, n + 1, button)   # n + 1 pages: the cards, then "End"
        self.revealed = False
        return None
