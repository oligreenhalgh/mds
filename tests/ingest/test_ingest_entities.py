import math
from pathlib import Path

import graphblas as gb
import pytest

from mds.ingest.entities import (
    CoOccurConfig,
    DictionaryDetector,
    EntityEntry,
    EntityError,
    EntitySpan,
    Match,
    SectionKey,
    co_occurrences,
    corpus_co_occurrences,
    detect_spans,
    document_frequency,
    idf,
    idf_weights,
    incidence,
    kept_entities,
    prune_k,
    section_keys,
)
from mds.ingest.parse import SourceFormat, parse_file, parse_text

FIXTURES = Path(__file__).parent.parent / "fixtures" / "entities"

L = math.log(2.5)  # idf for df = 2 of N = 3
M = math.log(4.0)  # idf for df = 1 of N = 3


def md(text: str):  # type: ignore[no-untyped-def]
    return parse_text(text, SourceFormat.MARKDOWN)


def spans_for(mentions: dict[SectionKey, list[str]]) -> list[EntitySpan]:
    return [
        EntitySpan(key, entity, 0, 1, entity) for key, ents in mentions.items() for entity in ents
    ]


# Span matching -------------------------------------------------------------


def found(detector: DictionaryDetector, text: str) -> list[tuple[str, str]]:
    return [(text[m.start : m.end], m.entity) for m in detector.detect(text)]


def test_longest_match_first() -> None:
    d = DictionaryDetector.from_mapping({"gvr": ["Guido", "Guido van Rossum"], "van": ["van"]})
    assert found(d, "Guido van Rossum met Guido in a van.") == [
        ("Guido van Rossum", "gvr"),
        ("Guido", "gvr"),
        ("van", "van"),
    ]


def test_whole_words_only() -> None:
    d = DictionaryDetector.from_mapping({"py": ["Python"], "pep": ["PEP 8"]})
    assert found(d, "Pythonic PEP 80 CPython (Python) PEP 8.") == [
        ("Python", "py"),
        ("PEP 8", "pep"),
    ]


def test_case_insensitive_by_default() -> None:
    d = DictionaryDetector.from_mapping({"py": ["Python"]})
    assert found(d, "python PYTHON Python") == [
        ("python", "py"),
        ("PYTHON", "py"),
        ("Python", "py"),
    ]


def test_case_sensitive_dictionary_and_entry() -> None:
    d = DictionaryDetector.from_mapping({"py": ["Python"]}, case_sensitive=True)
    assert found(d, "python Python") == [("Python", "py")]
    d = DictionaryDetector(
        [EntityEntry("gil", ("GIL",), case_sensitive=True), EntityEntry("py", ("Python",))]
    )
    assert found(d, "gil GIL python") == [("GIL", "gil"), ("python", "py")]


def test_case_sensitive_entry_wins_over_insensitive() -> None:
    d = DictionaryDetector(
        [EntityEntry("acronym", ("ABC",), case_sensitive=True), EntityEntry("word", ("abc",))]
    )
    assert found(d, "ABC abc Abc") == [("ABC", "acronym"), ("abc", "word"), ("Abc", "word")]


def test_key_is_the_name_when_no_names_given() -> None:
    d = DictionaryDetector([EntityEntry("Tantivy", ())])
    assert found(d, "uses tantivy") == [("tantivy", "Tantivy")]


def test_empty_dictionary_finds_nothing() -> None:
    assert DictionaryDetector([]).detect("anything") == []


def test_name_mapped_to_two_entities_is_rejected() -> None:
    with pytest.raises(EntityError, match="maps to both"):
        DictionaryDetector.from_mapping({"a": ["Name"], "b": ["name"]})


def test_empty_names_and_keys_are_rejected() -> None:
    with pytest.raises(EntityError, match="empty name"):
        DictionaryDetector.from_mapping({"a": ["  "]})
    with pytest.raises(EntityError, match="key"):
        DictionaryDetector([EntityEntry("", ("x",))])


def test_detector_protocol_accepts_other_detectors() -> None:
    class Fixed:
        def detect(self, text: str) -> list[Match]:
            return [Match(0, 3, "e")] if text.startswith("# A") else []

    spans = detect_spans("d", md("# A\n\nbody\n"), Fixed())
    assert [(s.entity, s.text) for s in spans] == [("e", "# A")]


# Spans in documents ----------------------------------------------------------


