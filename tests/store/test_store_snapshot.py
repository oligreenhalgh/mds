"""Snapshot reads at a CSN see exactly what MDS believed at that commit (DESIGN §6)."""

from datetime import date

import pytest

from mds.model import Atom, AtomKind, EdgeState, ReadSetEntry
from mds.store import Store, StoreError

DERIVATIVE = "# Policy\n\n## 4.2 Retention\n\nKeep records for six years.\n"


@pytest.fixture
def history(store: Store, two_atoms: tuple[Atom, Atom]) -> Store:
    """A short history over CSNs 1-5.

    1: document 0 and section 1
    2: version 1 of section 1; pending supersedes edge 0 (atom 2 created); breadcrumb 0
    3: edge 0 confirmed; version 2 of section 1
    4: section 1 tombstoned; references edge 1
    5: edge 1 closed
    """
    d1 = store.content.put(DERIVATIVE)
    d2 = store.content.put(DERIVATIVE.replace("six", "seven"))
    with store.commit("ingest", "2") as tx:
        tx.add_version(1, d1, 10, 16, "md/norm-1")
        amendment = tx.add_atom(AtomKind.SECTION, "4.2 (2023)", "policy-2023.md › 4.2", 1)
        tx.add_edge(
            "supersedes", amendment.atom_id, 1, 0.9, "agent", "a1", "pending",
            valid_from=date(2023, 3, 1),
        )  # fmt: skip
        tx.add_breadcrumb([1, 2], "4.2 amended in 2023", "a1", 1, [ReadSetEntry(1, 1)])
    with store.commit("alice", "3") as tx:
        tx.replace_edge(0, state=EdgeState.CONFIRMED)
        tx.add_version(1, d2, 10, 16, "md/norm-1")
    with store.commit("ingest", "4") as tx:
        tx.tombstone_atom(1)
        tx.add_edge("r1", 0, 2, 1.0, "extractor", "refs/1", "confirmed")
    with store.commit("ingest", "5") as tx:
        tx.close_edge(1)
    return store


def test_default_snapshot_is_latest(history: Store) -> None:
    assert history.snapshot().csn == history.latest_csn == 5


@pytest.mark.parametrize("csn", [-1, 6])
def test_snapshot_outside_history_rejected(history: Store, csn: int) -> None:
    with pytest.raises(StoreError, match=r"as_of_csn must be in \[0, 5\]"):
        history.snapshot(as_of_csn=csn)


def test_snapshot_at_zero_is_empty(history: Store) -> None:
    snap = history.snapshot(as_of_csn=0)
    assert snap.atoms() == [] and snap.edges() == [] and snap.breadcrumbs() == []


@pytest.mark.parametrize(
    ("csn", "visible"), [(1, [0, 1]), (2, [0, 1, 2]), (3, [0, 1, 2]), (4, [0, 2]), (5, [0, 2])]
)
def test_atoms_as_of(history: Store, csn: int, visible: list[int]) -> None:
    snap = history.snapshot(as_of_csn=csn)
    assert [a.atom_id for a in snap.atoms()] == visible
    assert (snap.atom(1) is not None) == (1 in visible)


def test_tombstone_hidden_before_it_happened(history: Store) -> None:
    assert history.snapshot(as_of_csn=3).atoms()[1].tombstone_csn is None
    tombstoned = history.snapshot().atoms(include_tombstoned=True)[1]
    assert tombstoned.atom_id == 1 and tombstoned.tombstone_csn == 4


@pytest.mark.parametrize(("csn", "version"), [(1, None), (2, 1), (3, 2), (5, 2)])
def test_current_version_as_of(history: Store, csn: int, version: int | None) -> None:
    current = history.snapshot(as_of_csn=csn).version(1)
    assert (current.version if current else None) == version


def test_closing_csn_hidden_before_it_happened(history: Store) -> None:
    v1 = history.snapshot(as_of_csn=2).versions(1)
    assert [(v.version, v.csn_to) for v in v1] == [(1, None)]
    v_all = history.snapshot().versions(1)
    assert [(v.version, v.csn_to) for v in v_all] == [(1, 3), (2, None)]


@pytest.mark.parametrize(
    ("csn", "state"), [(1, None), (2, EdgeState.PENDING), (3, EdgeState.CONFIRMED)]
)
def test_edge_state_as_of(history: Store, csn: int, state: EdgeState | None) -> None:
    edge = history.snapshot(as_of_csn=csn).edge(0)
    assert (edge.state if edge else None) == state
    if edge:
        assert edge.csn_to is None
        assert edge.valid_from == date(2023, 3, 1)
        assert edge.dim == "r4"


@pytest.mark.parametrize(("csn", "ids"), [(3, [0]), (4, [0, 1]), (5, [0])])
def test_edges_as_of(history: Store, csn: int, ids: list[int]) -> None:
    assert [e.edge_id for e in history.snapshot(as_of_csn=csn).edges()] == ids


def test_edges_filtered_by_dimension_id_or_name(history: Store) -> None:
    snap = history.snapshot(as_of_csn=4)
    assert [e.edge_id for e in snap.edges("supersedes")] == [0]
    assert [e.edge_id for e in snap.edges("r1")] == [1]
    assert snap.edges("contains") == []


def test_edge_history_as_of(history: Store) -> None:
    assert [e.state for e in history.snapshot(as_of_csn=2).edge_history(0)] == [EdgeState.PENDING]
    assert len(history.snapshot().edge_history(0)) == 2


def test_breadcrumbs_as_of(history: Store) -> None:
    assert history.snapshot(as_of_csn=1).breadcrumbs() == []
    crumbs = history.snapshot(as_of_csn=2).breadcrumbs()
    assert len(crumbs) == 1
    crumb = crumbs[0]
    assert crumb.atom_ids == (1, 2)
    assert crumb.read_set == (ReadSetEntry(1, 1),)
    assert crumb.acl_class == 1 and crumb.csn == 2


def test_breadcrumbs_by_pinned_atom(history: Store) -> None:
    snap = history.snapshot()
    assert [b.breadcrumb_id for b in snap.breadcrumbs(atom_id=2)] == [0]
    assert snap.breadcrumbs(atom_id=0) == []


def test_breadcrumbs_reading_an_atom(history: Store) -> None:
    snap = history.snapshot()
    assert [b.breadcrumb_id for b in snap.breadcrumbs_reading(1)] == [0]
    assert snap.breadcrumbs_reading(2) == []


def test_snapshot_ignores_later_and_open_commits(history: Store) -> None:
    snap = history.snapshot()
    with history.commit("ingest", "6") as tx:
        tx.add_atom(AtomKind.ENTITY, "Acme", "", 0)
        tx.replace_edge(0, weight=0.5)
        # Uncommitted writes in the open transaction are invisible to the earlier snapshot.
        assert [a.atom_id for a in snap.atoms()] == [0, 2]
        assert snap.edge(0) is not None and snap.edge(0).weight == 0.9  # type: ignore[union-attr]
    assert [a.atom_id for a in snap.atoms()] == [0, 2]
    assert [a.atom_id for a in history.snapshot().atoms()] == [0, 2, 3]
    assert history.snapshot().edge(0).weight == 0.5  # type: ignore[union-attr]


def test_content_round_trip_through_version(history: Store) -> None:
    v = history.snapshot(as_of_csn=2).version(1)
    assert v is not None
    assert history.content.slice(v.digest, v.byte_offset, v.byte_length) == "## 4.2 Retention"
