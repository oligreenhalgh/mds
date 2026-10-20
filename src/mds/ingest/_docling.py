"""PDF and DOCX through Docling (optional dependency: `mds-server[pdf]`).

`from_docling_document` turns a Docling document into a `ParsedDocument`. It
reads only the parts of Docling's document API it needs (`iterate_items()`,
item `label`, `text`, `level` and `prov`), so tests can drive it with fakes.

The derivative is the document's text blocks joined by blank lines, with each
heading on its own line. Origins are the page and bounding box of the block's
first provenance entry.
"""

from collections.abc import Iterable, Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from mds.ingest.parse import (
    NORM_VERSION,
    Heading,
    ParsedDocument,
    ParseError,
    SourceFormat,
    build_document,
    normalise,
)

# Labels whose text belongs in the derivative. Page furniture is dropped.
_BODY_LABELS = {
    "text",
    "paragraph",
    "list_item",
    "code",
    "formula",
    "caption",
    "footnote",
    "table",
    "reference",
}
_HEADING_LABELS = {"title", "section_header"}


def parse_with_docling(
    path: Path, fmt: SourceFormat, artifacts_path: Path | None = None
) -> ParsedDocument:
    """Convert a PDF or DOCX file with Docling, using only locally cached models."""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        raise ParseError(
            f"{path.name}: PDF and DOCX need Docling; install mds-server[pdf]"
        ) from None
    if not path.is_file():
        raise ParseError(f"cannot read {path}: no such file")
    converter = _converter(DocumentConverter, fmt, artifacts_path)
    try:
        result = converter.convert(str(path))
    except Exception as exc:  # Docling raises many types; report them uniformly.
        raise ParseError(f"Docling could not convert {path.name}: {exc}") from exc
    return from_docling_document(
        result.document, fmt, normaliser=docling_normaliser(), fallback_title=path.name
    )


def _converter(converter_cls: Any, fmt: SourceFormat, artifacts_path: Path | None) -> Any:
    if artifacts_path is None or fmt is not SourceFormat.PDF:
        return converter_cls()
    # Point the PDF pipeline at the local model cache so nothing is downloaded.
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import PdfFormatOption

    options = PdfPipelineOptions(artifacts_path=str(artifacts_path))
    return converter_cls(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def docling_normaliser() -> str:
    """Normaliser id for the installed Docling, e.g. `docling-2.x/norm-1`."""
    try:
        major = version("docling").split(".")[0]
    except PackageNotFoundError:
        major = "0"
    return f"docling-{major}.x/{NORM_VERSION}"


def from_docling_document(
    doc: Any, fmt: SourceFormat, normaliser: str, fallback_title: str = ""
) -> ParsedDocument:
    """Build a `ParsedDocument` from a Docling document."""
    blocks: list[str] = []
    headings: list[Heading] = []
    title = ""
    pos = 0
    first_origin: Mapping[str, Any] = {}
    for item, _depth in _items(doc):
        label = _label(item)
        if label not in _HEADING_LABELS and label not in _BODY_LABELS:
            continue
        text = _block_text(item, label, doc)
        if not text:
            continue
        origin = _origin(item)
        if not first_origin:
            first_origin = origin
        if label == "title":
            title = title or text
            headings.append(Heading(1, text, pos, origin))
        elif label == "section_header":
            # Section headers sit one level below a document title.
            level = max(int(getattr(item, "level", 1) or 1), 1) + 1
            headings.append(Heading(level, text, pos, origin))
        blocks.append(text)
        pos += len(text) + 2
    text = normalise("\n\n".join(blocks))
    if not title:
        headings = [Heading(h.level - 1, h.title, h.start, h.origin) for h in headings]
    _check_offsets(text, headings)
    return build_document(
        fmt=fmt,
        normaliser=normaliser,
        text=text,
        title=title or fallback_title,
        headings=headings,
        root_origin=first_origin,
    )


def _items(doc: Any) -> Iterable[tuple[Any, int]]:
    try:
        return list(doc.iterate_items())
    except AttributeError:
        raise ParseError("not a Docling document (no iterate_items)") from None


def _label(item: Any) -> str:
    label = getattr(item, "label", "")
    return str(getattr(label, "value", label)).lower()


def _block_text(item: Any, label: str, doc: Any) -> str:
    if label == "table":
        export = getattr(item, "export_to_markdown", None)
        if export is None:
            return ""
        try:
            raw = export(doc=doc)
        except TypeError:
            raw = export()
        return normalise(str(raw)).strip()
    return normalise(str(getattr(item, "text", "") or "")).strip()


def _origin(item: Any) -> dict[str, Any]:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return {}
    first = prov[0]
    origin: dict[str, Any] = {"page": getattr(first, "page_no", None)}
    bbox = getattr(first, "bbox", None)
    if bbox is not None:
        origin["bbox"] = [bbox.l, bbox.t, bbox.r, bbox.b]
    return origin


def _check_offsets(text: str, headings: list[Heading]) -> None:
    # Normalising the joined blocks must not move any heading.
    for h in headings:
        if not text.startswith(h.title, h.start):
            raise ParseError(f"heading {h.title!r} moved during normalisation")
