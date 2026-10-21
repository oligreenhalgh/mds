"""Entity spans and `co_occurs` (r2) construction (DESIGN §3.3, §9).

Two stages, both pure:

1. **Spans.** A `SpanDetector` finds entity mentions in a section's own text
   (its heading and body, without its subsections, so each mention belongs to
   exactly one section). v0.1 ships `DictionaryDetector`: names and aliases
   mapped to entity keys, longest match first. A model-backed detector (GLiNER,
   for example) can implement the same protocol; MDS does not ship one.
2. **co_occurs.** With `E` the section × entity incidence matrix and `W` a
   diagonal IDF weighting, `A_r2 = prune_k(E · W · Eᵀ)`. Entities whose
   document frequency exceeds a ceiling are dropped before the product, each
   row keeps its top `k` entries and self-loops are removed. The result is
   symmetric and returned both ways.

Nothing here writes to the store. Sections are keyed by
`(document key, heading_path, occurrence)`, where `occurrence` numbers repeated
heading paths in document order (the root is `(doc_key, (), 0)`); entities are
keyed by their dictionary key. Turning keys into atoms is the filing step's job.

IDF is computed over the whole ingested corpus, not per principal. That is the
documented residual leak in DESIGN §7.4.
"""

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import graphblas as gb

from mds.ingest.parse import ParsedDocument, Section

EXTRACTOR_ID = "entities/1"

SectionKey = tuple[str, tuple[str, ...], int]


class EntityError(ValueError):
    """An entity dictionary or co-occurrence setting is invalid."""


# Spans --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Match:
    """One mention found by a detector, as a character range in the text it was given."""

    start: int
    end: int
    entity: str


class SpanDetector(Protocol):
    """Finds entity mentions in a piece of text.

    Implementations return non-overlapping matches in text order. A model-backed
    detector (GLiNER or similar) must load its model from a local path and map
    its labels to entity keys before returning.
    """

    def detect(self, text: str) -> list[Match]:
        """Mentions in `text`, in order, without overlaps."""
        ...


@dataclass(frozen=True, slots=True)
class EntityEntry:
    """One entity in a dictionary: its key, the names it is known by, and how to match them.

    `case_sensitive=None` takes the dictionary's default.
    """

    key: str
    names: tuple[str, ...]
    case_sensitive: bool | None = None


class DictionaryDetector:
    """Dictionary-first span detection.

    Matches whole words only (a name never starts or ends inside a word),
    scanning left to right and taking the longest name at each position. A name
    may be case-sensitive (`PEP`) or not (`python`); when both kinds match the
    same text, the case-sensitive entry wins.
    """

    def __init__(self, entries: Iterable[EntityEntry], case_sensitive: bool = False) -> None:
        self._exact: dict[str, str] = {}
        self._folded: dict[str, str] = {}
        patterns: list[tuple[str, bool]] = []
        for entry in entries:
            if not entry.key:
                raise EntityError("entity key must not be empty")
            sensitive = case_sensitive if entry.case_sensitive is None else entry.case_sensitive
            # An entry with no names is matched by its key.
            for name in entry.names or (entry.key,):
                name = name.strip()
                if not name:
                    raise EntityError(f"entity {entry.key!r} has an empty name")
                table, lookup = (
                    (self._exact, name) if sensitive else (self._folded, name.casefold())
                )
                if table.get(lookup, entry.key) != entry.key:
                    raise EntityError(
                        f"name {name!r} maps to both {table[lookup]!r} and {entry.key!r}"
                    )
                if lookup not in table:
                    table[lookup] = entry.key
                    patterns.append((name, sensitive))
        # Longest first, so the alternation prefers the longest name at a position.
        patterns.sort(key=lambda p: (-len(p[0]), p[0]))
        alternatives = [re.escape(n) if s else f"(?i:{re.escape(n)})" for n, s in patterns]
        self._pattern = (
            re.compile(rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)") if alternatives else None
        )

    @classmethod
    def from_mapping(
        cls, names: Mapping[str, Sequence[str]], case_sensitive: bool = False
    ) -> "DictionaryDetector":
        """Build from `{entity_key: [name, alias, ...]}`."""
        return cls(
            (EntityEntry(key, tuple(aliases)) for key, aliases in names.items()), case_sensitive
        )

    def detect(self, text: str) -> list[Match]:
        """Mentions in `text`, leftmost then longest, without overlaps."""
        if self._pattern is None:
            return []
        out: list[Match] = []
        for m in self._pattern.finditer(text):
            found = m.group(0)
            entity = self._exact.get(found) or self._folded.get(found.casefold())
            if entity is not None:
                out.append(Match(m.start(), m.end(), entity))
        return out


