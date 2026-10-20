"""Filing documents as atoms: containment, re-anchoring, derivation (DESIGN §2.2, §9)."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from mds.ingest.filing import (
    CONTAINS_AUTHOR,
    DERIVED_AUTHOR,
    FolderResult,
    created_date,
    ingest_folder,
    path_hint,
    section_keys,
    source_files,
)
from mds.ingest.parse import SourceFormat, normalise, parse_text
from mds.model import AtomKind, AuthorKind, EdgeState
from mds.store import Snapshot, Store


def words(prefix: str, n: int = 40) -> str:
    """A paragraph of distinct words, so similarity between paragraphs is controlled."""
    return " ".join(f"{prefix}{i}" for i in range(n))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store(tmp_path / "data") as s:
        yield s


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "root"
    path.mkdir()
    return path


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def ingest(store: Store, root: Path) -> FolderResult:
    return ingest_folder(store, root, lambda rel: 7 if rel.startswith("secret/") else 0)


def by_hint(snap: Snapshot) -> dict[str, int]:
    return {a.path_hint: a.atom_id for a in snap.atoms()}


def contains_pairs(snap: Snapshot) -> set[tuple[str, str]]:
    hint = {a.atom_id: a.path_hint for a in snap.atoms(include_tombstoned=True)}
    return {(hint[e.src], hint[e.dst]) for e in snap.edges("contains")}


def text_of(store: Store, snap: Snapshot, atom_id: int) -> str:
    v = snap.version(atom_id)
    assert v is not None
    return store.content.slice(v.digest, v.byte_offset, v.byte_length)


POLICY = f"""# Policy

## Records

{words("rec")}

### Retention

{words("ret")}

## Review

