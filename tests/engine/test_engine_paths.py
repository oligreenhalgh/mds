"""Typed multi-step paths, hop counts and closure (DESIGN §5.2)."""

from collections.abc import Callable
from typing import Any

import pytest
from pyroaring import BitMap

from mds.engine import (
    MAX_PATH_STEPS,
    EngineError,
    GraphMatrices,
    LimitExceededError,
    Step,
    parse_path,
    traverse,
)
from mds.model import Edge, UnknownDimensionError, default_registry

EdgeFactory = Callable[..., Edge]
GraphFactory = Callable[..., GraphMatrices]
ALL = BitMap(range(64))


def _ids(graph: GraphMatrices, seeds: list[int], path: list[Any], **kw: Any) -> set[int]:
    return {r.atom_id for r in traverse(graph, seeds, path, ALL, **kw).results}


@pytest.fixture
def chain(graph: GraphFactory, edge: EdgeFactory) -> GraphMatrices:
    """references: 0 → 1 → 2 → 3."""
    return graph([edge(1, "r1", 0, 1), edge(2, "r1", 1, 2), edge(3, "r1", 2, 3)])


def test_integer_hops_follow_exactly_that_many_edges(chain: GraphMatrices) -> None:
    assert _ids(chain, [0], [("r1", "fwd", 1)]) == {1}
    assert _ids(chain, [0], [("r1", "fwd", 2)]) == {2}
    assert _ids(chain, [0], [("r1", "fwd", 3)]) == {3}
    assert _ids(chain, [0], [("r1", "fwd", 4)]) == set()


def test_star_is_the_closure_including_the_start(chain: GraphMatrices) -> None:
    res = traverse(chain, [1], [("r1", "fwd", "*")], ALL, operator="boolean")
    assert {r.atom_id for r in res.results} == {1, 2, 3}
    assert not res.truncated
    paths = {r.atom_id: [p.edge_id for p in r.path] for r in res.results}
    assert paths == {1: [], 2: [2], 3: [2, 3]}


def test_star_in_reverse(chain: GraphMatrices) -> None:
    assert _ids(chain, [2], [("r1", "rev", "*")]) == {0, 1, 2}


@pytest.mark.parametrize("operator", ["boolean", "max_plus", "bottleneck"])
def test_star_settles_on_a_cycle(graph: GraphFactory, edge: EdgeFactory, operator: str) -> None:
    g = graph([edge(1, "r1", 0, 1, 0.9), edge(2, "r1", 1, 2, 0.8), edge(3, "r1", 2, 0, 0.7)])
    res = traverse(g, [0], [("r1", "fwd", "*")], ALL, operator=operator)
    assert {r.atom_id for r in res.results} == {0, 1, 2}
    assert not res.truncated
    # The seed's best path stays the empty one; going round the loop is weaker.
    assert next(r for r in res.results if r.atom_id == 0).path == ()


def test_count_star_on_a_cycle_stops_at_the_hop_limit(
    graph: GraphFactory, edge: EdgeFactory
) -> None:
    g = graph([edge(1, "r1", 0, 1), edge(2, "r1", 1, 0)])
    res = traverse(g, [0], [("r1", "fwd", "*")], ALL, operator="count", hop_limit=4)
    # Walks of length 0..4 from 0: lengths 0, 2, 4 end at 0; lengths 1, 3 end at 1.
    assert {r.atom_id: r.value for r in res.results} == {0: 3.0, 1: 2.0}
    assert res.truncated


def test_count_star_on_a_dag_sums_paths_of_every_length(
    graph: GraphFactory, edge: EdgeFactory
) -> None:
    # 0 → 1 → 2 and 0 → 2: two paths reach 2.
    g = graph([edge(1, "r1", 0, 1), edge(2, "r1", 1, 2), edge(3, "r1", 0, 2)])
    res = traverse(g, [0], [("r1", "fwd", "*")], ALL, operator="count")
    assert {r.atom_id: r.value for r in res.results} == {0: 1.0, 1: 1.0, 2: 2.0}
    assert not res.truncated


