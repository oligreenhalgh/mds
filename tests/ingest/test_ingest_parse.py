from pathlib import Path

import pytest

from mds.ingest.parse import (
    ParsedDocument,
    ParseError,
    Section,
    SourceFormat,
    detect_format,
    header_fields,
    normalise,
    parse_file,
    parse_text,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "ingest"


def tree(doc: ParsedDocument) -> list[tuple[int, str]]:
    return [(s.level, s.title) for s in doc.root.walk()]


# Normalisation ------------------------------------------------------------


def test_normalise_line_endings_whitespace_and_final_newline() -> None:
    assert normalise("a  \r\nb\rc\t\n\n\n") == "a\nb\nc\n"


def test_normalise_strips_bom_and_applies_nfc() -> None:
    assert normalise("﻿café\n".encode()) == "café\n"


def test_normalise_empty() -> None:
    assert normalise("") == "" and normalise("\n\n") == ""


def test_normalise_rejects_invalid_utf8() -> None:
    with pytest.raises(ParseError, match="not valid UTF-8"):
        normalise(b"\xff\xfe")


def test_normalise_preserves_line_numbers() -> None:
    raw = "one\r\n\r\nthree  \r\n"
    assert normalise(raw).split("\n")[2] == "three"


def test_normalise_is_idempotent() -> None:
    once = normalise((FIXTURES / "policy.md").read_bytes())
    assert normalise(once) == once


def test_parse_text_requires_normalised_input() -> None:
    with pytest.raises(ParseError, match="normalised first"):
        parse_text("a\r\n", SourceFormat.TEXT)


# Format detection ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("a.md", SourceFormat.MARKDOWN),
        ("a.MARKDOWN", SourceFormat.MARKDOWN),
        ("a.rst", SourceFormat.RST),
        ("a.txt", SourceFormat.TEXT),
        ("a.pdf", SourceFormat.PDF),
        ("a.docx", SourceFormat.DOCX),
    ],
)
def test_detect_format(name: str, fmt: SourceFormat) -> None:
    assert detect_format(Path(name)) is fmt


def test_unsupported_format() -> None:
    with pytest.raises(ParseError, match="unsupported file type '.xls'"):
        parse_file(Path("sheet.xls"))


def test_missing_file() -> None:
    with pytest.raises(ParseError, match="cannot read"):
        parse_file(FIXTURES / "nope.md")


# Header fields ------------------------------------------------------------


def test_header_fields_with_continuation() -> None:
    fields, end = header_fields(["PEP: 1", "Author: A,", "  B", "Status: Final", "", "Body"])
    assert fields == {"PEP": "1", "Author": "A, B", "Status": "Final"}
    assert end == 4


@pytest.mark.parametrize(
    "lines",
    [["Abstract", "========"], ["http://example.org"], ["", "PEP: 1"], [".. note:: x"], []],
)
def test_no_header_block(lines: list[str]) -> None:
    assert header_fields(lines) == ({}, 0)


# reStructuredText ---------------------------------------------------------


@pytest.fixture
def pep() -> ParsedDocument:
    return parse_file(FIXTURES / "pep-9999.rst")


def test_rst_metadata_title_and_normaliser(pep: ParsedDocument) -> None:
    assert pep.format is SourceFormat.RST
    assert pep.normaliser == "rst/norm-1"
    assert pep.title == "Retention of Example Records"
    assert pep.metadata["Replaces"] == "9998"
    assert pep.metadata["Author"] == "A. Author <a@example.org>, B. Author <b@example.org>"


def test_rst_section_tree(pep: ParsedDocument) -> None:
    assert tree(pep) == [
        (0, "Retention of Example Records"),
        (1, "Abstract"),
        (1, "Specification"),
        (2, "Retention"),
        (2, "Deletion"),
        (3, "Notes"),
        (1, "Copyright"),
    ]


def test_rst_literal_block_is_not_a_heading(pep: ParsedDocument) -> None:
    assert "Not a heading" not in [s.title for s in pep.root.walk()]


def test_rst_section_spans_include_subsections(pep: ParsedDocument) -> None:
    spec = pep.root.children[1]
    text = pep.text_of(spec)
    assert text.startswith("Specification\n=============")
    assert text.endswith("Nested under Deletion.")
    assert "Copyright" not in text
    retention = spec.children[0]
    assert pep.text_of(retention).endswith("-------------")
    assert retention.heading_path == ("Specification", "Retention")


def test_rst_origin_lines(pep: ParsedDocument) -> None:
    lines = pep.text.split("\n")
    for section in pep.root.walk()[1:]:
        line = section.origin["line"]
        assert lines[line - 1].strip() == section.title
    assert pep.root.origin == {"line": 1, "end_line": len(lines) - 1}


