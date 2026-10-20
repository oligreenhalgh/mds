import pytest

from mds.ingest.similarity import shingles, signature


def words(prefix: str, n: int) -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def test_shingles_are_lowercase_word_trigrams() -> None:
    assert shingles("The Quick, brown fox!") == {"the quick brown", "quick brown fox"}


def test_short_and_empty_texts() -> None:
    assert shingles("two words") == {"two words"}
    assert shingles("  ...  ") == frozenset()


def test_identical_texts_have_jaccard_one() -> None:
    text = words("a", 50)
    assert signature(text).jaccard(signature(text)) == 1.0


def test_disjoint_texts_have_jaccard_near_zero() -> None:
    assert signature(words("a", 50)).jaccard(signature(words("b", 50))) < 0.1


def test_empty_text_is_never_similar() -> None:
    assert signature("").jaccard(signature("")) == 0.0
    assert signature("").containment_in(signature(words("a", 10))) == 0.0


def test_signatures_are_deterministic() -> None:
    text = words("a", 30)
    assert signature(text).minhash.digest().tolist() == signature(text).minhash.digest().tolist()


def test_containment_of_a_half() -> None:
    half = words("a", 100)
    whole = half + " " + words("b", 100)
    j = signature(half).jaccard(signature(whole))
    assert j == pytest.approx(0.5, abs=0.12)
    assert signature(half).containment_in(signature(whole)) == pytest.approx(1.0, abs=0.12)
    assert signature(whole).containment_in(signature(half)) == pytest.approx(0.5, abs=0.12)
