"""Parsing and normalisation: source file → derivative text, section tree, origin map (DESIGN §2.1).

Every format produces a `ParsedDocument`:

- `text` is the normalised derivative. It is what the content store holds and
  what atom byte ranges point into.
- `root` is the section tree. The root spans the whole derivative; each
  section spans its heading and body up to the next heading at the same or a
  higher level, so a section contains its subsections.
- Each section's `origin` locates it in the original file: line numbers for
  text formats, page and bounding box for PDF and DOCX.

Markdown, reStructuredText and plain text are parsed natively. PDF and DOCX go
through Docling, which is an optional dependency (`mds-server[pdf]`).
"""

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

NORM_VERSION = "norm-1"


class ParseError(Exception):
    """A file cannot be read or parsed."""


class SourceFormat(StrEnum):
    """Formats MDS can ingest (SCOPE goal 1)."""

    MARKDOWN = "md"
    RST = "rst"
    TEXT = "text"
    PDF = "pdf"
    DOCX = "docx"


SUFFIXES: Mapping[str, SourceFormat] = {
    ".md": SourceFormat.MARKDOWN,
    ".markdown": SourceFormat.MARKDOWN,
    ".rst": SourceFormat.RST,
    ".txt": SourceFormat.TEXT,
    ".text": SourceFormat.TEXT,
    ".pdf": SourceFormat.PDF,
    ".docx": SourceFormat.DOCX,
}


@dataclass(frozen=True, slots=True)
class Section:
    """One node of the section tree, located by a UTF-8 byte range in the derivative."""

    title: str
    level: int
    heading_path: tuple[str, ...]
    byte_offset: int
    byte_length: int
    origin: Mapping[str, Any] = field(hash=False)
    children: tuple["Section", ...] = ()

    def walk(self) -> list["Section"]:
        """This section and all its descendants, in document order."""
        out = [self]
        for child in self.children:
            out.extend(child.walk())
        return out


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """The result of parsing one source file."""

    format: SourceFormat
    normaliser: str
    text: str
    title: str
    root: Section
    # Leading `Key: value` header fields (PEP headers, for example), used by
    # metadata-declared supersession (DESIGN §9.1).
    metadata: Mapping[str, str] = field(default_factory=dict, hash=False)

    def text_of(self, section: Section) -> str:
        """The derivative text a section's byte range covers."""
        data = self.text.encode("utf-8")
        return data[section.byte_offset : section.byte_offset + section.byte_length].decode("utf-8")


@dataclass(frozen=True, slots=True)
class Heading:
    """A heading found by a format parser, before the tree is built.

    `start` is the character offset in the derivative where the heading begins
    (including an rST overline); `origin` locates it in the original.
    """

    level: int
    title: str
    start: int
    origin: Mapping[str, Any]


def detect_format(path: Path) -> SourceFormat:
    """The format implied by a file's suffix."""
    try:
        return SUFFIXES[path.suffix.lower()]
    except KeyError:
        known = ", ".join(sorted(SUFFIXES))
        raise ParseError(
            f"unsupported file type {path.suffix!r} ({path.name}); known: {known}"
        ) from None


