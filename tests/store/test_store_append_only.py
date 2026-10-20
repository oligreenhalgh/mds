"""Edge and version records are append-only; changes close old records (DESIGN §4, §13.3)."""

import sqlite3

import pytest

from mds.model import Atom, AtomKind, EdgeState
from mds.store import Store, StoreError
from mds.store.sqlite import DB_NAME

DERIVATIVE = "# Policy\n\n## 4.2 Retention\n\nKeep records for six years.\n"


@pytest.fixture
def populated(store: Store, two_atoms: tuple[Atom, Atom]) -> Store:
    """CSN 2 adds a version, an edge and a breadcrumb; CSN 3 tombstones nothing yet."""
    digest = store.content.put(DERIVATIVE)
    with store.commit("ingest", "content") as tx:
        tx.add_version(1, digest, 10, 16, "md/norm-1")
        tx.add_edge("contains", 0, 1, 1.0, "extractor", "contains/1", "confirmed")
        tx.add_breadcrumb([1], "six years", "agent", 0)
    return store


def _raw(store: Store) -> sqlite3.Connection:
    return sqlite3.connect(store.data_dir / DB_NAME)


@pytest.mark.parametrize(
    "table",
    [
        "atoms",
        "atom_versions",
        "edges",
        "breadcrumbs",
        "breadcrumb_atoms",
        "commits",
        "changes",
    ],
)
def test_rows_cannot_be_deleted(populated: Store, table: str) -> None:
    conn = _raw(populated)
    with pytest.raises(
        sqlite3.IntegrityError, match=f"append-only: {table} rows cannot be deleted"
    ):
        conn.execute(f"DELETE FROM {table}")


@pytest.mark.parametrize(
    ("table", "assignment"),
    [
        ("atoms", "title = 'renamed'"),
        ("atoms", "acl_class = 5"),
        ("atom_versions", "digest = 'x'"),
        ("atom_versions", "byte_length = 1"),
        ("edges", "weight = 0.5"),
        ("edges", "state = 'rejected'"),
        ("edges", "valid_from = '2020-01-01'"),
        ("edges", "csn_to = 9, note = 'sneaky'"),
        ("breadcrumbs", "text = 'edited'"),
        ("commits", "summary = 'rewritten'"),
        ("changes", "summary = 'rewritten'"),
    ],
)
def test_rows_cannot_be_edited(populated: Store, table: str, assignment: str) -> None:
    conn = _raw(populated)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(f"UPDATE {table} SET {assignment}")


@pytest.mark.parametrize(
    ("table", "col"), [("atoms", "tombstone_csn"), ("atom_versions", "csn_to"), ("edges", "csn_to")]
)
def test_closing_is_allowed_once_from_null(populated: Store, table: str, col: str) -> None:
    conn = _raw(populated)
    conn.execute(f"UPDATE {table} SET {col} = 9 WHERE {col} IS NULL")
    with pytest.raises(sqlite3.IntegrityError, match="only be closed, once"):
        conn.execute(f"UPDATE {table} SET {col} = 10")
    with pytest.raises(sqlite3.IntegrityError, match="only be closed, once"):
        conn.execute(f"UPDATE {table} SET {col} = NULL")


def test_replace_edge_closes_old_record_and_keeps_edge_id(populated: Store) -> None:
    with populated.commit("research-agent", "assert") as tx:
        pending = tx.add_edge("r1", 1, 0, 0.7, "agent", "research-agent", "pending")
    with populated.commit("alice", "review") as tx:
        confirmed = tx.replace_edge(pending.edge_id, state=EdgeState.CONFIRMED, note="ok")
    assert confirmed.edge_id == pending.edge_id
    assert confirmed.csn_from == 4
    history = populated.snapshot().edge_history(pending.edge_id)
    assert [(e.state, e.csn_from, e.csn_to) for e in history] == [
        (EdgeState.PENDING, 3, 4),
        (EdgeState.CONFIRMED, 4, None),
    ]
    assert history[1].author_id == "research-agent"
    assert history[1].note == "ok"


def test_close_edge_without_replacement(populated: Store) -> None:
    with populated.commit("ingest", "retire") as tx:
        tx.close_edge(0)
    assert populated.snapshot().edge(0) is None
    assert populated.snapshot(as_of_csn=2).edge(0) is not None
    with pytest.raises(StoreError, match="no current record"), populated.commit("i", "x") as tx:
        tx.close_edge(0)


def test_edge_cannot_be_changed_twice_in_one_commit(populated: Store) -> None:
    with (
        pytest.raises(StoreError, match="already written in this commit"),
        populated.commit("a", "x") as tx,
    ):
        tx.replace_edge(0, weight=0.9)
        tx.replace_edge(0, weight=0.8)
    with (
        pytest.raises(StoreError, match="already written in this commit"),
        populated.commit("a", "x") as tx,
    ):
        edge = tx.add_edge("r1", 0, 1, 1.0, "extractor", "x/1", "confirmed")
        tx.close_edge(edge.edge_id)


@pytest.mark.parametrize("field", ["dim", "src", "dst", "edge_id", "csn_from"])
def test_edge_identity_cannot_change(populated: Store, field: str) -> None:
    with (
        pytest.raises(StoreError, match=f"cannot change {field}"),
        populated.commit("a", "x") as tx,
    ):
        tx.replace_edge(0, **{field: 1})


def test_new_version_closes_previous(populated: Store) -> None:
    digest = populated.content.put(DERIVATIVE + "Amended.\n")
    with populated.commit("ingest", "revision") as tx:
        v2 = tx.add_version(1, digest, 10, 16, "md/norm-1", origin={"line": 3})
    assert v2.version == 2
    versions = populated.snapshot().versions(1)
    assert [(v.version, v.csn_from, v.csn_to) for v in versions] == [(1, 2, 3), (2, 3, None)]
    assert versions[1].origin == {"line": 3}


def test_only_one_new_version_per_commit(populated: Store) -> None:
    digest = populated.content.put(DERIVATIVE)
    with (
        pytest.raises(StoreError, match="already has a new version"),
        populated.commit("a", "x") as tx,
    ):
        tx.add_version(1, digest, 0, 1, "n")
        tx.add_version(1, digest, 0, 2, "n")


def test_tombstone_rules(populated: Store) -> None:
    with populated.commit("ingest", "removed") as tx:
        tx.tombstone_atom(1)
    with pytest.raises(StoreError, match="already tombstoned"), populated.commit("a", "x") as tx:
        tx.tombstone_atom(1)
    with (
        pytest.raises(StoreError, match="created in this commit"),
        populated.commit("a", "x") as tx,
    ):
        atom = tx.add_atom(AtomKind.ENTITY, "Acme", "", 0)
        tx.tombstone_atom(atom.atom_id)


def test_atom_ids_never_reused(populated: Store) -> None:
    with populated.commit("ingest", "removed") as tx:
        tx.tombstone_atom(1)
    with populated.commit("ingest", "new") as tx:
        atom = tx.add_atom(AtomKind.SECTION, "4.3", "policy.md › 4.3", 0)
    assert atom.atom_id == 2


def test_version_needs_stored_content(populated: Store) -> None:
    missing = "0" * 64
    with (
        pytest.raises(StoreError, match="not in the content store"),
        populated.commit("a", "x") as tx,
    ):
        tx.add_version(1, missing, 0, 1, "n")
    digest = populated.content.put("short")
    with pytest.raises(StoreError, match="outside"), populated.commit("a", "x") as tx:
        tx.add_version(1, digest, 3, 10, "n")