@dataclass(frozen=True, slots=True)
class EntitySpan:
    """One entity mention, located by a UTF-8 byte range in the document derivative."""

    section: SectionKey
    entity: str
    byte_offset: int
    byte_length: int
    text: str


def own_range(section: Section) -> tuple[int, int]:
    """Byte range of a section's own text: its heading and body, before its first subsection."""
    end = section.byte_offset + section.byte_length
    if section.children:
        end = section.children[0].byte_offset
    return section.byte_offset, end


def section_keys(doc_key: str, doc: ParsedDocument) -> list[tuple[SectionKey, Section]]:
    """Every section in document order with its key; repeated heading paths are numbered."""
    seen: dict[tuple[str, ...], int] = defaultdict(int)
    out: list[tuple[SectionKey, Section]] = []
    for section in doc.root.walk():
        path = section.heading_path
        out.append(((doc_key, path, seen[path]), section))
        seen[path] += 1
    return out


def detect_spans(doc_key: str, doc: ParsedDocument, detector: SpanDetector) -> list[EntitySpan]:
    """Entity mentions in every section of a document, in document order."""
    data = doc.text.encode("utf-8")
    spans: list[EntitySpan] = []
    for key, section in section_keys(doc_key, doc):
        start, end = own_range(section)
        text = data[start:end].decode("utf-8")
        for m in detector.detect(text):
            offset = start + len(text[: m.start].encode("utf-8"))
            mention = text[m.start : m.end]
            spans.append(EntitySpan(key, m.entity, offset, len(mention.encode("utf-8")), mention))
    return spans


# co_occurs ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoOccurConfig:
    """Settings for `co_occurs` construction.

    `max_df` is the document-frequency ceiling as a fraction of documents: an
    entity mentioned in more than `max_df · N` documents is dropped. `top_k` is
    how many neighbours each section keeps.
    """

    top_k: int = 10
    max_df: float = 0.5

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise EntityError(f"top_k must be at least 1, got {self.top_k}")
        if not 0.0 < self.max_df <= 1.0:
            raise EntityError(f"max_df must be in (0, 1], got {self.max_df}")


@dataclass(frozen=True, slots=True)
class CoOccurrence:
    """One `co_occurs` pair `a → b` with its weight in (0, 1] and the entities they share."""

    a: SectionKey
    b: SectionKey
    weight: float
    entities: tuple[str, ...] = field(default=())


def document_frequency(spans: Iterable[EntitySpan]) -> dict[str, int]:
    """Number of distinct documents mentioning each entity."""
    docs: dict[str, set[str]] = defaultdict(set)
    for span in spans:
        docs[span.entity].add(span.section[0])
    return {entity: len(d) for entity, d in docs.items()}


def idf(df: int, n_docs: int) -> float:
    """Smoothed inverse document frequency, `ln(1 + N / df)`. Always positive."""
    return math.log(1.0 + n_docs / df)


def kept_entities(df: Mapping[str, int], n_docs: int, max_df: float) -> list[str]:
    """Entities at or under the document-frequency ceiling, sorted by key."""
    return sorted(e for e, d in df.items() if d <= max_df * n_docs)


def incidence(
    spans: Iterable[EntitySpan], sections: Sequence[SectionKey], entities: Sequence[str]
) -> gb.Matrix:
    """The section × entity incidence matrix `E` (1.0 where a section mentions an entity)."""
    row = {key: i for i, key in enumerate(sections)}
    col = {e: j for j, e in enumerate(entities)}
    pairs = sorted({(row[s.section], col[s.entity]) for s in spans if s.entity in col})
    return gb.Matrix.from_coo(
        [i for i, _ in pairs],
        [j for _, j in pairs],
        1.0,
        nrows=len(sections),
        ncols=len(entities),
        dtype=gb.dtypes.FP64,
    )


def idf_weights(df: Mapping[str, int], entities: Sequence[str], n_docs: int) -> gb.Matrix:
    """The diagonal IDF matrix `W`, ordered like `entities`."""
    values = [idf(df[e], n_docs) for e in entities]
    return gb.Vector.from_coo(range(len(entities)), values, size=len(entities)).diag()


