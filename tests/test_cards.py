"""Flash cards and quotes. Run: python3 -m pytest tests/test_cards.py -v"""
import json

import pytest

from ereader.cards import BOX_DAYS, GRACE, MAX_CARDS, CardStore
from ereader.cardscreens import BrowseScreen, DeckListScreen, DeckScreen, ReviewScreen, draw_card
from ereader.controls import Button as B
from ereader.screens import LibraryOptionsScreen, LibraryScreen, blank, card_store
from tests.test_manager import run
from tests.test_pinned import make_ctx

DAY = 86400


def store(tmp_path):
    return CardStore(tmp_path / "cards.json")


# ---------------------------------------------------------------- the store

def test_add_persists_and_cleans_text(tmp_path):
    s = store(tmp_path)
    c = s.add("  quotes ", "  Be   kind.\r\n\r\n\r\n  Always.  ", "", "  Prophet   ﷺ ")
    again = CardStore(tmp_path / "cards.json").get(c["id"])
    assert again["deck"] == "quotes" and again["front"] == "Be kind.\n\nAlways." and again["source"] == "Prophet ﷺ"
    assert again["back"] == "" and again["box"] == 0
    s.add("QUOTES", "Another")                       # same deck in another case: one deck
    assert [d["name"] for d in s.decks()] == ["quotes"] and s.decks()[0]["total"] == 2


def test_bad_cards_are_refused(tmp_path):
    s = store(tmp_path)
    for args in (("", "front"), ("deck", "   "), ("deck", 5), (None, "front")):
        with pytest.raises(ValueError):
            s.add(*args)
    assert s.all() == []
    long = s.add("d", "x" * 5000, "y" * 5000, "z" * 5000)
    assert (len(long["front"]), len(long["back"]), len(long["source"])) == (1000, 2000, 200)


def test_update_and_delete(tmp_path):
    s = store(tmp_path)
    c = s.add("d", "front", "back")
    s.update(c["id"], {"front": "new front", "source": "src"})
    assert s.get(c["id"])["front"] == "new front" and s.get(c["id"])["source"] == "src"
    with pytest.raises(ValueError):                  # one bad value: nothing changes
        s.update(c["id"], {"front": "changed", "back": 7})
    assert s.get(c["id"])["front"] == "new front"
    for bad in ({}, {"box": 5}, {"front": ""}):
        with pytest.raises(ValueError):
            s.update(c["id"], bad)
    with pytest.raises(KeyError):
        s.update("nope", {"front": "x"})
    s.delete(c["id"])
    with pytest.raises(KeyError):
        s.delete(c["id"])
    assert s.all() == []


def test_delete_deck(tmp_path):
    s = store(tmp_path)
    s.add("a", "1"); s.add("a", "2"); s.add("b", "3")
    assert s.delete_deck("a") == 2 and [d["name"] for d in s.decks()] == ["b"]


def test_card_limit(tmp_path):
    s = store(tmp_path)
    s._cards = [{"id": str(i), "deck": "d", "front": "f"} for i in range(MAX_CARDS)]
    with pytest.raises(ValueError):
        s.add("d", "one more")


def test_a_damaged_file_starts_empty(tmp_path):
    (tmp_path / "cards.json").write_text("{not json")
    assert CardStore(tmp_path / "cards.json").all() == []
    (tmp_path / "cards.json").write_text('{"cards": ["junk", {"deck": "d"}]}')
    assert CardStore(tmp_path / "cards.json").all() == []


def test_leitner_boxes(tmp_path):
    s = store(tmp_path)
    c = s.add("d", "f", "b")["id"]
    assert len(s.due("d", now=100)) == 1             # new cards are due
    s.review(c, True, now=1000)
    assert s.get(c)["box"] == 1 and s.get(c)["due"] == 1000 + DAY * BOX_DAYS[1] - GRACE
    assert s.due("d", now=1001) == [] and len(s.due("d", now=1000 + DAY)) == 1   # a day later (a little early is fine)
    for _ in range(10):
        s.review(c, True, now=1000)
    assert s.get(c)["box"] == len(BOX_DAYS) - 1      # the top box is the last
    s.review(c, False, now=2000)
    assert s.get(c)["box"] == 0 and s.get(c)["due"] == 2000 and len(s.due("d", now=2000)) == 1
    s.review("gone", True)                           # a card deleted meanwhile: ignored


