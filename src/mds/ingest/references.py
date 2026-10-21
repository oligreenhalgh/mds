"""The `references` (r1) extractor: pattern rules for explicit references (DESIGN §3.1, §9).

Finds reference spans in a parsed document and resolves each one to a target.
Resolution tries the same document first (section numbers, heading text,
labels, anchors), then other documents in the corpus (`PEP 484`,
:pep:`484`, links to corpus files). A target outside the corpus becomes a
`Placeholder` so a later step can create a `placeholder` atom (DESIGN §2).

Rules, in priority order (a span claimed by one rule is not matched again):

- `requires`: the PEP `Requires:` header, from the document root.
- `link`: rST `` `text <target>`_ ``, Markdown `[text](target)` and bare URLs.
- `role`: rST :pep:, :rfc: and :ref: roles.
- `named`: rST `` `Name`_ `` resolved to a hyperlink target or heading text.
- `structural`: "section 4.2", "clause 3", "Schedule 2", "Appendix A", with an
  optional "of PEP 484" naming another document.
- `cited`: plain "PEP 484" and "RFC 2616".
- `term`: use of a term defined in the same document (`"Term" means …`).

Code (Markdown fences and inline code, rST literal blocks and inline literals)
is skipped. A structural reference that cannot be resolved inside its
document is dropped rather than guessed, and self-references are dropped.

This module is pure: it reads `ParsedDocument`s and returns `Reference`s; it
does not write to the store. Sections are keyed by (document key,
heading_path); the document itself is the section with heading_path `()`.
"""

import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from mds.ingest.parse import ParsedDocument, Section, SourceFormat

EXTRACTOR = "references"
EXTRACTOR_VERSION = 1
AUTHOR_ID = f"{EXTRACTOR}/{EXTRACTOR_VERSION}"

WEIGHT_EXPLICIT = 1.0
# Defined-term use is a word match rather than an explicit pointer.
WEIGHT_TERM = 0.8


@dataclass(frozen=True, slots=True, order=True)
class SectionKey:
    """A section of a document; heading_path `()` is the document itself."""

    doc: str
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, order=True)
class Placeholder:
    """A referenced target that is not in the corpus, e.g. `("pep", "9998")`.

    `kind` is `pep`, `rfc`, `url` or `ref`; `ref` is the normalised identifier.
    """

    kind: str
    ref: str


Target = SectionKey | Placeholder


@dataclass(frozen=True, slots=True)
class Reference:
    """One reference span and its resolved target.

    `span` is the UTF-8 byte range of the matched text in the derivative, the
    same coordinates atom versions use (DESIGN §2.1).
    """

    source: SectionKey
    target: Target
    span: tuple[int, int]
    text: str
    rule: str
    weight: float = WEIGHT_EXPLICIT
    author_id: str = AUTHOR_ID


@dataclass(frozen=True, slots=True)
class DocumentIndex:
    """The corpus as the extractor sees it: documents by key, plus aliases.

    Aliases map `pep:484`, file names (`pep-0484.rst`) and stems (`pep-0484`)
    to document keys, so references in any of those forms resolve.
    """

    docs: Mapping[str, ParsedDocument]
    aliases: Mapping[str, str] = field(default_factory=dict)

    def lookup(self, alias: str) -> str | None:
        """The document key for an alias, or None if the corpus does not hold it."""
        if alias in self.docs:
            return alias
        return self.aliases.get(alias)


