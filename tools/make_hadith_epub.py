"""Build hadith EPUBs from the JSON files of github.com/AhmedBaset/hadith-json.

    python3 tools/make_hadith_epub.py <folder with the JSON files> <output folder>
    python3 tools/make_hadith_epub.py data out --only riyad_assalihin    # one collection

The folder holds the files from the repository's db/by_book/ (flat: bukhari.json,
riyad_assalihin.json, ...). It writes:

    <output>/Hadith.epub          every collection in one book: Hadith > collection > chapter
    <output>/individual/<name>.epub   one book per collection (chapters only)

Each hadith is an Arabic paragraph followed by its English one, so the reader keeps
them together on a page (see layout.py). Each chapter is its own file, so the book
opens quickly and the contents list jumps straight to a chapter.

The data was scraped from sunnah.com and the repository states no license, and the
English translations belong to their translators: use the books on your own device
only, and don't publish them.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
import zipfile
from html import escape
from pathlib import Path

# (file name, short name used in the contents list). The order is the order in the book.
COLLECTIONS = [
    ("nawawi40", "Forty Hadith of an-Nawawi"),
    ("riyad_assalihin", "Riyad as-Salihin"),
    ("bukhari", "Sahih al-Bukhari"),
    ("muslim", "Sahih Muslim"),
    ("abudawud", "Sunan Abi Dawud"),
    ("tirmidhi", "Jami at-Tirmidhi"),
    ("nasai", "Sunan an-Nasa'i"),
    ("ibnmajah", "Sunan Ibn Majah"),
    ("malik", "Muwatta Malik"),
    ("ahmed", "Musnad Ahmad"),
    ("darimi", "Sunan ad-Darimi"),
    ("bulugh_almaram", "Bulugh al-Maram"),
    ("mishkat_almasabih", "Mishkat al-Masabih"),
    ("aladab_almufrad", "al-Adab al-Mufrad"),
    ("shamail_muhammadiyah", "Shama'il Muhammadiyah"),
    ("qudsi40", "Forty Hadith Qudsi"),
    ("shahwaliullah40", "Forty Hadith of Shah Waliullah"),
]

SOURCE_NOTE = ("Compiled for personal reading from the data at github.com/AhmedBaset/hadith-json, "
               "which comes from sunnah.com. The translations belong to their translators.")

TATWEEL = "ـ"  # the stretching line in "كتـــــاب"; it only makes titles ragged on a small screen

STYLE = "h1,h2{text-align:center}"


def clean_title(text: str) -> str:
    return " ".join(text.replace(TATWEEL, "").split())


class Collection:
    """One JSON file: its chapters, each with its hadiths in book order."""

    def __init__(self, key: str, short: str, data: dict):
        self.key, self.short = key, short
        meta = data["metadata"]
        self.title = meta["english"].get("title") or short
        self.arabic_title = clean_title(meta["arabic"].get("title") or "")
        self.author = meta["english"].get("author") or ""
        by_chapter: dict[int, list[dict]] = {}
        for h in data["hadiths"]:
            by_chapter.setdefault(h["chapterId"], []).append(h)
        self.chapters = []
        for position, ch in enumerate(data["chapters"], 1):
            hadiths = sorted(by_chapter.get(ch["id"], []), key=lambda h: h.get("idInBook", 0))
            if hadiths:  # an empty chapter would be a dead entry in the contents list
                self.chapters.append({"n": position, "english": clean_title(ch.get("english") or ""),
                                      "arabic": clean_title(ch.get("arabic") or ""), "hadiths": hadiths})
        self.count = sum(len(c["hadiths"]) for c in self.chapters)


def load(folder: Path, only: str | None = None) -> list[Collection]:
    found = []
    for key, short in COLLECTIONS:
        if only and key != only:
            continue
        path = folder / f"{key}.json"
        if path.exists():
            found.append(Collection(key, short, json.loads(path.read_text(encoding="utf-8"))))
        else:
            print(f"skipping {key}: {path} not found", file=sys.stderr)
    return found


# ------------------------------------------------------------------ pages

def page(title: str, body: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<head><meta charset="utf-8"/><title>{escape(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="../style.css"/></head>'
            f'<body>{body}</body></html>')


def hadith_html(h: dict) -> str:
    arabic = " ".join((h.get("arabic") or "").split())
    english = h.get("english") or {}
    narrator = " ".join((english.get("narrator") or "").split())
    text = " ".join((english.get("text") or "").split())
    number = f"<b>{h.get('idInBook', '')}.</b>"
    out = []
    if arabic:
        out.append(f'<p dir="rtl" lang="ar">{escape(arabic)}</p>')
    if narrator or text:
        who = f"<i>{escape(narrator)}</i> " if narrator else ""
        out.append(f'<p>{number} {who}{escape(text)}</p>')
    else:
        out.append(f"<p>{number}</p>")  # no translation: the number still shows under the Arabic
    return "".join(out)


def chapter_page(chapter: dict) -> str:
    heading = f"<h2>{escape(chapter['english'])}</h2>" if chapter["english"] else ""
    if chapter["arabic"]:
        heading += f"<h2>{escape(chapter['arabic'])}</h2>"
    title = chapter["english"] or chapter["arabic"]
    return page(title, heading + "".join(hadith_html(h) for h in chapter["hadiths"]))


def collection_page(c: Collection) -> str:
    body = f"<h1>{escape(c.title)}</h1>"
    if c.arabic_title:
        body += f"<h1>{escape(c.arabic_title)}</h1>"
    if c.author:
        body += f"<p>{escape(c.author)}</p>"
    body += f"<p>{c.count} hadith in {len(c.chapters)} chapters.</p>"
    return page(c.title, body)


def front_page(collections: list[Collection]) -> str:
    body = "<h1>Hadith</h1><h1>الحديث</h1>"
    body += "".join(f"<p>{escape(c.short)}: {c.count} hadith</p>" for c in collections)
    body += f"<p>{escape(SOURCE_NOTE)}</p>"
    return page("Hadith", body)


# ------------------------------------------------------------------ the package

class Package:
    """Files of one EPUB plus a (possibly nested) contents list."""

    def __init__(self, title: str, author: str):
        self.title, self.author = title, author
        self.files: dict[str, str] = {}   # path inside OEBPS -> content
        self.spine: list[str] = []
        self.toc: list[tuple[int, str, str]] = []   # (level, title, path)

    def add(self, path: str, content: str, toc_title: str = "", level: int = 0) -> None:
        self.files[path] = content
        self.spine.append(path)
        if toc_title:
            self.toc.append((level, toc_title, path))

    def _ncx(self, ident: str) -> str:
        out, depth, n = [], 0, 0
        for i, (level, title, path) in enumerate(self.toc):
            while depth > level:
                out.append("</navPoint>")
                depth -= 1
            n += 1
            out.append(f'<navPoint id="n{n}" playOrder="{n}"><navLabel><text>{escape(title)}</text></navLabel>'
                       f'<content src="{escape(path)}"/>')
            depth += 1
            nxt = self.toc[i + 1][0] if i + 1 < len(self.toc) else 0
            if nxt <= level:   # a leaf: close it now
                out.append("</navPoint>")
                depth -= 1
        out.append("</navPoint>" * depth)
        return ('<?xml version="1.0" encoding="utf-8"?>'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                f'<head><meta name="dtb:uid" content="{ident}"/></head>'
                f'<docTitle><text>{escape(self.title)}</text></docTitle><navMap>{"".join(out)}</navMap></ncx>')

    def _nav(self) -> str:
        out, depth = [], 0
        for i, (level, title, path) in enumerate(self.toc):
            out.append(f'<li><a href="{escape(path)}">{escape(title)}</a>')
            nxt = self.toc[i + 1][0] if i + 1 < len(self.toc) else 0
            if nxt > level:
                out.append("<ol>")
                depth += 1
            else:
                out.append("</li>")
                for _ in range(level - nxt):
                    out.append("</ol></li>")
                    depth -= 1
        return ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                f'<head><meta charset="utf-8"/><title>{escape(self.title)}</title></head><body>'
                f'<nav epub:type="toc"><ol>{"".join(out)}</ol></nav></body></html>')

    def write(self, out_path: Path) -> None:
        ident = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "pi-ereader/" + self.title))
        items = [("ncx", "toc.ncx", "application/x-dtbncx+xml", ""),
                 ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
                 ("css", "style.css", "text/css", "")]
        items += [(f"p{i}", path, "application/xhtml+xml", "") for i, path in enumerate(self.spine)]
        manifest = "".join(f'<item id="{i}" href="{h}" media-type="{t}"{p}/>' for i, h, t, p in items)
        spine = "".join(f'<itemref idref="p{i}"/>' for i in range(len(self.spine)))
        opf = ('<?xml version="1.0" encoding="utf-8"?>'
               '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
               f'<dc:identifier id="id">{ident}</dc:identifier><dc:title>{escape(self.title)}</dc:title>'
               f'<dc:creator>{escape(self.author)}</dc:creator><dc:language>en</dc:language>'
               '<dc:language>ar</dc:language></metadata>'
               f'<manifest>{manifest}</manifest><spine toc="ncx">{spine}</spine></package>')
        container = ('<?xml version="1.0"?><container version="1.0" '
                     'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                     '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                     '</rootfiles></container>')
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml", container)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/toc.ncx", self._ncx(ident))
            z.writestr("OEBPS/nav.xhtml", self._nav())
            z.writestr("OEBPS/style.css", STYLE)
            for path, content in self.files.items():
                z.writestr(f"OEBPS/{path}", content)
        tmp.replace(out_path)


def add_collection(pkg: Package, c: Collection, index: int, nested: bool) -> None:
    """Title page then one file per chapter. nested: the collection is a contents
    entry of its own and its chapters sit one level under it."""
    top = 1 if nested else 0
    if nested:
        pkg.add(f"text/c{index:02d}_title.xhtml", collection_page(c), c.short, 0)
    else:
        pkg.add(f"text/c{index:02d}_title.xhtml", collection_page(c))
    for ch in c.chapters:
        label = f"{ch['n']}. {ch['english'] or ch['arabic']}"
        pkg.add(f"text/c{index:02d}_ch{ch['n']:03d}.xhtml", chapter_page(ch), label, top)


def build_all(folder: Path, out: Path, only: str | None = None) -> list[Path]:
    collections = load(folder, only)
    written = []
    if not only:
        combined = Package("Hadith", "Various")
        combined.add("text/front.xhtml", front_page(collections))
        for i, c in enumerate(collections):
            add_collection(combined, c, i, nested=True)
        path = out / "Hadith.epub"
        combined.write(path)
        written.append(path)
    for i, c in enumerate(collections):
        pkg = Package(c.title, c.author or "Various")
        add_collection(pkg, c, i, nested=False)
        path = out / "individual" / f"{c.key}.epub"
        pkg.write(path)
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("data", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--only", help="build just this collection, e.g. riyad_assalihin")
    args = parser.parse_args()
    for path in build_all(args.data, args.out, args.only):
        print(f"{path.stat().st_size / 1e6:7.1f} MB  {path}")


if __name__ == "__main__":
    main()
