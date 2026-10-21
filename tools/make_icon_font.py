"""Build assets/MaterialSymbols-subset.ttf: just the icons the UI uses.

The full Material Symbols font is 10 MB; the subset is a few KB, which matters
on a Pi Zero. Run this only when you add an icon to ICONS in ereader/ui.py.

    pip install fonttools
    python3 tools/make_icon_font.py MaterialSymbolsOutlined.ttf MaterialSymbolsOutlined.codepoints

Get both input files from
https://github.com/google/material-design-icons/tree/master/variablefont
(the "[FILL,GRAD,opsz,wght]" .ttf and .codepoints). The font is Apache 2.0.
We freeze the variable axes at FILL=1 (solid icons read best on e-paper),
weight 400, optical size 24.
"""
import ast
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parents[1]


def icon_names() -> list[str]:
    """The ICONS dict keys in ereader/ui.py (read without importing it)."""
    tree = ast.parse((ROOT / "ereader" / "ui.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ICONS":
            return [k.value for k in node.value.keys]
    raise SystemExit("no ICONS dict found in ereader/ui.py")


def main(ttf: str, codepoints: str) -> None:
    table = dict(line.split() for line in Path(codepoints).read_text().splitlines())
    names = icon_names()
    unicodes = [int(table[n], 16) for n in names]

    font = TTFont(ttf)
    instantiateVariableFont(font, {"FILL": 1, "GRAD": 0, "opsz": 24, "wght": 400}, inplace=True)
    options = subset.Options()
    options.layout_features = []
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]  # keep copyright and license records
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(font)
    out = ROOT / "assets" / "MaterialSymbols-subset.ttf"
    font.save(out)
    print(f"{len(names)} icons -> {out} ({out.stat().st_size} bytes)")
    for n, u in zip(names, unicodes):
        print(f"  {n}: U+{u:04X}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
