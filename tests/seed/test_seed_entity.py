import pytest
from pyroaring import BitMap

from mds.model import AtomKind
from mds.seed import EntityIndex
from mds.seed.entity import CONTAINED_NAME, EXACT_ALIAS, EXACT_NAME


@pytest.fixture
def index() -> EntityIndex:
    ex = EntityIndex()
    ex.add(1, "Acme Holdings Ltd", aliases=["Acme"])
    ex.add(2, "Beta Corp")
    ex.add(3, "Société Générale", aliases=["SocGen"])
    return ex


ALL = BitMap([1, 2, 3])


def test_exact_name_beats_alias(index: EntityIndex) -> None:
    index.add(4, "Acme")
    hits = index.search("acme", mask=BitMap([1, 4]), k=10)
    assert [(h.atom_id, h.score) for h in hits] == [(4, EXACT_NAME), (1, EXACT_ALIAS)]
    assert all(h.kind is AtomKind.ENTITY for h in hits)


def test_match_ignores_case_accents_and_punctuation(index: EntityIndex) -> None:
    assert [h.atom_id for h in index.search("SOCIETE generale!", mask=ALL, k=10)] == [3]
    assert [h.atom_id for h in index.search("acme holdings, ltd.", mask=ALL, k=10)] == [1]


def test_names_inside_a_longer_query(index: EntityIndex) -> None:
    hits = index.search("what did Beta Corp agree with Acme", mask=ALL, k=10)
    assert [h.atom_id for h in hits] == [2, 1]
    assert hits[0].score == pytest.approx(CONTAINED_NAME * 2 / 7)
    assert all(h.score < EXACT_ALIAS for h in hits)


def test_partial_names_do_not_match(index: EntityIndex) -> None:
    assert index.search("Beta", mask=ALL, k=10) == []
    assert index.search("Holdings", mask=ALL, k=10) == []


def test_masked_entities_are_never_returned(index: EntityIndex) -> None:
    assert index.search("Acme", mask=BitMap([2, 3]), k=10) == []


def test_kinds_filter(index: EntityIndex) -> None:
    assert index.search("Acme", mask=ALL, kinds={AtomKind.SECTION}, k=10) == []
    assert index.search("Acme", mask=ALL, kinds={AtomKind.ENTITY}, k=10) != []


def test_replace_and_remove(index: EntityIndex) -> None:
    index.add(1, "Acme Holdings Ltd")
    assert index.search("Acme", mask=ALL, k=10) == []
    index.remove(2)
    assert index.search("Beta Corp", mask=ALL, k=10) == []
    index.remove(99)


def test_k_truncates(index: EntityIndex) -> None:
    assert len(index.search("Beta Corp and Acme", mask=ALL, k=1)) == 1