def prune_k(scores: gb.Matrix, k: int) -> gb.Matrix:
    """Keep each row's `k` highest entries; ties go to the lower column index."""
    rows, cols, values = scores.to_coo()
    by_row: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for i, j, v in zip(rows.tolist(), cols.tolist(), values.tolist(), strict=True):
        by_row[i].append((v, j))
    keep_i: list[int] = []
    keep_j: list[int] = []
    keep_v: list[float] = []
    for i, entries in by_row.items():
        for v, j in sorted(entries, key=lambda e: (-e[0], e[1]))[:k]:
            keep_i.append(i)
            keep_j.append(j)
            keep_v.append(v)
    return gb.Matrix.from_coo(
        keep_i, keep_j, keep_v, nrows=scores.nrows, ncols=scores.ncols, dtype=scores.dtype
    )


def co_occurs_matrix(e: gb.Matrix, w: gb.Matrix, k: int) -> gb.Matrix:
    """`A_r2`: `prune_k(E · W · Eᵀ)` without self-loops, made symmetric, scaled into (0, 1].

    Self-loops are removed before pruning so they never take a top-`k` slot.
    Pruning is per row, so a pair kept by either endpoint is kept both ways.
    Each score `s(a, b)` is divided by `√(s(a, a) · s(b, b))`; with a binary
    `E` and positive `W`, `s(a, b) ≤ min(s(a, a), s(b, b))`, so the result is
    in (0, 1].
    """
    scores = e.mxm(w).mxm(e.T).new()
    diag = scores.diag()
    off = scores.select("offdiag").new()
    pruned = prune_k(off, k)
    sym = pruned.ewise_add(pruned.T, gb.binary.max).new()
    self_score = dict(zip(*(a.tolist() for a in diag.to_coo()), strict=True))
    rows, cols, values = sym.to_coo()
    # √(x·y) rather than √x·√y so identical rows come out at exactly 1.0.
    scaled = [
        min(v / math.sqrt(self_score[i] * self_score[j]), 1.0)
        for i, j, v in zip(rows.tolist(), cols.tolist(), values.tolist(), strict=True)
    ]
    return gb.Matrix.from_coo(rows, cols, scaled, nrows=sym.nrows, ncols=sym.ncols)


def co_occurrences(
    spans: Iterable[EntitySpan],
    sections: Iterable[SectionKey],
    config: CoOccurConfig | None = None,
) -> list[CoOccurrence]:
    """`co_occurs` pairs for a corpus, both directions, sorted by `a` then `b`.

    `sections` is every section in the corpus (sections without mentions still
    count towards nothing but keep the index stable); `N` for IDF is the number
    of distinct documents among them.
    """
    config = config or CoOccurConfig()
    spans = list(spans)
    section_list = sorted(set(sections) | {s.section for s in spans})
    n_docs = len({key[0] for key in section_list})
    df = document_frequency(spans)
    entities = kept_entities(df, n_docs, config.max_df)
    if not entities:
        return []
    e = incidence(spans, section_list, entities)
    a = co_occurs_matrix(e, idf_weights(df, entities, n_docs), config.top_k)

    mentions: dict[SectionKey, set[str]] = defaultdict(set)
    kept = set(entities)
    for span in spans:
        if span.entity in kept:
            mentions[span.section].add(span.entity)
    rows, cols, values = a.to_coo()
    out = [
        CoOccurrence(
            section_list[i],
            section_list[j],
            v,
            tuple(sorted(mentions[section_list[i]] & mentions[section_list[j]])),
        )
        for i, j, v in zip(rows.tolist(), cols.tolist(), values.tolist(), strict=True)
    ]
    return sorted(out, key=lambda c: (c.a, c.b))


def corpus_co_occurrences(
    docs: Mapping[str, ParsedDocument],
    detector: SpanDetector,
    config: CoOccurConfig | None = None,
) -> tuple[list[EntitySpan], list[CoOccurrence]]:
    """Spans and `co_occurs` pairs for a whole corpus of parsed documents."""
    spans: list[EntitySpan] = []
    sections: list[SectionKey] = []
    for doc_key in sorted(docs):
        doc = docs[doc_key]
        sections.extend(key for key, _ in section_keys(doc_key, doc))
        spans.extend(detect_spans(doc_key, doc, detector))
    return spans, co_occurrences(spans, sections, config)