{words("rev")}
"""


# Helpers ------------------------------------------------------------------


def test_path_hint() -> None:
    assert path_hint("public/pep-0563.rst") == "pep-0563.rst"
    assert path_hint("a/b.md", ("4", "4.2")) == "b.md › 4 › 4.2"


def test_section_keys_number_repeated_paths() -> None:
    paths = [(("A",), None), (("A", "Ex"), None), (("A", "Ex"), None), (("B",), None)]
    assert section_keys(paths) == [(("A",), 0), (("A", "Ex"), 0), (("A", "Ex"), 1), (("B",), 0)]


@pytest.mark.parametrize(
    ("header", "expected"),
    [("22-Aug-2001", "2001-08-22"), ("2023-03-01", "2023-03-01"), ("someday", None), ("", None)],
)
def test_created_date(header: str, expected: str | None) -> None:
    text = normalise(f"Created: {header}\n\nBody.\n" if header else "Body.\n")
    got = created_date(parse_text(text, SourceFormat.RST))
    assert (got.isoformat() if got else None) == expected


def test_source_files_skips_hidden_and_unsupported(root: Path) -> None:
    for rel in ["a.md", "b/c.rst", ".hidden.md", ".git/x.md", "d.xls", "e.txt"]:
        write(root, rel, "x\n")
    assert [p.relative_to(root).as_posix() for p in source_files(root)] == [
        "a.md",
        "b/c.rst",
        "e.txt",
    ]


# First ingest -------------------------------------------------------------


def test_first_ingest_files_document_sections_and_contains(store: Store, root: Path) -> None:
    write(root, "policy.md", POLICY)
    result = ingest(store, root)
    snap = store.snapshot()
    assert result.csn == 1
    ids = by_hint(snap)
    assert set(ids) == {
        "policy.md",
        "policy.md › Policy",
        "policy.md › Policy › Records",
        "policy.md › Policy › Records › Retention",
        "policy.md › Policy › Review",
    }
    doc = snap.atom(ids["policy.md"])
    assert doc is not None
    assert doc.kind is AtomKind.DOCUMENT and doc.title == "Policy"
    assert doc.source_uri == (root / "policy.md").resolve().as_uri()
    assert contains_pairs(snap) == {
        ("policy.md", "policy.md › Policy"),
        ("policy.md › Policy", "policy.md › Policy › Records"),
        ("policy.md › Policy › Records", "policy.md › Policy › Records › Retention"),
        ("policy.md › Policy", "policy.md › Policy › Review"),
    }
    for edge in snap.edges("contains"):
        assert edge.author_id == CONTAINS_AUTHOR
        assert edge.author_kind is AuthorKind.EXTRACTOR and edge.state is EdgeState.CONFIRMED
    retention = ids["policy.md › Policy › Records › Retention"]
    assert text_of(store, snap, retention) == f"### Retention\n\n{words('ret')}"
    assert text_of(store, snap, ids["policy.md"]) == POLICY.rstrip()
    assert (
        result.documents["policy.md"].sections[(("Policy", "Records", "Retention"), 0)] == retention
    )


def test_acl_class_comes_from_relative_path(store: Store, root: Path) -> None:
    write(root, "open.md", "# Open\n")
    write(root, "secret/closed.md", "# Closed\n")
    ingest(store, root)
    classes = {a.path_hint: a.acl_class for a in store.snapshot().atoms()}
    assert classes == {"open.md": 0, "open.md › Open": 0, "closed.md": 7, "closed.md › Closed": 7}


def test_unchanged_reingest_writes_nothing(store: Store, root: Path) -> None:
    write(root, "policy.md", POLICY)
    first = ingest(store, root)
    again = ingest(store, root)
    assert store.latest_csn == 1
    assert not again.documents["policy.md"].changed
    assert again.documents["policy.md"].sections == first.documents["policy.md"].sections


def test_unparseable_file_is_skipped_and_reported(store: Store, root: Path) -> None:
    write(root, "good.md", "# Good\n")
    (root / "bad.md").write_bytes(b"\xff\xfe bad")
    result = ingest(store, root)
    assert "not valid UTF-8" in result.skipped["bad.md"]
    assert set(result.documents) == {"good.md"}


# Revisions ----------------------------------------------------------------


def test_edited_section_keeps_atom_and_gets_new_version(store: Store, root: Path) -> None:
    write(root, "policy.md", POLICY)
    ingest(store, root)
    before = by_hint(store.snapshot())
    write(root, "policy.md", POLICY.replace(words("ret"), words("ret", 39) + " amended"))
    result = ingest(store, root)
    snap = store.snapshot()
    assert by_hint(snap) == before
    retention = before["policy.md › Policy › Records › Retention"]
    review = before["policy.md › Policy › Review"]
    assert snap.version(retention).version == 2  # type: ignore[union-attr]
    # Unchanged sections keep their version, still pointing into the old derivative.
    assert snap.version(review).version == 1  # type: ignore[union-attr]
    assert text_of(store, snap, retention).endswith("amended")
    assert text_of(store, store.snapshot(as_of_csn=1), retention).endswith("ret39")
    filed = result.documents["policy.md"]
    assert retention in filed.versioned and review not in filed.versioned
    assert filed.created == () and filed.tombstoned == ()


def test_added_and_removed_sections(store: Store, root: Path) -> None:
    write(root, "policy.md", POLICY)
    ingest(store, root)
    before = by_hint(store.snapshot())
    revised = POLICY.replace(f"## Review\n\n{words('rev')}\n", f"## Appeals\n\n{words('app')}\n")
    write(root, "policy.md", revised)
    result = ingest(store, root)
    snap = store.snapshot()
    review = before["policy.md › Policy › Review"]
    appeals = by_hint(snap)["policy.md › Policy › Appeals"]
    assert snap.atom(review) is None
    assert result.documents["policy.md"].tombstoned == (review,)
    assert result.documents["policy.md"].created == (appeals,)
    assert ("policy.md › Policy", "policy.md › Policy › Appeals") in contains_pairs(snap)
    assert ("policy.md › Policy", "policy.md › Policy › Review") not in contains_pairs(snap)
    # History is intact at the earlier CSN.
    assert ("policy.md › Policy", "policy.md › Policy › Review") in contains_pairs(
        store.snapshot(as_of_csn=1)
    )
    assert result.documents["policy.md"].derived == ()


def test_renamed_heading_matched_by_similarity(store: Store, root: Path) -> None:
    write(root, "policy.md", POLICY)
    ingest(store, root)
    review = by_hint(store.snapshot())["policy.md › Policy › Review"]
    write(root, "policy.md", POLICY.replace("## Review", "## Annual review"))
    result = ingest(store, root)
    filed = result.documents["policy.md"]
    assert filed.sections[(("Policy", "Annual review"), 0)] == review
    assert filed.created == () and filed.tombstoned == ()
    # Atom records are append-only, so the title and path hint keep the first heading.
    assert store.snapshot().atom(review).title == "Review"  # type: ignore[union-attr]


def test_moved_section_is_reparented(store: Store, root: Path) -> None:
    write(root, "policy.md", POLICY)
    ingest(store, root)
    retention = by_hint(store.snapshot())["policy.md › Policy › Records › Retention"]
    moved = POLICY.replace(f"### Retention\n\n{words('ret')}\n\n", "") + (
        f"\n### Retention\n\n{words('ret')}\n"
    )
    write(root, "policy.md", moved)
    result = ingest(store, root)
    assert result.documents["policy.md"].sections[(("Policy", "Review", "Retention"), 0)] == (
        retention
    )
    snap = store.snapshot()
    parents = [e.src for e in snap.edges("contains") if e.dst == retention]
    assert parents == [by_hint(snap)["policy.md › Policy › Review"]]


def test_split_section_is_derived_from_original(store: Store, root: Path) -> None:
    doc = f"# Doc\n\n## Terms\n\n{words('t')}\n\n{words('u')}\n"
    write(root, "doc.md", doc)
    ingest(store, root)
    terms = by_hint(store.snapshot())["doc.md › Doc › Terms"]
    write(root, "doc.md", f"# Doc\n\n## Terms\n\n{words('t')}\n\n## More terms\n\n{words('u')}\n")
    result = ingest(store, root)
    snap = store.snapshot()
    more = by_hint(snap)["doc.md › Doc › More terms"]
    assert result.documents["doc.md"].derived == ((more, terms),)
    (edge,) = snap.edges("derived_from")
    assert (edge.src, edge.dst, edge.author_id) == (more, terms, DERIVED_AUTHOR)
    assert 0 < edge.weight <= 1


def test_merged_sections_derive_from_retired_one(store: Store, root: Path) -> None:
    doc = f"# Doc\n\n## A\n\n{words('a')}\n\n## B\n\n{words('b')}\n"
    write(root, "doc.md", doc)
    ingest(store, root)
    ids = by_hint(store.snapshot())
    write(root, "doc.md", f"# Doc\n\n## A\n\n{words('a')}\n\n{words('b')}\n")
    result = ingest(store, root)
    filed = result.documents["doc.md"]
    assert filed.tombstoned == (ids["doc.md › Doc › B"],)
    assert filed.derived == ((ids["doc.md › Doc › A"], ids["doc.md › Doc › B"]),)


def test_repeated_sibling_headings_stay_distinct(store: Store, root: Path) -> None:
    doc = f"# Doc\n\n## Example\n\n{words('x')}\n\n## Example\n\n{words('y')}\n"
    write(root, "doc.md", doc)
    first = ingest(store, root).documents["doc.md"].sections
    assert first[(("Doc", "Example"), 0)] != first[(("Doc", "Example"), 1)]
    write(root, "doc.md", doc.replace(words("y"), words("y", 39) + " changed"))
    again = ingest(store, root).documents["doc.md"]
    assert again.sections == first
    # The changed section and its parent (whose text includes it) get new versions.
    assert again.versioned[1:] == (first[(("Doc",), 0)], first[(("Doc", "Example"), 1)])


# Folder-level -------------------------------------------------------------


def test_deleted_file_is_retired(store: Store, root: Path) -> None:
    write(root, "keep.md", "# Keep\n")
    write(root, "drop.md", f"# Drop\n\n## Part\n\n{words('d')}\n")
    ingest(store, root)
    dropped = {h: i for h, i in by_hint(store.snapshot()).items() if h.startswith("drop.md")}
    (root / "drop.md").unlink()
    result = ingest(store, root)
    snap = store.snapshot()
    assert sorted(result.retired) == sorted(dropped.values())
    assert set(by_hint(snap)) == {"keep.md", "keep.md › Keep"}
    assert all(e.src not in dropped.values() for e in snap.edges("contains"))


def near_dup(created: str, title: str, tail: str) -> str:
    return f"Title: {title}\nCreated: {created}\n\n{words('w', 200)} {tail}\n"


def test_near_duplicates_link_newer_to_older(store: Store, root: Path) -> None:
    write(root, "old.rst", near_dup("01-Jan-2003", "Old", "first"))
    write(root, "new.rst", near_dup("01-Jan-2010", "New", "second"))
    write(root, "other.rst", f"Title: Other\nCreated: 01-Jan-2005\n\n{words('z', 200)}\n")
    result = ingest(store, root)
    ids = by_hint(store.snapshot())
    assert result.near_duplicates == ((ids["new.rst"], ids["old.rst"]),)
    (edge,) = store.snapshot().edges("derived_from")
    assert edge.weight >= 0.8
    # Re-ingesting after an edit does not duplicate the edge.
    write(root, "other.rst", f"Title: Other\nCreated: 01-Jan-2005\n\n{words('z', 199)}\n")
    assert ingest(store, root).near_duplicates == ()
    assert len(store.snapshot().edges("derived_from")) == 1


def test_near_duplicates_need_dates_to_order(store: Store, root: Path) -> None:
    write(root, "a.txt", f"{words('w', 200)} first\n")
    write(root, "b.txt", f"{words('w', 200)} second\n")
    assert ingest(store, root).near_duplicates == ()
