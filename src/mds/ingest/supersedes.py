"""The `supersedes` (r4) extractor: who replaces or amends whom, and from when (DESIGN §3.1, §9).

An edge `a → b` means `a` supersedes `b` from `valid_from` (DESIGN §5.5). Edges come
from two sources:

- **Metadata** (DESIGN §9.1). `Superseded-By: X` in document `b` gives `X → b`;
  `Replaces: Y` in document `a` gives `a → Y`. An rST `.. superseded:: X`
  directive reads like `Superseded-By`.
- **Amendment wording.** Phrase rules over the body text: "this document
  supersedes / replaces / amends X", "this document was superseded by X",
  "X is deleted and replaced", "X is amended to read", with explicit effective
  dates from "with effect from <date>" and similar.

The rules are deliberately conservative: hedged or negated wording ("would
supersede", "supplements rather than supersedes") produces nothing, and a phrase
whose target cannot be named produces nothing.

Documents are named by a stable document key (the path relative to the ingest
root). Targets outside the corpus that can still be named (`PEP 600`) become
placeholder keys (`placeholder:PEP 600`).

`valid_from` is, in order of precedence: an explicit effective date in the
sentence that declared the edge; an explicit effective date for the superseding
document as a whole (an `Effective-Date` header or a "this document ... with
effect from <date>" sentence); the superseding document's `Created` date; or
null when the superseding document is not held. `valid_to` is always null.

Everything here is pure: the functions take parsed documents and return edges.
Turning keys into atoms and writing to the store is the caller's job.
"""

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import PurePosixPath

from mds.ingest.parse import ParsedDocument, header_fields

EXTRACTOR = "supersedes"
VERSION = 1
AUTHOR_ID = f"{EXTRACTOR}/{VERSION}"

PLACEHOLDER_PREFIX = "placeholder:"

METADATA_WEIGHT = 1.0
PHRASE_WEIGHT = 0.8

REPLACES_FIELDS = ("Replaces",)
SUPERSEDED_BY_FIELDS = ("Superseded-By",)
CREATED_FIELDS = ("Created", "Date")
EFFECTIVE_FIELDS = ("Effective-Date", "Effective")


class DateSource(StrEnum):
    """Where an edge's `valid_from` came from."""

    EFFECTIVE = "effective"  # an explicit effective date in the document
    CREATED = "created"  # the superseding document's own date (DESIGN §9.1 fallback)
    UNKNOWN = "unknown"  # the superseding document is not held, so its date is unknown


@dataclass(frozen=True, slots=True)
class Span:
    """Evidence: the UTF-8 byte range in a document's derivative that declared an edge."""

    doc_key: str
    byte_offset: int
    byte_length: int
    rule: str


@dataclass(frozen=True, slots=True)
class SupersedesEdge:
    """One `supersedes` edge between document keys: `src` supersedes `dst`.

    `src` or `dst` may be a placeholder key. `evidence` lists every declaration of
    the pair once edges are merged.
    """

    src: str
    dst: str
    valid_from: date | None
    weight: float
    evidence: tuple[Span, ...]
    date_source: DateSource
    valid_to: date | None = None
    author_id: str = AUTHOR_ID


def placeholder_key(label: str) -> str:
    """The key for something named as a target but not held, e.g. `placeholder:PEP 600`."""
    return f"{PLACEHOLDER_PREFIX}{label}"


def is_placeholder(key: str) -> bool:
    """Whether a key names a placeholder rather than a held document."""
    return key.startswith(PLACEHOLDER_PREFIX)


# Dates ---------------------------------------------------------------------

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = r"(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"

DATE_PATTERN = (
    r"(?:"
    r"\d{4}-\d{2}-\d{2}"  # 2023-03-01
    rf"|\d{{1,2}}-{_MONTH}-\d{{4}}"  # 01-Mar-2023
    rf"|{_DAY}\s+{_MONTH}\s+\d{{4}}"  # 1 March 2023
    rf"|{_MONTH}\s+{_DAY},?\s+\d{{4}}"  # March 1, 2023
    r")"
)
_DATE_RE = re.compile(DATE_PATTERN, re.IGNORECASE)