def normalise(raw: str | bytes) -> str:
    """Normalise source text into a derivative (`norm-1`).

    Decodes UTF-8 (dropping a byte-order mark), converts line endings to LF,
    applies Unicode NFC, strips trailing whitespace from each line and ends the
    text with exactly one newline (none if empty). Line numbering is preserved,
    so line origins in the derivative match the original file.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ParseError(f"source is not valid UTF-8: {exc}") from None
    text = raw.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def parse_file(path: Path, artifacts_path: Path | None = None) -> ParsedDocument:
    """Parse a file, choosing the parser by suffix.

    `artifacts_path` is the local Docling model cache for PDF and DOCX; MDS
    never downloads models at runtime.
    """
    path = Path(path)
    fmt = detect_format(path)
    if fmt in (SourceFormat.PDF, SourceFormat.DOCX):
        from mds.ingest._docling import parse_with_docling

        return parse_with_docling(path, fmt, artifacts_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc
    return parse_text(normalise(raw), fmt, fallback_title=path.name)


def parse_text(text: str, fmt: SourceFormat, fallback_title: str = "") -> ParsedDocument:
    """Parse already-normalised text in a text format (Markdown, reStructuredText, plain)."""
    from mds.ingest._markdown import markdown_headings
    from mds.ingest._rst import rst_headings

    if text != normalise(text):
        raise ParseError("text must be normalised first (see normalise())")
    lines = text.split("\n")
    metadata, body_start = header_fields(lines) if fmt is not SourceFormat.MARKDOWN else ({}, 0)
    if fmt is SourceFormat.MARKDOWN:
        headings = markdown_headings(lines)
    elif fmt is SourceFormat.RST:
        headings = rst_headings(lines, body_start)
    elif fmt is SourceFormat.TEXT:
        headings = []
    else:
        raise ParseError(f"{fmt} is not a text format")
    title = metadata.get("Title") or _document_title(headings) or fallback_title
    return build_document(
        fmt=fmt,
        normaliser=f"{fmt.value}/{NORM_VERSION}",
        text=text,
        title=title,
        headings=headings,
        root_origin={"line": 1, "end_line": max(len(lines) - 1, 1)},
        metadata=metadata,
    )


def header_fields(lines: Sequence[str]) -> tuple[dict[str, str], int]:
    """Parse a leading RFC 822-style header block (`Key: value`, indented continuations).

    Returns the fields and the index of the first line after the block. If the
    first line is not a header field there is no block.
    """
    fields: dict[str, str] = {}
    key: str | None = None
    i = 0
    while i < len(lines) and lines[i]:
        line = lines[i]
        if line[0] in " \t" and key is not None:
            fields[key] = f"{fields[key]} {line.strip()}".strip()
        else:
            name, sep, value = line.partition(":")
            if not sep or not _is_field_name(name) or (value and not value.startswith(" ")):
                break
            key = name
            fields[key] = value.strip()
        i += 1
    if not fields:
        return {}, 0
    return fields, i


def _is_field_name(name: str) -> bool:
    return bool(name) and name[0].isalpha() and all(c.isalnum() or c == "-" for c in name)


def _document_title(headings: Sequence[Heading]) -> str:
    """The text of the single top-level heading, if the document has exactly one."""
    top = [h for h in headings if h.level == 1]
    return top[0].title if len(top) == 1 else ""


def build_document(
    fmt: SourceFormat,
    normaliser: str,
    text: str,
    title: str,
    headings: Sequence[Heading],
    root_origin: Mapping[str, Any],
    metadata: Mapping[str, str] | None = None,
) -> ParsedDocument:
    """Assemble the section tree from headings in document order."""
    data = text.encode("utf-8")
    byte_at = _char_to_byte(text)
    ends = _section_ends(text, headings)

    def build(index: int, path: tuple[str, ...]) -> tuple[Section, int]:
        heading = headings[index]
        own_path = (*path, heading.title)
        children: list[Section] = []
        j = index + 1
        while j < len(headings) and headings[j].level > heading.level:
            child, j = build(j, own_path)
            children.append(child)
        start = byte_at(heading.start)
        section = Section(
            title=heading.title,
            level=heading.level,
            heading_path=own_path,
            byte_offset=start,
            byte_length=byte_at(ends[index]) - start,
            origin=dict(heading.origin),
            children=tuple(children),
        )
        return section, j

    top: list[Section] = []
    i = 0
    while i < len(headings):
        section, i = build(i, ())
        top.append(section)
    root = Section(
        title=title,
        level=0,
        heading_path=(),
        byte_offset=0,
        byte_length=len(data.rstrip()),
        origin=dict(root_origin),
        children=tuple(top),
    )
    return ParsedDocument(fmt, normaliser, text, title, root, dict(metadata or {}))


def _section_ends(text: str, headings: Sequence[Heading]) -> list[int]:
    """Character offset where each section ends: the next heading at the same or a higher
    level, or the end of the text, with trailing whitespace trimmed."""
    ends: list[int] = []
    for i, heading in enumerate(headings):
        end = len(text)
        for later in headings[i + 1 :]:
            if later.level <= heading.level:
                end = later.start
                break
        ends.append(len(text[:end].rstrip()))
    return ends


def _char_to_byte(text: str) -> Any:
    if text.isascii():
        return lambda offset: offset
    return lambda offset: len(text[:offset].encode("utf-8"))


def line_starts(lines: Sequence[str]) -> list[int]:
    """Character offset of the start of each line in `"\\n".join(lines)`."""
    starts: list[int] = []
    pos = 0
    for line in lines:
        starts.append(pos)
        pos += len(line) + 1
    return starts
