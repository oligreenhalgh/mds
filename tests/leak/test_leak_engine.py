"""Hidden atoms and edges contribute nothing to traversal scores, counts or paths (DESIGN §7.3).

The strongest check is equivalence: traversing the full graph as a principal
must give exactly the same answer as traversing a graph from which every
hidden atom, and every edge the principal may not see, was never stored.
"""

from datetime import date
from typing import Any

import pytest
from pyroaring import BitMap

from mds.engine import GraphMatrices, Traversal, traverse
from mds.model import Edge

VISIBLE = BitMap([0, 1, 2, 3, 4, 5])
HIDDEN = BitMap([10, 11, 12])
T = date(2026, 10, 20)
OPERATORS = ["boolean", "count", "max_plus", "bottleneck"]


def _edge(
    edge_id: int,
    dim: str,
    src: int,
    dst: int,
    weight: float = 1.0,
    evidence: tuple[int, ...] | None = None,
    **kw: Any,
) -> Edge:
    return Edge(
        edge_id=edge_id,
        dim=dim,
        src=src,
        dst=dst,
        weight=weight,
        author_kind="extractor",
        author_id="test",
        state=kw.pop("state", "confirmed"),
        csn_from=1,
        evidence=evidence,
        **kw,
    )


EDGES = [
    # Visible route 0 → 1 → 3.
    _edge(1, "r1", 0, 1, 0.5),
    _edge(2, "r1", 1, 3, 0.5),
    # Stronger route through hidden atom 10.
    _edge(3, "r1", 0, 10, 1.0),
    _edge(4, "r1", 10, 3, 1.0),
    # A derived edge whose only evidence is hidden.
    _edge(5, "r1", 0, 2, 0.9, evidence=(11,)),
    # A derived edge with one visible evidence atom.
    _edge(6, "r1", 0, 4, 0.3, evidence=(11, 5)),
    # A heavier parallel record for 1 → 3, derived from a hidden atom.
    _edge(7, "r1", 1, 3, 0.95, evidence=(12,)),
    # Hidden fan-out from 0 that would crowd top_k and the degree cap.
    _edge(8, "r1", 0, 11, 1.0),
    _edge(9, "r1", 0, 12, 1.0),
    # Hidden middle of a chain to 5.
    _edge(10, "r1", 4, 10, 1.0),
    _edge(11, "r1", 10, 5, 1.0),
    # Supersession through a hidden replacement, and a visible one.
    _edge(12, "r4", 3, 1, valid_from=date(2020, 1, 1)),
    _edge(13, "r4", 10, 3, valid_from=date(2021, 1, 1)),
    _edge(14, "r4", 5, 3, 0.4, valid_from=date(2022, 1, 1), evidence=(12,)),
    # co_occurs stored both ways, with a hidden entity in between.
    _edge(15, "co_occurs", 1, 11, 0.8),
    _edge(16, "co_occurs", 11, 1, 0.8),
    _edge(17, "co_occurs", 11, 2, 0.8),
    _edge(18, "co_occurs", 2, 11, 0.8),
    _edge(19, "co_occurs", 1, 2, 0.2),
    _edge(20, "co_occurs", 2, 1, 0.2),
    # A pending edge through a hidden atom.
    _edge(21, "r1", 3, 12, 1.0, state="pending"),
    _edge(22, "r1", 12, 4, 1.0, state="pending"),
]


def _visible(edge: Edge) -> bool:
    ends = edge.src in VISIBLE and edge.dst in VISIBLE
    return ends and any(a in VISIBLE for a in edge.evidence or ())


FULL = GraphMatrices.from_records(1, VISIBLE | HIDDEN, EDGES)
STRIPPED = GraphMatrices.from_records(1, VISIBLE, [e for e in EDGES if _visible(e)])

PATHS: list[list[Any]] = [
    [("r1", "fwd", 1)],
    [("r1", "fwd", 2)],
    [("r1", "fwd", "*")],
    [("r1", "rev", "*")],
    [("co_occurs", "fwd", 1)],
    [("co_occurs", "fwd", 2)],
    [("r1", "fwd", 1), ("supersedes", "rev", "*")],
    [("supersedes", "rev", "*"), ("r1", "rev", 1)],
]


