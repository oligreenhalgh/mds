"""Vertex, evidence and valid-time masks (DESIGN §5.2, §6, §7).

Masks are applied to edge records before any matrix the traversal reads is
built, so a hidden atom or edge never contributes a weight, a count or a path.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from graphblas import Vector, dtypes
from pyroaring import AbstractBitMap, FrozenBitMap

from mds.engine.matrices import DimMatrices, GraphMatrices, weight_matrix
from mds.model import Edge, EdgeState


def visible_atoms(graph: GraphMatrices, mask: AbstractBitMap) -> FrozenBitMap:
    """The principal's vertex mask `p_u`, limited to atoms present at the graph's CSN."""
    return FrozenBitMap(graph.present & mask)


def vertex_mask_vector(atoms: Iterable[int], n: int) -> Any:
    """`p_u` as a boolean GraphBLAS vector of size `n`, for use as an output mask."""
    ids = [a for a in atoms if a < n]
    return Vector.from_coo(ids, [True] * len(ids), size=n, dtype=dtypes.BOOL)


def valid_at(edge: Edge, instant: date) -> bool:
    """True if `instant` lies in the edge's valid-time interval `[valid_from, valid_to)`."""
    if edge.valid_from is not None and instant < edge.valid_from:
        return False
    return edge.valid_to is None or instant < edge.valid_to


def evidence_visible(edge: Edge, atoms: AbstractBitMap) -> bool:
    """DESIGN §7.2: both endpoints visible and at least one evidence atom visible."""
    if edge.src not in atoms or edge.dst not in atoms:
        return False
    return any(a in atoms for a in edge.evidence or ())


def edge_visible(
    edge: Edge,
    atoms: AbstractBitMap,
    instant: date,
    include_pending: bool,
    time_bound: bool,
) -> bool:
    """The edge mask `E_u ∧ V(t)` for one record, plus the state filter (DESIGN §4)."""
    if edge.state is EdgeState.REJECTED:
        return False
    if edge.state is EdgeState.PENDING and not include_pending:
        return False
    if time_bound and not valid_at(edge, instant):
        return False
    return evidence_visible(edge, atoms)


@dataclass(frozen=True, slots=True)
class EdgeView:
    """One dimension as a principal sees it at one valid time.

    `chosen` maps each `(src, dst)` pair to one visible record: confirmed
    before pending, then the highest weight, then the lowest `edge_id`. `fwd`
    and `rev` hold the chosen records' weights, so a pair's score and its
    reported path use the same record.
    """

    dim: str
    fwd: Any
    rev: Any
    chosen: dict[tuple[int, int], Edge]

    def matrix(self, direction: str) -> Any:
        """`A_r` for `fwd`, `A_rᵀ` for `rev`."""
        return self.fwd if direction == "fwd" else self.rev


def edge_view(
    dm: DimMatrices,
    atoms: AbstractBitMap,
    instant: date,
    include_pending: bool,
    n: int,
) -> EdgeView:
    """Apply the edge mask to one dimension's records and build the matrices to walk."""
    kept = [
        e for e in dm.records if edge_visible(e, atoms, instant, include_pending, dm.time_bound)
    ]
    chosen: dict[tuple[int, int], Edge] = {}
    for edge in kept:
        pair = (edge.src, edge.dst)
        current = chosen.get(pair)
        if current is None or _preference(edge) < _preference(current):
            chosen[pair] = edge
    confirmed_total = sum(1 for e in dm.records if e.state is EdgeState.CONFIRMED)
    if len(kept) == confirmed_total and all(e.state is EdgeState.CONFIRMED for e in kept):
        # Nothing was masked out, so the materialised matrices are exact.
        return EdgeView(dim=dm.dim, fwd=dm.fwd, rev=dm.rev, chosen=chosen)
    # Weights come from the chosen records so scores and reported paths agree.
    fwd = weight_matrix(chosen.values(), n)
    return EdgeView(dim=dm.dim, fwd=fwd, rev=fwd.T.new(), chosen=chosen)


def _preference(edge: Edge) -> tuple[int, float, int]:
    return (0 if edge.state is EdgeState.CONFIRMED else 1, -edge.weight, edge.edge_id)
