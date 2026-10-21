"""Milestone 2. Run: python3 -m pytest tests/test_html_parser.py -v"""
from ereader.blocks import ImageBlock, Style, TextBlock
from ereader.html_parser import parse_blocks


def texts(block):
    return [w.text for w in block.words]


def test_paragraphs_become_blocks():
    blocks = parse_blocks("<p>Hello world</p><p>Second   para\n here</p>")
    assert [texts(b) for b in blocks] == [["Hello", "world"], ["Second", "para", "here"]]


def test_inline_styles():
    [block] = parse_blocks("<p>a <b>bold</b> <i>it</i> <b><i>both</i></b> z</p>")
    styles = {w.text: w.style for w in block.words}
    assert styles["a"] == Style.NORMAL
    assert styles["bold"] == Style.BOLD
    assert styles["it"] == Style.ITALIC
    assert styles["both"] == Style.BOLD | Style.ITALIC
    assert styles["z"] == Style.NORMAL  # style must END when the tag closes


def test_nested_bold_counts_depth():
    [block] = parse_blocks("<p><b>one <b>two</b> three</b> four</p>")
    styles = {w.text: w.style for w in block.words}
    assert styles["three"] == Style.BOLD   # still inside the outer <b>
    assert styles["four"] == Style.NORMAL


def test_headings():
    blocks = parse_blocks("<h1>Title</h1><p>Body</p>")
    assert blocks[0].is_heading and not blocks[1].is_heading
    assert blocks[0].words[0].style & Style.BOLD


def test_skips_head_and_style():
    html = "<html><head><title>NOPE</title><style>p{}</style></head><body><p>Yes</p></body></html>"
    assert [texts(b) for b in parse_blocks(html)] == [["Yes"]]


def test_images_and_empty_blocks():
    blocks = parse_blocks('<p></p><p>Before</p><img src="pic.png"/><p>After</p>')
    assert isinstance(blocks[0], TextBlock) and texts(blocks[0]) == ["Before"]
    assert isinstance(blocks[1], ImageBlock) and blocks[1].src == "pic.png"
    assert texts(blocks[2]) == ["After"]


def test_entities_decoded():
    [block] = parse_blocks("<p>Tom &amp; Jerry</p>")
    assert texts(block) == ["Tom", "&", "Jerry"]
