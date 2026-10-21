"""Filing parsed documents as atoms: containment, re-anchoring and derivation (DESIGN §2.2, §9).

`file_document` writes one parsed document into an open transaction:

- First ingest: a `document` atom, one `section` atom per heading, a version
  for each pointing into the stored derivative, and `contains` edges for the
  section tree.
- Revision (the derivative changed): new sections are matched to the existing
  atoms first by heading path, then by MinHash similarity. Matched atoms keep
  their `atom_id` and get a new version only if their text changed. Unmatched
  new sections become new atoms; unmatched old ones are tombstoned. Splits and
  merges are linked to the old atoms with `derived_from`. `contains` edges are
  closed and added so the current edges match the new tree.

`ingest_folder` files every supported file under a root in one commit, retires
documents whose files are gone, and links near-duplicate documents with
`derived_from` (newer → older).
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from mds.ingest.parse import ParsedDocument, ParseError, Section, detect_format, parse_file
from mds.ingest.similarity import Signature, signature
from mds.model import Atom, AtomKind, AuthorKind, EdgeState
from mds.store import ContentStore, Snapshot, Store, Transaction

CONTAINS_AUTHOR = "contains/1"
DERIVED_AUTHOR = "derived_from/1"
# Share of shingles two section texts must have in common to be the same
# section (Jaccard), or for one to be derived from the other (containment).
SECTION_THRESHOLD = 0.5
# Jaccard similarity above which two whole documents are near-duplicates.
DOCUMENT_THRESHOLD = 0.8
HINT_SEPARATOR = " › "

# A section's key within its document: heading path plus occurrence number,
# so repeated sibling headings ("Example", "Example") stay distinct.
SectionKey = tuple[tuple[str, ...], int]


@dataclass(frozen=True, slots=True)
class Filed:
    """What filing one document did. `sections` maps each section key to its atom."""

    document_id: int
    sections: Mapping[SectionKey, int]
    changed: bool
    created: tuple[int, ...] = ()
    versioned: tuple[int, ...] = ()
    tombstoned: tuple[int, ...] = ()
    derived: tuple[tuple[int, int], ...] = ()


@dataclass(slots=True)
class _Old:
    atom_id: int
    key: SectionKey
    text: str
    children: list[int] = field(default_factory=list)
    own: str = ""
    sig: Signature | None = None


@dataclass(slots=True)
class _New:
    section: Section
    key: SectionKey
    text: str
    own: str = ""
    sig: Signature | None = None
    atom_id: int | None = None
    matched: bool = field(default=False)


def path_hint(rel_path: str, heading_path: Sequence[str] = ()) -> str:
    """Human-readable location, e.g. `pep-0563.rst › Rationale › Details`."""
    return HINT_SEPARATOR.join([PurePosixPath(rel_path).name, *heading_path])


def section_keys(sections: Iterable[tuple[tuple[str, ...], object]]) -> list[SectionKey]:
    """Number repeated heading paths in document order."""
    seen: dict[tuple[str, ...], int] = defaultdict(int)
    keys: list[SectionKey] = []
    for heading_path, _ in sections:
        keys.append((heading_path, seen[heading_path]))
        seen[heading_path] += 1
    return keys


def find_document(snapshot: Snapshot, source_uri: str) -> Atom | None:
    """The current document atom for a source URI, if one exists."""
    for atom in snapshot.atoms():
        if atom.kind is AtomKind.DOCUMENT and atom.source_uri == source_uri:
            return atom
    return None


def file_document(
    tx: Transaction,
    snapshot: Snapshot,
    content: ContentStore,
    parsed: ParsedDocument,
    rel_path: str,
    source_uri: str,
    acl_class: int,
    threshold: float = SECTION_THRESHOLD,
) -> Filed:
    """File one parsed document into `tx`. `snapshot` is the store as it was before `tx`."""
    digest = content.put(parsed.text)
    new_sections = parsed.root.walk()[1:]
    new = [
        _New(s, key, parsed.text_of(s))
        for s, key in zip(
            new_sections, section_keys((s.heading_path, s) for s in new_sections), strict=True
        )
    ]
    text_by_section = {id(n.section): n.text for n in new}
    for n in new:
        n.own = own_text(n.text, [text_by_section[id(c)] for c in n.section.children])
    doc = find_document(snapshot, source_uri)
    if doc is None:
        doc = tx.add_atom(
            AtomKind.DOCUMENT, parsed.title, path_hint(rel_path), acl_class, source_uri=source_uri
        )
        old: list[_Old] = []
        old_edges: dict[tuple[int, int], int] = {}
    else:
        current = snapshot.version(doc.atom_id)
        if current is not None and current.digest == digest:
            tree = _old_tree(snapshot, doc.atom_id)
            return Filed(doc.atom_id, {o.key: o.atom_id for o in tree[0]}, changed=False)
        old, old_edges = _old_tree(snapshot, doc.atom_id)
        for o in old:
            o.text = _version_text(snapshot, content, o.atom_id)
        old_by_id = {o.atom_id: o for o in old}
        for o in old:
            o.own = own_text(o.text, [old_by_id[c].text for c in o.children if c in old_by_id])

    tx.add_version(
        doc.atom_id, digest, 0, parsed.root.byte_length, parsed.normaliser, parsed.root.origin
    )
    _match(new, old, threshold)

    created: list[int] = []
    versioned: list[int] = [doc.atom_id]
    old_text = {o.atom_id: o.text for o in old}
    for n in new:
        s = n.section
        if n.atom_id is None:
            atom = tx.add_atom(
                AtomKind.SECTION, s.title, path_hint(rel_path, s.heading_path), acl_class
            )
            n.atom_id = atom.atom_id
            created.append(atom.atom_id)
        elif old_text[n.atom_id] == n.text:
            continue
        tx.add_version(n.atom_id, digest, s.byte_offset, s.byte_length, parsed.normaliser, s.origin)
        versioned.append(n.atom_id)

    matched_ids = {n.atom_id for n in new}
    gone = [o for o in old if o.atom_id not in matched_ids]
    _sync_contains(tx, doc.atom_id, parsed.root, new, old_edges)
    for o in gone:
        tx.tombstone_atom(o.atom_id)
    derived = _derive(tx, new, old, gone, threshold)

    return Filed(
        document_id=doc.atom_id,
        sections={n.key: n.atom_id for n in new if n.atom_id is not None},
        changed=True,
        created=tuple(created),
        versioned=tuple(versioned),
        tombstoned=tuple(o.atom_id for o in gone),
        derived=tuple(derived),
    )


def _old_tree(snapshot: Snapshot, doc_id: int) -> tuple[list[_Old], dict[tuple[int, int], int]]:
    """The document's current section atoms (in tree order) and its contains edges by endpoints."""
    children: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for edge in snapshot.edges("contains"):
        children[edge.src].append((edge.edge_id, edge.dst))
    atoms = {a.atom_id: a for a in snapshot.atoms()}
    edges: dict[tuple[int, int], int] = {}
    walked: list[tuple[tuple[str, ...], int]] = []
    kids: dict[int, list[int]] = defaultdict(list)

    def walk(parent: int, path: tuple[str, ...]) -> None:
        for edge_id, child in sorted(children.get(parent, [])):
            edges[(parent, child)] = edge_id
            atom = atoms.get(child)
            if atom is None:
                continue
            kids[parent].append(child)
            own = (*path, atom.title)
            walked.append((own, child))
            walk(child, own)

    walk(doc_id, ())
    keys = section_keys(walked)
    old = [
        _Old(atom_id, key, "", children=kids[atom_id])
        for key, (_, atom_id) in zip(keys, walked, strict=True)
    ]
    return old, edges


