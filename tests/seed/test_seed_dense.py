from collections.abc import Sequence

import pytest
from pyroaring import BitMap

from mds.model import AtomKind
from mds.seed import DenseIndex, Embedder, dense_text

S = AtomKind.SECTION


@pytest.fixture
def index(fake_embedder: Embedder) -> DenseIndex:
    dx = DenseIndex(fake_embedder)
    dx.add(1, 0, S, "Retention of personal data")
    dx.add(2, 0, S, "Holiday entitlement")
    dx.add(3, 1, AtomKind.DOCUMENT, "Personal data retention")
    return dx


ALL = BitMap([1, 2, 3])


def test_ranks_by_cosine_similarity(index: DenseIndex) -> None:
    hits = index.search("personal data retention", mask=ALL, k=10)
    assert [h.atom_id for h in hits][:2] == [3, 1]
    assert hits[0].score == pytest.approx(1.0)
    assert 2 not in {h.atom_id for h in hits}


def test_masks_before_scoring(index: DenseIndex) -> None:
    hits = index.search("personal data retention", mask=BitMap([1, 2]), k=10)
    assert [h.atom_id for h in hits] == [1]


def test_kinds_filter_and_k(index: DenseIndex) -> None:
    hits = index.search("personal data", mask=ALL, kinds={AtomKind.DOCUMENT}, k=10)
    assert [h.atom_id for h in hits] == [3]
    assert len(index.search("personal data", mask=ALL, k=1)) == 1


def test_replace_and_remove(index: DenseIndex) -> None:
    index.add(2, 0, S, "Personal data retention")
    index.remove(3)
    hits = index.search("personal data retention", mask=ALL, k=10)
    assert [h.atom_id for h in hits][0] == 2
    assert 3 not in {h.atom_id for h in hits}
    assert len(index) == 2


def test_empty_text_and_query_are_skipped(index: DenseIndex) -> None:
    index.add(9, 0, S, "")
    assert len(index) == 3
    assert index.search("", mask=ALL, k=10) == []
    assert index.search("???", mask=ALL, k=10) == []


def test_dimension_mismatch_is_an_error(index: DenseIndex) -> None:
    class Short:
        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    index._embedder = Short()
    with pytest.raises(ValueError, match="dimensions"):
        index.search("anything", mask=ALL, k=10)


def test_dense_text_is_title_and_first_line() -> None:
    assert dense_text("4.2 Retention", "\n\nData is kept.\nMore text.") == (
        "4.2 Retention\nData is kept."
    )
    assert dense_text("Acme", "") == "Acme"
    assert dense_text("Title", "Title\nbody") == "Title"