def index_documents(docs: Mapping[str, ParsedDocument]) -> DocumentIndex:
    """Index documents keyed by their path relative to the corpus root (e.g. `public/pep-0484.rst`).

    Aliases come from the key's file name and stem, a `pep-NNNN` stem, and the
    `PEP:` header. An alias claimed by two documents is left out.
    """
    claims: dict[str, set[str]] = {}
    for key, doc in docs.items():
        name = PurePosixPath(key).name
        names = {name, name.lower(), PurePosixPath(name).stem.lower()}
        stem = re.fullmatch(r"pep-0*(\d+)", PurePosixPath(name).stem.lower())
        if stem:
            names.add(f"pep:{int(stem.group(1))}")
        number = doc.metadata.get("PEP", "").strip()
        if number.isdigit():
            names.add(f"pep:{int(number)}")
        for alias in names:
            claims.setdefault(alias, set()).add(key)
    aliases = {a: next(iter(keys)) for a, keys in claims.items() if len(keys) == 1}
    return DocumentIndex(docs=docs, aliases=aliases)


def extract_references(doc_key: str, index: DocumentIndex) -> list[Reference]:
    """All references in one indexed document, in document order."""
    return _Extractor(doc_key, index).run()


def extract_all(index: DocumentIndex) -> list[Reference]:
    """References for every document in the index, ordered by document key."""
    out: list[Reference] = []
    for key in sorted(index.docs):
        out.extend(extract_references(key, index))
    return out


def pairs(refs: Iterable[Reference]) -> set[tuple[SectionKey, Target]]:
    """Distinct (source, target) pairs, i.e. the edges the references imply."""
    return {(r.source, r.target) for r in refs}


# Patterns -----------------------------------------------------------------