def test_typed_path_combines_dimensions(graph: GraphFactory, edge: EdgeFactory) -> None:
    # Section 10 is in document 1; document 1 is superseded by 2, and 2 by 3.
    g = graph(
        [
            edge(1, "contains", 1, 10),
            edge(2, "supersedes", 2, 1),
            edge(3, "supersedes", 3, 2),
            edge(4, "references", 10, 1),
        ]
    )
    path = [("contains", "rev", 1), ("supersedes", "rev", "*")]
    res = traverse(g, [10], path, ALL, operator="boolean")
    assert {r.atom_id for r in res.results} == {1, 2, 3}
    best = {r.atom_id: [(p.dim, p.direction, p.edge_id) for p in r.path] for r in res.results}
    assert best[3] == [
        ("contains", "rev", 1),
        ("supersedes", "rev", 2),
        ("supersedes", "rev", 3),
    ]


def test_dimensions_do_not_leak_into_each_other(graph: GraphFactory, edge: EdgeFactory) -> None:
    g = graph([edge(1, "contains", 0, 1), edge(2, "references", 0, 2)])
    assert _ids(g, [0], [("contains", "fwd", 1)]) == {1}
    assert _ids(g, [0], [("r1", "fwd", 1)]) == {2}


def test_steps_accept_ids_names_tuples_mappings_and_steps() -> None:
    reg = default_registry()
    steps = parse_path(
        [
            ("references", "fwd", 1),
            {"dim": "r4", "direction": "rev", "hops": "*"},
            Step(dim="r0", direction="fwd", hops=2),
        ],
        reg,
        16,
    )
    assert steps == (
        Step("r1", "fwd", 1),
        Step("r4", "rev", "*"),
        Step("r0", "fwd", 2),
    )


@pytest.mark.parametrize(
    "path",
    [
        [],
        "r1:fwd:1",
        [("r1", "up", 1)],
        [("r1", "fwd", 0)],
        [("r1", "fwd", "2")],
        [("r1", "fwd", True)],
        [("r1", "fwd")],
        [{"dim": "r1", "direction": "fwd"}],
    ],
)
def test_malformed_paths_are_rejected(chain: GraphMatrices, path: Any) -> None:
    with pytest.raises(EngineError):
        traverse(chain, [0], path, ALL)


def test_unknown_dimension_is_reported(chain: GraphMatrices) -> None:
    with pytest.raises(UnknownDimensionError):
        traverse(chain, [0], [("cites", "fwd", 1)], ALL)


def test_path_length_and_hops_have_limits(chain: GraphMatrices) -> None:
    too_long = [("r1", "fwd", 1)] * (MAX_PATH_STEPS + 1)
    with pytest.raises(LimitExceededError):
        traverse(chain, [0], too_long, ALL)
    with pytest.raises(LimitExceededError):
        traverse(chain, [0], [("r1", "fwd", 17)], ALL)
    assert traverse(chain, [0], [("r1", "fwd", 1)] * MAX_PATH_STEPS, ALL).results == ()


@pytest.mark.parametrize("k", [0, 201, -1, True])
def test_k_is_bounded(chain: GraphMatrices, k: Any) -> None:
    with pytest.raises(EngineError):
        traverse(chain, [0], [("r1", "fwd", 1)], ALL, k=k)


@pytest.mark.parametrize("seeds", [{0: 0.0}, {0: -1.0}, {0: float("inf")}, {0: float("nan")}])
def test_seed_scores_must_be_positive(chain: GraphMatrices, seeds: dict[int, float]) -> None:
    with pytest.raises(EngineError):
        traverse(chain, seeds, [("r1", "fwd", 1)], ALL)


def test_unknown_seeds_are_dropped(chain: GraphMatrices) -> None:
    assert _ids(chain, [0, 99, 2**40, -3], [("r1", "fwd", 1)]) == {1}
    assert _ids(chain, [], [("r1", "fwd", "*")]) == set()
