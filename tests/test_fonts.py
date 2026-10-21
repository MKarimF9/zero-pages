"""Fonts. Run: python3 -m pytest tests/test_fonts.py -v"""
from ereader.blocks import Style
from ereader.fonts import ARABIC_FONTS, ASSETS_DIR, FAMILIES, Fonts, has_arabic

TEXT = "The quick brown fox jumps over the lazy dog"
ARABIC = "بسم الله الرحمن الرحيم"


def test_bundled_files_exist():
    for prefix in FAMILIES.values():
        for style in ("Regular", "Bold", "Italic", "BoldItalic"):
            assert (ASSETS_DIR / f"{prefix}-{style}.ttf").exists(), (prefix, style)
    assert (ASSETS_DIR / "NotoNaskhArabic-Regular.ttf").exists()


def test_families_are_really_different_fonts():
    widths = {name: Fonts(22, name).width(TEXT) for name in FAMILIES}
    assert len(set(widths.values())) == len(FAMILIES), widths
    for name in FAMILIES:  # bold is wider than regular in every family
        f = Fonts(22, name)
        assert f.width(TEXT, Style.BOLD) > f.width(TEXT)


def test_unknown_family_falls_back_to_default():
    assert Fonts(22, "Comic Sans").family == "Literata"


def test_arabic_uses_amiri_quran_in_every_family():
    assert has_arabic(ARABIC) and not has_arabic(TEXT)
    widths = {Fonts(22, name).width(ARABIC) for name in FAMILIES}
    assert len(widths) == 1                       # same Arabic font whatever the Latin one
    assert Fonts(22).get(Style.NORMAL, ARABIC) is not Fonts(22).get(Style.NORMAL, TEXT)
    assert ARABIC_FONTS[0][0].endswith("AmiriQuran-Regular.ttf")
    assert ARABIC_FONTS[0][1].endswith("NotoNaskhArabic-Bold.ttf")   # Amiri Quran has no bold


def test_arabic_word_with_punctuation_and_digits_has_no_missing_glyphs():
    # a verse marker, colon and digits inside an Arabic word must come from the Arabic font
    font = Fonts(22).get(Style.NORMAL, "الجزء: 7 ۝")
    for ch in "الجزء:7 ":
        assert font.getmask(ch).getbbox() is not None or ch == " "


def test_every_icon_is_in_the_icon_font():
    from fontTools.ttLib import TTFont  # dev tool: pip install fonttools
    from ereader.ui import ICON_FONT, ICONS
    cmap = TTFont(ICON_FONT).getBestCmap()
    missing = [name for name, ch in ICONS.items() if ord(ch) not in cmap]
    assert not missing, f"not in assets/MaterialSymbols-subset.ttf: {missing} (run tools/make_icon_font.py)"


def test_arabic_is_drawn_bigger_than_latin_with_room_for_marks():
    from ereader.fonts import ARABIC_SCALE
    f = Fonts(20)
    assert f.get(Style.NORMAL, ARABIC).size == round(20 * ARABIC_SCALE)
    assert f.get(Style.NORMAL, TEXT).size == 20
    assert f.arabic_pad > 0


def test_bold_arabic_differs_from_regular():
    f = Fonts(22)
    assert f.width(ARABIC, Style.BOLD) != f.width(ARABIC, Style.NORMAL)