def _version_text(snapshot: Snapshot, content: ContentStore, atom_id: int) -> str:
    version = snapshot.version(atom_id)
    if version is None:
        return ""
    return content.slice(version.digest, version.byte_offset, version.byte_length)


def own_text(text: str, child_texts: Sequence[str]) -> str:
    """A section's text without its subsections, so a parent is not similar to its children."""
    for child in child_texts:
        text = text.replace(child, "", 1)
    return text


def _sig(item: _Old | _New) -> Signature:
    """MinHash signature of the item's own text (excluding subsections)."""
    if item.sig is None:
        item.sig = signature(item.own)
    return item.sig


def _match(new: list[_New], old: list[_Old], threshold: float) -> None:
    """Assign old atom ids to new sections: by section key, then by best Jaccard, one-to-one."""
    by_key = {o.key: o for o in old}
    taken: set[int] = set()
    for n in new:
        o = by_key.get(n.key)
        if o is not None:
            n.atom_id, n.matched = o.atom_id, True
            taken.add(o.atom_id)
    candidates = [
        (_sig(n).jaccard(_sig(o)), i, o.atom_id)
        for i, n in enumerate(new)
        if n.atom_id is None
        for o in old
        if o.atom_id not in taken
    ]
    for score, i, atom_id in sorted(candidates, key=lambda c: (-c[0], c[1], c[2])):
        if score < threshold:
            break
        if new[i].atom_id is None and atom_id not in taken:
            new[i].atom_id, new[i].matched = atom_id, True
            taken.add(atom_id)


