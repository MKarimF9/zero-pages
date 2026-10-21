from ereader.blocks import Style
from ereader.fonts import Fonts, WIDTH_CACHE_MAX


def test_cached_width_matches_uncached():
    f = Fonts(22)
    for text, style in [("hello", Style.NORMAL), ("hello", Style.BOLD), ("مرحبا", Style.NORMAL), (" ", Style.ITALIC)]:
        expected = f.get(style, text).getlength(text)
        assert f.width(text, style) == expected
        assert f.width(text, style) == expected


def test_style_is_part_of_key():
    f = Fonts(22)
    assert f.width("Word", Style.NORMAL) != f.width("Word", Style.BOLD)


def test_second_call_skips_measuring():
    f = Fonts(22)
    f.width("again")
    f._widths[Style.NORMAL, "again"] = 123.0
    assert f.width("again") == 123.0


def test_cache_is_bounded():
    f = Fonts(22)
    for i in range(WIDTH_CACHE_MAX + 5):
        f.width(f"w{i}")
    assert len(f._widths) <= WIDTH_CACHE_MAX
