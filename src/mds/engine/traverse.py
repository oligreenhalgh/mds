"""Masked, typed, multi-step traversal (DESIGN §5.2).

Each hop computes

    v ← top_k( v ⊕.⊗ A ⟨E_u ∧ V(t)⟩ ) ⟨p_u⟩

The edge mask is applied when the step's matrix is built (`masks.edge_view`),
and the vertex mask is the output mask of the multiply, so `top_k`, the degree
cap and ranking only ever see what the principal may see (DESIGN §7.3).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import inf
from typing import Any

from graphblas import Matrix, Vector, agg, dtypes
from pyroaring import AbstractBitMap, BitMap

from mds.engine.errors import EngineError, LimitExceededError
from mds.engine.masks import EdgeView, edge_view, vertex_mask_vector, visible_atoms
from mds.engine.matrices import GraphMatrices
from mds.engine.operators import OperatorSpec, operator_spec
from mds.engine.path import STAR, Step, StepLike, parse_path
from mds.engine.rank import rank
from mds.model import ATOM_ID_LIMIT, EdgeState, Operator

DEFAULT_K = 20
MAX_K = 200
DEFAULT_HOP_LIMIT = 16
DEFAULT_DEGREE_CAP = 1000


@dataclass(frozen=True, slots=True)
class PathStep:
    """One edge on a result's best path, in the order it was followed."""

    from_atom: int
    dim: str
    direction: str
    edge_id: int
    weight: float
    state: EdgeState
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True, slots=True)
class TraversalResult:
    """One reached atom: its ranked score, raw operator value and best path."""

    atom_id: int
    score: float
    value: float
    path: tuple[PathStep, ...]