def parse_date(value: str) -> date | None:
    """Parse a date written in one of the forms documents use; `None` if it is not one.

    Accepts `2023-03-01`, `01-Mar-2023`, `1 March 2023`, `1st March 2023` and
    `March 1, 2023`. Impossible dates (`31-Feb-2023`) give `None`.
    """
    text = value.strip()
    if not _DATE_RE.fullmatch(text):
        return None
    parts = [p for p in re.split(r"[\s,\-.]+", text.lower()) if p]
    try:
        if len(parts) == 3 and parts[0].isdigit() and len(parts[0]) == 4:
            year, month, day = (int(p) for p in parts)
        elif parts[0][:1].isdigit():
            day, month, year = _day(parts[0]), _MONTHS[parts[1]], int(parts[2])
        else:
            month, day, year = _MONTHS[parts[0]], _day(parts[1]), int(parts[2])
        return date(year, month, day)
    except (KeyError, ValueError):
        return None


def _day(token: str) -> int:
    return int(re.sub(r"(st|nd|rd|th)$", "", token))


def _first_field(metadata: Mapping[str, str], names: Sequence[str]) -> str | None:
    for name in names:
        if metadata.get(name):
            return metadata[name]
    return None


# Document index and target resolution ---------------------------------------

# Identifier schemes that name documents in text and headers. Each maps a match to
# a canonical label; a held document claims a label through its metadata.
_PEP_REF_RE = re.compile(
    r":pep:\s*(?:[^<>\n]*?<(?P<role_title>\d{1,5})>|(?P<role>\d{1,5}))"
    r"|\bPEP\s*-?\s*(?P<plain>\d{1,5})\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d{1,5}")
_LIST_SEP_RE = re.compile(r",|;|\s+and\s+")


def _pep_label(number: str | int) -> str:
    return f"PEP {int(number)}"


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    """What the extractor needs to know about one held document."""

    key: str
    created: date | None
    effective: date | None
    labels: tuple[str, ...]
    aliases: tuple[str, ...]


class DocumentIndex:
    """Resolves the names a document can be cited by to document keys."""

    def __init__(self, documents: Mapping[str, ParsedDocument]) -> None:
        self._info: dict[str, DocumentInfo] = {}
        self._labels: dict[str, str] = {}
        self._aliases: dict[str, str] = {}
        for key in sorted(documents):
            info = describe(key, documents[key])
            self._info[key] = info
            for label in info.labels:
                self._labels.setdefault(label, key)
            for alias in info.aliases:
                self._aliases.setdefault(alias.casefold(), key)
        self._alias_re = (
            re.compile(
                r"(?<!\w)(?:"
                + "|".join(re.escape(a) for a in sorted(self._aliases, key=len, reverse=True))
                + r")(?!\w)",
                re.IGNORECASE,
            )
            if self._aliases
            else None
        )

    def __contains__(self, key: object) -> bool:
        return key in self._info

    def info(self, key: str) -> DocumentInfo | None:
        """The description of a held document, or `None` for a placeholder or unknown key."""
        return self._info.get(key)

    def resolve_label(self, label: str) -> str:
        """The key of the document holding a canonical label, or a placeholder key."""
        return self._labels.get(label, placeholder_key(label))

    def find_targets(self, text: str) -> list[str]:
        """Every document named in `text`, in order of first mention, without repeats.

        Identifier references (`PEP 386`, `:pep:`386``) resolve through labels and
        fall back to placeholders. File names and document titles resolve only to
        held documents.
        """
        found: list[tuple[int, str]] = []
        for match in _PEP_REF_RE.finditer(text):
            number = match.group("role_title") or match.group("role") or match.group("plain")
            found.append((match.start(), self.resolve_label(_pep_label(number))))
        if self._alias_re is not None:
            for match in self._alias_re.finditer(text):
                found.append((match.start(), self._aliases[match.group(0).casefold()]))
        return _unique(key for _, key in sorted(found))


def describe(key: str, doc: ParsedDocument) -> DocumentInfo:
    """Dates, identifier labels and citation aliases of one document."""
    created_value = _first_field(doc.metadata, CREATED_FIELDS)
    labels: list[str] = []
    pep = doc.metadata.get("PEP", "").strip()
    if pep.isdigit():
        labels.append(_pep_label(pep))
    name = PurePosixPath(key).name
    aliases = [name]
    # Titles are cited in prose, but short ones ("Enum") collide with ordinary words.
    if len(doc.title.split()) >= 3:
        aliases.append(doc.title)
    return DocumentInfo(
        key=key,
        created=parse_date(created_value) if created_value else None,
        effective=document_effective_date(doc),
        labels=tuple(labels),
        aliases=tuple(aliases),
    )