def test_decks_count_due(tmp_path):
    s = store(tmp_path)
    a, b = s.add("d", "1")["id"], s.add("d", "2")["id"]
    s.review(a, True, now=1000)
    assert s.decks(now=1001) == [{"name": "d", "total": 2, "due": 1}]


def test_import_anki_style_text(tmp_path):
    s = store(tmp_path)
    text = ("#separator:tab\n#html:true\n"
            "What is 2+2?<br>Think\t<b>Four</b> &amp; more\tMaths\n"
            "\n"
            "A quote on its own\n"
            "عربي\tكلمة\n")
    assert s.import_text("mixed", text) == 3
    cards = s.deck_cards("mixed")
    assert cards[0]["front"] == "What is 2+2?\nThink" and cards[0]["back"] == "Four & more" and cards[0]["source"] == "Maths"
    assert cards[1]["back"] == "" and cards[2]["front"] == "عربي"
    try:                                             # a line with an empty front stops the import and says where
        s.import_text("mixed", "ok\tfine\n\t\tno front\nnever")
        assert False, "should have refused the empty front"
    except ValueError as e:
        assert "Line 2" in str(e) and "1 cards were added" in str(e)
    assert len(s.deck_cards("mixed")) == 4
    with pytest.raises(ValueError):
        s.import_text("", "x")                       # no deck


# ---------------------------------------------------------------- over HTTP

def post(r, path, obj):
    return r.call("POST", path, json.dumps(obj, ensure_ascii=False).encode())


def test_api_cards_round_trip(tmp_path):
    srv, _, _ = run(tmp_path)
    with srv as r:
        status, body = post(r, "/api/cards/add", {"deck": "Quotes", "front": "كن لطيفا", "back": "Be kind", "source": "x"})
        assert status == 200 and body["ok"]
        cid = body["id"]
        status, got = r.call("GET", "/api/cards")
        assert status == 200 and got["decks"][0]["name"] == "Quotes"
        assert got["cards"] == [{"id": cid, "deck": "Quotes", "front": "كن لطيفا", "back": "Be kind", "source": "x", "due": True,
                                  "image": False, "back_image": False}]
        assert post(r, f"/api/cards/update?id={cid}", {"front": "new"})[0] == 200
        assert r.call("GET", "/api/cards")[1]["cards"][0]["front"] == "new"
        assert r.call("POST", "/api/cards/import?deck=Quotes", "a\tb\nc\td".encode())[1] == {"ok": True, "added": 2}
        assert r.call("POST", f"/api/cards/delete?id={cid}")[0] == 200
        assert r.call("POST", f"/api/cards/delete?id={cid}")[0] == 404
        assert r.call("POST", "/api/cards/delete?deck=Quotes")[1]["deleted"] == 2
        assert r.call("GET", "/api/cards")[1] == {"decks": [], "cards": []}


def test_api_cards_errors_and_pin(tmp_path):
    srv, _, _ = run(tmp_path)
    with srv as r:
        assert r.call("GET", "/api/cards", pin="0000")[0] == 403
        assert r.call("POST", "/api/cards/add", b'{"deck": "d", "front": "f"}', pin="0000")[0] == 403
        assert post(r, "/api/cards/add", {"deck": "d"})[0] == 400                        # no front
        assert post(r, "/api/cards/add", {"deck": "d", "front": "f", "box": 5})[0] == 400  # not a field
        assert post(r, "/api/cards/add", {"deck": "d", "front": 3})[0] == 400
        assert r.call("POST", "/api/cards/add", b"not json")[0] == 400
        assert r.call("POST", "/api/cards/add", b"")[0] == 400
        assert r.call("POST", "/api/cards/add", b"x" * 20000)[0] == 400                  # too large
        assert r.call("POST", "/api/cards/import?deck=d", b"x" * 600000)[0] == 400
        assert r.call("POST", "/api/cards/import?deck=d", b"f\tb")[0] == 200
        assert r.call("POST", "/api/cards/import", b"f\tb")[0] == 400                    # no deck
        assert r.call("POST", "/api/cards/nope", b"{}")[0] == 404
        assert r.call("GET", "/api/cards")[1]["cards"][0]["front"] == "f"


