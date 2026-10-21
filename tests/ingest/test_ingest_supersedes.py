from datetime import date
from pathlib import Path

import pytest
import yaml

from mds.ingest.parse import ParsedDocument, SourceFormat, parse_file, parse_text
from mds.ingest.supersedes import (
    AUTHOR_ID,
    DateSource,
    Span,
    SupersedesEdge,
    build_index,
    extract_from_metadata,
    extract_from_text,
    extract_supersedes,
    is_placeholder,
    merge_edges,
    parse_date,
    placeholder_key,
    sentences,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "supersedes"

Triple = tuple[str, str, date | None]


def load_corpus(name: str) -> dict[str, ParsedDocument]:
    root = FIXTURES / name
    return {
        p.relative_to(root).as_posix(): parse_file(p)
        for p in sorted(root.rglob("*"))
        if p.suffix in (".md", ".rst")
    }


def load_expected(name: str) -> set[Triple]:
    rows = yaml.safe_load((FIXTURES / name / "expected.yaml").read_text())
    return {(r["src"], r["dst"], r["valid_from"]) for r in rows}


def triples(edges: list[SupersedesEdge]) -> set[Triple]:
    return {(e.src, e.dst, e.valid_from) for e in edges}


def score(got: set[Triple], expected: set[Triple]) -> tuple[float, float]:
    """Precision and recall over (src, dst, valid_from) triples: a wrong date is a miss."""
    hits = len(got & expected)
    return hits / len(got) if got else 1.0, hits / len(expected) if expected else 1.0


def report(capsys: pytest.CaptureFixture[str], name: str, precision: float, recall: float) -> None:
    with capsys.disabled():
        print(f"\n[supersedes/{name}] precision={precision:.2f} recall={recall:.2f}")


def pep(number: int, headers: str = "", body: str = "Body.", created: str = "01-Jan-2020") -> str:
    return (
        f"PEP: {number}\nTitle: Test Proposal {number}\nStatus: Final\n{headers}"
        f"Created: {created}\n\n\nAbstract\n========\n\n{body}\n"
    )


def rst(text: str) -> ParsedDocument:
    return parse_text(text, SourceFormat.RST)


def md(text: str) -> ParsedDocument:
    return parse_text(text, SourceFormat.MARKDOWN)


# Precision and recall on fixtures ------------------------------------------


def test_metadata_fixtures_match_hand_written_edges(capsys: pytest.CaptureFixture[str]) -> None:
    got = triples(extract_supersedes(load_corpus("metadata")))
    expected = load_expected("metadata")
    precision, recall = score(got, expected)
    report(capsys, "metadata", precision, recall)
    assert got == expected


def test_phrase_fixtures_precision_and_recall(capsys: pytest.CaptureFixture[str]) -> None:
    got = triples(extract_supersedes(load_corpus("phrases")))
    expected = load_expected("phrases")
    precision, recall = score(got, expected)
    report(capsys, "phrases", precision, recall)
    # The fixtures include one known false positive and two phrasings the v1 rules
    # do not cover; see expected.yaml.
    assert precision >= 0.85
    assert recall >= 0.75
    assert ("pep-0102.rst", "pep-0100.rst", date(2010, 2, 2)) in got - expected


# Metadata rules (DESIGN §9.1) -----------------------------------------------


def test_superseded_by_gives_edge_from_the_replacement() -> None:
    docs = {"a.rst": rst(pep(1, "Superseded-By: 2\n")), "b.rst": rst(pep(2, created="2021-05-06"))}
    [edge] = extract_supersedes(docs)
    assert (edge.src, edge.dst) == ("b.rst", "a.rst")
    assert edge.valid_from == date(2021, 5, 6)
    assert edge.date_source is DateSource.CREATED


def test_replaces_gives_edge_from_the_declaring_document() -> None:
    docs = {"a.rst": rst(pep(1)), "b.rst": rst(pep(2, "Replaces: 1\n", created="06-May-2021"))}
    [edge] = extract_supersedes(docs)
    assert (edge.src, edge.dst, edge.valid_from) == ("b.rst", "a.rst", date(2021, 5, 6))


def test_both_headers_for_one_pair_give_one_edge_with_both_spans() -> None:
    docs = {
        "a.rst": rst(pep(1, "Superseded-By: 2\n")),
        "b.rst": rst(pep(2, "Replaces: 1\n")),
    }
    [edge] = extract_supersedes(docs)
    assert {span.rule for span in edge.evidence} == {"metadata:Superseded-By", "metadata:Replaces"}
    assert {span.doc_key for span in edge.evidence} == {"a.rst", "b.rst"}


def test_target_outside_corpus_becomes_placeholder() -> None:
    docs = {"a.rst": rst(pep(1, "Superseded-By: 600\n")), "b.rst": rst(pep(2, "Replaces: 77\n"))}
    edges = {(e.src, e.dst): e for e in extract_supersedes(docs)}
    assert set(edges) == {("placeholder:PEP 600", "a.rst"), ("b.rst", "placeholder:PEP 77")}
    unknown = edges[("placeholder:PEP 600", "a.rst")]
    assert unknown.valid_from is None and unknown.date_source is DateSource.UNKNOWN


def test_header_lists_and_continuations_give_one_edge_per_target() -> None:
    docs = {"a.rst": rst(pep(4, "Replaces: 1, 3,\n  PEP 77\n"))}
    targets = [e.dst for e in extract_supersedes(docs)]
    assert targets == ["placeholder:PEP 1", "placeholder:PEP 3", "placeholder:PEP 77"]


def test_effective_date_header_overrides_created() -> None:
    docs = {
        "a.rst": rst(pep(1, "Superseded-By: 2\n")),
        "b.rst": rst(pep(2, "Effective-Date: 1 March 2022\n", created="01-Jan-2021")),
    }
    [edge] = extract_supersedes(docs)
    assert edge.valid_from == date(2022, 3, 1) and edge.date_source is DateSource.EFFECTIVE


def test_effective_sentence_overrides_created_for_metadata_edges() -> None:
    body = "This PEP takes effect from 2022-03-01."
    docs = {"a.rst": rst(pep(1)), "b.rst": rst(pep(2, "Replaces: 1\n", body=body))}
    [edge] = extract_supersedes(docs)
    assert edge.valid_from == date(2022, 3, 1)


def test_superseded_directive_reads_like_superseded_by() -> None:
    docs = {"a.rst": rst(pep(1, body=".. superseded:: 2")), "b.rst": rst(pep(2))}
    [edge] = extract_supersedes(docs)
    assert (edge.src, edge.dst) == ("b.rst", "a.rst")
    assert edge.evidence[0].rule == "directive:superseded"


def test_header_fields_are_not_read_as_amendment_wording() -> None:
    doc = rst(pep(2, "Title-Note: This PEP supersedes PEP 1\n"))
    assert extract_from_text("b.rst", doc, build_index({"b.rst": doc})) == []


def test_metadata_evidence_span_points_at_the_header() -> None:
    doc = rst(pep(2, "Replaces: 1, 3,\n  5\n"))
    [first, *_] = extract_from_metadata("b.rst", doc, build_index({"b.rst": doc}))
    span = first.evidence[0]
    covered = doc.text.encode()[span.byte_offset : span.byte_offset + span.byte_length].decode()
    assert covered == "Replaces: 1, 3,\n  5"


# Amendment wording -----------------------------------------------------------


def phrase_edges(docs: dict[str, ParsedDocument]) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in extract_supersedes(docs)}