def test_spans_use_own_text_and_byte_offsets() -> None:
    doc = md("# Café\n\nGuido here.\n\n## Sub\n\nGuido again.\n")
    detector = DictionaryDetector.from_mapping({"gvr": ["Guido"]})
    spans = detect_spans("d", doc, detector)
    # Each mention belongs to exactly one section: the parent does not repeat the child's.
    assert [(s.section, s.entity) for s in spans] == [
        (("d", ("Café",), 0), "gvr"),
        (("d", ("Café", "Sub"), 0), "gvr"),
    ]
    data = doc.text.encode("utf-8")
    for s in spans:
        assert data[s.byte_offset : s.byte_offset + s.byte_length].decode() == "Guido"
    assert spans[0].byte_offset == doc.text.index("Guido") + 1  # "é" is two bytes


def test_repeated_heading_paths_are_numbered() -> None:
    doc = md("# A\n\n## Ex\n\none\n\n## Ex\n\ntwo\n")
    assert [key for key, _ in section_keys("d", doc)] == [
        ("d", (), 0),
        ("d", ("A",), 0),
        ("d", ("A", "Ex"), 0),
        ("d", ("A", "Ex"), 1),
    ]


def test_root_preamble_is_a_section() -> None:
    doc = parse_text("PEP: 1\nAuthor: Guido\n\nAbstract\n========\n\nText.\n", SourceFormat.RST)
    spans = detect_spans("pep-1", doc, DictionaryDetector.from_mapping({"gvr": ["Guido"]}))
    assert [s.section for s in spans] == [("pep-1", (), 0)]


# IDF and the ceiling ---------------------------------------------------------


S1: SectionKey = ("d1", ("A",), 0)
S2: SectionKey = ("d1", ("B",), 0)
S3: SectionKey = ("d2", ("C",), 0)
S4: SectionKey = ("d3", ("D",), 0)
SECTIONS = [S1, S2, S3, S4]

# x and y in two of three documents, z in one, common in all three.
MENTIONS = {S1: ["x", "y", "common"], S2: ["x", "z"], S3: ["x", "y", "common"], S4: ["common"]}


def test_document_frequency_counts_documents_not_sections() -> None:
    assert document_frequency(spans_for(MENTIONS)) == {"x": 2, "y": 2, "z": 1, "common": 3}


def test_idf_values() -> None:
    assert idf(2, 3) == pytest.approx(L)
    assert idf(1, 3) == pytest.approx(M)
    assert idf(3, 3) == pytest.approx(math.log(2.0))


def test_ceiling_drops_entities_above_it_and_keeps_those_at_it() -> None:
    df = {"x": 2, "y": 2, "z": 1, "common": 3}
    assert kept_entities(df, 3, 0.7) == ["x", "y", "z"]  # ceiling 2.1
    assert kept_entities(df, 3, 0.5) == ["z"]  # ceiling 1.5
    assert kept_entities(df, 4, 0.5) == ["x", "y", "z"]  # ceiling 2.0, df 2 kept
    assert kept_entities(df, 3, 1.0) == ["common", "x", "y", "z"]


def test_incidence_and_weights() -> None:
    e = incidence(spans_for(MENTIONS), SECTIONS, ["x", "y", "z"])
    assert (e.nrows, e.ncols) == (4, 3)
    assert sorted(zip(*(a.tolist() for a in e.to_coo()[:2]), strict=True)) == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 2),
        (2, 0),
        (2, 1),
    ]
    w = idf_weights({"x": 2, "y": 2, "z": 1}, ["x", "y", "z"], 3)
    assert w.select("offdiag").new().nvals == 0
    assert w.diag().to_coo()[1].tolist() == pytest.approx([L, L, M])


# Pruning, symmetry, self-loops -----------------------------------------------


def test_prune_k_keeps_top_entries_and_breaks_ties_by_column() -> None:
    m = gb.Matrix.from_coo([0, 0, 0, 1, 1], [1, 2, 3, 0, 2], [2.0, 3.0, 3.0, 1.0, 1.0])
    kept = prune_k(m, 2)
    assert sorted(zip(*(a.tolist() for a in kept.to_coo()), strict=True)) == [
        (0, 2, 3.0),
        (0, 3, 3.0),
        (1, 0, 1.0),
        (1, 2, 1.0),
    ]
    assert sorted(zip(*(a.tolist() for a in prune_k(m, 1).to_coo()[:2]), strict=True)) == [
        (0, 2),
        (1, 0),
    ]