@dataclass(frozen=True, slots=True)
class Traversal:
    """A ranked traversal result.

    `truncated` is true when `top_k`, the degree cap or the hop limit dropped
    anything the principal could otherwise have reached.
    """

    csn: int
    results: tuple[TraversalResult, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class _Best:
    strength: float
    path: tuple[PathStep, ...]


@dataclass(frozen=True, slots=True)
class _Frontier:
    values: Any
    best: Mapping[int, _Best]


@dataclass(frozen=True, slots=True)
class _Hop:
    """Everything one step needs, built once per step."""

    step: Step
    dim_name: str
    view: EdgeView
    weights: Any
    operand: Any
    capped_rows: BitMap


def traverse(
    graph: GraphMatrices,
    seeds: Mapping[int, float] | Iterable[int],
    path: Sequence[StepLike],
    mask: AbstractBitMap,
    operator: Operator | str = Operator.COUNT,
    k: int = DEFAULT_K,
    valid_at: date | None = None,
    include_pending: bool = False,
    degree_cap: int = DEFAULT_DEGREE_CAP,
    hop_limit: int = DEFAULT_HOP_LIMIT,
) -> Traversal:
    """Follow `path` from `seeds` as the principal whose vertex mask is `mask`.

    `seeds` is a list of atom ids (score 1.0 each) or a mapping of atom id to
    a positive seed score. Seeds the principal cannot see, or that do not
    exist at the graph's CSN, are dropped as if they were never given.
    `valid_at` defaults to today (UTC). Raises `EngineError`,
    `LimitExceededError` or `UnknownDimensionError` for bad requests.
    """
    spec = _spec(operator)
    _check_bound("k", k, MAX_K)
    _check_bound("degree_cap", degree_cap, None)
    _check_bound("hop_limit", hop_limit, None)
    steps = parse_path(path, graph.registry, hop_limit)
    instant = valid_at if valid_at is not None else datetime.now(UTC).date()

    atoms = visible_atoms(graph, mask)
    vmask = vertex_mask_vector(atoms, graph.n)
    frontier = _seed_frontier(seeds, atoms, spec, graph.n)
    truncated = False
    for step in steps:
        hop = _build_hop(graph, step, atoms, instant, include_pending, spec, degree_cap)
        frontier, cut = _walk(frontier, hop, spec, vmask, k, hop_limit)
        truncated = truncated or cut

    ranked = rank(spec.operator, _entries(frontier, spec))
    if len(ranked) > k:
        ranked, truncated = ranked[:k], True
    results = tuple(
        TraversalResult(
            atom_id=r.atom_id, score=r.score, value=r.value, path=frontier.best[r.atom_id].path
        )
        for r in ranked
    )
    return Traversal(csn=graph.csn, results=results, truncated=truncated)


# Set-up ---------------------------------------------------------------------


def _spec(operator: Operator | str) -> OperatorSpec:
    try:
        return operator_spec(operator)
    except ValueError:
        names = ", ".join(o.value for o in Operator)
        raise EngineError(f"unknown operator {operator!r}; expected one of {names}") from None


def _check_bound(name: str, value: int, maximum: int | None) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise EngineError(f"{name} must be an int >= 1, got {value!r}")
    if maximum is not None and value > maximum:
        raise LimitExceededError(f"{name} is {value}; the maximum is {maximum}")


def _seed_frontier(
    seeds: Mapping[int, float] | Iterable[int],
    atoms: AbstractBitMap,
    spec: OperatorSpec,
    n: int,
) -> _Frontier:
    scores = dict(seeds) if isinstance(seeds, Mapping) else dict.fromkeys(seeds, 1.0)
    for atom_id, score in scores.items():
        if not isinstance(score, int | float) or isinstance(score, bool) or not 0 < score < inf:
            raise EngineError(f"seed {atom_id} needs a positive finite score, got {score!r}")
    visible = {a: float(s) for a, s in scores.items() if _is_atom_id(a) and a in atoms}
    top = max(visible.values(), default=1.0)
    ids = sorted(visible)
    strengths = [visible[a] / top for a in ids]
    values = Vector.from_coo(ids, [spec.seed_value(s) for s in strengths], size=n, dtype=spec.dtype)
    best = {a: _Best(strength=s, path=()) for a, s in zip(ids, strengths, strict=True)}
    return _Frontier(values=values, best=best)


def _is_atom_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < ATOM_ID_LIMIT


def _build_hop(
    graph: GraphMatrices,
    step: Step,
    atoms: AbstractBitMap,
    instant: date,
    include_pending: bool,
    spec: OperatorSpec,
    degree_cap: int,
) -> _Hop:
    dm = graph.dims[step.dim]
    view = edge_view(dm, atoms, instant, include_pending, graph.n)
    weights, capped = cap_degree(view.matrix(step.direction), degree_cap)
    return _Hop(
        step=step,
        dim_name=graph.registry.get(step.dim).name,
        view=view,
        weights=weights,
        operand=spec.operand(weights),
        capped_rows=capped,
    )


def cap_degree(weights: Any, cap: int) -> tuple[Any, BitMap]:
    """Keep each row's `cap` heaviest entries (ties: lower column id).

    Returns the capped matrix and the rows that lost entries. The input is
    already edge-masked, so hidden edges never take a place under the cap.
    """
    degrees = weights.reduce_rowwise(agg.count).new()
    rows, counts = degrees.to_coo()
    over = [int(r) for r, c in zip(rows.tolist(), counts.tolist(), strict=True) if c > cap]
    if not over:
        return weights, BitMap()
    drop_rows: list[int] = []
    drop_cols: list[int] = []
    for row in over:
        cols, vals = weights[row, :].new().to_coo()
        ordered = sorted(
            zip(vals.tolist(), cols.tolist(), strict=True), key=lambda e: (-e[0], e[1])
        )
        for _, col in ordered[cap:]:
            drop_rows.append(row)
            drop_cols.append(int(col))
    n = weights.nrows
    drop = Matrix.from_coo(
        drop_rows, drop_cols, [True] * len(drop_rows), nrows=n, ncols=n, dtype=dtypes.BOOL
    )
    return weights.dup(mask=~drop.S), BitMap(over)


# Walking --------------------------------------------------------------------


def _walk(
    frontier: _Frontier,
    hop: _Hop,
    spec: OperatorSpec,
    vmask: Any,
    k: int,
    hop_limit: int,
) -> tuple[_Frontier, bool]:
    if hop.step.hops == STAR:
        return _closure(frontier, hop, spec, vmask, k, hop_limit)
    truncated = False
    for _ in range(int(hop.step.hops)):
        if not frontier.best:
            break
        frontier, cut = _one_hop(frontier, hop, spec, vmask, k)
        truncated = truncated or cut
    return frontier, truncated


def _closure(
    frontier: _Frontier,
    hop: _Hop,
    spec: OperatorSpec,
    vmask: Any,
    k: int,
    hop_limit: int,
) -> tuple[_Frontier, bool]:
    """`hops: "*"`: accumulate `v ⊕ vA ⊕ vA² ⊕ …` until it stops changing."""
    total, current = frontier, frontier
    truncated = False
    for _ in range(hop_limit):
        current, cut = _one_hop(current, hop, spec, vmask, k)
        truncated = truncated or cut
        if not current.best:
            return total, truncated
        merged, cut = _merge(total, current, spec, k)
        truncated = truncated or cut
        if merged.values.isequal(total.values) and merged.best == total.best:
            return total, truncated
        total = merged
    # The hop limit stopped the closure before it settled.
    return total, True


def _one_hop(
    frontier: _Frontier, hop: _Hop, spec: OperatorSpec, vmask: Any, k: int
) -> tuple[_Frontier, bool]:
    values = frontier.values.vxm(hop.operand, spec.semiring).new(mask=vmask.S)
    capped = any(a in hop.capped_rows for a in frontier.best)
    best = _best_paths(frontier, values, hop, spec)
    kept, dropped = _top_k(values, best, spec, k)
    return kept, capped or dropped


def _best_paths(
    frontier: _Frontier, values: Any, hop: _Hop, spec: OperatorSpec
) -> dict[int, _Best]:
    """For each reached atom, the strongest single path into it (ties: lower `from_atom`)."""
    sources = sorted(frontier.best)
    targets = [int(t) for t in values.to_coo()[0].tolist()]
    if not sources or not targets:
        return {}
    rows, cols, weights = hop.weights[sources, targets].new().to_coo()
    best: dict[int, tuple[float, int, float]] = {}
    for r, c, w in zip(rows.tolist(), cols.tolist(), weights.tolist(), strict=True):
        src, dst = sources[r], targets[c]
        strength = spec.extend(frontier.best[src].strength, w)
        current = best.get(dst)
        if current is None or (strength, -src) > (current[0], -current[1]):
            best[dst] = (strength, src, w)
    return {
        dst: _Best(strength=s, path=frontier.best[src].path + (_path_step(hop, src, dst),))
        for dst, (s, src, _) in best.items()
    }


def _path_step(hop: _Hop, src: int, dst: int) -> PathStep:
    pair = (src, dst) if hop.step.direction == "fwd" else (dst, src)
    edge = hop.view.chosen[pair]
    return PathStep(
        from_atom=src,
        dim=hop.dim_name,
        direction=hop.step.direction,
        edge_id=edge.edge_id,
        weight=edge.weight,
        state=edge.state,
        valid_from=edge.valid_from,
        valid_to=edge.valid_to,
    )


def _merge(
    total: _Frontier, current: _Frontier, spec: OperatorSpec, k: int
) -> tuple[_Frontier, bool]:
    values = total.values.ewise_add(current.values, spec.add).new()
    best = dict(total.best)
    for atom_id, candidate in current.best.items():
        existing = best.get(atom_id)
        if existing is None or candidate.strength > existing.strength:
            best[atom_id] = candidate
    return _top_k(values, best, spec, k)


def _top_k(
    values: Any, best: Mapping[int, _Best], spec: OperatorSpec, k: int
) -> tuple[_Frontier, bool]:
    """Keep the `k` highest values (ties: stronger best path, then lower atom id)."""
    ids, vals = values.to_coo()
    entries = [(int(a), v) for a, v in zip(ids.tolist(), vals.tolist(), strict=True)]
    if len(entries) <= k:
        return _Frontier(values=values, best={a: best[a] for a, _ in entries}), False
    entries.sort(key=lambda e: (-spec.unit_value(e[1]), -best[e[0]].strength, e[0]))
    kept = sorted(entries[:k])
    vector = Vector.from_coo(
        [a for a, _ in kept], [v for _, v in kept], size=values.size, dtype=spec.dtype
    )
    return _Frontier(values=vector, best={a: best[a] for a, _ in kept}), True


def _entries(frontier: _Frontier, spec: OperatorSpec) -> list[tuple[int, float, float]]:
    ids, vals = frontier.values.to_coo()
    return [
        (int(a), spec.unit_value(v), frontier.best[int(a)].strength)
        for a, v in zip(ids.tolist(), vals.tolist(), strict=True)
    ]