def build_index(documents: Mapping[str, ParsedDocument]) -> DocumentIndex:
    """Index a set of parsed documents keyed by document key."""
    return DocumentIndex(documents)


# Metadata rules (DESIGN §9.1) -----------------------------------------------

_DIRECTIVE_RE = re.compile(r"^\.\.\s+superseded::\s*(?P<value>.+)$", re.MULTILINE)


def extract_from_metadata(
    key: str, doc: ParsedDocument, index: DocumentIndex
) -> list[SupersedesEdge]:
    """Edges declared by `Replaces` and `Superseded-By` headers and `.. superseded::` directives.

    One edge per declaration; use `merge_edges` to collapse a pair declared twice.
    """
    edges: list[SupersedesEdge] = []
    for name in REPLACES_FIELDS:
        for target in _metadata_targets(doc.metadata.get(name, ""), index):
            span = _header_span(key, doc, name)
            edges.extend(_edge(key, target, index, METADATA_WEIGHT, span, None))
    for name in SUPERSEDED_BY_FIELDS:
        for source in _metadata_targets(doc.metadata.get(name, ""), index):
            span = _header_span(key, doc, name)
            edges.extend(_edge(source, key, index, METADATA_WEIGHT, span, None))
    for match in _DIRECTIVE_RE.finditer(doc.text):
        span = _span(key, doc.text, match.start(), match.end(), "directive:superseded")
        for source in _metadata_targets(match.group("value"), index):
            edges.extend(_edge(source, key, index, METADATA_WEIGHT, span, None))
    return edges


def _metadata_targets(value: str, index: DocumentIndex) -> list[str]:
    """Targets in a header value: `245, 246`, `PEP 245`, or a held document's alias."""
    targets: list[str] = []
    for item in _LIST_SEP_RE.split(value):
        item = item.strip()
        if _NUMBER_RE.fullmatch(item):
            targets.append(index.resolve_label(_pep_label(item)))
        elif item:
            targets.extend(index.find_targets(item))
    return _unique(targets)


def _header_span(key: str, doc: ParsedDocument, name: str) -> Span:
    match = re.search(rf"^{re.escape(name)}:.*(?:\n[ \t]+.*)*", doc.text, re.MULTILINE)
    if match is None:
        return Span(key, 0, 0, f"metadata:{name}")
    return _span(key, doc.text, match.start(), match.end(), f"metadata:{name}")


# Amendment wording ----------------------------------------------------------

_SELF_NOUNS = (
    "pep|document|policy|standard|specification|spec|proposal|regulations?|instrument"
    "|amendment|notice|version|edition|guidance|procedure|agreement|rules?|order|directive"
    "|circular|memorandum|handbook|manual|code"
)
_SELF = rf"\bthis\s+(?:{_SELF_NOUNS})\b"
_SELF_RE = re.compile(_SELF, re.IGNORECASE)
_ADVERB = r"(?:(?:hereby|now|also|formally|fully|partly|partially|entirely)\s+)?"

# "This PEP supersedes PEP 386 ..." — the subject must be the document itself and the
# verb must follow it directly, so "this PEP would supersede" and "this PEP
# supplements rather than supersedes" do not match.
_ACTIVE_RE = re.compile(
    rf"{_SELF}\s+{_ADVERB}(?P<verb>supersedes|replaces|amends)\b", re.IGNORECASE
)

# "<left> is superseded by <right>", "<left> is deleted and replaced", "<left> is amended
# to read". Only definite tenses; "would be superseded" does not match.
_PASSIVE_RE = re.compile(
    rf"\b(?:is|are|was|were|has\s+been|have\s+been)\s+{_ADVERB}"
    r"(?P<verb>deleted\s+and\s+replaced|revoked\s+and\s+replaced|amended\s+to\s+read"
    r"|superseded|replaced)\b"
    r"(?:\s+(?:by|with)\b(?P<by>))?",
    re.IGNORECASE,
)
# Passive verbs whose replacement is the amending document itself when no other is named.
_SELF_REPLACING_VERBS = ("deleted", "revoked", "amended")