def _run(graph: GraphMatrices, mask: BitMap, seeds: Any, path: list[Any], **kw: Any) -> Traversal:
    return traverse(graph, seeds, path, mask, valid_at=T, **kw)


@pytest.mark.parametrize("operator", OPERATORS)
@pytest.mark.parametrize("path", PATHS, ids=str)
@pytest.mark.parametrize("seed", [0, 1, 3])
def test_masked_traversal_equals_traversal_without_hidden_records(
    operator: str, path: list[Any], seed: int
) -> None:
    for kw in ({}, {"include_pending": True}, {"k": 2}, {"degree_cap": 1}):
        masked = _run(FULL, VISIBLE, [seed], path, operator=operator, **kw)
        clean = _run(STRIPPED, VISIBLE | HIDDEN, [seed], path, operator=operator, **kw)
        assert masked == clean, kw


@pytest.mark.parametrize("operator", OPERATORS)
def test_no_hidden_atom_or_edge_appears_in_results_or_paths(operator: str) -> None:
    hidden_edges = {e.edge_id for e in EDGES if not _visible(e)}
    for path in PATHS:
        res = _run(FULL, VISIBLE, [0, 1, 3, 10, 11], path, operator=operator, include_pending=True)
        for r in res.results:
            assert r.atom_id in VISIBLE
            for step in r.path:
                assert step.from_atom in VISIBLE
                assert step.edge_id not in hidden_edges


@pytest.mark.parametrize(
    ("operator", "expected"),
    [("count", 0.25), ("max_plus", 0.25), ("bottleneck", 0.5), ("boolean", 1.0)],
)
def test_hidden_intermediate_and_parallel_records_add_nothing(
    operator: str, expected: float
) -> None:
    # With 10 and 12 visible, 0 → 3 would score 0.5*0.95 + 1.0 in count, 1.0 in max_plus.
    res = _run(FULL, VISIBLE, [0], [("r1", "fwd", 2)], operator=operator)
    by_atom = {r.atom_id: r for r in res.results}
    assert by_atom[3].value == pytest.approx(expected)
    assert [s.edge_id for s in by_atom[3].path] == [1, 2]


def test_evidence_mask_hides_edges_derived_only_from_hidden_atoms() -> None:
    res = _run(FULL, VISIBLE, [0], [("r1", "fwd", 1)], operator="boolean")
    assert {r.atom_id for r in res.results} == {1, 4}
    insider = _run(FULL, VISIBLE | BitMap([11]), [0], [("r1", "fwd", 1)], operator="boolean")
    assert {2, 4} <= {r.atom_id for r in insider.results}


def test_hidden_seeds_are_dropped_and_do_not_shift_seed_scores() -> None:
    path = [("r1", "fwd", 1)]
    assert _run(FULL, VISIBLE, [10], path).results == ()
    alone = _run(FULL, VISIBLE, {0: 0.5}, path, operator="max_plus")
    with_hidden = _run(FULL, VISIBLE, {0: 0.5, 10: 1.0}, path, operator="max_plus")
    assert alone == with_hidden


def test_hidden_atoms_take_no_top_k_slot_and_do_not_set_truncated() -> None:
    res = _run(FULL, VISIBLE, [0], [("r1", "fwd", 1)], k=2)
    assert {r.atom_id for r in res.results} == {1, 4}
    assert not res.truncated


def test_hidden_neighbours_do_not_count_towards_the_degree_cap() -> None:
    res = _run(FULL, VISIBLE, [0], [("r1", "fwd", 1)], degree_cap=2)
    assert {r.atom_id for r in res.results} == {1, 4}
    assert not res.truncated


def test_hidden_middle_of_a_chain_breaks_the_closure() -> None:
    res = _run(FULL, VISIBLE, [4], [("r1", "fwd", "*")], operator="boolean")
    assert {r.atom_id for r in res.results} == {4}


def test_hidden_replacement_does_not_appear_in_supersession() -> None:
    res = _run(FULL, VISIBLE, [3], [("supersedes", "rev", "*")], operator="boolean")
    # 10 supersedes 3 but is hidden; 5 → 3 is derived only from hidden 12.
    assert {r.atom_id for r in res.results} == {3}
