"""Matrices built from a store snapshot (DESIGN §5.1, §6)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pyroaring import BitMap

from mds.engine import GraphMatrices, traverse
from mds.model import AtomKind
from mds.store import Store

ALL = BitMap(range(64))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store(tmp_path / "data") as s:
        yield s


def _three_sections(store: Store) -> list[int]:
    with store.commit("test", "atoms") as tx:
        ids = [tx.add_atom(AtomKind.SECTION, f"s{i}", f"doc › {i}", 0).atom_id for i in range(3)]
    return ids


def test_one_matrix_pair_per_dimension(store: Store) -> None:
    a, b, c = _three_sections(store)
    with store.commit("test", "edges") as tx:
        tx.add_edge("references", a, b, 0.5, "extractor", "x", "confirmed")
        tx.add_edge("references", b, c, 1.0, "extractor", "x", "pending")
        tx.add_edge("contains", a, c, 1.0, "extractor", "x", "confirmed")
    g = GraphMatrices.from_snapshot(store.snapshot())
    assert set(g.dims) == {"r0", "r1", "r2", "r3", "r4"}
    assert g.csn == store.latest_csn
    refs = g.dim("references")
    # Only confirmed records are materialised; pending ones stay in the side table.
    assert refs.fwd.nvals == 1
    assert refs.fwd[a, b].new().value == 0.5
    assert refs.rev[b, a].new().value == 0.5
    assert len(refs.records) == 2
    assert g.dim("r0").fwd.nvals == 1
    assert g.dim("co_occurs").fwd.nvals == 0


def test_older_snapshot_does_not_see_later_edges(store: Store) -> None:
    a, b, c = _three_sections(store)
    with store.commit("test", "first") as tx:
        tx.add_edge("references", a, b, 1.0, "extractor", "x", "confirmed")
    first = store.latest_csn
    with store.commit("test", "second") as tx:
        tx.add_edge("references", a, c, 1.0, "extractor", "x", "confirmed")
    then = GraphMatrices.from_snapshot(store.snapshot(first))
    now = GraphMatrices.from_snapshot(store.snapshot())
    path = [("r1", "fwd", 1)]
    assert {r.atom_id for r in traverse(then, [a], path, ALL).results} == {b}
    assert {r.atom_id for r in traverse(now, [a], path, ALL).results} == {b, c}
    assert traverse(then, [a], path, ALL).csn == first


def test_closed_edges_are_not_traversed(store: Store) -> None:
    a, b, _ = _three_sections(store)
    with store.commit("test", "edge") as tx:
        e = tx.add_edge("references", a, b, 1.0, "extractor", "x", "confirmed")
    with store.commit("test", "close") as tx:
        tx.close_edge(e.edge_id)
    g = GraphMatrices.from_snapshot(store.snapshot())
    assert traverse(g, [a], [("r1", "fwd", 1)], ALL).results == ()


def test_tombstoned_atoms_are_not_reached(store: Store) -> None:
    a, b, _ = _three_sections(store)
    with store.commit("test", "edge") as tx:
        tx.add_edge("references", a, b, 1.0, "extractor", "x", "confirmed")
    with store.commit("test", "gone") as tx:
        tx.tombstone_atom(b)
    g = GraphMatrices.from_snapshot(store.snapshot())
    assert b not in g.present
    assert traverse(g, [a], [("r1", "fwd", 1)], ALL).results == ()
    assert traverse(g, [b], [("r1", "rev", 1)], ALL).results == ()


def test_empty_store_builds_empty_matrices(store: Store) -> None:
    g = GraphMatrices.from_snapshot(store.snapshot())
    assert g.n == 1
    assert traverse(g, [0], [("r1", "fwd", "*")], ALL).results == ()
