"""Chapter XHTML -> list of blocks.     >>> YOUR CODE: milestone 2 <<<

atomic14 calls his version RubbishHtmlParser, and that's the right
attitude: don't try to be a web browser. Support a small set of tags,
ignore CSS, and you'll handle most books well.

Rules (same as his):
  - Block tags (<p>, <div>, <h1>...) start a new TextBlock.
  - Inline tags (<b>, <i>, ...) change the style of the words inside them.
  - <img> becomes an ImageBlock.
  - <br> can just start a new block (simple, good enough).
  - Text inside <head>, <style>, <script> is not book content: skip it.

Done when: `python3 -m pytest tests/test_html_parser.py` passes.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from .blocks import Block, ImageBlock, Style, TextBlock, Word

BLOCK_TAGS = {"p", "div", "li", "blockquote", "section", "h1", "h2", "h3", "h4", "h5", "h6"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BOLD_TAGS = {"b", "strong"}
ITALIC_TAGS = {"i", "em"}
SKIP_TAGS = {"head", "style", "script"}

# Many books don't use <h1>..<h6> but mark titles with a class, role or epub:type:
# <div class="chapter-title">, <p class="subtitle">, role="heading"...
_HEADING_CLASS = re.compile(r"(?:^|[-_])(?:title|subtitle|heading)(?:$|[-_])", re.I)
MAX_CLASS_HEADING_WORDS = 40  # a class-marked block longer than this is a paragraph, not a heading


def looks_like_heading(attrs) -> bool:
    """True if a tag's class, role or epub:type says it is a title."""
    attributes = dict(attrs)
    if (attributes.get("role") or "").lower() == "heading":
        return True
    if {"title", "subtitle"} & set((attributes.get("epub:type") or "").lower().split()):
        return True
    return any(_HEADING_CLASS.search(token) for token in (attributes.get("class") or "").split())


class BlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # turns &amp; etc. into real characters
        self.blocks: list[Block] = []
        self._current: TextBlock | None = None
        # Counters instead of True/False, so nested tags work:
        # <b>one <b>two</b> still bold</b>
        self._bold_depth = 0
        self._italic_depth = 0
        self._skip_depth = 0
        self._in_heading = 0
        self._open_blocks: list[tuple[str, bool]] = []  # (tag, is a class-marked heading) of open blocks
        self._current_soft = False  # is the current block a heading only because of its class?

    # ------------------------------------------------------------------ TODOs

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._finish_block()
            # a block inside a class-marked heading is part of that heading
            soft = looks_like_heading(attrs) or self._inside_soft_heading()
            self._open_blocks.append((tag, soft))
            self._current = TextBlock(is_heading=tag in HEADING_TAGS or soft)
            self._current_soft = soft and tag not in HEADING_TAGS
            if tag in HEADING_TAGS:
                self._in_heading += 1
        elif tag in BOLD_TAGS:
            self._bold_depth += 1
        elif tag in ITALIC_TAGS:
            self._italic_depth += 1
        elif tag == "img":
            self._finish_block()
            attributes = dict(attrs)
            src = attributes.get("src")
            if src:
                self.blocks.append(ImageBlock(src, alt=(attributes.get("alt") or "").strip()))
        elif tag == "br":
            self._finish_block()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._finish_block()
            for i in range(len(self._open_blocks) - 1, -1, -1):  # pop through to the matching tag,
                if self._open_blocks[i][0] == tag:               # so sloppy HTML can't leave a heading "open"
                    del self._open_blocks[i:]
                    break
            if tag in HEADING_TAGS:
                self._in_heading = max(0, self._in_heading - 1)
        elif tag in BOLD_TAGS:
            self._bold_depth = max(0, self._bold_depth - 1)
        elif tag in ITALIC_TAGS:
            self._italic_depth = max(0, self._italic_depth - 1)

    def handle_data(self, data):
        if self._skip_depth:
            return
        pieces = data.split()
        if not pieces:
            return
        if self._current is None:  # text outside any block tag
            self._current = TextBlock(is_heading=self._in_heading > 0 or self._inside_soft_heading())
            self._current_soft = self._inside_soft_heading() and self._in_heading == 0
        style = self._current_style()
        self._current.words.extend(Word(piece, style) for piece in pieces)

    def _current_style(self) -> Style:
        style = Style.NORMAL
        if self._bold_depth > 0 or self._in_heading > 0 or (self._current is not None and self._current.is_heading):
            style |= Style.BOLD
        if self._italic_depth > 0:
            style |= Style.ITALIC
        return style

    def _inside_soft_heading(self) -> bool:
        return any(soft for _, soft in self._open_blocks)

    def _finish_block(self) -> None:
        block = self._current
        if block is not None and block.words:
            if self._current_soft and len(block.words) > MAX_CLASS_HEADING_WORDS:
                block.is_heading = False   # too long to be a title: an ordinary paragraph after all
                for word in block.words:
                    word.style &= ~Style.BOLD
            self.blocks.append(block)
        self._current = None
        self._current_soft = False

    def close(self):
        super().close()
        self._finish_block()  # don't lose the last paragraph


def parse_blocks(html: str) -> list[Block]:
    parser = BlockParser()
    parser.feed(html)
    parser.close()
    return parser.blocks


# Stretch goal: "un<i>believ</i>able" currently becomes three separate
# words. How would you keep it as one word with mixed styles?
