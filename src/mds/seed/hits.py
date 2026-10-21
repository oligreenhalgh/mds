"""Result records shared by the seeders."""

from dataclasses import dataclass
from enum import StrEnum

from mds.model import AtomKind


class Match(StrEnum):
    """Which seeder produced a result (`match` in `mds_seed`, INTERFACES §2)."""

    LEXICAL = "lexical"
    DENSE = "dense"
    ENTITY = "entity"


@dataclass(frozen=True, slots=True)
class Hit:
    """One atom found by a single seeder, with that seeder's own score."""

    atom_id: int
    kind: AtomKind
    score: float


@dataclass(frozen=True, slots=True)
class SeedHit:
    """One fused seeding result: the fused score and the seeder that ranked it best."""

    atom_id: int
    kind: AtomKind
    score: float
    match: Match


def top_k(hits: list[Hit], k: int) -> list[Hit]:
    """Highest score first, ties by `atom_id` so results are deterministic."""
    return sorted(hits, key=lambda h: (-h.score, h.atom_id))[:k]
