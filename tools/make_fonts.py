"""Build assets/fonts/: the book fonts, as small static TTFs.

    pip install fonttools
    python3 tools/make_fonts.py <folder with the downloaded font files>

Sources, all from https://github.com/google/fonts/tree/main/ofl (SIL OFL 1.1):
  literata/      Literata[opsz,wght].ttf  Literata-Italic[opsz,wght].ttf
  notoserif/     NotoSerif[wdth,wght].ttf  NotoSerif-Italic[wdth,wght].ttf
  notonaskharabic/  NotoNaskhArabic[wght].ttf
  charissil/     CharisSIL-{Regular,Bold,Italic,BoldItalic}.ttf
plus each family's OFL.txt (renamed OFL-<Family>.txt).

Variable fonts are frozen to regular (400) and bold (700) and cut down to the
characters we need, so Pillow doesn't need variable-font support and the Pi
reads kilobytes instead of megabytes. Charis SIL has a Reserved Font Name
under the OFL, so it must not be modified: its files are copied unchanged.
"""
import shutil
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

OUT = Path(__file__).resolve().parents[1] / "assets" / "fonts"

LATIN = (list(range(0x20, 0x7F)) + list(range(0xA0, 0x180)) +      # Basic Latin .. Latin Extended-A
         list(range(0x2000, 0x2070)) + [0x20AC, 0x2212, 0x2190, 0x2192, 0x25CC] +
         list(range(0x2B0, 0x300)) + list(range(0x300, 0x370)) +      # modifier letters (ʿ ʾ) and combining marks
         list(range(0x1E00, 0x1F00)))                                  # Latin Extended Additional (ḥ ṣ ḍ ṭ ẓ, for transliteration)
ARABIC = (list(range(0x600, 0x700)) + list(range(0x750, 0x780)) + list(range(0x8A0, 0x900)) +
          list(range(0xFB50, 0xFE00)) + list(range(0xFE70, 0xFF00)) +
          list(range(0x20, 0x7F)) + [0xA0, 0xAB, 0xBB, 0x25CC] + list(range(0x2000, 0x2070)))

# (output name, source file, axes to freeze, characters to keep)
JOBS = [
    ("Literata-Regular", "Literata[opsz,wght].ttf", {"opsz": 14, "wght": 400}, LATIN),
    ("Literata-Bold", "Literata[opsz,wght].ttf", {"opsz": 14, "wght": 700}, LATIN),
    ("Literata-Italic", "Literata-Italic[opsz,wght].ttf", {"opsz": 14, "wght": 400}, LATIN),
    ("Literata-BoldItalic", "Literata-Italic[opsz,wght].ttf", {"opsz": 14, "wght": 700}, LATIN),
    ("NotoSerif-Regular", "NotoSerif[wdth,wght].ttf", {"wdth": 100, "wght": 400}, LATIN),
    ("NotoSerif-Bold", "NotoSerif[wdth,wght].ttf", {"wdth": 100, "wght": 700}, LATIN),
    ("NotoSerif-Italic", "NotoSerif-Italic[wdth,wght].ttf", {"wdth": 100, "wght": 400}, LATIN),
    ("NotoSerif-BoldItalic", "NotoSerif-Italic[wdth,wght].ttf", {"wdth": 100, "wght": 700}, LATIN),
    ("NotoNaskhArabic-Regular", "NotoNaskhArabic[wght].ttf", {"wght": 400}, ARABIC),
    ("NotoNaskhArabic-Bold", "NotoNaskhArabic[wght].ttf", {"wght": 700}, ARABIC),
]


def build(src: Path, name: str, source: str, axes: dict, chars: list[int]) -> None:
    font = TTFont(src / source)
    instantiateVariableFont(font, axes, inplace=True)
    options = subset.Options()
    options.layout_features = ["*"]            # keep shaping: Arabic joining, kerning, ligatures
    options.name_IDs = ["*"]                   # keep copyright and license records
    options.notdef_outline = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=chars)
    subsetter.subset(font)
    font.save(OUT / f"{name}.ttf")


def main(src: str) -> None:
    src = Path(src)
    OUT.mkdir(parents=True, exist_ok=True)
    for job in JOBS:
        build(src, *job)
    for style in ("Regular", "Bold", "Italic", "BoldItalic"):
        shutil.copy(src / f"CharisSIL-{style}.ttf", OUT / f"CharisSIL-{style}.ttf")
    for lic in src.glob("OFL-*.txt"):
        shutil.copy(lic, OUT / lic.name)
    for f in sorted(OUT.glob("*.ttf")):
        print(f"{f.name:32} {f.stat().st_size // 1024:5} KB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
