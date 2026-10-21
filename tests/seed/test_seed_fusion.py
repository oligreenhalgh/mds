import pytest

from mds.model import AtomKind
from mds.seed import RRF_K, Hit, Match, reciprocal_rank_fusion

S = AtomKind.SECTION
E = AtomKind.ENTITY


def hits(*ids: int, kind: AtomKind = S) -> list[Hit]:
    return [Hit(i, kind, 1.0) for i in ids]


def test_scores_are_sums_of_reciprocal_ranks() -> None:
    fused = reciprocal_rank_fusion({Match.LEXICAL: hits(1, 2, 3), Match.DENSE: hits(3, 1)}, k=10)
    scores = {h.atom_id: h.score for h in fused}
    assert scores[1] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    assert scores[2] == pytest.approx(1 / (RRF_K + 2))
    assert scores[3] == pytest.approx(1 / (RRF_K + 3) + 1 / (RRF_K + 1))
    assert [h.atom_id for h in fused] == [1, 3, 2]


def test_match_is_the_seeder_that_ranked_it_best() -> None:
    fused = reciprocal_rank_fusion({Match.LEXICAL: hits(1, 2), Match.DENSE: hits(2, 1)}, k=10)
    by_id = {h.atom_id: h.match for h in fused}
    assert by_id == {1: Match.LEXICAL, 2: Match.DENSE}


def test_equal_rank_prefers_entity_then_lexical_then_dense() -> None:
    fused = reciprocal_rank_fusion(
        {
            Match.DENSE: hits(1, kind=E),
            Match.LEXICAL: hits(1, kind=E),
            Match.ENTITY: hits(1, kind=E),
        },
        k=10,
    )
    assert fused[0].match is Match.ENTITY
    fused = reciprocal_rank_fusion({Match.DENSE: hits(1), Match.LEXICAL: hits(1)}, k=10)
    assert fused[0].match is Match.LEXICAL


def test_ties_break_by_atom_id_and_k_truncates() -> None:
    fused = reciprocal_rank_fusion({Match.LEXICAL: hits(5), Match.DENSE: hits(2)}, k=10)
    assert [h.atom_id for h in fused] == [2, 5]
    assert len(reciprocal_rank_fusion({Match.LEXICAL: hits(1, 2, 3)}, k=2)) == 2


def test_empty_rankings_fuse_to_nothing() -> None:
    assert reciprocal_rank_fusion({}, k=10) == []
    assert reciprocal_rank_fusion({Match.LEXICAL: []}, k=10) == []