def test_rst_overline_style_and_level_order() -> None:
    text = normalise(
        "=====\nTitle\n=====\n\nIntro.\n\nPart\n----\n\nBody.\n\n=======\n Other\n=======\n"
    )
    doc = parse_text(text, SourceFormat.RST, fallback_title="x.rst")
    assert tree(doc) == [(0, "x.rst"), (1, "Title"), (2, "Part"), (1, "Other")]
    # The overline is part of the section.
    assert doc.text_of(doc.root.children[0]).startswith("=====\nTitle")


def test_rst_single_top_heading_is_the_title() -> None:
    doc = parse_text(normalise("Guide\n=====\n\nUse\n---\n\nx\n"), SourceFormat.RST, "g.rst")
    assert doc.title == "Guide"


def test_rst_short_underline_and_transition_are_not_headings() -> None:
    text = normalise("Long title\n----\n\npara\n\n--------\n\nmore\n")
    assert tree(parse_text(text, SourceFormat.RST, "t.rst")) == [(0, "t.rst")]


def test_rst_simple_table_is_not_a_heading() -> None:
    text = normalise("=====  =====\nA      B\n=====  =====\n1      2\n=====  =====\n")
    assert tree(parse_text(text, SourceFormat.RST, "t.rst")) == [(0, "t.rst")]


def test_rst_heading_needs_blank_line_before() -> None:
    text = normalise("para line\nTitle\n=====\n")
    assert tree(parse_text(text, SourceFormat.RST, "t.rst")) == [(0, "t.rst")]


# Markdown -----------------------------------------------------------------


@pytest.fixture
def md() -> ParsedDocument:
    return parse_file(FIXTURES / "policy.md")


def test_markdown_tree_and_title(md: ParsedDocument) -> None:
    assert md.normaliser == "md/norm-1"
    assert md.title == "Policy"
    assert md.metadata == {}
    assert tree(md) == [
        (0, "Policy"),
        (1, "Policy"),
        (2, "4 Records"),
        (3, "4.2 Retention"),
        (2, "5 Review"),
    ]


def test_markdown_fenced_code_is_not_a_heading(md: ParsedDocument) -> None:
    assert "not a heading" not in md.text_of(md.root.children[0].children[1])
    assert "# not a heading" in md.text_of(md.root.children[0].children[0])


def test_markdown_crlf_and_utf8_byte_ranges(md: ParsedDocument) -> None:
    assert "\r" not in md.text
    review = md.root.children[0].children[1]
    assert md.text_of(review) == "## 5 Review\n\nCafé review."
    data = md.text.encode("utf-8")
    assert review.byte_offset + review.byte_length == len(data.rstrip())


def test_markdown_setext_and_closing_hashes() -> None:
    text = normalise("Title\n=====\n\nSub\n---\n\n## Closed ##\n\n#\n\n#hashtag\n")
    doc = parse_text(text, SourceFormat.MARKDOWN, "t.md")
    assert tree(doc) == [(0, "Title"), (1, "Title"), (2, "Sub"), (2, "Closed")]


def test_markdown_thematic_break_after_paragraph_block_is_not_setext() -> None:
    text = normalise("line one\nline two\n---\n\n---\n")
    assert tree(parse_text(text, SourceFormat.MARKDOWN, "t.md")) == [(0, "t.md")]


def test_markdown_metadata_lookalike_ignored() -> None:
    doc = parse_text(normalise("Title: not a header\n\n# Real\n"), SourceFormat.MARKDOWN)
    assert doc.metadata == {} and doc.title == "Real"


# Plain text ---------------------------------------------------------------


def test_plain_text_is_one_section() -> None:
    doc = parse_file(FIXTURES / "notes.txt")
    assert doc.normaliser == "text/norm-1"
    assert doc.title == "notes.txt"
    assert tree(doc) == [(0, "notes.txt")]
    assert doc.text_of(doc.root) == "Plain notes.\nNo structure here."


def test_empty_document() -> None:
    doc = parse_text("", SourceFormat.TEXT, "empty.txt")
    assert doc.root.byte_length == 0 and doc.root.children == ()


# Tree invariants ----------------------------------------------------------


@pytest.mark.parametrize("name", ["pep-9999.rst", "policy.md", "notes.txt"])
def test_children_lie_inside_parents(name: str) -> None:
    doc = parse_file(FIXTURES / name)

    def check(section: Section) -> None:
        end = section.byte_offset + section.byte_length
        for child in section.children:
            assert section.byte_offset <= child.byte_offset
            assert child.byte_offset + child.byte_length <= end
            assert child.level > section.level
            check(child)

    check(doc.root)
    assert doc.root.byte_offset == 0


def test_text_formats_cannot_be_parsed_as_pdf() -> None:
    with pytest.raises(ParseError, match="not a text format"):
        parse_text("x\n", SourceFormat.PDF)
