"""The Docling adapter, driven by fakes shaped like Docling's document API (no models needed)."""

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mds.ingest._docling import docling_normaliser, from_docling_document
from mds.ingest.parse import ParseError, SourceFormat, parse_file


@dataclass
class BBox:
    l: float  # noqa: E741 - Docling's field name
    t: float
    r: float
    b: float


@dataclass
class Prov:
    page_no: int
    bbox: BBox


@dataclass
class Item:
    label: str
    text: str = ""
    level: int = 1
    prov: list[Prov] = field(default_factory=list)
    table_md: str | None = None

    def export_to_markdown(self, doc: Any = None) -> str:
        return self.table_md or ""


@dataclass
class FakeDoc:
    items: list[Item]

    def iterate_items(self) -> list[tuple[Item, int]]:
        return [(item, 0) for item in self.items]


def p(page: int) -> list[Prov]:
    return [Prov(page, BBox(10.0, 700.0, 500.0, 680.0))]


def sample() -> FakeDoc:
    return FakeDoc(
        [
            Item("page_header", "ACME Ltd — confidential", prov=p(1)),
            Item("title", "Records Policy", prov=p(1)),
            Item("section_header", "4 Records", level=1, prov=p(1)),
            Item("text", "Records are kept.  ", prov=p(1)),
            Item("section_header", "4.2 Retention", level=2, prov=p(2)),
            Item("list_item", "Six years.", prov=p(2)),
            Item("table", table_md="| a | b |\n|---|---|\n| 1 | 2 |", prov=p(2)),
            Item("picture", "", prov=p(2)),
            Item("section_header", "5 Review", level=1, prov=p(3)),
            Item("text", "Café review.", prov=p(3)),
            Item("page_footer", "Page 3", prov=p(3)),
        ]
    )


def test_tree_from_docling_document() -> None:
    doc = from_docling_document(sample(), SourceFormat.PDF, "docling-2.x/norm-1", "p.pdf")
    assert doc.title == "Records Policy"
    assert doc.normaliser == "docling-2.x/norm-1"
    assert [(s.level, s.title) for s in doc.root.walk()] == [
        (0, "Records Policy"),
        (1, "Records Policy"),
        (2, "4 Records"),
        (3, "4.2 Retention"),
        (2, "5 Review"),
    ]


def test_page_furniture_dropped_and_tables_kept() -> None:
    doc = from_docling_document(sample(), SourceFormat.PDF, "n")
    assert "confidential" not in doc.text and "Page 3" not in doc.text
    assert "| 1 | 2 |" in doc.text
    retention = doc.root.children[0].children[0].children[0]
    assert doc.text_of(retention).startswith("4.2 Retention\n\nSix years.")
    assert doc.text_of(retention).endswith("| 1 | 2 |")


def test_origins_are_page_and_bbox() -> None:
    doc = from_docling_document(sample(), SourceFormat.PDF, "n")
    review = doc.root.children[0].children[1]
    assert review.origin == {"page": 3, "bbox": [10.0, 700.0, 500.0, 680.0]}
    assert doc.root.origin["page"] == 1


def test_utf8_byte_ranges() -> None:
    doc = from_docling_document(sample(), SourceFormat.DOCX, "n")
    review = doc.root.children[0].children[1]
    assert doc.text_of(review) == "5 Review\n\nCafé review."


def test_without_title_section_headers_are_top_level() -> None:
    fake = FakeDoc(
        [Item("section_header", "Intro"), Item("text", "x"), Item("section_header", "B")]
    )
    doc = from_docling_document(fake, SourceFormat.DOCX, "n", fallback_title="b.docx")
    assert doc.title == "b.docx"
    assert [(s.level, s.title) for s in doc.root.walk()] == [(0, "b.docx"), (1, "Intro"), (1, "B")]
    assert doc.root.origin == {}


def test_not_a_docling_document() -> None:
    with pytest.raises(ParseError, match="not a Docling document"):
        from_docling_document(object(), SourceFormat.PDF, "n")


def test_normaliser_id_shape() -> None:
    assert docling_normaliser().startswith("docling-")
    assert docling_normaliser().endswith(".x/norm-1")


@pytest.mark.skipif(importlib.util.find_spec("docling") is not None, reason="docling installed")
def test_pdf_without_docling_gives_install_hint(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4")
    with pytest.raises(ParseError, match=r"install mds-server\[pdf\]"):
        parse_file(path)
