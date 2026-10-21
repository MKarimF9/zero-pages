"""EPUB file access.            >>> YOUR CODE: milestone 1 <<<

An EPUB is a ZIP. Finding the content takes three hops:

  META-INF/container.xml  -> path of the .opf file
  <something>.opf         -> metadata (title, author), manifest (id -> file),
                             spine (reading order, as a list of ids)
  chapter .xhtml files    -> the actual text

Same structure as atomic14's Epub class, but Python's standard library
replaces miniz (zip) and TinyXML2 (xml).

Done when: `python3 -m pytest tests/test_epub.py` passes, and the library
screen shows real book titles instead of file names.
"""
from __future__ import annotations

import posixpath
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

# The XML in EPUBs uses namespaces. ElementTree writes tag names like
# "{urn:oasis:names:tc:opendocument:xmlns:container}rootfile".
# Search with a namespace map, e.g. root.find("c:rootfiles/c:rootfile", NS)
NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}
XHTML = "{http://www.w3.org/1999/xhtml}"
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"


@dataclass
class TocEntry:
    title: str
    section: int  # index into Epub.spine


class Epub:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.title: str = self.path.stem   # fallback until the .opf is parsed
        self.author: str = ""
        self.manifest: dict[str, str] = {}  # item id -> href (full path inside zip)
        self.spine: list[str] = []          # hrefs, in reading order
        self.toc: list[TocEntry] = []       # table of contents, in spine order
        self.rtl = False                    # page-progression-direction="rtl"
        self._zip: zipfile.ZipFile | None = None
        self._opf_path: str = ""

    def open(self) -> "Epub":
        self._zip = zipfile.ZipFile(self.path)
        self._opf_path = self._find_opf()
        self._parse_opf()
        return self

    def section_size(self, index: int) -> int:
        """Uncompressed size in bytes of spine item `index` (0 if missing).
        Used to estimate reading progress without laying out the whole book."""
        assert self._zip, "call open() first"
        try:
            return self._zip.getinfo(self.spine[index]).file_size
        except KeyError:
            return 0

    def close(self) -> None:
        if self._zip:
            self._zip.close()
            self._zip = None

    def read(self, href: str) -> bytes:
        """Raw bytes of a file inside the EPUB (href = full path in the zip)."""
        assert self._zip, "call open() first"
        return self._zip.read(href)

    # ------------------------------------------------------------------ TODOs

    def _find_opf(self) -> str:
        """Return the path of the .opf file inside the zip.

        TODO:
          1. read "META-INF/container.xml"
          2. parse it with ET.fromstring
          3. find the <rootfile> element (use NS) and return its "full-path" attribute
        """
        root = ET.fromstring(self.read("META-INF/container.xml"))
        rootfile = root.find("c:rootfiles/c:rootfile", NS)
        if rootfile is None:
            raise ValueError(f"{self.path.name}: no <rootfile> in container.xml")
        return rootfile.get("full-path")

    def _parse_opf(self) -> None:
        """Fill in self.title, self.author, self.manifest and self.spine.

        TODO:
          - title:  <metadata><dc:title>
          - author: <metadata><dc:creator>   (may be missing: keep "")
          - manifest: every <manifest><item id="..." href="..."/>
                      store hrefs as FULL zip paths with self._resolve(href)
          - spine: for each <spine><itemref idref="..."/>, look the id up in
                   the manifest and append the resolved href

        Watch out for:
          - hrefs are relative to the .opf's folder (that's what _resolve is for)
          - some hrefs are URL-encoded ("my%20chapter.xhtml") -> urllib.parse.unquote
          - a spine idref missing from the manifest: skip it, don't crash
        """
        root = ET.fromstring(self.read(self._opf_path))

        title = root.find("opf:metadata/dc:title", NS)
        if title is not None and (title.text or "").strip():
            self.title = title.text.strip()
        creator = root.find("opf:metadata/dc:creator", NS)
        if creator is not None and creator.text:
            self.author = creator.text.strip()

        self.manifest = {}
        nav_href = None  # EPUB 3 navigation document
        for item in root.findall("opf:manifest/opf:item", NS):
            self.manifest[item.get("id")] = self._resolve(item.get("href"))
            if "nav" in (item.get("properties") or "").split():
                nav_href = self.manifest[item.get("id")]

        self.spine = []
        for ref in root.findall("opf:spine/opf:itemref", NS):
            href = self.manifest.get(ref.get("idref"))
            if href:
                self.spine.append(href)

        spine = root.find("opf:spine", NS)
        self.rtl = spine is not None and spine.get("page-progression-direction") == "rtl"
        ncx_href = self.manifest.get(spine.get("toc")) if spine is not None else None
        try:
            self.toc = self._parse_toc(ncx_href, nav_href)
        except (ET.ParseError, KeyError, ValueError):
            self.toc = []  # a broken table of contents must not make the book unreadable

    def _parse_toc(self, ncx_href: str | None, nav_href: str | None) -> list[TocEntry]:
        """Table of contents from the EPUB 2 .ncx, else the EPUB 3 nav document.
        Entries pointing outside the spine are dropped; the rest are sorted by
        spine position (stable, so document order is kept within a section)."""
        raw: list[tuple[str, str, str]] = []  # (title, src, toc file path)
        if ncx_href:
            root = ET.fromstring(self.read(ncx_href))
            for point in root.iter("{%s}navPoint" % NS["ncx"]):
                text = point.find("ncx:navLabel/ncx:text", NS)
                content = point.find("ncx:content", NS)
                if text is not None and content is not None:
                    raw.append(("".join(text.itertext()), content.get("src", ""), ncx_href))
        elif nav_href:
            root = ET.fromstring(self.read(nav_href))
            for nav in root.iter(XHTML + "nav"):
                if "toc" in (nav.get(EPUB_TYPE) or "").split():
                    for a in nav.iter(XHTML + "a"):
                        raw.append(("".join(a.itertext()), a.get("href", ""), nav_href))
        index = {href: i for i, href in enumerate(self.spine)}
        entries = []
        for title, src, base in raw:
            path = posixpath.normpath(posixpath.join(posixpath.dirname(base),
                                                     unquote(src.split("#")[0])))
            title = " ".join(title.split())
            if title and path in index:
                entries.append(TocEntry(title, index[path]))
        return sorted(entries, key=lambda e: e.section)

    def _resolve(self, href: str) -> str:
        """Turn an href relative to the .opf into a full path inside the zip.

        Example: opf at "OEBPS/content.opf", href "text/ch1.xhtml"
                 -> "OEBPS/text/ch1.xhtml"

        TODO: use posixpath.dirname, posixpath.join and posixpath.normpath.
        (posixpath, not os.path: zip paths always use "/", even on Windows.)
        """
        base = posixpath.dirname(self._opf_path)
        return posixpath.normpath(posixpath.join(base, unquote(href)))


__all__ = ["Epub", "NS", "TocEntry", "posixpath"]