@pytest.mark.parametrize(
    "sentence",
    [
        "This PEP supersedes PEP 1.",
        "This PEP hereby replaces :pep:`1`.",
        "This document amends PEP 1 in two places.",
        "PEP 1 is superseded by this PEP.",
        "Section 3 of PEP 1 is deleted and replaced by the following.",
        "Clause 2 of :pep:`Test Proposal 1 <1>` is amended to read as follows.",
        "Note: *this PEP was superseded by* nothing; PEP 1 has been superseded by this PEP.",
    ],
)
def test_phrase_rules_find_the_amended_document(sentence: str) -> None:
    docs = {"a.rst": rst(pep(1)), "b.rst": rst(pep(2, body=sentence))}
    assert phrase_edges(docs) == {("b.rst", "a.rst")}


def test_passive_rule_names_the_replacement() -> None:
    body = "*Note: this PEP was superseded by* :pep:`3`, *which has been accepted.*"
    docs = {"a.rst": rst(pep(1, body=body)), "c.rst": rst(pep(3))}
    assert phrase_edges(docs) == {("c.rst", "a.rst")}


@pytest.mark.parametrize(
    "sentence",
    [
        "This PEP supplements rather than supersedes PEP 1.",
        "If accepted, this PEP would *supersede* :pep:`1`.",
        "This PEP does not replace PEP 1.",
        "PEP 1 is in danger of being superseded.",
        "This PEP has been superseded by the newer specification.",
        "The reactor in PEP 1 was replaced by an event loop.",
        "PEP 1 would be superseded by this PEP.",
    ],
)
def test_hedged_negated_or_unnamed_wording_gives_nothing(sentence: str) -> None:
    docs = {"a.rst": rst(pep(1)), "b.rst": rst(pep(2, body=sentence))}
    assert phrase_edges(docs) == set()


def test_title_and_file_name_targets_resolve_to_held_documents() -> None:
    docs = {
        "old/retention.md": md("# Records Retention Policy\n\nSix years.\n"),
        "notes.md": md("# Notes\n\nNothing.\n"),
        "new.md": md(
            "# New\n\nThis policy supersedes the records retention policy.\n\n"
            "Clause 1 of notes.md is deleted and replaced.\n"
        ),
    }
    assert phrase_edges(docs) == {("new.md", "old/retention.md"), ("new.md", "notes.md")}


