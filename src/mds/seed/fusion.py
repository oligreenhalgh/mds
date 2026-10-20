"""Reciprocal rank fusion of the seeders' rankings (DESIGN §8)."""

from collections.abc import Mapping, Sequence

from mds.seed.hits import Hit, Match, SeedHit

RRF_K = 60
# When an atom ranks equally well in two seeders, report the more specific match.
_PRIORITY = (Match.ENTITY, Match.LEXICAL, Match.DENSE)


def reciprocal_rank_fusion(
    rankings: Mapping[Match, Sequence[Hit]], *, k: int, rrf_k: int = RRF_K
) -> list[SeedHit]:
    """Fuse ranked lists: each atom scores the sum of `1 / (rrf_k + rank)`, rank from 1.

    Each result's `match` is the seeder that ranked it best. Ties in fused score
    are broken by `atom_id`.
    """
    scores: dict[int, float] = {}
    best: dict[int, tuple[int, int, Hit]] = {}
    for source, hits in rankings.items():
        priority = _PRIORITY.index(source)
        for rank, hit in enumerate(hits, start=1):
            scores[hit.atom_id] = scores.get(hit.atom_id, 0.0) + 1.0 / (rrf_k + rank)
            current = best.get(hit.atom_id)
            if current is None or (rank, priority) < current[:2]:
                best[hit.atom_id] = (rank, priority, hit)
    fused = [
        SeedHit(atom_id, best[atom_id][2].kind, s, _PRIORITY[best[atom_id][1]])
        for atom_id, s in scores.items()
    ]
    fused.sort(key=lambda h: (-h.score, h.atom_id))
    return fused[:k]
