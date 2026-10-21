from pathlib import Path

import pytest

from mds.ingest.parse import SourceFormat, normalise, parse_file, parse_text
from mds.ingest.references import (
    AUTHOR_ID,
    WEIGHT_TERM,
    DocumentIndex,
    Placeholder,
    Reference,
    SectionKey,
    Target,
    code_mask,
    extract_all,
    extract_references,
    heading_label,
    index_documents,
    pairs,
    slugify,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "references"

A = "pep-9001.rst"
B = "pep-9002.rst"
C = "contract.md"
AGREEMENT = "Services Agreement"


def s(doc: str, *path: str) -> SectionKey:
    return SectionKey(doc, tuple(path))


def c(*path: str) -> SectionKey:
    return SectionKey(C, (AGREEMENT, *path))


# Expected (source, target) pairs, written by hand from reading the fixtures.
EXPECTED: set[tuple[SectionKey, Target]] = {
    # pep-9001.rst
    (s(A), s(B)),  # Requires: 9002
    (s(A), Placeholder("pep", "9500")),  # Requires: 9500
    (s(A), Placeholder("url", "https://discuss.example.org/t/1")),  # Post-History link
    (s(A, "Abstract"), s(B)),  # :pep:`9002`
    (s(A, "Abstract"), Placeholder("pep", "9500")),  # PEP 9500
    (s(A, "Abstract"), s(A, "Rationale")),  # `Rationale`_
    (s(A, "Specification"), Placeholder("rfc", "2616")),
    (s(A, "Specification"), s(B, "Rationale")),  # :pep:`... <9002#rationale>`
    (s(A, "Specification"), s(A, "Appendix A: Worked examples")),  # Appendix A
    (s(A, "Specification"), s(B)),  # peps.python.org link
    (s(A, "Specification"), Placeholder("ref", "typing:generics")),
    (s(A, "Specification"), Placeholder("url", "https://example.org/glossary")),  # `glossary`_
    (s(A, "Rationale"), s(A, "Specification")),  # :ref:`spec-label`
    (s(A, "Appendix A: Worked examples"), s(A, "Abstract")),  # `Abstract`_
    (s(A, "Appendix A: Worked examples"), s(A, "Specification")),  # "the Specification" (vague)
    # pep-9002.rst
    (s(B, "Abstract"), s(A, "Appendix A: Worked examples")),  # Appendix A of PEP 9001
    (s(B, "Abstract"), s(A)),  # section 2 of PEP 9001 (no such section: the document)
    (s(B, "Rationale"), s(A)),  # PEPs 9001 and 9003
    (s(B, "Rationale"), Placeholder("pep", "9003")),
    (s(B, "Rationale"), s(A, "Rationale")),  # `PEP 9001 <pep-9001.rst#rationale>`_
    # contract.md
    (c("1 Definitions"), c("2 Services")),  # clause 2
    (c("1 Definitions"), c("Schedule 1 Pricing")),  # Schedule 1
    (c("2 Services"), c("1 Definitions")),  # uses "Supplier" and "Services"
    (c("2 Services", "2.1 Scope"), c("2 Services")),  # clauses 2 and 3
    (c("2 Services", "2.1 Scope"), c("3 Fees")),  # clauses 2 and 3; [..](#3-fees)
    (c("2 Services", "2.1 Scope"), s(B, "Rationale")),  # [..](pep-9002.rst#rationale)
    (c("3 Fees"), c("Schedule 1 Pricing")),
    (c("3 Fees"), Placeholder("url", "https://example.com/terms")),
    (c("3 Fees"), c("1 Definitions")),  # "the Definitions section" (vague)
    (c("Schedule 1 Pricing"), c("1 Definitions")),  # uses "Services"
}


@pytest.fixture(scope="module")
def index() -> DocumentIndex:
    return index_documents({p.name: parse_file(p) for p in sorted(FIXTURES.iterdir())})


@pytest.fixture(scope="module")
def refs(index: DocumentIndex) -> list[Reference]:
    return extract_all(index)


def precision_recall(
    found: set[tuple[SectionKey, Target]], expected: set[tuple[SectionKey, Target]]
) -> tuple[float, float]:
    hits = len(found & expected)
    return hits / len(found) if found else 1.0, hits / len(expected) if expected else 1.0


# Acceptance: precision and recall on the fixtures --------------------------


def test_precision_and_recall_on_fixtures(refs: list[Reference]) -> None:
    found = pairs(refs)
    precision, recall = precision_recall(found, EXPECTED)
    print(
        f"\nreferences/1 on {len(EXPECTED)} expected pairs: "
        f"precision {precision:.3f}, recall {recall:.3f}"
    )
    for pair in sorted(found - EXPECTED, key=str):
        print("  unexpected:", pair)
    for pair in sorted(EXPECTED - found, key=str):
        print("  missed:", pair)
    assert precision == 1.0
    assert recall >= 0.9


@pytest.mark.parametrize("doc", [A, B, C])
def test_precision_and_recall_per_document(refs: list[Reference], doc: str) -> None:
    found = {p for p in pairs(refs) if p[0].doc == doc}
    expected = {p for p in EXPECTED if p[0].doc == doc}
    precision, recall = precision_recall(found, expected)
    print(f"\n{doc}: precision {precision:.3f}, recall {recall:.3f}")
    assert precision == 1.0
    assert recall >= 0.85


def test_only_the_vague_references_are_missed(refs: list[Reference]) -> None:
    # Bare heading words ("the Definitions section") are not pattern rules in v1.
    assert EXPECTED - pairs(refs) == {
        (s(A, "Appendix A: Worked examples"), s(A, "Specification")),
        (c("3 Fees"), c("1 Definitions")),
    }


# Result shape -------------------------------------------------------------


def test_spans_are_utf8_byte_ranges_of_the_matched_text(
    index: DocumentIndex, refs: list[Reference]
) -> None:
    for ref in refs:
        data = index.docs[ref.source.doc].text.encode("utf-8")
        assert data[ref.span[0] : ref.span[1]].decode("utf-8") == ref.text


def test_author_id_and_weights(refs: list[Reference]) -> None:
    assert AUTHOR_ID == "references/1"
    assert {r.author_id for r in refs} == {AUTHOR_ID}
    assert {r.weight for r in refs if r.rule != "term"} == {1.0}
    assert {r.weight for r in refs if r.rule == "term"} == {WEIGHT_TERM}


def test_no_self_references(refs: list[Reference]) -> None:
    assert all(r.source != r.target for r in refs)
    # "This PEP 9001" inside PEP 9001 names the document itself.
    assert not [r for r in refs if r.source.doc == A and r.text == "PEP 9001"]


def test_output_is_deterministic(index: DocumentIndex) -> None:
    assert extract_all(index) == extract_all(index)


def test_rules_are_recorded(refs: list[Reference]) -> None:
    by_text = {(r.source.doc, r.text): r.rule for r in refs}
    assert by_text[(A, "9500")] == "requires"
    assert by_text[(A, ":pep:`9002`")] == "role"
    assert by_text[(A, "`Rationale`_")] == "named"
    assert by_text[(A, "Appendix A")] == "structural"
    assert by_text[(A, "PEP 9500")] == "cited"
    assert by_text[(C, "Supplier")] == "term"
    assert by_text[(C, "https://example.com/terms")] == "link"


# Individual rules ---------------------------------------------------------


def one(text: str, fmt: SourceFormat = SourceFormat.MARKDOWN, key: str = "doc.md") -> DocumentIndex:
    return index_documents({key: parse_text(normalise(text), fmt)})


def targets(index: DocumentIndex, key: str = "doc.md") -> list[Target]:
    return [r.target for r in extract_references(key, index)]


def test_code_is_skipped_in_markdown_and_rst() -> None:
    md = one("# T\n\n```\nPEP 1\n```\n\nand `PEP 2` but PEP 3\n")
    assert targets(md) == [Placeholder("pep", "3")]
    rst = one(
        "T\n=\n\nCode::\n\n    PEP 1\n\nand ``PEP 2`` and\n\n.. code-block:: text\n\n   PEP 4\n\n"
        "but PEP 3\n",
        SourceFormat.RST,
        "doc.rst",
    )
    assert targets(rst, "doc.rst") == [Placeholder("pep", "3")]


def test_code_mask_covers_unterminated_fence() -> None:
    text = "a\n```\nPEP 1\n"
    assert code_mask(text, SourceFormat.MARKDOWN) == [(2, len(text))]


def test_unresolved_internal_structural_reference_is_dropped() -> None:
    assert targets(one("# T\n\n## 1 One\n\nSee clause 4 and section 1.\n")) == []


def test_structural_reference_to_other_standard_is_not_resolved_locally() -> None:
    idx = one("# T\n\n## 3 Three\n\nSee section 3 of RFC 2616.\n\n## 4 Four\n")
    assert targets(idx) == [Placeholder("rfc", "2616")]


def test_structural_reference_of_this_document() -> None:
    idx = one("# T\n\n## 3 Three\n\n## 4 Four\n\nAs section 3 of this document says.\n")
    assert targets(idx) == [s("doc.md", "T", "3 Three")]


def test_subclause_falls_back_to_clause() -> None:
    idx = one("# T\n\n## 4.2 Retention\n\n## 5 Other\n\nSee clause 4.2(a).\n")
    assert targets(idx) == [s("doc.md", "T", "4.2 Retention")]


def test_lettered_references_need_matching_word() -> None:
    idx = one("# T\n\n## Appendix A Forms\n\n## Schedule 2\n\nSee Schedule A and Appendix 2.\n")
    assert targets(idx) == []


def test_placeholders_for_unknown_external_targets() -> None:
    idx = one("# T\n\nSee PEP 0008, RFCs 1 and 2, [x](other.md) and <https://a.example/b>.\n")
    assert targets(idx) == [
        Placeholder("pep", "8"),
        Placeholder("rfc", "1"),
        Placeholder("rfc", "2"),
        Placeholder("url", "other.md"),
        Placeholder("url", "https://a.example/b"),
    ]


def test_rst_label_not_above_a_heading_names_the_section_holding_it() -> None:
    text = "Aa\n==\n\n.. _item:\n\nAn item.\n\nBb\n==\n\nSee :ref:`item`.\n"
    idx = one(text, SourceFormat.RST, "doc.rst")
    assert targets(idx, "doc.rst") == [s("doc.rst", "Aa")]


def test_rst_named_reference_ignores_unknown_names() -> None:
    idx = one("Aa\n==\n\nSee `Nowhere`_, `Aa`_ and `Bb`_.\n\nBb\n==\n", SourceFormat.RST, "doc.rst")
    # `Aa`_ inside Aa is a self-reference.
    assert targets(idx, "doc.rst") == [s("doc.rst", "Bb")]


def test_index_aliases() -> None:
    doc = parse_text(normalise("PEP: 12\nTitle: T\n\nBody\n"), SourceFormat.RST)
    other = parse_text(normalise("# X\n"), SourceFormat.MARKDOWN)
    idx = index_documents({"public/pep-0012.rst": doc, "a/x.md": other, "b/x.md": other})
    assert idx.lookup("pep:12") == "public/pep-0012.rst"
    assert idx.lookup("pep-0012.rst") == "public/pep-0012.rst"
    assert idx.lookup("pep-0012") == "public/pep-0012.rst"
    assert idx.lookup("x.md") is None  # claimed by two documents


def test_heading_label() -> None:
    assert heading_label("4.2 Retention") == ("", "4.2")
    assert heading_label("Appendix A: Forms") == ("appendix", "A")
    assert heading_label("Schedule 2") == ("schedule", "2")
    assert heading_label("1. Introduction") == ("", "1")
    assert heading_label("A note on style") is None
    assert heading_label("Appendix: Notes") is None


def test_slugify() -> None:
    assert slugify("3 Fees") == "3-fees"
    assert slugify("Rationale") == "rationale"
    assert slugify("Use of ``with``!") == "use-of-with"
