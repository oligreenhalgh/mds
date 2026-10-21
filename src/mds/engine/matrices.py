"""Per-dimension sparse matrices built from a store snapshot (DESIGN §5.1)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from graphblas import Matrix, binary, dtypes
from pyroaring import BitMap, FrozenBitMap

from mds.model import DimensionRegistry, Edge, EdgeState, default_registry
from mds.store import Snapshot


@dataclass(frozen=True, slots=True)
class DimMatrices:
    """One dimension at one CSN.

    `fwd` is `A_r`: entry `(i, j)` is the highest weight among confirmed edge
    records `i → j`. `rev` is its materialised transpose. `records` is the side
    table: every confirmed or pending record in the dimension, which masks and
    paths read for valid time, state, author and evidence. Rejected records are
    dropped here because nothing ever traverses them.
    """

    dim: str
    time_bound: bool
    fwd: Any
    rev: Any
    records: tuple[Edge, ...]


@dataclass(frozen=True, slots=True)
class GraphMatrices:
    """Every dimension's matrices at one CSN, over the atom id space `[0, n)`.

    `present` holds the atoms that exist (and are not tombstoned) at `csn`;
    traversal never reaches anything outside it.
    """

    csn: int
    n: int
    present: FrozenBitMap
    dims: Mapping[str, DimMatrices]
    registry: DimensionRegistry

    @classmethod
    def from_snapshot(
        cls, snapshot: Snapshot, registry: DimensionRegistry | None = None
    ) -> "GraphMatrices":
        """Build matrices for every registered dimension from a store snapshot."""
        return cls.from_records(
            snapshot.csn,
            (a.atom_id for a in snapshot.atoms()),
            snapshot.edges(),
            registry,
        )

    @classmethod
    def from_records(
        cls,
        csn: int,
        atom_ids: Iterable[int],
        edges: Iterable[Edge],
        registry: DimensionRegistry | None = None,
    ) -> "GraphMatrices":
        """Build matrices from atom ids and edge records already read at `csn`."""
        registry = registry or default_registry()
        present = FrozenBitMap(atom_ids)
        by_dim: dict[str, list[Edge]] = {d.id: [] for d in registry}
        for edge in edges:
            if edge.state is not EdgeState.REJECTED:
                by_dim[registry.get(edge.dim).id].append(edge)
        n = _id_space(present, by_dim.values())
        dims = {d.id: _dim_matrices(d.id, d.time_bound, by_dim[d.id], n) for d in registry}
        return cls(csn=csn, n=n, present=present, dims=dims, registry=registry)

    def dim(self, key: str) -> DimMatrices:
        """Matrices for a dimension given by id or name."""
        return self.dims[self.registry.get(key).id]


def weight_matrix(records: Iterable[Edge], n: int) -> Any:
    """An `n × n` FP64 matrix of record weights; parallel records keep the highest."""
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for edge in records:
        rows.append(edge.src)
        cols.append(edge.dst)
        vals.append(float(edge.weight))
    return Matrix.from_coo(rows, cols, vals, nrows=n, ncols=n, dtype=dtypes.FP64, dup_op=binary.max)


def _dim_matrices(dim: str, time_bound: bool, records: list[Edge], n: int) -> DimMatrices:
    confirmed = [e for e in records if e.state is EdgeState.CONFIRMED]
    fwd = weight_matrix(confirmed, n)
    return DimMatrices(
        dim=dim, time_bound=time_bound, fwd=fwd, rev=fwd.T.new(), records=tuple(records)
    )


def _id_space(present: FrozenBitMap, edge_lists: Iterable[list[Edge]]) -> int:
    ids = BitMap(present)
    for edges in edge_lists:
        for edge in edges:
            ids.add(edge.src)
            ids.add(edge.dst)
    return ids.max() + 1 if ids else 1
