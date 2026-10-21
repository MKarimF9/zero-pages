"""Builds a tiny valid EPUB for tests. Also a good reference for the
structure you're parsing: read this file next to epub.py."""
from __future__ import annotations

import zipfile
from pathlib import Path

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book</dc:title>
    <dc:creator>Ada Lovelace</dc:creator>
  </metadata>
  <manifest>
    <item id="ch2" href="text/chapter%202.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="ch1"/>
    <itemref idref="missing-id"/>
    <itemref idref="ch2"/>
  </spine>
</package>"""

NCX = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
  <navPoint id="b"><navLabel><text>Second   Chapter</text></navLabel><content src="text/chapter%202.xhtml"/></navPoint>
  <navPoint id="a"><navLabel><text>Chapter One</text></navLabel><content src="text/ch1.xhtml#top"/>
    <navPoint id="a1"><navLabel><text>Elsewhere</text></navLabel><content src="text/not-in-spine.xhtml"/></navPoint>
  </navPoint>
</navMap></ncx>"""

CH1 = """<html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title></head>
<body><h1>Chapter One</h1><p>It was a <b>bright</b> cold day.</p></body></html>"""

CH2 = """<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Second chapter.</p></body></html>"""


def _cover_png() -> bytes:
    """A 90x120 grayscale gradient: something a dither can actually show."""
    import io
    from PIL import Image
    img = Image.new("L", (90, 120))
    img.putdata([(x * 255) // 89 for y in range(120) for x in range(90)])
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def make_epub(path: Path, cover: str | None = None) -> Path:
    """cover: None, \"epub3\" (properties=cover-image) or \"epub2\" (<meta name=cover>)."""
    opf = OPF
    if cover == "epub3":
        opf = opf.replace("</manifest>", '<item id="pic" href="images/cover.png" media-type="image/png" properties="cover-image"/></manifest>')
    elif cover == "epub2":
        opf = opf.replace("</manifest>", '<item id="pic" href="images/cover.png" media-type="image/png"/></manifest>')
        opf = opf.replace("</metadata>", '<meta name="cover" content="pic"/></metadata>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        if cover:
            z.writestr("OEBPS/images/cover.png", _cover_png())
        z.writestr("OEBPS/text/ch1.xhtml", CH1)
        z.writestr("OEBPS/text/chapter 2.xhtml", CH2)
        z.writestr("OEBPS/style.css", "p { margin: 0 }")
        z.writestr("OEBPS/toc.ncx", NCX)
    return path
