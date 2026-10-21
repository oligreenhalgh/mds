"""Final ranking of a traversal's frontier (DESIGN §5.4).

The score blends two things, both in `[0, 1]`:

- the operator's value for the atom, normalised: `boolean` is 1 for every
  reached atom; `count` is divided by the largest count in the result;
  `max_plus` is the best path's probability; `bottleneck` is its weakest link;
- the strength of the atom's best path, which carries the seed score and the
  confidence of every edge on that path.

`score = VALUE_WEIGHT * value + STRENGTH_WEIGHT * strength`

This formula is not part of the public contract (DESIGN §5.4); golden tests
pin its behaviour.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from mds.model import Operator

VALUE_WEIGHT = 0.7
STRENGTH_WEIGHT = 0.3


@dataclass(frozen=True, slots=True)
class Ranked:
    """One atom with its operator value, path strength and blended score."""

    atom_id: int
    value: float
    strength: float
    score: float


def rank(operator: Operator, entries: Iterable[tuple[int, float, float]]) -> list[Ranked]:
    """Score `(atom_id, value, strength)` entries and sort them best first.

    Ties break on the lower `atom_id`, so results are deterministic.
    """
    entries = list(entries)
    if not entries:
        return []
    top = max(v for _, v, _ in entries)
    ranked = [
        Ranked(atom_id=a, value=v, strength=s, score=_blend(_normalise(operator, v, top), s))
        for a, v, s in entries
    ]
    ranked.sort(key=lambda r: (-r.score, r.atom_id))
    return ranked


def _normalise(operator: Operator, value: float, top: float) -> float:
    if operator is Operator.COUNT:
        return value / top if top > 0 else 0.0
    return value


def _blend(value: float, strength: float) -> float:
    return VALUE_WEIGHT * value + STRENGTH_WEIGHT * strength
