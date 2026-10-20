from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

import pytest
from pyroaring import BitMap

from mds.engine import GraphMatrices
from mds.model import Edge

EdgeFactory = Callable[..., Edge]
GraphFactory = Callable[..., GraphMatrices]


def make_edge(
    edge_id: int,
    dim: str,
    src: int,
    dst: int,
    weight: float = 1.0,
    state: str = "confirmed",
    valid_from: date | None = None,
    valid_to: date | None = None,
    evidence: tuple[int, ...] | None = None,
) -> Edge:
    """An edge record with test defaults for the fields traversal ignores."""
    return Edge(
        edge_id=edge_id,
        dim=dim,
        src=src,
        dst=dst,
        weight=weight,
        author_kind="extractor",
        author_id="test",
        state=state,
        csn_from=1,
        valid_from=valid_from,
        valid_to=valid_to,
        evidence=evidence,
    )


@pytest.fixture
def edge() -> EdgeFactory:
    return make_edge


@pytest.fixture
def graph() -> GraphFactory:
    def build(
        edges: Iterable[Edge], atoms: Iterable[int] | None = None, **kw: Any
    ) -> GraphMatrices:
        edges = list(edges)
        if atoms is None:
            atoms = {a for e in edges for a in (e.src, e.dst)}
        return GraphMatrices.from_records(1, atoms, edges, **kw)

    return build


@pytest.fixture
def everyone() -> BitMap:
    """A vertex mask that sees every atom used in these tests."""
    return BitMap(range(64))
