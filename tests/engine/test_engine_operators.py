"""Each operator against hand-computed results on small graphs (DESIGN §5.3)."""

import math
from collections.abc import Callable

import pytest
from pyroaring import BitMap

from mds.engine import EngineError, GraphMatrices, traverse
from mds.model import Edge

EdgeFactory = Callable[..., Edge]
GraphFactory = Callable[..., GraphMatrices]


@pytest.fixture
def diamond(graph: GraphFactory, edge: EdgeFactory) -> GraphMatrices:
    """0 → 1 → 3 (0.5, 0.5) and 0 → 2 → 3 (0.8, 0.25), plus 0 → 4 (0.9)."""
    return graph(
        [
            edge(1, "references", 0, 1, 0.5),
            edge(2, "references", 0, 2, 0.8),
            edge(3, "references", 1, 3, 0.5),
            edge(4, "references", 2, 3, 0.25),
            edge(5, "references", 0, 4, 0.9),
        ]
    )


TWO_HOPS = [("references", "fwd", 2)]


def _only(diamond: GraphMatrices, everyone: BitMap, operator: str) -> tuple[int, float]:
    res = traverse(diamond, [0], TWO_HOPS, everyone, operator=operator)
    assert [r.atom_id for r in res.results] == [3]
    return res.results[0].atom_id, res.results[0].value


def test_boolean_reaches_without_weighting(diamond: GraphMatrices, everyone: BitMap) -> None:
    assert _only(diamond, everyone, "boolean") == (3, 1.0)


def test_count_sums_path_products(diamond: GraphMatrices, everyone: BitMap) -> None:
    # 0.5 * 0.5 + 0.8 * 0.25
    _, value = _only(diamond, everyone, "count")
    assert value == pytest.approx(0.45)


def test_count_with_unit_weights_counts_paths(graph: GraphFactory, edge: EdgeFactory) -> None:
    g = graph(
        [
            edge(1, "r1", 0, 1),
            edge(2, "r1", 0, 2),
            edge(3, "r1", 1, 3),
            edge(4, "r1", 2, 3),
        ]
    )
    res = traverse(g, [0], TWO_HOPS, BitMap(range(4)), operator="count")
    assert res.results[0].value == pytest.approx(2.0)


def test_max_plus_takes_the_most_confident_path(diamond: GraphMatrices, everyone: BitMap) -> None:
    # max(0.5 * 0.5, 0.8 * 0.25), computed as max of summed log-weights.
    _, value = _only(diamond, everyone, "max_plus")
    assert value == pytest.approx(0.25)


def test_bottleneck_takes_the_strongest_weakest_link(
    diamond: GraphMatrices, everyone: BitMap
) -> None:
    # max(min(0.5, 0.5), min(0.8, 0.25))
    _, value = _only(diamond, everyone, "bottleneck")
    assert value == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("operator", "expected"),
    [("boolean", 1.0), ("count", 1.5), ("max_plus", 1.0), ("bottleneck", 1.0)],
)
def test_seed_scores_enter_the_frontier(
    graph: GraphFactory, edge: EdgeFactory, operator: str, expected: float
) -> None:
    g = graph([edge(1, "r1", 0, 3), edge(2, "r1", 5, 3)])
    res = traverse(g, {0: 1.0, 5: 0.5}, [("r1", "fwd", 1)], BitMap(range(6)), operator=operator)
    assert res.results[0].value == pytest.approx(expected)


def test_seed_scores_are_normalised_to_the_largest(graph: GraphFactory, edge: EdgeFactory) -> None:
    g = graph([edge(1, "r1", 0, 1, 0.5)])
    res = traverse(g, {0: 4.0}, [("r1", "fwd", 1)], BitMap(range(2)), operator="max_plus")
    assert res.results[0].value == pytest.approx(0.5)


def test_rev_walks_the_transpose(diamond: GraphMatrices, everyone: BitMap) -> None:
    res = traverse(diamond, [3], [("references", "rev", 1)], everyone, operator="count")
    assert {r.atom_id: r.value for r in res.results} == pytest.approx({1: 0.5, 2: 0.25})
    by_atom = {r.atom_id: r.path for r in res.results}
    assert [(p.from_atom, p.edge_id, p.direction) for p in by_atom[1]] == [(3, 3, "rev")]
    assert [(p.from_atom, p.edge_id) for p in by_atom[2]] == [(3, 4)]


@pytest.mark.parametrize("operator", ["boolean", "count", "max_plus", "bottleneck"])
def test_best_path_is_the_strongest_single_path(
    diamond: GraphMatrices, everyone: BitMap, operator: str
) -> None:
    res = traverse(diamond, [0], TWO_HOPS, everyone, operator=operator)
    path = res.results[0].path
    assert [p.edge_id for p in path] == [1, 3]
    assert [p.from_atom for p in path] == [0, 1]
    assert path[0].dim == "references"
    assert path[0].weight == 0.5
    assert path[0].state == "confirmed"


def test_bottleneck_best_path_uses_the_weakest_link(graph: GraphFactory, edge: EdgeFactory) -> None:
    # Products: 0.9 * 0.3 = 0.27 vs 0.5 * 0.5 = 0.25; weakest links: 0.3 vs 0.5.
    g = graph(
        [
            edge(1, "r1", 0, 1, 0.9),
            edge(2, "r1", 1, 3, 0.3),
            edge(3, "r1", 0, 2, 0.5),
            edge(4, "r1", 2, 3, 0.5),
        ]
    )
    mask = BitMap(range(4))
    product = traverse(g, [0], [("r1", "fwd", 2)], mask, operator="max_plus")
    weakest = traverse(g, [0], [("r1", "fwd", 2)], mask, operator="bottleneck")
    assert [p.edge_id for p in product.results[0].path] == [1, 2]
    assert [p.edge_id for p in weakest.results[0].path] == [3, 4]


def test_max_plus_value_matches_log_sum(graph: GraphFactory, edge: EdgeFactory) -> None:
    g = graph([edge(1, "r1", 0, 1, 0.9), edge(2, "r1", 1, 2, 0.7), edge(3, "r1", 2, 3, 0.6)])
    res = traverse(g, [0], [("r1", "fwd", 3)], BitMap(range(4)), operator="max_plus")
    assert math.log(res.results[0].value) == pytest.approx(
        math.log(0.9) + math.log(0.7) + math.log(0.6)
    )


def test_default_operator_is_count(diamond: GraphMatrices, everyone: BitMap) -> None:
    res = traverse(diamond, [0], TWO_HOPS, everyone)
    assert res.results[0].value == pytest.approx(0.45)


def test_unknown_operator_is_rejected(diamond: GraphMatrices, everyone: BitMap) -> None:
    with pytest.raises(EngineError, match="unknown operator"):
        traverse(diamond, [0], TWO_HOPS, everyone, operator="min_plus")
