"""The four traversal operators (DESIGN §5.3).

Each operator is a semiring `(⊕, ⊗)` plus the rules for single paths: how a
seed score enters the frontier and how one path's strength grows by an edge.
Path strength is always in the probability domain `(0, 1]`, whatever the
operator, so best paths and ranking compare like with like.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from graphblas import dtypes, monoid, semiring, unary

from mds.model import Operator


@dataclass(frozen=True, slots=True)
class OperatorSpec:
    """How one operator walks the graph.

    - `semiring`: the `⊕.⊗` used for `v ⊕.⊗ A`.
    - `add`: the `⊕` monoid used to merge frontiers (for `hops: "*"`).
    - `dtype`: frontier and matrix element type.
    - `operand`: turns a weight matrix into the operator's domain.
    - `seed_value`: frontier value of a seed with score `s`.
    - `unit_value`: frontier value mapped back to a plain float for ranking.
    - `extend`: one path's strength after following an edge of weight `w`.
    """

    operator: Operator
    semiring: Any
    add: Any
    dtype: Any
    operand: Callable[[Any], Any]
    seed_value: Callable[[float], Any]
    unit_value: Callable[[Any], float]
    extend: Callable[[float, float], float]


def _same(matrix: Any) -> Any:
    return matrix


def _as_bool(matrix: Any) -> Any:
    # Every stored weight is in (0, 1], so every entry becomes True.
    return matrix.dup(dtype=dtypes.BOOL)


def _log(matrix: Any) -> Any:
    return matrix.apply(unary.log).new()


def _product(strength: float, weight: float) -> float:
    return strength * weight


_SPECS = {
    Operator.BOOLEAN: OperatorSpec(
        operator=Operator.BOOLEAN,
        semiring=semiring.lor_land,
        add=monoid.lor,
        dtype=dtypes.BOOL,
        operand=_as_bool,
        seed_value=lambda s: True,
        unit_value=lambda v: 1.0,
        extend=_product,
    ),
    Operator.COUNT: OperatorSpec(
        operator=Operator.COUNT,
        semiring=semiring.plus_times,
        add=monoid.plus,
        dtype=dtypes.FP64,
        operand=_same,
        seed_value=float,
        unit_value=float,
        extend=_product,
    ),
    Operator.MAX_PLUS: OperatorSpec(
        operator=Operator.MAX_PLUS,
        semiring=semiring.max_plus,
        add=monoid.max,
        dtype=dtypes.FP64,
        operand=_log,
        seed_value=math.log,
        unit_value=lambda v: math.exp(v),
        extend=_product,
    ),
    Operator.BOTTLENECK: OperatorSpec(
        operator=Operator.BOTTLENECK,
        semiring=semiring.max_min,
        add=monoid.max,
        dtype=dtypes.FP64,
        operand=_same,
        seed_value=float,
        unit_value=float,
        extend=min,
    ),
}


def operator_spec(operator: Operator | str) -> OperatorSpec:
    """The spec for an operator name, raising `ValueError` for unknown names."""
    return _SPECS[Operator(operator)]
