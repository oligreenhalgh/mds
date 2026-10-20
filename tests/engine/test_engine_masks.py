"""Valid-time mask, edge states and the edge-mask predicate (DESIGN §4, §5.2, §6, §7.2)."""

from collections.abc import Callable
from datetime import date

import pytest
from pyroaring import BitMap

from mds.engine import GraphMatrices, edge_view, edge_visible, traverse
from mds.model import Edge

EdgeFactory = Callable[..., Edge]
GraphFactory = Callable[..., GraphMatrices]
ALL = BitMap(range(64))
TODAY = date(2026, 10, 20)


@pytest.fixture
def amended(graph: GraphFactory, edge: EdgeFactory) -> GraphMatrices:
    """2 supersedes 1 from 2023-03-01 until 2025-01-01; 3 supersedes 2 from 2025-01-01."""
    return graph(
        [
            edge(1, "supersedes", 2, 1, valid_from=date(2023, 3, 1), valid_to=date(2025, 1, 1)),
            edge(2, "supersedes", 3, 2, valid_from=date(2025, 1, 1)),
        ]
    )


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (date(2023, 2, 28), set()),
        (date(2023, 3, 1), {2}),
        (date(2024, 12, 31), {2}),
        (date(2025, 1, 1), set()),
    ],
)
def test_valid_time_interval_is_half_open(
    amended: GraphMatrices, instant: date, expected: set[int]
) -> None:
    res = traverse(amended, [1], [("supersedes", "rev", 1)], ALL, valid_at=instant)
    assert {r.atom_id for r in res.results} == expected


def test_valid_time_applies_on_every_hop(amended: GraphMatrices) -> None:
    path = [("supersedes", "rev", "*")]
    early = traverse(amended, [1], path, ALL, valid_at=date(2024, 1, 1), operator="boolean")
    late = traverse(amended, [2], path, ALL, valid_at=date(2025, 6, 1), operator="boolean")
    assert {r.atom_id for r in early.results} == {1, 2}
    assert {r.atom_id for r in late.results} == {2, 3}
    path_step = next(r for r in early.results if r.atom_id == 2).path[0]
    assert (path_step.valid_from, path_step.valid_to) == (date(2023, 3, 1), date(2025, 1, 1))


def test_valid_at_defaults_to_today(amended: GraphMatrices) -> None:
    res = traverse(amended, [2], [("supersedes", "rev", 1)], ALL)
    assert {r.atom_id for r in res.results} == {3}


def test_pending_edges_need_opting_in(graph: GraphFactory, edge: EdgeFactory) -> None:
    g = graph([edge(1, "r1", 0, 1), edge(2, "r1", 0, 2, state="pending")])
    default = traverse(g, [0], [("r1", "fwd", 1)], ALL)
    opted = traverse(g, [0], [("r1", "fwd", 1)], ALL, include_pending=True)
    assert {r.atom_id for r in default.results} == {1}
    assert {r.atom_id for r in opted.results} == {1, 2}
    states = {r.atom_id: r.path[0].state for r in opted.results}
    assert states == {1: "confirmed", 2: "pending"}


def test_rejected_edges_are_never_traversed(graph: GraphFactory, edge: EdgeFactory) -> None:
    g = graph([edge(1, "r1", 0, 1, state="rejected")], atoms=[0, 1])
    res = traverse(g, [0], [("r1", "fwd", 1)], ALL, include_pending=True)
    assert res.results == ()
    assert g.dim("r1").records == ()


def test_confirmed_record_is_preferred_over_pending(graph: GraphFactory, edge: EdgeFactory) -> None:
    g = graph([edge(1, "r1", 0, 1, 0.4), edge(2, "r1", 0, 1, 0.9, state="pending")])
    res = traverse(g, [0], [("r1", "fwd", 1)], ALL, include_pending=True)
    step = res.results[0].path[0]
    assert (step.edge_id, step.state) == (1, "confirmed")
    # The score uses the same record the path reports.
    assert res.results[0].value == pytest.approx(0.4)


def test_parallel_confirmed_records_use_the_heaviest(
    graph: GraphFactory, edge: EdgeFactory
) -> None:
    g = graph([edge(1, "r1", 0, 1, 0.4), edge(2, "r1", 0, 1, 0.7)])
    res = traverse(g, [0], [("r1", "fwd", 1)], ALL)
    assert res.results[0].value == pytest.approx(0.7)
    assert res.results[0].path[0].edge_id == 2


def test_edge_visible_requires_endpoints_and_one_evidence_atom(edge: EdgeFactory) -> None:
    e = edge(1, "r1", 0, 1, evidence=(5, 6))
    assert edge_visible(e, BitMap([0, 1, 6]), TODAY, False, False)
    assert not edge_visible(e, BitMap([0, 1]), TODAY, False, False)
    assert not edge_visible(e, BitMap([0, 5, 6]), TODAY, False, False)
    assert not edge_visible(e, BitMap([1, 5, 6]), TODAY, False, False)


def test_edge_view_reuses_materialised_matrices_when_nothing_is_hidden(
    graph: GraphFactory, edge: EdgeFactory
) -> None:
    g = graph([edge(1, "r1", 0, 1), edge(2, "r1", 1, 2)])
    dm = g.dim("references")
    assert edge_view(dm, ALL, TODAY, False, g.n).fwd is dm.fwd
    hidden = edge_view(dm, BitMap([0, 1]), TODAY, False, g.n)
    assert hidden.fwd is not dm.fwd
    assert hidden.fwd.nvals == 1
    assert hidden.rev.nvals == 1