def test_without_a_manager_cards_routes_do_not_exist(tmp_path):
    from tests.test_upload import Running
    with Running(tmp_path) as r:
        assert r.call("GET", "/api/cards")[0] == 404
        assert r.call("POST", "/api/cards/add", b"{}")[0] == 404


def test_page_has_a_cards_tab_and_never_puts_card_text_into_html():
    from ereader.upload import PAGE
    assert 'id="cardsView"' in PAGE and 'id="tabCards"' in PAGE
    assert PAGE.count("innerHTML") == PAGE.count("innerHTML = ''")
    assert "textContent: c.front" in PAGE


# ---------------------------------------------------------------- on the reader

def cards_ctx(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    s = card_store(ctx)
    s.add("Quotes", "Be kind.", "", "Somebody")
    s.add("Quotes", "Another quote")
    s.add("Words", "كتاب", "book", "Arabic 1")
    s.add("Words", "قلم", "pen")
    return ctx, s


def test_the_options_menu_opens_the_deck_list(tmp_path):
    ctx, s = cards_ctx(tmp_path)
    opts = LibraryScreen(ctx).handle(B.SELECT_LONG)
    assert isinstance(opts, LibraryOptionsScreen)
    opts.cursor = [r[0] for r in opts._rows()].index("cards")
    decks = opts.handle(B.SELECT)
    assert isinstance(decks, DeckListScreen) and decks.render().size == (400, 300)
    assert [d["name"] for d in decks.decks] == ["Quotes", "Words"]
    decks.cursor = 2                                   # "Back to library"
    assert isinstance(decks.handle(B.SELECT), LibraryScreen)


def test_empty_deck_list_says_how_to_add_cards(tmp_path):
    ctx, _, _ = make_ctx(tmp_path)
    decks = DeckListScreen(ctx)
    assert decks.render().size == (400, 300) and decks.decks == []
    assert isinstance(decks.handle(B.SELECT), LibraryScreen)          # only "Back" is there


def test_deck_screen_opens_review_and_browse(tmp_path):
    ctx, s = cards_ctx(tmp_path)
    deck = DeckListScreen(ctx).handle(B.SELECT)
    assert isinstance(deck, DeckScreen) and deck.deck == "Quotes" and deck.render().size == (400, 300)
    assert isinstance(deck.handle(B.SELECT), ReviewScreen)
    deck.handle(B.DOWN)
    assert isinstance(deck.handle(B.SELECT), BrowseScreen)
    deck.handle(B.DOWN)
    assert isinstance(deck.handle(B.SELECT), DeckListScreen)


def test_review_two_sided_cards(tmp_path):
    ctx, s = cards_ctx(tmp_path)
    rev = ReviewScreen(ctx, "Words")
    assert len(rev.queue) == 2 and rev.render().size == (400, 300)
    first = rev.queue[0]
    rev.handle(B.SELECT); assert rev.revealed and rev.render().size == (400, 300)
    rev.handle(B.SELECT); assert not rev.revealed                      # select again hides
    rev.handle(B.SELECT)
    rev.handle(B.UP)                                                    # forgot: back to the end, due at once
    assert rev.queue[-1] == first and not rev.revealed and s.get(first)["box"] == 0
    second = rev.queue[0]
    rev.handle(B.SELECT); rev.handle(B.DOWN)                            # got it
    assert second not in rev.queue and s.get(second)["box"] == 1 and rev.done == 1
    rev.handle(B.SELECT); rev.handle(B.DOWN)                            # the forgotten one, now known
    assert rev.queue == [] and rev.done == 2
    assert rev.render().size == (400, 300)                              # the "All done" page
    assert isinstance(rev.handle(B.SELECT), DeckScreen)
    assert len(s.due("Words")) == 0


def test_skip_and_exit_record_nothing(tmp_path):
    ctx, s = cards_ctx(tmp_path)
    rev = ReviewScreen(ctx, "Words")
    first = rev.queue[0]
    rev.handle(B.DOWN)                                                  # not revealed: skip
    assert rev.queue[-1] == first and s.get(first)["box"] == 0 and s.get(first)["due"] == 0
    assert isinstance(rev.handle(B.UP), DeckScreen)                     # not revealed: exit


def test_review_a_quote_counts_as_seen(tmp_path):
    ctx, s = cards_ctx(tmp_path)
    rev = ReviewScreen(ctx, "Quotes")
    quote = rev.queue[0]
    assert rev.render().size == (400, 300)
    rev.handle(B.SELECT)
    assert quote not in rev.queue and s.get(quote)["box"] == 1


def test_nothing_due_is_said(tmp_path):
    ctx, s = cards_ctx(tmp_path)
    for c in s.deck_cards("Words"):
        s.review(c["id"], True)
    rev = ReviewScreen(ctx, "Words")
    assert rev.queue == [] and rev.render().size == (400, 300)
    assert isinstance(rev.handle(B.SELECT), DeckScreen)


def test_a_card_deleted_from_the_phone_during_review_is_skipped(tmp_path):
    ctx, s = cards_ctx(tmp_path)
    rev = ReviewScreen(ctx, "Words")
    s.delete(rev.queue[0])
    assert rev.render().size == (400, 300) and len(rev.queue) == 1


def test_browse_pages_through_then_end_page(tmp_path):
    ctx, s = cards_ctx(tmp_path)
    br = BrowseScreen(ctx, "Words")
    seen = []
    for _ in range(3):                                                  # card, card, End
        assert br.render().size == (400, 300)
        seen.append(br.index)
        br.handle(B.DOWN)
    assert seen == [0, 1, 2] and br.index == 0                          # wraps round
    br.handle(B.SELECT); assert br.revealed
    br.handle(B.DOWN); assert not br.revealed                           # moving on hides the answer
    br.handle(B.DOWN); assert br.index == 2
    assert isinstance(br.handle(B.SELECT), DeckScreen)                  # select on the End page leaves
    q = BrowseScreen(ctx, "Quotes")
    assert isinstance(q.handle(B.SELECT), DeckScreen)                   # a quote has no answer: select goes back
    assert BrowseScreen(ctx, "Empty").render().size == (400, 300)


def ink_below(img, y):
    return any(img.getpixel((x, yy)) == 0 for yy in range(y, img.height) for x in range(img.width))


def test_cards_render_without_running_off_the_screen(tmp_path):
    from PIL import ImageDraw
    ctx, s = cards_ctx(tmp_path)
    arabic = "بسم الله الرحمن الرحيم " * 30
    cases = [{"front": "Hi", "back": "", "source": ""},
             {"front": "short front", "back": "short back", "source": "Book 1"},
             {"front": arabic, "back": "x " * 400, "source": "s" * 100},
             {"front": "line one\n\nline three\nline four " * 5, "back": arabic, "source": ""}]
    for card in cases:
        for revealed in (False, True):
            img = blank(ctx); d = ImageDraw.Draw(img)
            top, bottom = 40, 260
            draw_card(ctx, img, card, top, bottom, revealed)
            assert not any(img.getpixel((x, y)) == 0 for y in list(range(top)) + list(range(bottom, 300)) for x in range(400)), (card["front"][:10], revealed)
            assert any(img.getpixel((x, y)) == 0 for y in range(top, bottom) for x in range(400))


def test_short_text_is_bigger_than_long_text(tmp_path):
    from ereader.cardscreens import _fit
    ctx, _ = cards_ctx(tmp_path)
    small = _fit(ctx.fonts, ["word " * 80], 372, 200)[0].size
    big = _fit(ctx.fonts, ["word"], 372, 200)[0].size
    assert big > small >= 14


def test_forget_book_state_and_cards_are_separate(tmp_path):
    ctx, s = cards_ctx(tmp_path)
    ctx.state.forget("anything")
    assert len(s.all()) == 4 and (tmp_path / "state" / "cards.json").exists()
