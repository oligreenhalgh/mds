"""top_k, the per-hop degree cap and ranking (DESIGN §5.2, §5.4)."""

from collections.abc import Callable

import pytest
from pyroaring import BitMap

from mds.engine import GraphMatrices, cap_degree, rank, traverse
from mds.engine.matrices import weight_matrix
from mds.engine.rank import STRENGTH_WEIGHT, VALUE_WEIGHT
from mds.model import Edge, Operator

EdgeFactory = Callable[..., Edge]
GraphFactory = Callable[..., GraphMatrices]
ALL = BitMap(range(64))


@pytest.fixture
def star(graph: GraphFactory, edge: EdgeFactory) -> GraphMatrices:
    """0 references 1..5 with weights 0.1, 0.5, 0.3, 0.9, 0.5."""
    weights = {1: 0.1, 2: 0.5, 3: 0.3, 4: 0.9, 5: 0.5}
    return graph([edge(i, "r1", 0, dst, w) for i, (dst, w) in enumerate(weights.items(), 1)])


def test_top_k_keeps_the_highest_scores(star: GraphMatrices) -> None:
    res = traverse(star, [0], [("r1", "fwd", 1)], ALL, k=3)
    # Ties on 0.5 break on the lower atom id.
    assert [r.atom_id for r in res.results] == [4, 2, 5]
    assert res.truncated


def test_untruncated_result_says_so(star: GraphMatrices) -> None:
    res = traverse(star, [0], [("r1", "fwd", 1)], ALL, k=5)
    assert [r.atom_id for r in res.results] == [4, 2, 5, 3, 1]
    assert not res.truncated


def test_top_k_applies_at_every_hop(graph: GraphFactory, edge: EdgeFactory) -> None:
    # Only the best first-hop atom survives k=1, so only its child is reached.
    g = graph(
        [
            edge(1, "r1", 0, 1, 0.9),
            edge(2, "r1", 0, 2, 0.2),
            edge(3, "r1", 1, 3, 0.1),
            edge(4, "r1", 2, 4, 1.0),
        ]
    )
    res = traverse(g, [0], [("r1", "fwd", 2)], ALL, k=1, operator="max_plus")
    assert [r.atom_id for r in res.results] == [3]
    assert res.truncated


def test_degree_cap_keeps_the_heaviest_edges(star: GraphMatrices) -> None:
    res = traverse(star, [0], [("r1", "fwd", 1)], ALL, degree_cap=2)
    assert {r.atom_id for r in res.results} == {4, 2}
    assert res.truncated


def test_degree_cap_only_flags_rows_the_frontier_used(
    graph: GraphFactory, edge: EdgeFactory
) -> None:
    g = graph([edge(1, "r1", 0, 1), edge(2, "r1", 0, 2), edge(3, "r1", 5, 6)])
    res = traverse(g, [5], [("r1", "fwd", 1)], ALL, degree_cap=1)
    assert [r.atom_id for r in res.results] == [6]
    assert not res.truncated


def test_cap_degree_on_a_matrix(edge: EdgeFactory) -> None:
    row0 = [(1, 0.2), (2, 0.9), (3, 0.9), (4, 0.1)]
    edges = [edge(i, "r1", 0, d, w) for i, (d, w) in enumerate(row0, 1)]
    edges.append(edge(9, "r1", 1, 2, 0.5))
    capped, rows = cap_degree(weight_matrix(edges, 5), 2)
    assert set(rows) == {0}
    srcs, dsts, _ = capped.to_coo()
    assert sorted(zip(srcs.tolist(), dsts.tolist(), strict=True)) == [(0, 2), (0, 3), (1, 2)]


def test_rank_blends_value_and_strength() -> None:
    ranked = rank(Operator.COUNT, [(1, 2.0, 0.5), (2, 4.0, 0.25), (3, 4.0, 0.25)])
    assert [r.atom_id for r in ranked] == [2, 3, 1]
    assert ranked[0].score == pytest.approx(VALUE_WEIGHT * 1.0 + STRENGTH_WEIGHT * 0.25)
    assert ranked[2].score == pytest.approx(VALUE_WEIGHT * 0.5 + STRENGTH_WEIGHT * 0.5)


def test_boolean_rank_orders_by_path_strength() -> None:
    ranked = rank(Operator.BOOLEAN, [(1, 1.0, 0.2), (2, 1.0, 0.9)])
    assert [r.atom_id for r in ranked] == [2, 1]


@pytest.mark.parametrize("operator", ["boolean", "count", "max_plus", "bottleneck"])
def test_scores_are_between_zero_and_one(star: GraphMatrices, operator: str) -> None:
    res = traverse(star, [0], [("r1", "fwd", 1)], ALL, operator=operator)
    assert all(0.0 < r.score <= 1.0 for r in res.results)
    scores = [r.score for r in res.results]
    assert scores == sorted(scores, reverse=True)


def test_rank_of_nothing_is_empty() -> None:
    assert rank(Operator.COUNT, []) == []
