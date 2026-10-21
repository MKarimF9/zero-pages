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

from html.parser import HTMLParser

from .blocks import Block, ImageBlock, Style, TextBlock, Word

BLOCK_TAGS = {"p", "div", "li", "blockquote", "section", "h1", "h2", "h3", "h4", "h5", "h6"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BOLD_TAGS = {"b", "strong"}
ITALIC_TAGS = {"i", "em"}
SKIP_TAGS = {"head", "style", "script"}


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

    # ------------------------------------------------------------------ TODOs

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._finish_block()
            self._current = TextBlock(is_heading=tag in HEADING_TAGS)
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
            self._current = TextBlock(is_heading=self._in_heading > 0)
        style = self._current_style()
        self._current.words.extend(Word(piece, style) for piece in pieces)

    def _current_style(self) -> Style:
        style = Style.NORMAL
        if self._bold_depth > 0 or self._in_heading > 0:
            style |= Style.BOLD
        if self._italic_depth > 0:
            style |= Style.ITALIC
        return style

    def _finish_block(self) -> None:
        if self._current is not None and self._current.words:
            self.blocks.append(self._current)
        self._current = None

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