_URL = r"https?://[^\s<>`'\"\]\[(){}]+"
_RE_RST_LINK = re.compile(r"`([^`<]*?)\s*<([^<>`]+)>`__?")
_RE_MD_LINK = re.compile(r"(?<!!)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_RE_URL = re.compile(_URL)
_RE_ROLE = re.compile(r":(pep|rfc|ref):`([^`]+)`")
_RE_NAMED = re.compile(r"(?<![`\w])`([^`<>]+)`__?(?!\w)")
_RE_TARGET_DEF = re.compile(r"^\.\. _([^:\n]+|`[^`]+`):[ \t]*(\S*)[ \t]*$", re.MULTILINE)
_RE_REQUIRES = re.compile(r"^Requires:[ \t]*(.*(?:\n[ \t]+.*)*)", re.MULTILINE)

_NUM = r"\d+(?:\.\d+)*(?:\([a-z0-9]{1,4}\))?"
_STRUCT_WORDS = "section|clause|paragraph|schedule|appendix|annex|part"
_RE_STRUCTURAL = re.compile(
    rf"\b(?P<word>(?i:{_STRUCT_WORDS}))s?\s+"
    rf"(?P<labels>(?:{_NUM}|[A-Z])(?!\w)(?:(?:\s*,\s*|\s+(?:and|or|to)\s+)(?:{_NUM}|[A-Z])(?!\w))*)"
    r"(?P<of>\s+of\s+(?:the\s+)?(?P<ofref>:pep:`[^`]+`|PEP\s+\d+|\S+))?"
)
_RE_LABEL = re.compile(rf"{_NUM}|[A-Z]")
_RE_CITED = re.compile(r"\b(PEP|RFC)s?\s+(\d+(?:(?:\s*,\s*|\s+(?:and|or)\s+)\d+)*)\b")
_RE_DEFINITION = re.compile(
    r"[\"“](?P<a>[A-Z][\w'’ -]{0,40}?)[\"”]\s+(?:means|has the meaning)"
    r"|\(the\s+[\"“](?P<b>[A-Z][\w'’ -]{0,40}?)[\"”]\)"
)

# A numbered or lettered heading: "4.2 Retention", "Appendix A: Foo", "Schedule 2".
_RE_HEADING_LABEL = re.compile(
    rf"^(?:(?P<word>(?i:{_STRUCT_WORDS}))\s+)?(?P<label>{_NUM}|[A-Z])(?=$|[\s.:)\-–—])"
)
_LETTERED = {"schedule", "appendix", "annex", "part"}


def slugify(text: str) -> str:
    """A heading's anchor id, as docutils and most Markdown renderers make it."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _norm_name(text: str) -> str:
    """rST reference-name normalisation: case and whitespace insensitive."""
    return " ".join(text.split()).lower()


def heading_label(title: str) -> tuple[str, str] | None:
    """The (word, label) a heading is numbered with, e.g. `("appendix", "A")`, `("", "4.2")`."""
    m = _RE_HEADING_LABEL.match(title.strip())
    if not m:
        return None
    word = (m.group("word") or "").lower()
    label = m.group("label")
    # A bare capital letter is a word ("A note on…"), not a label.
    if not word and not label[0].isdigit():
        return None
    return word, label


# Masking ------------------------------------------------------------------


def code_mask(text: str, fmt: SourceFormat) -> list[tuple[int, int]]:
    """Character ranges of code, where references are not looked for."""
    ranges: list[tuple[int, int]] = []
    lines = text.split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    if fmt is SourceFormat.MARKDOWN:
        fence: str | None = None
        begin = 0
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if fence is None and stripped.startswith(("```", "~~~")):
                fence, begin = stripped[:3], starts[i]
            elif fence is not None and stripped.startswith(fence):
                ranges.append((begin, starts[i + 1]))
                fence = None
        if fence is not None:
            ranges.append((begin, len(text)))
        ranges.extend(m.span() for m in re.finditer(r"(`+)[^`\n]+?\1", text))
    elif fmt is SourceFormat.RST:
        i = 0
        while i < len(lines):
            line = lines[i]
            opens = line.rstrip().endswith("::") or re.match(
                r"\s*\.\. (?:code-block|code|sourcecode|productionlist)::", line
            )
            if not opens or line.lstrip().startswith(".. _"):
                i += 1
                continue
            indent = len(line) - len(line.lstrip())
            j = i + 1
            while j < len(lines) and (
                not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent
            ):
                j += 1
            if j > i + 1:
                ranges.append((starts[i + 1], starts[j] - 1 if j < len(lines) else len(text)))
            i = j
        ranges.extend(m.span() for m in re.finditer(r"``[^`\n]+?``", text))
    return _merge(ranges)


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


# Extraction ---------------------------------------------------------------


@dataclass(slots=True)
class _Owned:
    """A section and the character range of its own text (before its first child)."""

    section: Section
    start: int
    end: int


class _Extractor:
    """Per-document state for one extraction run."""

    def __init__(self, doc_key: str, index: DocumentIndex) -> None:
        self.key = doc_key
        self.index = index
        self.doc = index.docs[doc_key]
        self.text = self.doc.text
        self.claimed: list[tuple[int, int]] = list(code_mask(self.text, self.doc.format))
        self.out: list[Reference] = []
        self._byte_at = _CharToByte(self.text)
        self.owned = self._own_ranges()
        self._owned_starts = [o.start for o in self.owned]
        self.targets: dict[str, Target] = {}
        self.targets, target_def_spans = self._target_defs()
        self.claimed.extend(target_def_spans)

    def run(self) -> list[Reference]:
        self._requires()
        self._links()
        self._roles()
        self._named()
        self._structural()
        self._cited()
        self._terms()
        self.out.sort(key=lambda r: (r.span, r.rule))
        return self.out

    # Section geometry

    def _own_ranges(self) -> list[_Owned]:
        to_char = _ByteToChar(self.text)
        owned: list[_Owned] = []
        for s in self.doc.root.walk():
            start = to_char(s.byte_offset)
            end = to_char(
                s.children[0].byte_offset if s.children else s.byte_offset + s.byte_length
            )
            owned.append(_Owned(s, start, end))
        owned.sort(key=lambda o: o.start)
        return owned

    def _source_at(self, pos: int) -> SectionKey:
        i = bisect_right(self._owned_starts, pos) - 1
        # Text between a section's end and the next heading (trailing blank
        # lines) still belongs to the latest section that started before it.
        return SectionKey(self.key, self.owned[max(i, 0)].section.heading_path)

    def _heading_spans(self) -> list[tuple[int, int]]:
        """Character ranges of heading text (with any rST overline and underline)."""
        spans: list[tuple[int, int]] = []
        for o in self.owned:
            if not o.section.heading_path:
                continue
            at = self.text.find(o.section.title, o.start)
            if at < 0:
                continue
            end = self.text.find("\n", at + len(o.section.title))
            if self.doc.format is SourceFormat.RST:
                end = self.text.find("\n", end + 1)
            spans.append((o.start, len(self.text) if end < 0 else end))
        return spans

    def _sections(self, key: str) -> list[Section]:
        return self.index.docs[key].root.walk()[1:]

    # Bookkeeping

    def _free(self, start: int, end: int) -> bool:
        return all(end <= a or start >= b for a, b in self.claimed)

    def _emit(
        self,
        start: int,
        end: int,
        target: Target | None,
        rule: str,
        weight: float = WEIGHT_EXPLICIT,
        source: SectionKey | None = None,
    ) -> None:
        """Record a reference over chars [start, end) unless it resolves to nothing or to itself."""
        source = source or self._source_at(start)
        if target is None or target == source:
            return
        self.out.append(
            Reference(
                source=source,
                target=target,
                span=(self._byte_at(start), self._byte_at(end)),
                text=self.text[start:end],
                rule=rule,
                weight=weight,
            )
        )

    def _matches(self, pattern: re.Pattern[str]) -> list[re.Match[str]]:
        """Matches of a pattern that do not overlap code or an earlier rule's span."""
        return [m for m in pattern.finditer(self.text) if self._free(*m.span())]

    def _claim(self, m: re.Match[str]) -> None:
        self.claimed.append(m.span())

    # rST hyperlink targets and labels

    def _target_defs(self) -> tuple[dict[str, Target], list[tuple[int, int]]]:
        """`.. _name: url` targets and `.. _label:` labels, by normalised name."""
        if self.doc.format is not SourceFormat.RST:
            return {}, []
        targets: dict[str, Target] = {}
        spans: list[tuple[int, int]] = []
        section_starts = sorted((o.start, o.section) for o in self.owned if o.section.heading_path)
        for m in _RE_TARGET_DEF.finditer(self.text):
            name = _norm_name(m.group(1).strip("`"))
            spans.append(m.span())
            if m.group(2):
                resolved = self._resolve_link(m.group(2))
                if resolved is not None:
                    targets[name] = resolved
                continue
            # A label directly above a heading names that section; anywhere else
            # it names an element inside the section that holds it.
            following = [(start, s) for start, s in section_starts if start > m.start()]
            if following:
                start, section = following[0]
                gap = self.text[m.end() : start]
                if all(not line.strip() or _RE_TARGET_DEF.match(line) for line in gap.split("\n")):
                    targets[name] = SectionKey(self.key, section.heading_path)
                    continue
            targets[name] = self._source_at(m.start())
        return targets, spans

    # Rules

    def _requires(self) -> None:
        if "Requires" not in self.doc.metadata:
            return
        root = SectionKey(self.key)
        header_end = self.owned[0].end if self.owned else len(self.text)
        m = _RE_REQUIRES.search(self.text, 0, header_end)
        if not m:
            return
        for n in re.finditer(r"\d+", m.group(1)):
            start, end = m.start(1) + n.start(), m.start(1) + n.end()
            self._emit(start, end, self._pep(n.group()), "requires", source=root)
        self._claim(m)

    def _links(self) -> None:
        patterns = [_RE_MD_LINK] if self.doc.format is SourceFormat.MARKDOWN else [_RE_RST_LINK]
        for pattern in [*patterns, _RE_URL]:
            for m in self._matches(pattern):
                url = m.group(2) if pattern is not _RE_URL else m.group().rstrip(".,;:!?")
                if pattern is _RE_RST_LINK and url.endswith("_") and not _RE_URL.match(url):
                    target = self._resolve_name(url[:-1])
                else:
                    target = self._resolve_link(url)
                end = m.start() + len(url) if pattern is _RE_URL else m.end()
                self._emit(m.start(), end, target, "link")
                self.claimed.append((m.start(), end))

    def _roles(self) -> None:
        if self.doc.format is not SourceFormat.RST:
            return
        for m in self._matches(_RE_ROLE):
            role, body = m.group(1), m.group(2)
            embedded = re.search(r"<([^<>]+)>\s*$", body)
            ref = (embedded.group(1) if embedded else body).strip()
            if role == "ref":
                local = self.targets.get(_norm_name(ref))
                target: Target | None = local or Placeholder("ref", ref)
            elif role == "pep":
                number, _, anchor = ref.partition("#")
                target = self._pep(number, anchor) if number.strip().isdigit() else None
            else:
                number = ref.partition("#")[0].strip()
                target = Placeholder("rfc", str(int(number))) if number.isdigit() else None
            self._emit(m.start(), m.end(), target, "role")
            self._claim(m)

    def _named(self) -> None:
        if self.doc.format is not SourceFormat.RST:
            return
        for m in self._matches(_RE_NAMED):
            target = self._resolve_name(m.group(1))
            if target is not None:
                self._emit(m.start(), m.end(), target, "named")
                self._claim(m)

    def _structural(self) -> None:
        for m in self._matches(_RE_STRUCTURAL):
            word = m.group("word").lower()
            doc_key = self.key
            end = m.end("labels")
            if m.group("of") and m.group("ofref").lower() != "this":
                other = self._of_document(m.group("ofref"))
                if other is None:
                    # "section 3 of RFC 2616", "Part 2 of the manual": not ours to resolve.
                    continue
                doc_key, end = other, m.end()
            for label in _RE_LABEL.finditer(m.group("labels")):
                if word not in _LETTERED and not label.group()[0].isdigit():
                    continue
                target = self._resolve_structural(doc_key, word, label.group())
                if target is None and doc_key != self.key and m.group("of"):
                    target = SectionKey(doc_key)
                if target is not None:
                    self._emit(m.start(), end, target, "structural")
                    self.claimed.append((m.start(), end))

    def _cited(self) -> None:
        for m in self._matches(_RE_CITED):
            kind = m.group(1).lower()
            for n in re.finditer(r"\d+", m.group(2)):
                target = self._pep(n.group()) if kind == "pep" else Placeholder("rfc", n.group())
                self._emit(m.start(), m.end(), target, "cited")
            self._claim(m)

    def _terms(self) -> None:
        defined: dict[str, SectionKey] = {}
        definition_spans: list[tuple[int, int]] = []
        for m in self._matches(_RE_DEFINITION):
            term = m.group("a") or m.group("b")
            defined.setdefault(term, self._source_at(m.start()))
            group = "a" if m.group("a") else "b"
            definition_spans.append(m.span(group))
        self.claimed.extend(definition_spans)
        if defined:
            # A heading is a label for its section, not a use of a term.
            self.claimed.extend(self._heading_spans())
        for term, home in defined.items():
            pattern = re.compile(rf"(?<![\w-]){re.escape(term)}(?![\w-])")
            for m in self._matches(pattern):
                self._emit(m.start(), m.end(), home, "term", weight=WEIGHT_TERM)

    # Resolution

    def _pep(self, number: str, anchor: str = "") -> Target | None:
        """A PEP number as a corpus document (or a section by anchor), else a placeholder.

        A document naming itself ("This PEP 9999 …") is not a reference.
        """
        n = str(int(number))
        key = self.index.lookup(f"pep:{n}")
        if key is None:
            return Placeholder("pep", n)
        section = self._resolve_anchor(key, anchor)
        if section is None and key == self.key:
            return None
        return section or SectionKey(key)

    def _of_document(self, ref: str) -> str | None:
        """The corpus document named after "of" in "section 3 of PEP 484"."""
        m = re.fullmatch(r":pep:`(?:[^`<]*<)?\s*(\d+)[^`]*`|PEP\s+(\d+)", ref)
        if not m:
            return None
        return self.index.lookup(f"pep:{int(m.group(1) or m.group(2))}")

    def _resolve_structural(self, doc_key: str, word: str, label: str) -> SectionKey | None:
        """The heading numbered `label` in a document, trying "4.2(a)" then "4.2"."""
        wanted = [label]
        if "(" in label:
            wanted.append(label.split("(")[0])
        sections = self._sections(doc_key)
        for want in wanted:
            for s in sections:
                found = heading_label(s.title)
                if found is None or found[1] != want:
                    continue
                heading_word = found[0]
                same_kind = (
                    heading_word == word if word in _LETTERED or heading_word in _LETTERED else True
                )
                if same_kind:
                    return SectionKey(doc_key, s.heading_path)
        return None

    def _resolve_name(self, name: str) -> Target | None:
        """An rST reference name: a hyperlink target, a label, then a heading title."""
        norm = _norm_name(name)
        if norm in self.targets:
            return self.targets[norm]
        for s in self._sections(self.key):
            if _norm_name(s.title) == norm:
                return SectionKey(self.key, s.heading_path)
        return None

    def _resolve_anchor(self, doc_key: str, anchor: str) -> SectionKey | None:
        """The section of a document whose heading slug (or rST label) is `anchor`."""
        anchor = unquote(anchor).strip().lower()
        if not anchor:
            return None
        for s in self._sections(doc_key):
            if slugify(s.title) == anchor:
                return SectionKey(doc_key, s.heading_path)
        if doc_key == self.key:
            label = self.targets.get(_norm_name(anchor.replace("-", " "))) or self.targets.get(
                anchor
            )
            if isinstance(label, SectionKey):
                return label
        return None

    def _resolve_link(self, url: str) -> Target | None:
        """A link: same-document anchor, then a corpus document, then a URL placeholder."""
        url = url.strip()
        if url.startswith("#"):
            return self._resolve_anchor(self.key, url[1:])
        parts = urlsplit(url)
        if parts.scheme in ("http", "https"):
            host = parts.netloc.lower().removeprefix("www.")
            m = re.fullmatch(r"/pep-0*(\d+)(?:\.html)?/?", parts.path)
            if host in ("peps.python.org", "python.org") and m:
                return self._pep(m.group(1), parts.fragment)
            m = re.fullmatch(r"/dev/peps/pep-0*(\d+)/?", parts.path)
            if host == "python.org" and m:
                return self._pep(m.group(1), parts.fragment)
            return Placeholder("url", url)
        if parts.scheme or parts.netloc:
            return Placeholder("url", url)
        name = PurePosixPath(unquote(parts.path)).name
        key = self.index.lookup(name) or self.index.lookup(name.lower())
        if key is None:
            stem = re.fullmatch(r"pep-0*(\d+)(?:\.\w+)?", name.lower())
            if stem:
                return self._pep(stem.group(1), parts.fragment)
            return Placeholder("url", url) if name else None
        return self._resolve_anchor(key, parts.fragment) or SectionKey(key)


class _CharToByte:
    """Character offset → UTF-8 byte offset, O(1) for ASCII text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.ascii = text.isascii()

    def __call__(self, offset: int) -> int:
        return offset if self.ascii else len(self.text[:offset].encode("utf-8"))


class _ByteToChar:
    """UTF-8 byte offset → character offset."""

    def __init__(self, text: str) -> None:
        self.data = text.encode("utf-8")
        self.ascii = text.isascii()

    def __call__(self, offset: int) -> int:
        return offset if self.ascii else len(self.data[:offset].decode("utf-8"))