def _sync_contains(
    tx: Transaction,
    doc_id: int,
    root: Section,
    new: list[_New],
    old_edges: Mapping[tuple[int, int], int],
) -> None:
    """Close contains edges that no longer hold and add the missing ones."""
    atom_of = {id(n.section): n.atom_id for n in new}
    wanted: set[tuple[int, int]] = set()

    def walk(section: Section, parent: int) -> None:
        for child in section.children:
            child_id = atom_of[id(child)]
            assert child_id is not None
            wanted.add((parent, child_id))
            walk(child, child_id)

    walk(root, doc_id)
    for pair, edge_id in old_edges.items():
        if pair not in wanted:
            tx.close_edge(edge_id)
    for parent, child in sorted(wanted - set(old_edges)):
        tx.add_edge(
            "contains",
            parent,
            child,
            1.0,
            AuthorKind.EXTRACTOR,
            CONTAINS_AUTHOR,
            EdgeState.CONFIRMED,
        )


def _derive(
    tx: Transaction, new: list[_New], old: list[_Old], gone: list[_Old], threshold: float
) -> list[tuple[int, int]]:
    """`derived_from` edges for splits (a new section mostly contained in an old one) and
    merges (a retired old section mostly contained in a new one)."""
    pairs: dict[tuple[int, int], float] = {}
    for n in new:
        if n.matched:
            continue
        for o in old:
            score = _sig(n).containment_in(_sig(o))
            if score >= threshold:
                pairs[(n.atom_id or 0, o.atom_id)] = score
    for o in gone:
        for n in new:
            score = _sig(o).containment_in(_sig(n))
            if score >= threshold and n.atom_id != o.atom_id:
                key = (n.atom_id or 0, o.atom_id)
                pairs[key] = max(score, pairs.get(key, 0.0))
    for (src, dst), score in sorted(pairs.items()):
        tx.add_edge(
            "derived_from",
            src,
            dst,
            round(min(max(score, 0.01), 1.0), 3),
            AuthorKind.EXTRACTOR,
            DERIVED_AUTHOR,
            EdgeState.CONFIRMED,
        )
    return sorted(pairs)


# Folder ingest ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FolderResult:
    """What one folder ingest did."""

    csn: int
    documents: Mapping[str, Filed]
    retired: tuple[int, ...]
    near_duplicates: tuple[tuple[int, int], ...]
    skipped: Mapping[str, str]


def source_files(root: Path) -> list[Path]:
    """Supported files under `root`, sorted, skipping hidden files and folders."""
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or any(part.startswith(".") for part in rel.parts):
            continue
        try:
            detect_format(path)
        except ParseError:
            continue
        files.append(path)
    return files