def as_dict(pairs: list) -> dict[tuple[SectionKey, SectionKey], float]:  # type: ignore[type-arg]
    return {(c.a, c.b): c.weight for c in pairs}


def test_co_occurs_hand_computed() -> None:
    pairs = co_occurrences(spans_for(MENTIONS), SECTIONS, CoOccurConfig(top_k=10, max_df=0.7))
    # common (df 3 > 2.1) is dropped, so S4 has no neighbours.
    # s(S1,S1) = s(S3,S3) = 2L, s(S2,S2) = L + M, s(S1,S3) = 2L, s(S1,S2) = s(S2,S3) = L.
    side = L / math.sqrt(2 * L * (L + M))
    assert as_dict(pairs) == pytest.approx(
        {
            (S1, S2): side,
            (S1, S3): 1.0,
            (S2, S1): side,
            (S2, S3): side,
            (S3, S1): 1.0,
            (S3, S2): side,
        }
    )
    shared = {(c.a, c.b): c.entities for c in pairs}
    assert shared[(S1, S3)] == ("x", "y")
    assert shared[(S2, S3)] == ("x",)


def test_identical_rows_weigh_exactly_one() -> None:
    pairs = co_occurrences(spans_for(MENTIONS), SECTIONS, CoOccurConfig(max_df=0.7))
    assert as_dict(pairs)[(S1, S3)] == 1.0


def test_without_ceiling_common_entity_links_everything() -> None:
    pairs = co_occurrences(spans_for(MENTIONS), SECTIONS, CoOccurConfig(max_df=1.0))
    weights = as_dict(pairs)
    c = math.log(2.0)
    assert weights[(S1, S4)] == pytest.approx(c / math.sqrt((2 * L + c) * c))
    assert (S2, S4) not in weights


def test_top_k_prunes_per_row_then_symmetrises() -> None:
    pairs = co_occurrences(spans_for(MENTIONS), SECTIONS, CoOccurConfig(top_k=1, max_df=0.7))
    # S1 keeps S3 (2L); S2 ties S1 and S3 at L and keeps S1 (lower index); S3 keeps S1.
    # The union is symmetric, so S1 → S2 survives though S1 did not pick it; S2–S3 does not.
    assert sorted(as_dict(pairs)) == [(S1, S2), (S1, S3), (S2, S1), (S3, S1)]


def test_symmetric_no_self_loops_weights_in_range() -> None:
    for k in (1, 2, 10):
        pairs = co_occurrences(spans_for(MENTIONS), SECTIONS, CoOccurConfig(top_k=k, max_df=1.0))
        weights = as_dict(pairs)
        assert weights
        for (a, b), w in weights.items():
            assert a != b
            assert weights[(b, a)] == w
            assert 0.0 < w <= 1.0


def test_nothing_survives_the_ceiling() -> None:
    assert co_occurrences(spans_for(MENTIONS), SECTIONS, CoOccurConfig(max_df=0.3)) == []
    assert co_occurrences([], SECTIONS) == []


def test_config_validation() -> None:
    with pytest.raises(EntityError, match="top_k"):
        CoOccurConfig(top_k=0)
    for bad in (0.0, 1.5):
        with pytest.raises(EntityError, match="max_df"):
            CoOccurConfig(max_df=bad)


# Whole corpus ------------------------------------------------------------------


def test_corpus_from_fixtures() -> None:
    docs = {p.stem: parse_file(p) for p in sorted(FIXTURES.glob("*.md"))}
    detector = DictionaryDetector.from_mapping(
        {
            "guido": ["Guido van Rossum", "Guido"],
            "gil": ["GIL", "global interpreter lock"],
            "python": ["Python"],
            "tantivy": ["Tantivy"],
        }
    )
    spans, pairs = corpus_co_occurrences(docs, detector, CoOccurConfig(top_k=5, max_df=0.7))
    assert document_frequency(spans) == {"guido": 2, "gil": 2, "python": 3, "tantivy": 1}
    keys = {(c.a, c.b) for c in pairs}
    threads = ("alpha", ("Alpha", "Threads"), 0)
    locking = ("beta", ("Beta", "Locking"), 0)
    assert (threads, locking) in keys and (locking, threads) in keys
    # python is in every document, so it links nothing on its own.
    assert all("python" not in c.entities for c in pairs)
