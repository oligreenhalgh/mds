"""Edges: typed, directed, dated, attributed links between atoms (DESIGN.md section 4)."""

from dataclasses import dataclass
from datetime import date, datetime

from mds.model.dimensions import default_registry
from mds.model.enums import AuthorKind, EdgeState
from mds.model.errors import (
    ModelError,
    as_enum,
    check_atom_id,
    check_csn_interval,
    check_non_negative,
)


@dataclass(frozen=True, slots=True)
class Edge:
    """One edge record. `dim` may be given as an id or name and is stored as the id.

    `evidence` defaults to `(src, dst)`. Valid-time fields are only allowed on
    time-bound dimensions; the interval is `[valid_from, valid_to)`.
    """

    edge_id: int
    dim: str
    src: int
    dst: int
    weight: float
    author_kind: AuthorKind
    author_id: str
    state: EdgeState
    csn_from: int
    csn_to: int | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    evidence: tuple[int, ...] | None = None
    note: str = ""

    def __post_init__(self) -> None:
        check_non_negative(self.edge_id, "edge_id")
        dimension = default_registry().get(self.dim)
        object.__setattr__(self, "dim", dimension.id)
        check_atom_id(self.src, "src")
        check_atom_id(self.dst, "dst")
        object.__setattr__(
            self, "author_kind", as_enum(AuthorKind, self.author_kind, "author_kind")
        )
        object.__setattr__(self, "state", as_enum(EdgeState, self.state, "state"))
        if not self.author_id:
            raise ModelError("author_id must not be empty")
        _check_weight(self.weight)
        _check_valid_time(self.valid_from, self.valid_to, dimension.time_bound, dimension.id)
        check_csn_interval(self.csn_from, self.csn_to)

        evidence = (self.src, self.dst) if self.evidence is None else tuple(self.evidence)
        if not evidence:
            raise ModelError("evidence must not be empty")
        for atom_id in evidence:
            check_atom_id(atom_id, "evidence atom_id")
        object.__setattr__(self, "evidence", evidence)


def _check_weight(weight: float) -> None:
    if isinstance(weight, bool) or not isinstance(weight, int | float):
        raise ModelError(f"weight must be a number, got {type(weight).__name__}")
    # Written as a positive test so NaN fails too.
    if not 0.0 < weight <= 1.0:
        raise ModelError(f"weight must be in (0, 1], got {weight}")


def _check_valid_time(
    valid_from: date | None, valid_to: date | None, time_bound: bool, dim: str
) -> None:
    if not time_bound and (valid_from is not None or valid_to is not None):
        raise ModelError(f"dimension {dim} is not time-bound; valid_from/valid_to must be null")
    for name, value in (("valid_from", valid_from), ("valid_to", valid_to)):
        # datetime is a date subclass; valid time is a calendar date (DESIGN §6).
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise ModelError(f"{name} must be a date, got {type(value).__name__}")
    if valid_from is not None and valid_to is not None and valid_from >= valid_to:
        raise ModelError(f"valid_from ({valid_from}) must be before valid_to ({valid_to})")