def ingest_folder(
    store: Store,
    root: Path,
    acl_class_for: Callable[[str], int],
    author_id: str = "ingest",
    threshold: float = SECTION_THRESHOLD,
    document_threshold: float = DOCUMENT_THRESHOLD,
    artifacts_path: Path | None = None,
) -> FolderResult:
    """Ingest every supported file under `root` in one commit.

    `acl_class_for` maps a path relative to the root to the atom's interned
    `acl_class` (see `mds.acl.acl_class_for_path`). Files that fail to parse
    are skipped and reported; the rest are filed.
    """
    root = Path(root).resolve()
    snapshot = store.snapshot()
    filed: dict[str, Filed] = {}
    parsed_docs: dict[str, ParsedDocument] = {}
    skipped: dict[str, str] = {}
    with store.commit(author_id, f"ingest {root.name}") as tx:
        for path in source_files(root):
            rel = path.relative_to(root).as_posix()
            try:
                parsed = parse_file(path, artifacts_path)
            except ParseError as exc:
                skipped[rel] = str(exc)
                continue
            parsed_docs[rel] = parsed
            filed[rel] = file_document(
                tx,
                snapshot,
                store.content,
                parsed,
                rel,
                path.as_uri(),
                acl_class_for(rel),
                threshold,
            )
        retired = _retire_missing(tx, snapshot, root, {path_uri(root, r) for r in filed})
        near = _near_duplicates(tx, snapshot, filed, parsed_docs, document_threshold)
    return FolderResult(store.latest_csn, filed, retired, near, skipped)


def path_uri(root: Path, rel: str) -> str:
    """The `file://` URI of a path relative to the ingest root."""
    return (root / rel).as_uri()


def _retire_missing(
    tx: Transaction, snapshot: Snapshot, root: Path, present: set[str]
) -> tuple[int, ...]:
    """Tombstone documents under `root` whose files are gone, with their sections."""
    prefix = root.as_uri() + "/"
    retired: list[int] = []
    for atom in snapshot.atoms():
        uri = atom.source_uri
        if atom.kind is not AtomKind.DOCUMENT or uri is None or not uri.startswith(prefix):
            continue
        if uri in present:
            continue
        sections, edges = _old_tree(snapshot, atom.atom_id)
        for edge_id in edges.values():
            tx.close_edge(edge_id)
        for atom_id in [atom.atom_id, *(s.atom_id for s in sections)]:
            tx.tombstone_atom(atom_id)
            retired.append(atom_id)
    return tuple(retired)


def _near_duplicates(
    tx: Transaction,
    snapshot: Snapshot,
    filed: Mapping[str, Filed],
    parsed: Mapping[str, ParsedDocument],
    threshold: float,
) -> tuple[tuple[int, int], ...]:
    """`derived_from` newer → older for near-duplicate documents, ordered by `Created` date."""
    existing = {
        (e.src, e.dst) for e in snapshot.edges("derived_from") if e.author_id == DERIVED_AUTHOR
    }
    sigs = {rel: signature(doc.text) for rel, doc in parsed.items()}
    rels = sorted(parsed)
    added: list[tuple[int, int]] = []
    for i, a in enumerate(rels):
        for b in rels[i + 1 :]:
            score = sigs[a].jaccard(sigs[b])
            if score < threshold:
                continue
            order = _older_first(parsed[a], parsed[b])
            if order is None:
                continue
            older, newer = (a, b) if order else (b, a)
            pair = (filed[newer].document_id, filed[older].document_id)
            if pair in existing:
                continue
            tx.add_edge(
                "derived_from",
                *pair,
                round(min(score, 1.0), 3),
                AuthorKind.EXTRACTOR,
                DERIVED_AUTHOR,
                EdgeState.CONFIRMED,
            )
            added.append(pair)
    return tuple(added)


def _older_first(a: ParsedDocument, b: ParsedDocument) -> bool | None:
    """True if `a` is older, False if `b` is, None if the dates don't decide it."""
    da, db = created_date(a), created_date(b)
    if da is None or db is None or da == db:
        return None
    return da < db


def created_date(doc: ParsedDocument) -> date | None:
    """The document's `Created` header as a date (PEP style `22-Aug-2001` or ISO)."""
    raw = doc.metadata.get("Created", "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None