def test_unnamed_non_identifier_target_gives_nothing() -> None:
    docs = {"new.md": md("# New\n\nThis policy supersedes the Records Retention Policy.\n")}
    assert phrase_edges(docs) == set()


def test_with_effect_from_sets_valid_from_for_that_sentence() -> None:
    body = (
        "This PEP replaces PEP 1 with effect from 1st March 2023.\n\n"
        "This PEP also supersedes PEP 3."
    )
    docs = {"a.rst": rst(pep(1)), "c.rst": rst(pep(3)), "b.rst": rst(pep(2, body=body))}
    dates = {e.dst: e.valid_from for e in extract_supersedes(docs)}
    # The first sentence is about this PEP, so its date applies to the whole document.
    assert dates == {"a.rst": date(2023, 3, 1), "c.rst": date(2023, 3, 1)}


def test_sentence_effective_date_beats_document_effective_date() -> None:
    body = (
        "This PEP comes into force on 1 January 2023.\n\n"
        "PEP 1 is deleted and replaced with effect from March 5, 2023."
    )
    docs = {"a.rst": rst(pep(1)), "b.rst": rst(pep(2, "Replaces: 3\n", body=body))}
    dates = {e.dst: e.valid_from for e in extract_supersedes(docs)}
    assert dates == {"a.rst": date(2023, 3, 5), "placeholder:PEP 3": date(2023, 1, 1)}


def test_merged_edge_prefers_explicit_date_and_highest_weight() -> None:
    body = "This PEP replaces PEP 1 with effect from 2023-03-01."
    docs = {"a.rst": rst(pep(1)), "b.rst": rst(pep(2, "Replaces: 1\n", body=body))}
    [edge] = extract_supersedes(docs)
    assert edge.valid_from == date(2023, 3, 1) and edge.date_source is DateSource.EFFECTIVE
    assert edge.weight == 1.0 and len(edge.evidence) == 2


def test_phrase_evidence_span_covers_the_sentence() -> None:
    body = "Intro. Café rules apply. This PEP\nsupersedes PEP 1. After."
    doc = rst(pep(2, body=body))
    [edge] = extract_from_text("b.rst", doc, build_index({"b.rst": doc}))
    span = edge.evidence[0]
    covered = doc.text.encode()[span.byte_offset : span.byte_offset + span.byte_length].decode()
    assert covered == "This PEP\nsupersedes PEP 1."
    assert span == Span("b.rst", span.byte_offset, span.byte_length, "phrase:supersedes")


def test_fenced_code_is_ignored() -> None:
    doc = md("# New\n\n```\nThis policy supersedes PEP 1.\n```\n")
    assert extract_from_text("new.md", doc, build_index({"new.md": doc})) == []


def test_self_reference_gives_no_self_loop() -> None:
    docs = {"b.rst": rst(pep(2, "Replaces: 2\n", body="This PEP supersedes PEP 2."))}
    assert extract_supersedes(docs) == []


# Result type and helpers -------------------------------------------------------


def test_edges_carry_author_id_and_open_valid_to() -> None:
    edges = extract_supersedes(load_corpus("metadata"))
    assert edges
    assert all(e.author_id == AUTHOR_ID == "supersedes/1" for e in edges)
    assert all(e.valid_to is None for e in edges)
    assert all(0.0 < e.weight <= 1.0 for e in edges)


def test_extraction_is_deterministic() -> None:
    docs = load_corpus("phrases")
    assert extract_supersedes(docs) == extract_supersedes(dict(reversed(list(docs.items()))))


def test_placeholder_keys() -> None:
    assert placeholder_key("PEP 600") == "placeholder:PEP 600"
    assert is_placeholder("placeholder:PEP 600") and not is_placeholder("pep-0600.rst")


def test_merge_keeps_distinct_pairs_apart() -> None:
    span = Span("x", 0, 1, "r")
    a = SupersedesEdge("b", "a", date(2020, 1, 1), 0.8, (span,), DateSource.CREATED)
    c = SupersedesEdge("c", "a", None, 1.0, (span,), DateSource.UNKNOWN)
    assert merge_edges([c, a, a]) == [a, c]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2023-03-01", date(2023, 3, 1)),
        ("01-Mar-2023", date(2023, 3, 1)),
        ("1-March-2023", date(2023, 3, 1)),
        ("1 March 2023", date(2023, 3, 1)),
        ("21st Sept 2023", date(2023, 9, 21)),
        ("March 1, 2023", date(2023, 3, 1)),
        ("  09-Jan-2002 ", date(2002, 1, 9)),
        ("31-Feb-2023", None),
        ("2023-13-01", None),
        ("soon", None),
        ("", None),
    ],
)
def test_parse_date(text: str, expected: date | None) -> None:
    assert parse_date(text) == expected


def test_sentences_split_on_full_stops_and_blank_lines() -> None:
    text = "One two. Three\nfour.\n\nfive six\n"
    assert [text[s:e] for s, e in sentences(text)] == ["One two.", "Three\nfour.", "five six"]
