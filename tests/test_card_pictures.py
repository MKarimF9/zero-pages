"""Pictures on flash cards. Run: python3 -m pytest tests/test_card_pictures.py -v"""
import io
import json
import urllib.error
import urllib.request

import pytest
from PIL import Image, ImageDraw

from ereader.cards import CardStore
from ereader.cardscreens import BrowseScreen, ReviewScreen, _pictures, draw_card, two_sided
from ereader.controls import Button as B
from ereader.images import MAX_PIXELS, STORE_MAX, prepare_picture
from ereader.screens import blank, card_store
from tests.test_manager import run
from tests.test_pinned import make_ctx


def png(size=(300, 200), mode="RGB", fmt="PNG", color=None, **save):
    """A test picture: a diagonal gradient with a black box (so it is not blank)."""
    img = Image.new(mode, size, color if color is not None else (200, 200, 200)[: len(mode)] if mode == "RGB" else 200)
    d = ImageDraw.Draw(img)
    d.rectangle((size[0] // 4, size[1] // 4, size[0] // 2, size[1] // 2), fill=0 if mode != "RGB" else (0, 0, 0))
    out = io.BytesIO()
    img.save(out, format=fmt, **save)
    return out.getvalue()


# ---------------------------------------------------------------- preparing a picture

def test_a_big_picture_is_shrunk_and_made_gray():
    gray = prepare_picture(png((2400, 1800)))
    assert gray.mode == "L" and gray.width <= STORE_MAX[0] and gray.height <= STORE_MAX[1]
    assert abs(gray.width / gray.height - 4 / 3) < 0.02                     # same shape
    assert prepare_picture(png((100, 80))).size == (100, 80)                # a small one is not enlarged


def test_jpeg_gif_webp_bmp_are_accepted():
    for fmt in ("JPEG", "GIF", "WEBP", "BMP"):
        assert prepare_picture(png((50, 40), fmt=fmt)).size == (50, 40), fmt


def test_transparent_areas_become_white():
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    out = io.BytesIO(); img.save(out, format="PNG")
    assert prepare_picture(out.getvalue()).getpixel((5, 5)) == 255


def test_a_phone_photo_taken_sideways_is_turned_upright():
    img = Image.new("RGB", (80, 40), (120, 120, 120))
    exif = Image.Exif(); exif[0x0112] = 6                                    # "rotate 90 degrees"
    out = io.BytesIO(); img.save(out, format="JPEG", exif=exif)
    assert prepare_picture(out.getvalue()).size == (40, 80)


def test_things_that_are_not_pictures_are_refused():
    bad = [b"", b"hello", b"\x89PNG\r\n\x1a\n" + b"x" * 50, png((20, 20), fmt="TIFF"), b"<svg xmlns='http://www.w3.org/2000/svg'/>"]
    for data in bad:
        try:
            prepare_picture(data)
            assert False, data[:12]
        except ValueError as e:
            assert "not a picture" in str(e)


def test_a_decompression_bomb_is_refused_before_it_is_decoded():
    side = int(MAX_PIXELS ** 0.5) + 10
    data = png((side, side), mode="1", color=1)                              # tiny file, huge when decoded
    assert len(data) < 200_000
    try:
        prepare_picture(data)
        assert False, "should have been refused"
    except ValueError:
        pass


# ---------------------------------------------------------------- the store

def store(tmp_path):
    return CardStore(tmp_path / "cards.json")


def test_set_replace_and_remove_a_picture(tmp_path):
    s = store(tmp_path)
    c = s.add("d", "front", "back")
    assert s.picture_path(s.get(c["id"]), "front") is None
    s.set_picture(c["id"], "front", png((300, 200)))
    s.set_picture(c["id"], "back", png((100, 100), fmt="JPEG"))
    card = CardStore(tmp_path / "cards.json").get(c["id"])                   # survives a restart
    assert card["image"] and card["back_image"]
    front = s.picture_path(card, "front")
    assert front.name == f"{c['id']}-front.png" and Image.open(front).size == (300, 200)
    s.set_picture(c["id"], "front", png((60, 50)))                           # replaced, not added
    assert Image.open(front).size == (60, 50) and len(list(s.images_dir.iterdir())) == 2
    s.remove_picture(c["id"], "front")
    assert not front.exists() and s.picture_path(s.get(c["id"]), "front") is None and s.get(c["id"])["back_image"]


def test_a_bad_picture_or_side_changes_nothing(tmp_path):
    s = store(tmp_path)
    c = s.add("d", "front")["id"]
    for args in (("front", b"junk"), ("left", png()), ("", png())):
        with pytest.raises(ValueError):
            s.set_picture(c, *args)
    with pytest.raises(ValueError):
        s.remove_picture(c, "left")
    with pytest.raises(KeyError):
        s.set_picture("nope", "front", png())
    with pytest.raises(KeyError):
        s.remove_picture("nope", "front")
    assert not s.get(c).get("image")
    assert not s.images_dir.exists() or list(s.images_dir.iterdir()) == []


def test_deleting_a_card_or_deck_deletes_its_pictures(tmp_path):
    s = store(tmp_path)
    a, b, c = s.add("x", "1")["id"], s.add("x", "2")["id"], s.add("y", "3")["id"]
    for card in (a, b, c):
        s.set_picture(card, "front", png((40, 40)))
        s.set_picture(card, "back", png((40, 40)))
    s.delete(a)
    assert sorted(p.name for p in s.images_dir.iterdir()) == sorted(f"{i}-{side}.png" for i in (b, c) for side in ("front", "back"))
    assert s.delete_deck("x") == 1
    assert sorted(p.name for p in s.images_dir.iterdir()) == [f"{c}-back.png", f"{c}-front.png"]


def test_a_flag_without_its_file_means_no_picture(tmp_path):
    s = store(tmp_path)
    c = s.add("d", "f")["id"]
    s.set_picture(c, "front", png())
    next(s.images_dir.iterdir()).unlink()                                    # the file was lost
    assert s.picture_path(s.get(c), "front") is None


def test_only_a_back_picture_still_makes_a_two_sided_card(tmp_path):
    s = store(tmp_path)
    c = s.add("d", "What is this?")["id"]
    assert not two_sided(s.get(c))
    s.set_picture(c, "back", png())
    assert two_sided(s.get(c))


# ---------------------------------------------------------------- over HTTP

def fetch(r, path, pin="1234"):
    req = urllib.request.Request(r.base + path, headers={"X-PIN": pin})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def test_api_picture_round_trip(tmp_path):
    srv, _, _ = run(tmp_path)
    with srv as r:
        cid = r.call("POST", "/api/cards/add", json.dumps({"deck": "d", "front": "f", "back": "b"}).encode())[1]["id"]
        assert r.call("POST", f"/api/cards/image?id={cid}&side=front", png((800, 600), fmt="JPEG"))[0] == 200
        card = r.call("GET", "/api/cards")[1]["cards"][0]
        assert card["image"] is True and card["back_image"] is False
        status, ctype, body = fetch(r, f"/api/cards/image?id={cid}&side=front")
        assert status == 200 and ctype == "image/png" and Image.open(io.BytesIO(body)).size == (600, 450)
        status, _, body = fetch(r, f"/api/cards/image?id={cid}&side=front&thumb=1")
        assert status == 200 and max(Image.open(io.BytesIO(body)).size) == 160
        assert fetch(r, f"/api/cards/image?id={cid}&side=back")[0] == 404
        assert fetch(r, f"/api/cards/image?id=nope&side=front")[0] == 404
        assert r.call("POST", f"/api/cards/image-remove?id={cid}&side=front")[0] == 200
        assert fetch(r, f"/api/cards/image?id={cid}&side=front")[0] == 404
        assert r.call("GET", "/api/cards")[1]["cards"][0]["image"] is False


def test_api_picture_errors_and_pin(tmp_path):
    srv, _, _ = run(tmp_path)
    with srv as r:
        cid = r.call("POST", "/api/cards/add", json.dumps({"deck": "d", "front": "f"}).encode())[1]["id"]
        good = png()
        assert r.call("POST", f"/api/cards/image?id={cid}&side=front", good, pin="0000")[0] == 403
        assert fetch(r, f"/api/cards/image?id={cid}&side=front", pin="0000")[0] == 403
        assert r.call("POST", f"/api/cards/image?id={cid}&side=front", b"not a picture")[0] == 400
        assert r.call("POST", f"/api/cards/image?id={cid}&side=left", good)[0] == 400
        assert r.call("POST", f"/api/cards/image?id={cid}&side=front", b"")[0] == 400
        assert r.call("POST", f"/api/cards/image?id={cid}&side=front", b"x" * (4 * 1024 * 1024 + 1))[0] == 400
        assert r.call("POST", f"/api/cards/image?id=nope&side=front", good)[0] == 404
        assert r.call("POST", f"/api/cards/image-remove?id=nope&side=front")[0] == 404
        assert r.call("GET", "/api/cards")[1]["cards"][0]["image"] is False


def test_api_deleting_a_card_deletes_its_picture(tmp_path):
    srv, _, _ = run(tmp_path)
    with srv as r:
        cid = r.call("POST", "/api/cards/add", json.dumps({"deck": "d", "front": "f"}).encode())[1]["id"]
        r.call("POST", f"/api/cards/image?id={cid}&side=front", png())
        images = tmp_path / "state" / "card-images"
        assert [p.name for p in images.iterdir()] == [f"{cid}-front.png"]
        r.call("POST", f"/api/cards/delete?id={cid}")
        assert list(images.iterdir()) == []


def test_page_can_pick_pictures_and_never_puts_card_text_into_html():
    from ereader.upload import PAGE
    assert 'id="cpicf"' in PAGE and 'id="cpicb"' in PAGE and "sendPicture" in PAGE and "createImageBitmap" in PAGE
    assert PAGE.count("innerHTML") == PAGE.count("innerHTML = ''")


# ---------------------------------------------------------------- on the reader

def pic_ctx(tmp_path):
    ctx, state, books = make_ctx(tmp_path)
    s = card_store(ctx)
    return ctx, s


def ink(img, box):
    x0, y0, x1, y1 = box
    return sum(img.getpixel((x, y)) == 0 for y in range(y0, y1) for x in range(x0, x1))


def render_card(ctx, card, revealed, top=40, bottom=260):
    from PIL import ImageDraw as D
    img = blank(ctx)
    draw_card(ctx, img, card, top, bottom, revealed)
    return img


def test_a_picture_card_draws_inside_the_bars(tmp_path):
    ctx, s = pic_ctx(tmp_path)
    c = s.add("d", "What is this?", "A box", "Atlas p. 3")["id"]
    s.set_picture(c, "front", png((600, 450)))
    s.set_picture(c, "back", png((600, 450), fmt="JPEG"))
    for revealed in (False, True):
        img = render_card(ctx, s.get(c), revealed)
        assert ink(img, (0, 0, 400, 40)) == 0 and ink(img, (0, 260, 400, 300)) == 0, revealed   # nothing over the bars
        assert ink(img, (0, 40, 400, 260)) > 500


def test_a_picture_alone_fills_the_room_and_text_shrinks_it(tmp_path):
    from ereader.cardscreens import _picture_size
    ctx, s = pic_ctx(tmp_path)
    c = s.add("d", "x")["id"]
    s.set_picture(c, "front", png((600, 450)))
    path = s.picture_path(s.get(c), "front")
    tall = _picture_size(path, 372, 200)
    assert tall == (267, 200)                                                # as tall as the room allows, same shape
    assert _picture_size(path, 372, 100) == (133, 100)
    assert _picture_size(path, 1000, 1000) == (600, 450)                    # never enlarged


def test_long_text_with_a_picture_still_fits_between_the_bars(tmp_path):
    ctx, s = pic_ctx(tmp_path)
    short = s.add("d", "Hi")["id"]
    long = s.add("d", "word " * 120)["id"]
    for card in (short, long):
        s.set_picture(card, "front", png((600, 450)))
    a = render_card(ctx, s.get(short), False)
    b = render_card(ctx, s.get(long), False)
    for img in (a, b):
        assert ink(img, (0, 0, 400, 40)) == 0 and ink(img, (0, 260, 400, 300)) == 0
    assert ink(a, (0, 40, 400, 260)) > 0 and ink(b, (0, 40, 400, 260)) > 0


def test_pictures_are_remembered_not_redone_on_every_press(tmp_path):
    ctx, s = pic_ctx(tmp_path)
    c = s.add("d", "f", "b")["id"]
    s.set_picture(c, "front", png((600, 450)))
    _pictures.clear()
    render_card(ctx, s.get(c), False)
    assert len(_pictures) == 1
    render_card(ctx, s.get(c), False)
    assert len(_pictures) == 1
    for i in range(12):                                                      # the memory has a bound
        card = s.add("d", f"c{i}")["id"]
        s.set_picture(card, "front", png((100 + i, 80)))
        render_card(ctx, s.get(card), False)
    assert len(_pictures) <= 8


def test_a_missing_picture_file_is_just_text(tmp_path):
    ctx, s = pic_ctx(tmp_path)
    c = s.add("d", "Only text now")["id"]
    s.set_picture(c, "front", png())
    next(s.images_dir.iterdir()).unlink()
    assert ink(render_card(ctx, s.get(c), False), (0, 40, 400, 260)) > 50


def test_review_and_browse_work_with_a_back_picture_only(tmp_path):
    ctx, s = pic_ctx(tmp_path)
    c = s.add("d", "Name this shape")["id"]
    s.set_picture(c, "back", png())
    rev = ReviewScreen(ctx, "d")
    assert rev.render().size == (400, 300)
    rev.handle(B.SELECT)
    assert rev.revealed and rev.render().size == (400, 300)                  # an answer, not a quote
    rev.handle(B.DOWN)
    assert rev.queue == [] and s.get(c)["box"] == 1
    br = BrowseScreen(ctx, "d")
    br.handle(B.SELECT)
    assert br.revealed and br.render().size == (400, 300)


def test_the_answers_picture_replaces_the_questions_once_shown(tmp_path):
    ctx, s = pic_ctx(tmp_path)
    c = s.add("d", "Q", "A " * 60)["id"]
    s.set_picture(c, "front", png((600, 450)))
    s.set_picture(c, "back", png((600, 450)))
    front_only = render_card(ctx, s.get(c), False)
    both = render_card(ctx, s.get(c), True)
    for img in (front_only, both):
        assert ink(img, (0, 0, 400, 40)) == 0 and ink(img, (0, 260, 400, 300)) == 0
    # the front's picture is a big gradient block; once the answer is shown the front is just "Q"
    assert ink(front_only, (0, 40, 400, 150)) > 3 * ink(both, (0, 40, 400, 90))