_EFFECTIVE_RE = re.compile(
    r"\b(?:with\s+effect\s+(?:from|on)|effective\s+(?:from|on|as\s+of)"
    r"|(?:takes|take|took|has|have)\s+effect\s+(?:from|on)"
    r"|comes?\s+into\s+(?:force|effect|operation)\s+(?:from|on))\s+"
    rf"(?P<date>{DATE_PATTERN})",
    re.IGNORECASE,
)

# Where a target list ends: the next clause or an effective-date phrase.
_CLAUSE_END_RE = re.compile(
    r";|\(|\.(?:\s|$)|,\s*(?:which|as|see|but|because|since|while|where|and\s+(?:is|was))\b"
    r"|\b(?:with\s+effect|effective|in\s+(?:favou?r|light)\s+of|except|other\s+than)\b",
    re.IGNORECASE,
)
# Clause separators; the colon of a `:pep:` role is not one.
_CLAUSE_SEP_RE = re.compile(r"(?:;|,|(?<!:pep):)\s", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[*`:])|\n\s*\n")
# Inline markup characters, blanked out so rules match "*supersede*" and ":pep:`386`".
_MARKUP_RE = re.compile(r"[*`]")
_LITERAL_FENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def extract_from_text(key: str, doc: ParsedDocument, index: DocumentIndex) -> list[SupersedesEdge]:
    """Edges declared by amendment wording in the body (header block excluded)."""
    edges: list[SupersedesEdge] = []
    for start, end in sentences(doc.text, _body_start(doc)):
        sentence = _plain(doc.text[start:end])
        found = list(_sentence_edges(key, sentence, index))
        if not found:
            continue
        effective = _effective_date(sentence)
        for rule, src, dst in found:
            span = _span(key, doc.text, start, end, rule)
            edges.extend(_edge(src, dst, index, PHRASE_WEIGHT, span, effective))
    return edges


def _sentence_edges(
    key: str, sentence: str, index: DocumentIndex
) -> Iterator[tuple[str, str, str]]:
    """`(rule, src, dst)` for every amendment phrase in one sentence."""
    for match in _ACTIVE_RE.finditer(sentence):
        verb = match.group("verb").lower()
        for target in index.find_targets(_clause_after(sentence, match.end())):
            yield f"phrase:{verb}", key, target
    for match in _PASSIVE_RE.finditer(sentence):
        verb = match.group("verb").lower().split()[0]
        left = _clause_before(sentence, match.start())
        replaced = [key] if _SELF_RE.search(left) else index.find_targets(left)
        if match.group("by") is not None:
            right = _clause_after(sentence, match.end())
            replacing = [key] if _SELF_RE.match(right.lstrip()) else index.find_targets(right)
        else:
            replacing = []
        if not replacing and verb in _SELF_REPLACING_VERBS:
            replacing = [key]
        for src in replacing:
            for dst in replaced:
                yield f"phrase:{verb}", src, dst


def _clause_after(sentence: str, pos: int) -> str:
    rest = sentence[pos:]
    end = _CLAUSE_END_RE.search(rest)
    return rest[: end.start()] if end else rest


def _clause_before(sentence: str, pos: int) -> str:
    before = sentence[:pos]
    separators = list(_CLAUSE_SEP_RE.finditer(before))
    return before[separators[-1].end() :] if separators else before


def _effective_date(sentence: str) -> date | None:
    match = _EFFECTIVE_RE.search(sentence)
    return parse_date(match.group("date")) if match else None


def document_effective_date(doc: ParsedDocument) -> date | None:
    """An explicit effective date for the document as a whole, if it states one.

    From an `Effective-Date` header, or else the first body sentence about the
    document itself ("this document ...") that has an effective-date phrase.
    """
    value = _first_field(doc.metadata, EFFECTIVE_FIELDS)
    if value and (parsed := parse_date(value)) is not None:
        return parsed
    for start, end in sentences(doc.text, _body_start(doc)):
        sentence = _plain(doc.text[start:end])
        if _SELF_RE.search(sentence) and (found := _effective_date(sentence)) is not None:
            return found
    return None


def sentences(text: str, start: int = 0) -> list[tuple[int, int]]:
    """Character ranges of the sentences in `text[start:]`, skipping fenced code blocks.

    Sentences end at `.`, `!` or `?` followed by whitespace and a capital (or
    markup), and at blank lines. Ranges are trimmed of surrounding whitespace.
    """
    body = text[start:]
    for fence in _LITERAL_FENCE_RE.finditer(body):
        body = body[: fence.start()] + " " * (fence.end() - fence.start()) + body[fence.end() :]
    ranges: list[tuple[int, int]] = []
    pos = 0
    for brk in [*_SENTENCE_END_RE.finditer(body), None]:
        end = brk.start() if brk else len(body)
        chunk = body[pos:end]
        if chunk.strip():
            lead = len(chunk) - len(chunk.lstrip())
            ranges.append((start + pos + lead, start + pos + len(chunk.rstrip())))
        pos = brk.end() if brk else len(body)
    return ranges


def _body_start(doc: ParsedDocument) -> int:
    """Character offset of the first line after the leading header block."""
    if not doc.metadata:
        return 0
    lines = doc.text.split("\n")
    _, first_body_line = header_fields(lines)
    return sum(len(line) + 1 for line in lines[:first_body_line])


def _plain(text: str) -> str:
    """`text` with inline markup blanked out; same length, so offsets carry over."""
    return _MARKUP_RE.sub(" ", text)


# Assembly -------------------------------------------------------------------


def _edge(
    src: str,
    dst: str,
    index: DocumentIndex,
    weight: float,
    span: Span,
    effective: date | None,
) -> list[SupersedesEdge]:
    """One edge with its valid-time fallback applied, or none for a self-loop."""
    if src == dst:
        return []
    valid_from: date | None = effective
    source = DateSource.EFFECTIVE
    if valid_from is None:
        valid_from, source = _fallback_date(src, index)
    return [SupersedesEdge(src, dst, valid_from, weight, (span,), source)]


def _fallback_date(src: str, index: DocumentIndex) -> tuple[date | None, DateSource]:
    info = index.info(src)
    if info is None:
        return None, DateSource.UNKNOWN
    if info.effective is not None:
        return info.effective, DateSource.EFFECTIVE
    if info.created is not None:
        return info.created, DateSource.CREATED
    return None, DateSource.UNKNOWN


_DATE_RANK = {DateSource.EFFECTIVE: 0, DateSource.CREATED: 1, DateSource.UNKNOWN: 2}


def merge_edges(edges: Iterable[SupersedesEdge]) -> list[SupersedesEdge]:
    """Collapse edges for the same `(src, dst)` pair into one, sorted by pair.

    The merged edge keeps the highest weight and every evidence span. Its
    `valid_from` is the earliest explicit effective date if any declaration has
    one, else the earliest fallback date.
    """
    groups: dict[tuple[str, str], list[SupersedesEdge]] = {}
    for edge in edges:
        groups.setdefault((edge.src, edge.dst), []).append(edge)
    merged: list[SupersedesEdge] = []
    for (src, dst), group in sorted(groups.items()):
        best = min(_DATE_RANK[e.date_source] for e in group)
        dated = [e.valid_from for e in group if _DATE_RANK[e.date_source] == best and e.valid_from]
        merged.append(
            SupersedesEdge(
                src=src,
                dst=dst,
                valid_from=min(dated) if dated else None,
                weight=max(e.weight for e in group),
                evidence=tuple(_unique(span for e in group for span in e.evidence)),
                date_source=next(s for s, rank in _DATE_RANK.items() if rank == best),
            )
        )
    return merged


def extract_supersedes(documents: Mapping[str, ParsedDocument]) -> list[SupersedesEdge]:
    """All `supersedes` edges for a set of documents keyed by document key, merged."""
    index = build_index(documents)
    edges: list[SupersedesEdge] = []
    for key in sorted(documents):
        edges.extend(extract_from_metadata(key, documents[key], index))
        edges.extend(extract_from_text(key, documents[key], index))
    return merge_edges(edges)


def _span(key: str, text: str, start: int, end: int, rule: str) -> Span:
    byte_start = len(text[:start].encode("utf-8"))
    return Span(key, byte_start, len(text[start:end].encode("utf-8")), rule)


def _unique[T](items: Iterable[T]) -> list[T]:
    seen: set[T] = set()
    out: list[T] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
